from __future__ import annotations
"""
交易信号机器人 - 主程序入口

用法:
    python main.py           # 立即扫描一次，然后定时循环
    python main.py --once    # 只扫描一次后退出（调试/测试用）
"""
import sys
import logging
import time
import threading
from datetime import datetime, timezone

from config import (
    SYMBOLS, TIMEFRAME, SCAN_INTERVAL_MINUTES,
    MIN_SIGNAL_SCORE, ONLY_DIRECTIONAL_SIGNALS,
)
from market_data import fetch_klines, fetch_ticker_price, fetch_24h_change
from analyzer import analyze
from discord_notifier import send_signal, send_summary, send_startup_message

# ──────────────────────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("signal_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def scan_market():
    """扫描所有币种，生成并推送信号"""
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"=== 开始市场扫描 [{scan_time}] ===")

    all_results = []

    for symbol in SYMBOLS:
        logger.info(f"  → 分析 {symbol} ...")

        # 1. 获取 K 线
        df = fetch_klines(symbol, TIMEFRAME)
        if df is None:
            continue

        # 2. 获取实时价格 & 24h 数据
        price = fetch_ticker_price(symbol)
        if price is None:
            price = float(df.iloc[-1]["close"])

        stats = fetch_24h_change(symbol)
        change_24h = stats["price_change_pct"] if stats else 0.0

        # 3. 技术分析
        result = analyze(symbol, df, price, change_24h)
        if result is None:
            continue

        all_results.append(result)

        logger.info(
            f"     {symbol}: {result.direction} (强度 {result.score}) "
            f"价格={price:.4f} RSI={result.rsi:.1f}"
        )

        # 4. 是否推送单条信号
        should_push = (
            result.score >= MIN_SIGNAL_SCORE
            and (not ONLY_DIRECTIONAL_SIGNALS or result.direction != "NEUTRAL")
        )
        if should_push:
            ok = send_signal(result)
            logger.info(f"     ✓ Discord 推送{'成功' if ok else '失败'}")
        else:
            logger.info(f"     · 信号强度不足或为中性，跳过推送")

    # 5. 发送汇总报告
    if all_results:
        send_summary(all_results, scan_time)
        logger.info(f"汇总报告已发送，共分析 {len(all_results)} 个币种")

    logger.info("=== 扫描完成 ===\n")


def _scheduler(interval_seconds: int, stop_event: threading.Event):
    """后台定时线程：每 interval_seconds 秒执行一次 scan_market"""
    while not stop_event.is_set():
        stop_event.wait(interval_seconds)
        if not stop_event.is_set():
            scan_market()


def main():
    once_mode = "--once" in sys.argv

    logger.info("🤖 交易信号机器人启动")
    logger.info(f"   监控币种: {', '.join(SYMBOLS)}")
    logger.info(f"   时间周期: {TIMEFRAME.upper()}")
    logger.info(f"   扫描间隔: {SCAN_INTERVAL_MINUTES} 分钟")

    # 启动通知
    send_startup_message()

    # 立即执行一次
    scan_market()

    if once_mode:
        logger.info("--once 模式，退出")
        return

    # 定时循环
    interval_seconds = SCAN_INTERVAL_MINUTES * 60
    stop_event = threading.Event()
    t = threading.Thread(
        target=_scheduler,
        args=(interval_seconds, stop_event),
        daemon=True,
    )
    t.start()
    logger.info(f"已设置定时任务，每 {SCAN_INTERVAL_MINUTES} 分钟执行一次（Ctrl+C 停止）")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在停止...")
        stop_event.set()
        t.join(timeout=5)
        logger.info("机器人已停止")


if __name__ == "__main__":
    main()
