from __future__ import annotations

"""
交易信号机器人 v2 — 主程序
支持：
  --once   单次扫描（GitHub Actions 模式）
  无参数   持续循环运行（本地后台模式）
"""

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timezone

from analyzer import analyze
from config import SCAN_INTERVAL_MINUTES, SYMBOLS
from discord_notifier import send_signal, send_startup_message, send_summary
from market_data import fetch_multi_tf, fetch_ticker, validate_symbol

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


# ── 核心扫描函数 ──────────────────────────────────────────────────────────────

def scan_market() -> None:
    """扫描所有配置的币种，分析信号并推送到 Discord"""
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("═" * 55)
    logger.info("开始市场扫描  %s", scan_time)
    logger.info("═" * 55)

    results = []
    for symbol in SYMBOLS:
        try:
            logger.info("分析 %s ...", symbol)

            # 拉取多时间框架数据（同时验证币种可用性）
            tf_data = fetch_multi_tf(symbol)
            if tf_data is None:
                logger.warning("⚠️  %s 数据不可用，跳过", symbol)
                continue

            # 拉取 24h ticker
            ticker = fetch_ticker(symbol)
            if ticker is None:
                logger.warning("⚠️  %s ticker 获取失败，跳过", symbol)
                continue

            # 综合分析
            result = analyze(symbol, tf_data, ticker)
            results.append(result)

            logger.info(
                "  %s → %s  %d星  价格=%s  信号=%s",
                symbol,
                result.direction,
                result.stars,
                result.price,
                "触发" if result.should_send else "未达门槛",
            )

            # 达到门槛才推送单独信号卡
            if result.should_send:
                send_signal(result)
                time.sleep(0.5)   # 避免频率限制

        except Exception as exc:
            logger.error("分析 %s 时出现异常: %s", symbol, exc, exc_info=True)

    # 汇总推送
    if results:
        send_summary(results, scan_time)

    triggered_count = sum(1 for r in results if r.should_send)
    logger.info("扫描完成：%d/%d 个币种触发信号（≥3星）", triggered_count, len(results))


# ── 调度器 ────────────────────────────────────────────────────────────────────

_stop_event = threading.Event()


def _scheduler_loop() -> None:
    interval = SCAN_INTERVAL_MINUTES * 60
    while not _stop_event.is_set():
        try:
            scan_market()
        except Exception as exc:
            logger.error("扫描异常: %s", exc, exc_info=True)
        logger.info("下次扫描将在 %d 分钟后进行...", SCAN_INTERVAL_MINUTES)
        _stop_event.wait(interval)


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="加密货币交易信号机器人 v2")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一次扫描（用于 GitHub Actions）",
    )
    args = parser.parse_args()

    logger.info("交易信号机器人 v2 启动")
    logger.info("配置：%d 个币种，最低%d星触发", len(SYMBOLS), 3)

    # 验证并分类币种
    valid_symbols, skipped_symbols = [], []
    for sym in SYMBOLS:
        ok, _ = validate_symbol(sym)
        if ok:
            valid_symbols.append(sym)
        else:
            skipped_symbols.append(sym)

    if not valid_symbols:
        logger.error("没有可用的币种，请检查配置！")
        sys.exit(1)

    logger.info("有效币种（%d）：%s", len(valid_symbols), ", ".join(valid_symbols))
    if skipped_symbols:
        logger.warning("已跳过不可用币种：%s", ", ".join(skipped_symbols))

    # 发送启动通知
    send_startup_message(valid_symbols, skipped_symbols)

    if args.once:
        # GitHub Actions 模式：单次扫描
        scan_market()
    else:
        # 本地持续运行模式
        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()
        try:
            while thread.is_alive():
                thread.join(timeout=1)
        except KeyboardInterrupt:
            logger.info("接收到中断信号，正在停止...")
            _stop_event.set()
            thread.join(timeout=5)
            logger.info("机器人已停止。")


if __name__ == "__main__":
    main()
