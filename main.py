from __future__ import annotations

"""
交易信号机器人 v2 — 事件驱动模式
只在信号真正触发时推送，避免行情噪音：
  - 新信号出现（之前是 NEUTRAL）→ 推送
  - 信号方向反转（BUY→SELL 或反之）→ 推送
  - 信号强度升级（星级提升≥2）→ 推送
  - 同方向信号：4小时冷却期内不重复推送
"""

import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from analyzer import SignalResult, analyze
from config import SCAN_INTERVAL_MINUTES, SYMBOLS
from discord_notifier import send_signal, send_startup_message
from market_data import fetch_multi_tf, fetch_ticker

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("signal_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── 信号状态管理 ──────────────────────────────────────────────────────────────
STATE_FILE    = Path("signal_state.json")
COOLDOWN_HRS  = 4   # 同方向信号冷却时间（小时）


def _load_state() -> dict:
    """加载上次推送状态"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    """保存当前推送状态"""
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _should_send(result: SignalResult, state: dict) -> bool:
    """
    判断是否需要推送信号：
    1. 信号未达门槛（<3星 或 NEUTRAL）→ 不推送
    2. 该币种首次出现有效信号 → 推送
    3. 信号方向反转 → 推送
    4. 信号星级提升 ≥2 → 推送（大幅增强）
    5. 同方向但超过冷却期 → 推送
    6. 其余情况 → 不推送（避免噪音）
    """
    if not result.should_send:
        return False

    sym  = result.symbol
    prev = state.get(sym)

    # 首次出现有效信号
    if prev is None:
        return True

    prev_dir   = prev.get("direction", "NEUTRAL")
    prev_stars = prev.get("stars", 0)

    # 方向反转
    if prev_dir != result.direction:
        return True

    # 信号大幅增强（星级+2）
    if result.stars >= prev_stars + 2:
        return True

    # 冷却期检查
    sent_at_str = prev.get("sent_at", "")
    if sent_at_str:
        try:
            sent_at = datetime.fromisoformat(sent_at_str)
            hours_elapsed = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600
            if hours_elapsed >= COOLDOWN_HRS:
                return True
        except Exception:
            return True

    # 同方向 + 冷却期内 → 不重复推送
    return False


def _update_state(state: dict, result: SignalResult) -> None:
    state[result.symbol] = {
        "direction": result.direction,
        "stars":     result.stars,
        "sent_at":   datetime.now(timezone.utc).isoformat(),
    }


# ── 核心扫描函数 ──────────────────────────────────────────────────────────────

def scan_market(state: dict) -> dict:
    """
    扫描所有币种，只在条件触发时推送信号。
    返回更新后的 state。
    """
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("═" * 55)
    logger.info("开始静默扫描  %s", scan_time)
    logger.info("═" * 55)

    sent_count = 0

    for symbol in SYMBOLS:
        try:
            logger.info("分析 %s ...", symbol)

            tf_data = fetch_multi_tf(symbol)
            if tf_data is None:
                logger.warning("⚠️  %s 数据不可用，跳过", symbol)
                continue

            ticker = fetch_ticker(symbol)
            if ticker is None:
                logger.warning("⚠️  %s ticker 获取失败，跳过", symbol)
                continue

            result = analyze(symbol, tf_data, ticker)

            status = "—"
            if result.should_send:
                if _should_send(result, state):
                    status = "🔔 推送"
                    send_signal(result)
                    _update_state(state, result)
                    sent_count += 1
                    time.sleep(0.5)
                else:
                    status = "🔇 冷却中（同方向已推送）"
            else:
                status = f"静默（{result.stars}星 未达门槛）"

            logger.info(
                "  %s → %s %d星  %s",
                symbol, result.direction, result.stars, status,
            )

        except Exception as exc:
            logger.error("分析 %s 时异常: %s", symbol, exc, exc_info=True)

    logger.info("扫描完成：本轮推送 %d 个信号", sent_count)
    return state


# ── 调度器 ────────────────────────────────────────────────────────────────────

_stop_event = threading.Event()


def _scheduler_loop() -> None:
    interval = SCAN_INTERVAL_MINUTES * 60
    state = _load_state()
    while not _stop_event.is_set():
        try:
            state = scan_market(state)
            _save_state(state)
        except Exception as exc:
            logger.error("扫描异常: %s", exc, exc_info=True)
        logger.info("下次扫描将在 %d 分钟后进行...", SCAN_INTERVAL_MINUTES)
        _stop_event.wait(interval)


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="加密货币交易信号机器人 v2（事件驱动）")
    parser.add_argument("--once", action="store_true", help="单次扫描（GitHub Actions 模式）")
    args = parser.parse_args()

    logger.info("交易信号机器人 v2 启动（事件驱动模式）")
    logger.info("推送规则：新信号/方向反转/升级≥2星 → 推送；同方向冷却 %dh", COOLDOWN_HRS)

    send_startup_message(SYMBOLS, [])

    if args.once:
        state = _load_state()
        state = scan_market(state)
        _save_state(state)
    else:
        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()
        try:
            while thread.is_alive():
                thread.join(timeout=1)
        except KeyboardInterrupt:
            logger.info("正在停止...")
            _stop_event.set()
            thread.join(timeout=5)
            logger.info("已停止。")


if __name__ == "__main__":
    main()
