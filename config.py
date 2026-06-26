from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ─────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# ── 监控币种 ─────────────────────────────────────────────────────────────────
# 注意：XAGUSDT/NAS100USDT/CLUSDT 等可能仅在 Binance Futures 有效，
#       代码会在运行时自动验证并跳过不可用的币种。
SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "XAUUSDT",
    "XAGUSDT",
    "HYPEUSDT",
    "CLUSDT",
    "MUSDT",
    "SPCXUSDT",
    "NAS100USDT",
]

# ── 时间框架 ─────────────────────────────────────────────────────────────────
TIMEFRAMES: dict[str, dict] = {
    "1h":  {"interval": "1h",  "limit": 250},
    "4h":  {"interval": "4h",  "limit": 200},
    "1d":  {"interval": "1d",  "limit": 100},
}

# ── 信号设置 ─────────────────────────────────────────────────────────────────
MIN_STAR_RATING: int = 3          # 最低星级才推送（1-5 星）
SCAN_INTERVAL_MINUTES: int = 60   # 本地运行扫描间隔

# ── 技术指标参数 ──────────────────────────────────────────────────────────────
EMA_SHORT: int          = 20       # EMA20
EMA_VEGAS_FAST: int     = 144      # Vegas 通道快线
EMA_VEGAS_SLOW: int     = 169      # Vegas 通道慢线
RSI_PERIOD: int         = 14
ATR_PERIOD: int         = 14
VOLUME_MA_PERIOD: int   = 20

# ATR 止损/止盈倍数
ATR_SL_MULT: float = 1.5
ATR_TP1_MULT: float = 2.0
ATR_TP2_MULT: float = 3.5

# ICT 参数
ORDER_BLOCK_LOOKBACK: int         = 60    # Order Block 回溯K线数
FVG_LOOKBACK: int                 = 40    # Fair Value Gap 回溯K线数
BREAKOUT_VOLUME_MULT: float       = 1.5   # 真实突破需要成交量倍数
LIQUIDITY_SWEEP_TOLERANCE: float  = 0.003 # 流动性扫描容忍度 0.3%

# 多时间框架对齐奖励分
MTF_BONUS: int = 2
