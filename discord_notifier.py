"""
Discord 推送模块
通过 Webhook 发送美观的 Embed 信号卡片
"""
import requests
import logging
from datetime import datetime, timezone
from analyzer import SignalResult
from config import DISCORD_WEBHOOK_URL, TIMEFRAME

logger = logging.getLogger(__name__)

# 信号颜色（Discord Embed 颜色用十进制）
COLOR_BUY     = 0x00C853   # 绿色
COLOR_SELL    = 0xD50000   # 红色
COLOR_NEUTRAL = 0x607D8B   # 灰蓝色
COLOR_SUMMARY = 0x6200EA   # 紫色（汇总报告）

# 信号强度图标
SCORE_EMOJI = {1: "⚡", 2: "⚡⚡", 3: "⚡⚡⚡", 4: "⚡⚡⚡⚡"}
DIRECTION_EMOJI = {"BUY": "🟢 做多", "SELL": "🔴 做空", "NEUTRAL": "⚪ 观望"}


def _format_price(price: float) -> str:
    """自动选择小数位数"""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    else:
        return f"{price:.6f}"


def _build_signal_embed(result: SignalResult) -> dict:
    """构建单个信号的 Embed 对象"""
    color = COLOR_BUY if result.direction == "BUY" else (
        COLOR_SELL if result.direction == "SELL" else COLOR_NEUTRAL
    )
    score_str = SCORE_EMOJI.get(result.score, "⚡" * result.score) or "·"
    change_str = f"+{result.change_24h:.2f}%" if result.change_24h >= 0 else f"{result.change_24h:.2f}%"
    change_emoji = "📈" if result.change_24h >= 0 else "📉"

    reasons_text = "\n".join(result.reasons) if result.reasons else "无额外信息"

    embed = {
        "title": f"{DIRECTION_EMOJI[result.direction]}  {result.symbol}  {score_str}",
        "color": color,
        "fields": [
            {
                "name": "💰 当前价格",
                "value": f"**${_format_price(result.price)}**",
                "inline": True,
            },
            {
                "name": f"{change_emoji} 24h涨跌",
                "value": f"**{change_str}**",
                "inline": True,
            },
            {
                "name": "⏱ 时间周期",
                "value": TIMEFRAME.upper(),
                "inline": True,
            },
            {
                "name": f"📊 MA{_cfg('MA_FAST')} / MA{_cfg('MA_SLOW')}",
                "value": (
                    f"`{_format_price(result.ma_fast)}` / `{_format_price(result.ma_slow)}`"
                ),
                "inline": True,
            },
            {
                "name": "📉 RSI",
                "value": f"`{result.rsi:.1f}`",
                "inline": True,
            },
            {
                "name": "〰️ MACD 柱状图",
                "value": f"`{result.macd_hist:.4f}`",
                "inline": True,
            },
            {
                "name": "📐 布林带",
                "value": (
                    f"上: `{_format_price(result.boll_upper)}`\n"
                    f"中: `{_format_price(result.boll_mid)}`\n"
                    f"下: `{_format_price(result.boll_lower)}`"
                ),
                "inline": True,
            },
            {
                "name": "🔍 信号依据",
                "value": reasons_text,
                "inline": False,
            },
        ],
        "footer": {
            "text": f"信号强度 {result.score}/4 · 由交易信号机器人生成"
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return embed


def _cfg(name: str) -> str:
    """从 config 动态读取参数（避免循环导入）"""
    import config as cfg
    return str(getattr(cfg, name, "?"))


def send_signal(result: SignalResult) -> bool:
    """发送单条信号到 Discord"""
    embed = _build_signal_embed(result)
    payload = {"embeds": [embed]}
    return _post(payload)


def send_summary(results: list[SignalResult], scan_time: str) -> bool:
    """发送汇总报告 Embed"""
    buy_list  = [r for r in results if r.direction == "BUY"]
    sell_list = [r for r in results if r.direction == "SELL"]
    neutral_list = [r for r in results if r.direction == "NEUTRAL"]

    def fmt_list(items):
        if not items:
            return "无"
        return "\n".join(
            f"**{r.symbol}** `${_format_price(r.price)}` {SCORE_EMOJI.get(r.score, '')}"
            for r in items
        )

    embed = {
        "title": "📋 市场扫描汇总报告",
        "color": COLOR_SUMMARY,
        "fields": [
            {
                "name": f"🟢 做多信号（{len(buy_list)}）",
                "value": fmt_list(buy_list),
                "inline": False,
            },
            {
                "name": f"🔴 做空信号（{len(sell_list)}）",
                "value": fmt_list(sell_list),
                "inline": False,
            },
            {
                "name": f"⚪ 观望（{len(neutral_list)}）",
                "value": fmt_list(neutral_list) if neutral_list else "无",
                "inline": False,
            },
        ],
        "footer": {"text": f"扫描完成 · {scan_time}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload = {"embeds": [embed]}
    return _post(payload)


def send_startup_message() -> bool:
    """机器人启动通知"""
    import config as cfg
    symbols_str = ", ".join(cfg.SYMBOLS)
    embed = {
        "title": "🤖 交易信号机器人已启动",
        "color": 0x2196F3,
        "fields": [
            {"name": "监控币种", "value": symbols_str, "inline": False},
            {"name": "时间周期", "value": cfg.TIMEFRAME.upper(), "inline": True},
            {"name": "扫描间隔", "value": f"{cfg.SCAN_INTERVAL_MINUTES} 分钟", "inline": True},
            {"name": "最低信号强度", "value": f"{cfg.MIN_SIGNAL_SCORE}/4", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _post({"embeds": [embed]})


def _post(payload: dict) -> bool:
    """发送 POST 请求到 Webhook"""
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            return True
        logger.error(f"Discord 推送失败: {resp.status_code} {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        logger.error(f"Discord 推送异常: {e}")
        return False
