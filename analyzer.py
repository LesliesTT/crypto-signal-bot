from __future__ import annotations

"""
增强版分析引擎
融合指标：
  - EMA20（趋势过滤）
  - Vegas 通道（EMA144 / EMA169）
  - ICT Order Blocks（订单块）
  - ICT Fair Value Gaps（公平价值缺口）
  - ICT 市场结构（BOS / 趋势判断）
  - 流动性扫描 / 假突破 vs 真突破（成交量验证）
  - 价格行为形态（Pin Bar、吞没、内包）
  - ATR 止损止盈建议
  - 多时间框架趋势确认
  - 1-5 星综合评分（≥3 星才触发推送）
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    ATR_PERIOD, ATR_SL_MULT, ATR_TP1_MULT, ATR_TP2_MULT,
    BREAKOUT_VOLUME_MULT, EMA_SHORT, EMA_VEGAS_FAST, EMA_VEGAS_SLOW,
    FVG_LOOKBACK, LIQUIDITY_SWEEP_TOLERANCE, MIN_STAR_RATING,
    MTF_BONUS, ORDER_BLOCK_LOOKBACK, RSI_PERIOD, VOLUME_MA_PERIOD,
)

logger = logging.getLogger(__name__)


# ─── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass
class TimeframeTrend:
    label: str           # '1h' / '4h' / '1d'
    trend: str           # 'UPTREND' / 'DOWNTREND' / 'NEUTRAL'
    emoji: str           # 🟢 / 🔴 / 🟡
    price: float = 0.0
    ema20: float = 0.0
    vegas_fast: float = 0.0
    vegas_slow: float = 0.0
    above_ema20: bool = False
    above_vegas: bool = False
    below_vegas: bool = False


@dataclass
class SignalResult:
    symbol: str
    direction: str           # 'BUY' / 'SELL' / 'NEUTRAL'
    stars: int               # 1-5
    score: int               # 原始分
    price: float
    change_24h: float
    volume_24h: float
    triggered_models: list[str] = field(default_factory=list)
    tf_trends: list[TimeframeTrend] = field(default_factory=list)
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    atr: float = 0.0
    rr_ratio: str = ""
    should_send: bool = False  # 是否达到最低星级门槛


# ─── 基础指标计算 ─────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series]:
    """返回 (MACD线, 信号线)"""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line


def _detect_divergence(price: pd.Series, indicator: pd.Series, lookback: int = 30) -> str:
    """
    检测 RSI / MACD 的顶背离和底背离。

    底背离（看涨）：价格创近期新低，但指标未创新低 → 下跌动能减弱
    顶背离（看跌）：价格创近期新高，但指标未创新高 → 上涨动能减弱

    返回 'BULLISH' | 'BEARISH' | 'NONE'
    """
    if len(price) < lookback + 5:
        return "NONE"

    p = price.iloc[-lookback:].values
    ind = indicator.iloc[-lookback:].values

    # 找摆动低点（局部最低）
    def local_lows(arr, window=5):
        lows = []
        for i in range(window, len(arr) - window):
            if arr[i] == min(arr[i - window: i + window + 1]):
                lows.append(i)
        return lows

    def local_highs(arr, window=5):
        highs = []
        for i in range(window, len(arr) - window):
            if arr[i] == max(arr[i - window: i + window + 1]):
                highs.append(i)
        return highs

    # 底背离：价格低点下降，指标低点上升
    p_lows  = local_lows(p)
    i_lows  = local_lows(ind)
    if len(p_lows) >= 2 and len(i_lows) >= 2:
        # 取最近两个摆动低点
        pl1, pl2 = p_lows[-2], p_lows[-1]
        # 寻找对应指标低点（时间窗口±3根K线）
        il_near = [il for il in i_lows if abs(il - pl2) <= 4]
        il_prev = [il for il in i_lows if abs(il - pl1) <= 4]
        if il_near and il_prev:
            # 价格新低 & 指标未创新低
            if p[pl2] < p[pl1] and ind[il_near[-1]] > ind[il_prev[-1]]:
                return "BULLISH"

    # 顶背离：价格高点上升，指标高点下降
    p_highs = local_highs(p)
    i_highs = local_highs(ind)
    if len(p_highs) >= 2 and len(i_highs) >= 2:
        ph1, ph2 = p_highs[-2], p_highs[-1]
        ih_near = [ih for ih in i_highs if abs(ih - ph2) <= 4]
        ih_prev = [ih for ih in i_highs if abs(ih - ph1) <= 4]
        if ih_near and ih_prev:
            # 价格新高 & 指标未创新高
            if p[ph2] > p[ph1] and ind[ih_near[-1]] < ind[ih_prev[-1]]:
                return "BEARISH"

    return "NONE"


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─── ICT：市场结构 ────────────────────────────────────────────────────────────

def _swing_points(df: pd.DataFrame, window: int = 5) -> tuple[list, list]:
    """
    识别摆动高点和低点。
    返回 (swing_highs, swing_lows) 各为 [(index, price)] 列表。
    """
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)
    sh, sl = [], []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            sh.append((i, highs[i]))
        if lows[i] == min(lows[i - window: i + window + 1]):
            sl.append((i, lows[i]))
    return sh, sl


def _market_structure(df: pd.DataFrame) -> str:
    """返回 'UPTREND' / 'DOWNTREND' / 'NEUTRAL'"""
    sh, sl = _swing_points(df, window=5)
    if len(sh) < 2 or len(sl) < 2:
        return "NEUTRAL"
    hh = sh[-1][1] > sh[-2][1]
    hl = sl[-1][1] > sl[-2][1]
    lh = sh[-1][1] < sh[-2][1]
    ll = sl[-1][1] < sl[-2][1]
    if hh and hl:
        return "UPTREND"
    if lh and ll:
        return "DOWNTREND"
    return "NEUTRAL"


# ─── ICT：Order Blocks ────────────────────────────────────────────────────────

def _find_order_blocks(df: pd.DataFrame, lookback: int = 60) -> tuple[list, list]:
    """
    看涨OB：空头K线后出现强力多头上涨（价格随后突破OB高点且尚未收盘跌破OB低点）。
    看跌OB：多头K线后出现强力空头下跌（价格随后跌破OB低点且尚未收盘涨过OB高点）。
    返回 (bullish_obs, bearish_obs) — 每个为 {'high', 'low'} dict 列表。
    """
    recent = df.tail(lookback).reset_index(drop=True)
    n = len(recent)
    bull_obs, bear_obs = [], []

    for i in range(0, n - 3):
        c  = recent.iloc[i]
        n1 = recent.iloc[i + 1]
        n2 = recent.iloc[i + 2]

        # 看涨 OB：空头蜡烛 → 后续多头冲击突破OB高点
        if c["close"] < c["open"]:
            impulse = (n2["close"] - c["close"]) / (c["close"] + 1e-9)
            if impulse > 0.004 and n1["close"] > n1["open"]:
                ob = {"high": c["high"], "low": c["low"]}
                # 检查后续K线未收盘跌破低点（OB仍有效）
                subsequent = recent.iloc[i + 1:]
                if not any(subsequent["close"] < ob["low"]):
                    bull_obs.append(ob)

        # 看跌 OB：多头蜡烛 → 后续空头冲击跌破OB低点
        if c["close"] > c["open"]:
            impulse = (c["close"] - n2["close"]) / (c["close"] + 1e-9)
            if impulse > 0.004 and n1["close"] < n1["open"]:
                ob = {"high": c["high"], "low": c["low"]}
                subsequent = recent.iloc[i + 1:]
                if not any(subsequent["close"] > ob["high"]):
                    bear_obs.append(ob)

    return bull_obs, bear_obs


def _price_near_ob(price: float, obs: list, tolerance: float = 0.015) -> bool:
    """价格是否在 OB 区域附近（±1.5%）"""
    for ob in obs:
        mid = (ob["high"] + ob["low"]) / 2
        if abs(price - mid) / mid <= tolerance:
            return True
        # 也检查是否在 OB 范围内
        if ob["low"] * (1 - tolerance) <= price <= ob["high"] * (1 + tolerance):
            return True
    return False


# ─── ICT：Fair Value Gaps ─────────────────────────────────────────────────────

def _find_fvg(df: pd.DataFrame, lookback: int = 40) -> tuple[list, list]:
    """
    看涨FVG：candle[i-2].high < candle[i].low（K线间存在向上缺口）。
    看跌FVG：candle[i-2].low  > candle[i].high（K线间存在向下缺口）。
    返回近期未填补的 FVG 列表。
    """
    recent = df.tail(lookback + 2).reset_index(drop=True)
    n = len(recent)
    bull_fvgs, bear_fvgs = [], []

    for i in range(2, n):
        c0 = recent.iloc[i - 2]
        c2 = recent.iloc[i]
        if c0["high"] < c2["low"]:
            bull_fvgs.append({"upper": c2["low"], "lower": c0["high"]})
        elif c0["low"] > c2["high"]:
            bear_fvgs.append({"upper": c0["low"], "lower": c2["high"]})

    return bull_fvgs, bear_fvgs


def _price_in_fvg(price: float, fvgs: list) -> bool:
    """价格是否位于 FVG 区域内（±0.5% 容忍）"""
    tol = 0.005
    for fvg in fvgs[-5:]:   # 只看最近5个
        lo = fvg["lower"] * (1 - tol)
        hi = fvg["upper"] * (1 + tol)
        if lo <= price <= hi:
            return True
    return False


# ─── 突破分析：真实 vs 假突破 ─────────────────────────────────────────────────

def _breakout_analysis(df: pd.DataFrame) -> Optional[dict]:
    """
    基于20根K线高低点判断是否发生突破，并通过成交量验证真假。
    返回 {'direction': 'UP'/'DOWN', 'type': 'REAL'/'FAKE', 'vol_ratio': float} 或 None。
    """
    if len(df) < 25:
        return None
    vol_ma = df["volume"].rolling(VOLUME_MA_PERIOD).mean()
    current_vol = df["volume"].iloc[-1]
    avg_vol = vol_ma.iloc[-2]
    if avg_vol == 0:
        return None
    vol_ratio = current_vol / avg_vol

    lookback_h = df["high"].iloc[-22:-2].max()
    lookback_l = df["low"].iloc[-22:-2].min()
    cur_h = df["high"].iloc[-1]
    cur_l = df["low"].iloc[-1]

    direction = None
    if cur_h > lookback_h:
        direction = "UP"
    elif cur_l < lookback_l:
        direction = "DOWN"

    if direction is None:
        return None

    btype = "REAL" if vol_ratio >= BREAKOUT_VOLUME_MULT else "FAKE"
    return {"direction": direction, "type": btype, "vol_ratio": round(vol_ratio, 2)}


# ─── 价格行为形态 ─────────────────────────────────────────────────────────────

def _price_action_patterns(df: pd.DataFrame) -> list[dict]:
    """检测最近两根K线的价格行为形态"""
    if len(df) < 3:
        return []
    last = df.iloc[-1]
    prev = df.iloc[-2]
    patterns = []

    body   = abs(last["close"] - last["open"])
    total  = last["high"] - last["low"]
    if total < 1e-9:
        return patterns
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]

    # 看涨 Pin Bar（锤子线）
    if (lower_wick > 2 * body and
            lower_wick > upper_wick * 2 and
            body < total * 0.35):
        patterns.append({"name": "🔨 锤子线 (看涨Pin Bar)", "dir": "BUY"})

    # 看跌 Pin Bar（流星线）
    if (upper_wick > 2 * body and
            upper_wick > lower_wick * 2 and
            body < total * 0.35):
        patterns.append({"name": "⭐ 流星线 (看跌Pin Bar)", "dir": "SELL"})

    # 看涨吞没
    prev_body = abs(prev["close"] - prev["open"])
    if (last["close"] > last["open"] and
            prev["close"] < prev["open"] and
            last["open"] <= prev["close"] and
            last["close"] >= prev["open"] and
            body > prev_body * 0.8):
        patterns.append({"name": "🟢 看涨吞没", "dir": "BUY"})

    # 看跌吞没
    if (last["close"] < last["open"] and
            prev["close"] > prev["open"] and
            last["open"] >= prev["close"] and
            last["close"] <= prev["open"] and
            body > prev_body * 0.8):
        patterns.append({"name": "🔴 看跌吞没", "dir": "SELL"})

    # 内包线（Inside Bar）—— 盘整/蓄势
    if last["high"] < prev["high"] and last["low"] > prev["low"]:
        patterns.append({"name": "📦 内包线 (蓄势)", "dir": "NEUTRAL"})

    return patterns


# ─── 单时间框架分析 ───────────────────────────────────────────────────────────

def _analyze_tf(df: pd.DataFrame, label: str) -> TimeframeTrend:
    """计算单个时间框架的趋势状态"""
    close = df["close"]
    price = float(close.iloc[-1])

    ema20      = float(_ema(close, EMA_SHORT).iloc[-1])
    vegas_fast = float(_ema(close, EMA_VEGAS_FAST).iloc[-1])
    vegas_slow = float(_ema(close, EMA_VEGAS_SLOW).iloc[-1])

    ms = _market_structure(df)
    above_ema20  = price > ema20
    above_vegas  = price > max(vegas_fast, vegas_slow)
    below_vegas  = price < min(vegas_fast, vegas_slow)
    in_tunnel    = not above_vegas and not below_vegas

    # 综合趋势判断
    if ms == "UPTREND" and above_ema20:
        trend, emoji = "上涨", "🟢"
    elif ms == "DOWNTREND" and not above_ema20:
        trend, emoji = "下跌", "🔴"
    elif ms == "UPTREND" or above_ema20:
        trend, emoji = "偏多", "🟡"
    elif ms == "DOWNTREND" or not above_ema20:
        trend, emoji = "偏空", "🟠"
    else:
        trend, emoji = "盘整", "⚪"

    return TimeframeTrend(
        label=label,
        trend=trend,
        emoji=emoji,
        price=price,
        ema20=ema20,
        vegas_fast=vegas_fast,
        vegas_slow=vegas_slow,
        above_ema20=above_ema20,
        above_vegas=above_vegas,
        below_vegas=below_vegas,
    )


# ─── 主分析入口 ───────────────────────────────────────────────────────────────

def analyze(
    symbol: str,
    tf_data: dict[str, pd.DataFrame],
    ticker: dict,
) -> SignalResult:
    """
    综合分析入口。
    tf_data: {'1h': df, '4h': df, '1d': df}
    ticker:  Binance 24hr ticker dict
    """
    price      = float(ticker.get("lastPrice", ticker.get("price", 0)))
    change_24h = float(ticker.get("priceChangePercent", 0))
    volume_24h = float(ticker.get("quoteVolume", ticker.get("volume", 0)))

    df_1h = tf_data["1h"]
    df_4h = tf_data["4h"]
    df_1d = tf_data["1d"]

    # ── 多时间框架趋势 ───────────────────────────────────────────────────────
    tf_1h = _analyze_tf(df_1h, "1H")
    tf_4h = _analyze_tf(df_4h, "4H")
    tf_1d = _analyze_tf(df_1d, "日线")
    tf_trends = [tf_1h, tf_4h, tf_1d]

    buy_score:  int = 0
    sell_score: int = 0
    triggered: list[str] = []

    # ── EMA20 方向分（用1H框架）──────────────────────────────────────────────
    if tf_1h.above_ema20:
        buy_score += 1
        triggered.append("✅ EMA20 多头排列")
    else:
        sell_score += 1
        triggered.append("✅ EMA20 空头排列")

    # ── Vegas 通道（用1H框架）────────────────────────────────────────────────
    if tf_1h.above_vegas:
        buy_score += 1
        triggered.append("✅ Vegas通道：价格在通道上方（看涨）")
    elif tf_1h.below_vegas:
        sell_score += 1
        triggered.append("✅ Vegas通道：价格在通道下方（看跌）")

    # ── ICT Order Blocks（用1H）──────────────────────────────────────────────
    bull_obs, bear_obs = _find_order_blocks(df_1h, ORDER_BLOCK_LOOKBACK)
    at_bull_ob = _price_near_ob(price, bull_obs)
    at_bear_ob = _price_near_ob(price, bear_obs)
    if at_bull_ob:
        buy_score += 2
        triggered.append(f"✅ ICT 看涨订单块支撑（共 {len(bull_obs)} 个有效OB）")
    if at_bear_ob:
        sell_score += 2
        triggered.append(f"✅ ICT 看跌订单块压力（共 {len(bear_obs)} 个有效OB）")

    # ── ICT Fair Value Gaps（用1H）───────────────────────────────────────────
    bull_fvg, bear_fvg = _find_fvg(df_1h, FVG_LOOKBACK)
    if _price_in_fvg(price, bull_fvg):
        buy_score += 1
        triggered.append("✅ ICT 看涨FVG（价值缺口支撑）")
    if _price_in_fvg(price, bear_fvg):
        sell_score += 1
        triggered.append("✅ ICT 看跌FVG（价值缺口阻力）")

    # ── 突破分析（用1H）─────────────────────────────────────────────────────
    bo = _breakout_analysis(df_1h)
    if bo:
        if bo["type"] == "REAL":
            label = f"✅ 真实突破 {'向上' if bo['direction'] == 'UP' else '向下'} (量能{bo['vol_ratio']}x)"
            if bo["direction"] == "UP":
                buy_score += 1
            else:
                sell_score += 1
            triggered.append(label)
        else:
            triggered.append(f"⚠️  假突破 {'向上' if bo['direction'] == 'UP' else '向下'} (量能不足{bo['vol_ratio']}x)")

    # ── 价格行为形态（用1H）──────────────────────────────────────────────────
    pa_patterns = _price_action_patterns(df_1h)
    for pat in pa_patterns:
        triggered.append(f"✅ 价格行为：{pat['name']}")
        if pat["dir"] == "BUY":
            buy_score += 1
        elif pat["dir"] == "SELL":
            sell_score += 1

    # ── RSI 水平 + 背离（用1H）──────────────────────────────────────────────
    rsi_series = _rsi(df_1h["close"], RSI_PERIOD)
    rsi_val = float(rsi_series.iloc[-1])
    if rsi_val < 35:
        buy_score += 1
        triggered.append(f"✅ RSI超卖 ({rsi_val:.1f})")
    elif rsi_val > 65:
        sell_score += 1
        triggered.append(f"✅ RSI超买 ({rsi_val:.1f})")

    rsi_div = _detect_divergence(df_1h["close"], rsi_series, lookback=30)
    if rsi_div == "BULLISH":
        buy_score += 2
        triggered.append(f"✅ RSI 底背离（价格新低，RSI未创新低）")
    elif rsi_div == "BEARISH":
        sell_score += 2
        triggered.append(f"✅ RSI 顶背离（价格新高，RSI未创新高）")

    # ── MACD 背离（用1H）────────────────────────────────────────────────────
    macd_line, _ = _macd(df_1h["close"])
    macd_div = _detect_divergence(df_1h["close"], macd_line, lookback=30)
    if macd_div == "BULLISH":
        buy_score += 2
        triggered.append("✅ MACD 底背离（价格新低，MACD柱未创新低）")
    elif macd_div == "BEARISH":
        sell_score += 2
        triggered.append("✅ MACD 顶背离（价格新高，MACD柱未创新高）")

    # ── 多时间框架对齐奖励 ───────────────────────────────────────────────────
    bullish_tfs = sum(1 for tf in tf_trends if "上涨" in tf.trend or "偏多" in tf.trend)
    bearish_tfs = sum(1 for tf in tf_trends if "下跌" in tf.trend or "偏空" in tf.trend)

    if bullish_tfs == 3:
        buy_score += MTF_BONUS
        triggered.append("✅ 三个时间框架均看涨（强烈多头共振）")
    elif bullish_tfs == 2:
        buy_score += 1
        triggered.append("✅ 两个时间框架看涨（多头共振）")
    elif bearish_tfs == 3:
        sell_score += MTF_BONUS
        triggered.append("✅ 三个时间框架均看跌（强烈空头共振）")
    elif bearish_tfs == 2:
        sell_score += 1
        triggered.append("✅ 两个时间框架看跌（空头共振）")

    # ── 综合方向与星级 ───────────────────────────────────────────────────────
    net = buy_score - sell_score
    if net > 0:
        direction = "BUY"
        score = buy_score
    elif net < 0:
        direction = "SELL"
        score = sell_score
    else:
        direction = "NEUTRAL"
        score = max(buy_score, sell_score)

    # 星级映射（最高5星）
    stars = min(5, max(1, (score + 1) // 2))

    # ── ATR 止损止盈 ─────────────────────────────────────────────────────────
    atr_series = _atr(df_1h, ATR_PERIOD)
    atr_val = float(atr_series.iloc[-1])

    if direction == "BUY":
        sl  = price - atr_val * ATR_SL_MULT
        tp1 = price + atr_val * ATR_TP1_MULT
        tp2 = price + atr_val * ATR_TP2_MULT
    elif direction == "SELL":
        sl  = price + atr_val * ATR_SL_MULT
        tp1 = price - atr_val * ATR_TP1_MULT
        tp2 = price - atr_val * ATR_TP2_MULT
    else:
        sl = tp1 = tp2 = 0.0

    # 风险回报比
    if sl != 0 and sl != price:
        risk   = abs(price - sl)
        reward = abs(tp1 - price)
        rr     = f"1:{reward / risk:.1f}" if risk > 0 else "—"
    else:
        rr = "—"

    should_send = stars >= MIN_STAR_RATING and direction != "NEUTRAL"

    return SignalResult(
        symbol=symbol,
        direction=direction,
        stars=stars,
        score=score,
        price=price,
        change_24h=change_24h,
        volume_24h=volume_24h,
        triggered_models=triggered,
        tf_trends=tf_trends,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        atr=atr_val,
        rr_ratio=rr,
        should_send=should_send,
    )
