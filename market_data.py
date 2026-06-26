from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd
import requests

from config import TIMEFRAMES

logger = logging.getLogger(__name__)

# Binance 公开数据节点（无地区限制，适合 GitHub Actions）
DATA_URL  = "https://data-api.binance.vision"
# 备用：标准节点
BASE_URL  = "https://api.binance.com"
# 期货节点
FAPI_URL  = "https://fapi.binance.com"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "CryptoSignalBot/2.0"})


def _get(url: str, params: dict, timeout: int = 15) -> Optional[dict | list]:
    """带重试的 GET 请求"""
    for attempt in range(3):
        try:
            resp = _SESSION.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("HTTP %s for %s (attempt %d)", resp.status_code, url, attempt + 1)
        except Exception as exc:
            logger.warning("请求失败 %s: %s (attempt %d)", url, exc, attempt + 1)
        if attempt < 2:
            time.sleep(2)
    return None


def fetch_klines(symbol: str, interval: str, limit: int = 250) -> Optional[pd.DataFrame]:
    """
    拉取 K 线数据。
    优先使用 data-api.binance.vision（无地区限制），
    失败则回退到标准 API，再失败则尝试期货 API。
    """
    urls = [
        f"{DATA_URL}/api/v3/klines",   # 公开节点，无限制
        f"{BASE_URL}/api/v3/klines",   # 标准现货
        f"{FAPI_URL}/fapi/v1/klines",  # 期货（XAUUSDT 等）
    ]
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    for url in urls:
        data = _get(url, params)
        if data and isinstance(data, list) and len(data) > 0:
            try:
                df = pd.DataFrame(data, columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_volume", "trades",
                    "taker_buy_base", "taker_buy_quote", "ignore"
                ])
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
                df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
                df.reset_index(drop=True, inplace=True)
                return df
            except Exception as exc:
                logger.error("解析K线失败 %s %s: %s", symbol, interval, exc)

    logger.warning("⚠️  %s %s 所有节点均无法获取数据", symbol, interval)
    return None


def fetch_ticker(symbol: str) -> Optional[dict]:
    """
    拉取 24h ticker 数据。
    优先公开节点，失败回退标准节点，再失败尝试期货。
    """
    urls = [
        f"{DATA_URL}/api/v3/ticker/24hr",
        f"{BASE_URL}/api/v3/ticker/24hr",
        f"{FAPI_URL}/fapi/v1/ticker/24hr",
    ]
    params = {"symbol": symbol}
    for url in urls:
        data = _get(url, params)
        if data and isinstance(data, dict) and "lastPrice" in data:
            return data
    return None


def fetch_multi_tf(symbol: str) -> Optional[dict[str, pd.DataFrame]]:
    """
    同时拉取 1h / 4h / 1d 三个时间框架的 K 线数据。
    返回 {'1h': df, '4h': df, '1d': df} 或 None（数据不可用）
    不做预验证，直接尝试拉取数据，失败则跳过。
    """
    result: dict[str, pd.DataFrame] = {}
    for tf_name, tf_cfg in TIMEFRAMES.items():
        df = fetch_klines(symbol, tf_cfg["interval"], tf_cfg["limit"])
        if df is None or len(df) < 50:
            logger.warning("%s %s 数据不足或不可用，跳过该币种", symbol, tf_name)
            return None
        result[tf_name] = df

    return result
