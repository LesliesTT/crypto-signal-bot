from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from analyzer import SignalResult
from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)

COLOR_BUY     = 0x00C805
COLOR_SELL    = 0xFF3B30
COLOR_NEUTRAL = 0x8E8E93
COLOR_SUMMARY = 0x5865F2
COLOR_STARTUP = 0xFFCC00

STAR_MAP = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


def _post(payload: dict) -> bool:
    if DISCORD_WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        logger.error("Discord Webhook URL 未配置！")
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        logger.error("Discord 推送失败: HTTP %s — %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Discord 推送异常: %s", exc)
    return False


def _fmt(price: float, symbol: str) -> str:
    """根据价格大小自动选择小数位数"""
    if price >= 10000:
        return f"{price:,.2f}"
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _direction_header(result: SignalResult) -> str:
    stars = STAR_MAP.get(result.stars, "⭐")
    icon = "📈" if result.direction == "BUY" else ("📉" if result.direction == "SELL" else "⚪")
    label = "做多信号" if result.direction == "BUY" else ("做空信号" if result.direction == "SELL" else "观望")
    return f"{icon} {label}  {stars} ({result.stars}/5星)"


def _tf_trend_line(result: SignalResult) -> str:
    return "  |  ".join(f"{tf.label}: {tf.emoji} {tf.trend}" for tf in result.tf_trends)


def _entry_fields(result: SignalResult) -> list[dict]:
    """入场建议区间"""
    if result.direction == "NEUTRAL" or result.entry_price == 0:
        return []
    sym = result.symbol

    # 如果有 OB/FVG 提供精确区间
    if result.entry_zone_low != result.entry_zone_high:
        zone_str = f"`{_fmt(result.entry_zone_low, sym)}` — `{_fmt(result.entry_zone_high, sym)}`"
    else:
        zone_str = f"`{_fmt(result.entry_price, sym)}`（当前价）"

    entry_pct = abs(result.entry_price - result.price) / result.price * 100
    entry_note = f"（距现价 {entry_pct:.1f}%）" if entry_pct > 0.05 else "（当前即入场机会）"

    return [
        {
            "name": "🎯 建议入场价",
            "value": f"`{_fmt(result.entry_price, sym)}` {entry_note}",
            "inline": True,
        },
        {
            "name": "📐 入场区间",
            "value": zone_str,
            "inline": True,
        },
    ]


def _ob_fields(result: SignalResult) -> list[dict]:
    """有效 OB 位置（只列与信号方向一致的）"""
    if not result.signal_obs:
        return []
    sym = result.symbol
    icon = "🟢" if result.direction == "BUY" else "🔴"
    label = "看涨OB 支撑区" if result.direction == "BUY" else "看跌OB 压力区"

    lines = []
    for i, ob in enumerate(result.signal_obs[:3], 1):
        lo = _fmt(ob["low"], sym)
        hi = _fmt(ob["high"], sym)
        lines.append(f"OB{i}：`{lo}` — `{hi}`")

    return [{
        "name": f"{icon} {label}（订单块）",
        "value": "\n".join(lines),
        "inline": False,
    }]


def _fvg_fields(result: SignalResult) -> list[dict]:
    """有效 FVG 位置（只列与信号方向一致的）"""
    if not result.signal_fvgs:
        return []
    sym = result.symbol
    icon = "🔵" if result.direction == "BUY" else "🟣"
    label = "看涨FVG 缺口支撑" if result.direction == "BUY" else "看跌FVG 缺口阻力"

    lines = []
    for i, fvg in enumerate(result.signal_fvgs[:2], 1):
        lo = _fmt(fvg["lower"], sym)
        hi = _fmt(fvg["upper"], sym)
        lines.append(f"FVG{i}：`{lo}` — `{hi}`")

    return [{
        "name": f"{icon} {label}（价值缺口）",
        "value": "\n".join(lines),
        "inline": False,
    }]


def _sl_tp_fields(result: SignalResult) -> list[dict]:
    if result.direction == "NEUTRAL" or result.sl == 0:
        return []
    sym = result.symbol
    sl_pct  = abs(result.price - result.sl)  / result.price * 100
    tp1_pct = abs(result.tp1 - result.price) / result.price * 100
    tp2_pct = abs(result.tp2 - result.price) / result.price * 100
    arrow = "⬆️" if result.direction == "BUY" else "⬇️"

    return [
        {"name": "🛡 止损 (SL)",          "value": f"`{_fmt(result.sl, sym)}`  ({sl_pct:.1f}%)",    "inline": True},
        {"name": f"🎯 止盈1 (TP1) {arrow}", "value": f"`{_fmt(result.tp1, sym)}`  (+{tp1_pct:.1f}%)", "inline": True},
        {"name": f"🎯 止盈2 (TP2) {arrow}", "value": f"`{_fmt(result.tp2, sym)}`  (+{tp2_pct:.1f}%)", "inline": True},
        {"name": "⚖️ 风险回报",            "value": f"`{result.rr_ratio}`",                           "inline": True},
    ]


def send_signal(result: SignalResult) -> bool:
    """推送单个信号卡片"""
    color = COLOR_BUY if result.direction == "BUY" else (COLOR_SELL if result.direction == "SELL" else COLOR_NEUTRAL)
    sym = result.symbol
    chg_emoji = "🔺" if result.change_24h >= 0 else "🔻"
    model_lines = "\n".join(result.triggered_models[:10]) or "无触发模型"

    now_str = datetime.now(timezone.utc).strftime("今天%H:%M")

    fields: list[dict] = [
        {"name": "💰 当前价格", "value": f"`{_fmt(result.price, sym)}`",          "inline": True},
        {"name": "📊 24H涨跌",  "value": f"{chg_emoji} {result.change_24h:+.2f}%", "inline": True},
        {"name": "📈 多时间框架趋势", "value": _tf_trend_line(result),             "inline": False},
        {"name": "🔔 触发信号",       "value": model_lines,                        "inline": False},
    ]

    # 入场建议
    fields += _entry_fields(result)

    # OB 位置（信号方向）
    fields += _ob_fields(result)

    # FVG 位置（信号方向）
    fields += _fvg_fields(result)

    # 止损止盈
    fields += _sl_tp_fields(result)

    embed = {
        "title": f"**{sym}**  —  {_direction_header(result)}",
        "color": color,
        "fields": fields,
        "footer": {"text": f"CryptoSignalBot v2  ·  {now_str}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ok = _post({"embeds": [embed]})
    if ok:
        logger.info("✅ 已推送信号: %s %s %s星", sym, result.direction, result.stars)
    return ok


def send_startup_message(valid_symbols: list[str], skipped: list[str]) -> bool:
    skip_text = "、".join(skipped) if skipped else "无"
    desc = (
        f"🚀 **CryptoSignalBot v2 已启动**\n\n"
        f"✅ 监控币种（{len(valid_symbols)}个）：{', '.join(valid_symbols)}\n"
        f"⚠️ 跳过不可用：{skip_text}\n\n"
        f"📡 分析模型：ICT订单块 · FVG · Vegas通道(144/169) · EMA20 · "
        f"RSI/MACD背离 · 价格行为 · 假突破检测 · 多时间框架共振\n\n"
        f"🎯 信号推送规则：新信号/方向反转/升级≥2星 才推送，同方向4h内不重复\n"
        f"⭐ 最低门槛：3星（满分5星）"
    )
    embed = {
        "description": desc,
        "color": COLOR_STARTUP,
        "footer": {"text": f"CryptoSignalBot v2  ·  {datetime.now(timezone.utc).strftime('今天%H:%M')}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _post({"embeds": [embed]})
