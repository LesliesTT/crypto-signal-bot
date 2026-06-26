from __future__ import annotations
"""
技术指标分析模块（纯 numpy / pandas 实现，无需第三方 ta 库）
计算 MA / RSI / MACD / 布林带，输出综合信号
"""
import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field
from config import (
    MA_FAST, MA_SLOW,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BOLL_PERIOD, BOLL_STD,
)

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    symbol: str
    direction: str          # "BUY" | "SELL" | "NEUTRAL"
    score: int              # 信号强度 0-4
    price: float
    change_24h: float

    ma_fast: float = 0.0
    ma_slow: float = 0.0
    rsi: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    boll_upper: float = 0.0
    boll_mid: float = 0.0
    boll_lower: float = 0.0

    reasons: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 纯 pandas / numpy 指标函数
# ─────────────────────────────────────────────────────────────

def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int, slow: int, signal: int):
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _bbands(series: pd.Series, period: int, std_mult: float):
    mid = _sma(series, period)
    std = series.rolling(window=period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]

    df[f"ma{MA_FAST}"] = _sma(close, MA_FAST)
    df[f"ma{MA_SLOW}"] = _sma(close, MA_SLOW)
    df["rsi"] = _rsi(close, RSI_PERIOD)

    macd_line, sig_line, hist = _macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    df["macd"]        = macd_line
    df["macd_signal"] = sig_line
    df["macd_hist"]   = hist

    boll_upper, boll_mid, boll_lower = _bbands(close, BOLL_PERIOD, BOLL_STD)
    df["boll_upper"] = boll_upper
    df["boll_mid"]   = boll_mid
    df["boll_lower"] = boll_lower

    return df


def _safe(val) -> float:
    try:
        if pd.isna(val):
            return 0.0
    except Exception:
        pass
    return float(val) if val is not None else 0.0


def analyze(symbol: str, df: pd.DataFrame, price: float, change_24h: float):
    """
    对最新一根 K 线综合评分，返回 SignalResult

    评分规则（每项 +0.5 / +1 分）：
      MA 金叉 → BUY+1  |  MA 死叉 → SELL+1
      多头排列 → BUY+0.5  |  空头排列 → SELL+0.5
      RSI<超卖 → BUY+1  |  RSI>超买 → SELL+1
      MACD柱由负转正 → BUY+1  |  由正转负 → SELL+1
      布林下轨 → BUY+1  |  布林上轨 → SELL+1
    """
    if df is None or len(df) < MA_SLOW + 5:
        logger.warning(f"[{symbol}] 数据不足，跳过分析")
        return None

    df = compute_indicators(df.copy())
    last = df.iloc[-1]
    prev = df.iloc[-2]

    ma_fast_cur  = _safe(last.get(f"ma{MA_FAST}"))
    ma_slow_cur  = _safe(last.get(f"ma{MA_SLOW}"))
    ma_fast_prev = _safe(prev.get(f"ma{MA_FAST}"))
    ma_slow_prev = _safe(prev.get(f"ma{MA_SLOW}"))

    rsi_cur  = _safe(last.get("rsi"))
    macd_cur = _safe(last.get("macd"))
    macd_sig = _safe(last.get("macd_signal"))
    hist_cur = _safe(last.get("macd_hist"))
    hist_pre = _safe(prev.get("macd_hist"))

    boll_upper = _safe(last.get("boll_upper"))
    boll_mid   = _safe(last.get("boll_mid"))
    boll_lower = _safe(last.get("boll_lower"))

    buy_score  = 0.0
    sell_score = 0.0
    reasons    = []

    # ── MA 均线 ──────────────────────────────────────────────
    if ma_fast_cur > 0 and ma_slow_cur > 0:
        if ma_fast_cur > ma_slow_cur and ma_fast_prev <= ma_slow_prev:
            buy_score += 1
            reasons.append(f"✅ MA金叉：MA{MA_FAST} 上穿 MA{MA_SLOW}")
        elif ma_fast_cur < ma_slow_cur and ma_fast_prev >= ma_slow_prev:
            sell_score += 1
            reasons.append(f"🔴 MA死叉：MA{MA_FAST} 下穿 MA{MA_SLOW}")
        elif ma_fast_cur > ma_slow_cur:
            buy_score += 0.5
            reasons.append(f"📈 多头排列：MA{MA_FAST} > MA{MA_SLOW}")
        else:
            sell_score += 0.5
            reasons.append(f"📉 空头排列：MA{MA_FAST} < MA{MA_SLOW}")

    # ── RSI ──────────────────────────────────────────────────
    if rsi_cur > 0:
        if rsi_cur < RSI_OVERSOLD:
            buy_score += 1
            reasons.append(f"✅ RSI超卖：RSI = {rsi_cur:.1f} < {RSI_OVERSOLD}")
        elif rsi_cur > RSI_OVERBOUGHT:
            sell_score += 1
            reasons.append(f"🔴 RSI超买：RSI = {rsi_cur:.1f} > {RSI_OVERBOUGHT}")
        else:
            reasons.append(f"➖ RSI中性：RSI = {rsi_cur:.1f}")

    # ── MACD ─────────────────────────────────────────────────
    if hist_cur != 0 or hist_pre != 0:
        if hist_cur > 0 and hist_pre <= 0:
            buy_score += 1
            reasons.append(f"✅ MACD金叉：柱状图由负转正 ({hist_cur:.4f})")
        elif hist_cur < 0 and hist_pre >= 0:
            sell_score += 1
            reasons.append(f"🔴 MACD死叉：柱状图由正转负 ({hist_cur:.4f})")
        elif hist_cur > 0:
            buy_score += 0.5
            reasons.append(f"📈 MACD看多：柱状图 {hist_cur:.4f} > 0")
        else:
            sell_score += 0.5
            reasons.append(f"📉 MACD看空：柱状图 {hist_cur:.4f} < 0")

    # ── 布林带 ───────────────────────────────────────────────
    if boll_upper > 0 and boll_lower > 0:
        band_width = boll_upper - boll_lower
        if band_width > 0:
            pct = (price - boll_lower) / band_width
            if price <= boll_lower or pct < 0.1:
                buy_score += 1
                reasons.append(f"✅ 布林下轨触及：价格接近下轨 {boll_lower:.4f}")
            elif price >= boll_upper or pct > 0.9:
                sell_score += 1
                reasons.append(f"🔴 布林上轨触及：价格接近上轨 {boll_upper:.4f}")
            else:
                reasons.append(f"➖ 布林中性：价格位于通道中部 ({pct*100:.0f}%)")

    # ── 综合判断 ─────────────────────────────────────────────
    net = buy_score - sell_score
    total_score = int(max(buy_score, sell_score))

    if net > 0:
        direction = "BUY"
    elif net < 0:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    return SignalResult(
        symbol=symbol,
        direction=direction,
        score=total_score,
        price=price,
        change_24h=change_24h,
        ma_fast=ma_fast_cur,
        ma_slow=ma_slow_cur,
        rsi=rsi_cur,
        macd=macd_cur,
        macd_signal=macd_sig,
        macd_hist=hist_cur,
        boll_upper=boll_upper,
        boll_mid=boll_mid,
        boll_lower=boll_lower,
        reasons=reasons,
    )
