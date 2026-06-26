from __future__ import annotations

"""
Discord 信号推送模块（增强版）
信号卡包含：
  - 方向 + 星级（⭐⭐⭐⭐⭐）
  - 多时间框架趋势（1H / 4H / 日线）
  - 触发的模型列表
  - 止损 / 止盈1 / 止盈2
  - 风险回报比
"""

import logging
from datetime import datetime, timezone

import requests

from analyzer import SignalResult
from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)

# 颜色常量（Discord Embed 左侧条颜色）
COLOR_BUY     = 0x00C805   # 绿
COLOR_SELL    = 0xFF3B30   # 红
COLOR_NEUTRAL = 0x8E8E93   # 灰
COLOR_SUMMARY = 0x5865F2   # Discord 紫
COLOR_STARTUP = 0xFFCC00   # 黄

STAR_MAP = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


def _post(payload: dict) -> bool:
    """发送 Webhook 请求"""
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


def _fmt_price(price: float, symbol: str) -> str:
    """根据价格大小选择合适的小数位数"""
    if "XAU" in symbol or "NAS" in symbol:
        return f"{price:,.2f}"
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _direction_header(result: SignalResult) -> str:
    stars = STAR_MAP.get(result.stars, "⭐")
    if result.direction == "BUY":
        return f"📈 做多信号  {stars} ({result.stars}/5星)"
    if result.direction == "SELL":
        return f"📉 做空信号  {stars} ({result.stars}/5星)"
    return f"⚪ 观望  {stars} ({result.stars}/5星)"


def _tf_trend_line(result: SignalResult) -> str:
    parts = []
    for tf in result.tf_trends:
        parts.append(f"{tf.label}: {tf.emoji} {tf.trend}")
    return "  |  ".join(parts)


def _sl_tp_fields(result: SignalResult) -> list[dict]:
    """生成止损止盈 Embed Fields"""
    if result.direction == "NEUTRAL" or result.sl == 0:
        return []
    sym = result.symbol
    sl_pct  = abs(result.price - result.sl)  / result.price * 100
    tp1_pct = abs(result.tp1 - result.price) / result.price * 100
    tp2_pct = abs(result.tp2 - result.price) / result.price * 100

    arrow = "⬆️" if result.direction == "BUY" else "⬇️"

    return [
        {
            "name": "🛡 止损 (SL)",
            "value": f"`{_fmt_price(result.sl, sym)}`  ({sl_pct:.1f}%)",
            "inline": True,
        },
        {
            "name": f"🎯 止盈1 (TP1) {arrow}",
            "value": f"`{_fmt_price(result.tp1, sym)}`  (+{tp1_pct:.1f}%)",
            "inline": True,
        },
        {
            "name": f"🎯 止盈2 (TP2) {arrow}",
            "value": f"`{_fmt_price(result.tp2, sym)}`  (+{tp2_pct:.1f}%)",
            "inline": True,
        },
        {
            "name": "⚖️ 风险回报",
            "value": f"`{result.rr_ratio}`",
            "inline": True,
        },
    ]


def send_signal(result: SignalResult) -> bool:
    """推送单个信号卡片"""
    color = COLOR_BUY if result.direction == "BUY" else (COLOR_SELL if result.direction == "SELL" else COLOR_NEUTRAL)
    sym = result.symbol
    price_str = _fmt_price(result.price, sym)
    chg_emoji = "🔺" if result.change_24h >= 0 else "🔻"
    chg_str   = f"{chg_emoji} {result.change_24h:+.2f}%"

    # 触发的模型（最多显示10条，防止超出Embed限制）
    model_lines = "\n".join(result.triggered_models[:10]) or "无触发模型"

    fields: list[dict] = [
        {"name": "💰 当前价格", "value": f"`{price_str}`", "inline": True},
        {"name": "📊 24H涨跌", "value": chg_str, "inline": True},
        {"name": "📈 多时间框架趋势", "value": _tf_trend_line(result), "inline": False},
        {"name": "🔔 触发信号", "value": model_lines, "inline": False},
    ]
    fields += _sl_tp_fields(result)

    embed = {
        "title": f"**{sym}**  —  {_direction_header(result)}",
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"CryptoSignalBot v2  •  ICT + Vegas + 价格行为 + 背离分析"
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload = {"embeds": [embed]}
    ok = _post(payload)
    if ok:
        logger.info("✅ 已推送信号: %s %s %s星", sym, result.direction, result.stars)
    return ok


def send_summary(results: list[SignalResult], scan_time: str) -> bool:
    """推送本轮扫描汇总"""
    buy_list  = [r for r in results if r.direction == "BUY"  and r.should_send]
    sell_list = [r for r in results if r.direction == "SELL" and r.should_send]
    neutral   = [r for r in results if not r.should_send]

    def fmt_row(r: SignalResult) -> str:
        stars = STAR_MAP.get(r.stars, "")
        chg = f"{r.change_24h:+.2f}%"
        return f"**{r.symbol}** {stars}  `{_fmt_price(r.price, r.symbol)}`  {chg}"

    buy_text  = "\n".join(fmt_row(r) for r in buy_list)  or "—"
    sell_text = "\n".join(fmt_row(r) for r in sell_list) or "—"

    # 按涨跌幅排序观望列表
    neutral_sorted = sorted(neutral, key=lambda x: x.change_24h, reverse=True)
    watch_text = "\n".join(
        f"{r.symbol}  `{_fmt_price(r.price, r.symbol)}`  {r.change_24h:+.2f}%"
        for r in neutral_sorted
    ) or "—"

    total_signals = len(buy_list) + len(sell_list)

    embed = {
        "title": f"📋 市场扫描汇总 — {scan_time}",
        "description": f"共扫描 **{len(results)}** 个币种，触发信号 **{total_signals}** 个（≥3星）",
        "color": COLOR_SUMMARY,
        "fields": [
            {"name": f"📈 做多信号 ({len(buy_list)})",  "value": buy_text,  "inline": False},
            {"name": f"📉 做空信号 ({len(sell_list)})", "value": sell_text, "inline": False},
            {"name": f"⚪ 观望 ({len(neutral)})",       "value": watch_text,"inline": False},
        ],
        "footer": {"text": "CryptoSignalBot v2  •  每小时自动扫描"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _post({"embeds": [embed]})


def send_startup_message(valid_symbols: list[str], skipped: list[str]) -> bool:
    """机器人启动通知"""
    skip_text = "、".join(skipped) if skipped else "无"
    desc = (
        f"🚀 **交易信号机器人 v2 已启动**\n\n"
        f"✅ 监控币种（{len(valid_symbols)}个）：{', '.join(valid_symbols)}\n"
        f"⚠️ 不可用已跳过：{skip_text}\n\n"
        f"📡 分析模型：ICT订单块 · FVG · Vegas通道 · EMA20 · "
        f"RSI/MACD背离 · 价格行为 · 假突破检测 · 多时间框架共振\n"
        f"⭐ 最低触发门槛：3星（最高5星）"
    )
    embed = {
        "description": desc,
        "color": COLOR_STARTUP,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _post({"embeds": [embed]})
