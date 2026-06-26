from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd
import requests

from config import TIMEFRAMES

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"
# 备用：期货 API（部分商品/指数只有期货合约）
FAPI_URL = "https://fapi.binance.com"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "CryptoSignalBot/2.0"})

# 运行时缓存：已验证可用的币种
_VALID_SPOT: set[str] = set()
_VALID_FUTURES: set[str] = set()
_CHECKED: set[str] = set()


def _get(url: str, params: dict, timeout: int = 10) -> Optional[dict | list]:
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
            time.sleep(1.5)
    return None


def validate_symbol(symbol: str) -> tuple[bool, str]:
    """
    验证币种是否可交易，优先现货，其次期货。
    返回 (可用, 类型) — 类型为 'spot' | 'futures' | ''
    """
    if symbol in _CHECKED:
        if symbol in _VALID_SPOT:
            return True, "spot"
        if symbol in _VALID_FUTURES:
            return True, "futures"
        return False, ""

    _CHECKED.add(symbol)

    # 尝试现货
    data = _get(f"{BASE_URL}/api/v3/ticker/price", {"symbol": symbol})
    if data and "price" in data:
        _VALID_SPOT.add(symbol)
        return True, "spot"

    # 尝试期货
    data = _get(f"{FAPI_URL}/fapi/v1/ticker/price", {"symbol": symbol})
    if data and "price" in data:
        _VALID_FUTURES.add(symbol)
        return True, "futures"

    logger.warning("⚠️  %s 在现货和期货均不可用，已跳过", symbol)
    return False, ""


def _klines_url(symbol: str) -> tuple[str, str]:
    """根据币种类型返回对应 K 线 URL 和 endpoint"""
    if symbol in _VALID_FUTURES:
        return f"{FAPI_URL}/fapi/v1/klines", "futures"
    return f"{BASE_URL}/api/v3/klines", "spot"


def fetch_klines(symbol: str, interval: str, limit: int = 250) -> Optional[pd.DataFrame]:
    """拉取 K 线数据，返回 OHLCV DataFrame"""
    url, _ = _klines_url(symbol)
    data = _get(url, {"symbol": symbol, "interval": interval, "limit": limit})
    if not data:
        return None
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
        return None


def fetch_ticker(symbol: str) -> Optional[dict]:
    """拉取 24h ticker 数据"""
    if symbol in _VALID_FUTURES:
        data = _get(f"{FAPI_URL}/fapi/v1/ticker/24hr", {"symbol": symbol})
    else:
        data = _get(f"{BASE_URL}/api/v3/ticker/24hr", {"symbol": symbol})
    return data


def fetch_multi_tf(symbol: str) -> Optional[dict[str, pd.DataFrame]]:
    """
    同时拉取 1h / 4h / 1d 三个时间框架的 K 线数据。
    返回 {'1h': df, '4h': df, '1d': df} 或 None（币种不可用）
    """
    ok, _ = validate_symbol(symbol)
    if not ok:
        return None

    result: dict[str, pd.DataFrame] = {}
    for tf_name, tf_cfg in TIMEFRAMES.items():
        df = fetch_klines(symbol, tf_cfg["interval"], tf_cfg["limit"])
        if df is None or len(df) < 50:
            logger.warning("%s %s 数据不足，跳过", symbol, tf_name)
            return None
        result[tf_name] = df

    return result
