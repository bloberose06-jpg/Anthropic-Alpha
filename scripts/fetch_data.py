"""
fetch_data.py
Obtiene datos de precio BTC desde Bybit (sin restricciones geo) y
liquidaciones/OI/Long-Short desde Coinglass.
Guarda todo en output/market_data.json para el paso de análisis.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "")

BYBIT_BASE = "https://api.bybit.com"
HEADERS    = {"Content-Type": "application/json"}

# ─────────────────────────────────────────────
# 1. BYBIT — Precio, velas, order book, funding
# ─────────────────────────────────────────────

def get_btc_price() -> dict:
    url    = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "linear", "symbol": "BTCUSDT"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()["result"]["list"][0]
    return {
        "price":        float(d["lastPrice"]),
        "price_change": float(d["price24hPcnt"]) * 100,
        "high_24h":     float(d["highPrice24h"]),
        "low_24h":      float(d["lowPrice24h"]),
        "volume_24h":   float(d["turnover24h"]),
    }


def get_ohlcv(interval: str = "240", limit: int = 50) -> list:
    url    = f"{BYBIT_BASE}/v5/market/kline"
    params = {"category": "linear", "symbol": "BTCUSDT",
              "interval": interval, "limit": limit}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    raw = r.json()["result"]["list"]
    candles = []
    for k in reversed(raw):
        candles.append({
            "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).isoformat(),
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4]),
            "volume":float(k[5]),
        })
    return candles


def get_order_book() -> dict:
    url    = f"{BYBIT_BASE}/v5/market/orderbook"
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 10}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()["result"]
    return {
        "bids": [[float(p), float(q)] for p, q in d["b"]],
        "asks": [[float(p), float(q)] for p, q in d["a"]],
    }


def get_funding_rate() -> dict:
    url    = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "linear", "symbol": "BTCUSDT"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    ticker  = r.json()["result"]["list"][0]
    current = float(ticker.get("fundingRate", 0))

    url2    = f"{BYBIT_BASE}/v5/market/funding/history"
    params2 = {"category": "linear", "symbol": "BTCUSDT", "limit": 3}
    r2 = requests.get(url2, params=params2, headers=HEADERS, timeout=10)
    r2.raise_for_status()
    history = [float(x["fundingRate"]) for x in r2.json()["result"]["list"]]

    return {
        "current_funding_rate": current,
        "recent_funding_rates": history,
    }


# ─────────────────────────────────────────────
# 2. COINGLASS — Liquidaciones, OI, L/S ratio
# ─────────────────────────────────────────────

COINGLASS_BASE    = "https://open-api.coinglass.com/public/v2"
COINGLASS_HEADERS = {
    "coinglassSecret": COINGLASS_API_KEY,
    "Content-Type":    "application/json",
}


def _cg_get(endpoint: str, params: dict) -> dict:
    if not COINGLASS_API_KEY:
        return {"error": "No COINGLASS_API_KEY configured"}
    url = f"{COINGLASS_BASE}/{endpoint}"
    r   = requests.get(url, headers=COINGLASS_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    if not data.get("success"):
        return {"error": data.get("msg", "Unknown Coinglass error")}
    return data


def get_liquidations() -> dict:
    data = _cg_get("indicator/liquidation_history",
                   {"symbol": "BTC", "interval": "h4", "limit": 12})
    if "error" in data:
        return data
    return {
        "liquidation_history_4h": [
            {
                "time":      datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "long_liq":  x.get("longLiquidationUsd", 0),
                "short_liq": x.get("shortLiquidationUsd", 0),
                "total_liq": x.get("longLiquidationUsd", 0) + x.get("shortLiquidationUsd", 0),
            }
            for x in data.get("data", [])
        ]
    }


def get_open_interest() -> dict:
    data = _cg_get("indicator/open_interest",
                   {"symbol": "BTC", "interval": "h4", "limit": 12})
    if "error" in data:
        return data
    return {
        "open_interest_4h": [
            {
                "time":          datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "open_interest": x.get("openInterest", 0),
                "oi_change_pct": x.get("openInterestChangePercent", 0),
            }
            for x in data.get("data", [])
        ]
    }


def get_long_short_ratio() -> dict:
    data = _cg_get("indicator/top_long_short_account_ratio",
                   {"symbol": "BTCUSDT", "interval": "h4", "limit": 12})
    if "error" in data:
        return data
    return {
        "long_short_ratio_4h": [
            {
                "time":      datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "long_pct":  x.get("longPercent", 0),
                "short_pct": x.get("shortPercent", 0),
                "ratio":     x.get("longShortRatio", 0),
            }
            for x in data.get("data", [])
        ]
    }


# ─────────────────────────────────────────────
# 3. Main
# ─────────────────────────────────────────────

def main():
    print("🔄 Obteniendo datos de mercado…")
    timestamp   = datetime.now(tz=timezone.utc).isoformat()
    market_data = {
        "fetched_at":     timestamp,
        "symbol":         "BTCUSDT",
        "source":         "Bybit",
        "timeframe_main": "4h",
    }

    print("  → Precio BTC (Bybit)…")
    market_data["price_ticker"] = get_btc_price()
    time.sleep(0.3)

    print("  → Velas 4h (Bybit)…")
    market_data["ohlcv_4h"] = get_ohlcv("240", 50)
    time.sleep(0.3)

    print("  → Velas 1h (Bybit)…")
    market_data["ohlcv_1h"] = get_ohlcv("60", 24)
    time.sleep(0.3)

    print("  → Order book (Bybit)…")
    market_data["order_book"] = get_order_book()
    time.sleep(0.3)

    print("  → Funding rate (Bybit)…")
    market_data["funding"] = get_funding_rate()
    time.sleep(0.3)

    print("  → Liquidaciones (Coinglass)…")
    market_data["liquidations"] = get_liquidations()
    time.sleep(0.3)

    print("  → Open Interest (Coinglass)…")
    market_data["open_interest"] = get_open_interest()
    time.sleep(0.3)

    print("  → Long/Short ratio (Coinglass)…")
    market_data["long_short"] = get_long_short_ratio()

    out_path = OUTPUT_DIR / "market_data.json"
    out_path.write_text(json.dumps(market_data, indent=2))
    print(f"\n✅ Datos guardados en {out_path}")
    print(f"   Precio actual: ${market_data['price_ticker']['price']:,.2f}")


if __name__ == "__main__":
    main()
