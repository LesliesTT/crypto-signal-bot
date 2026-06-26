from __future__ import annotations
"""
行情获取模块 - 通过 Binance 公开 REST API 拉取 K 线数据
无需 API Key
"""
import requests
import pandas as pd
import logging
from config import BINANCE_BASE_URL, KLINE_LIMIT

logger = logging.getLogger(__name__)


def fetch_klines(symbol: str, interval: str, limit: int = KLINE_LIMIT) -> pd.DataFrame | None:
    """
    获取指定交易对的 K 线数据

    参数:
        symbol:   交易对，如 "BTCUSDT"
        interval: 时间周期，如 "1h"
        limit:    K 线数量

    返回:
        DataFrame，列: [open_time, open, high, low, close, volume]
        或 None（请求失败时）
    """
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        logger.error(f"[{symbol}] 获取 K 线失败: {e}")
        return None

    if not raw:
        logger.warning(f"[{symbol}] 返回数据为空")
        return None

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    # 类型转换
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df.reset_index(drop=True, inplace=True)

    return df


def fetch_ticker_price(symbol: str) -> float | None:
    """获取最新价格"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    try:
        resp = requests.get(url, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        logger.error(f"[{symbol}] 获取最新价格失败: {e}")
        return None


def fetch_24h_change(symbol: str) -> dict | None:
    """获取 24 小时涨跌幅"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return {
            "price_change_pct": float(data["priceChangePercent"]),
            "high_24h": float(data["highPrice"]),
            "low_24h": float(data["lowPrice"]),
            "volume_24h": float(data["volume"]),
        }
    except Exception as e:
        logger.error(f"[{symbol}] 获取 24h 数据失败: {e}")
        return None
