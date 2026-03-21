"""
fetch_data.py
Obtiene datos de precio BTC desde OKX (sin restricciones geo) y
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

OKX_BASE = "https://www.okx.com"
HEADERS  = {"Content-Type": "application/json"}

# ─────────────────────────────────────────────
# 1. OKX — Precio, velas, order book, funding
# ─────────────────────────────────────────────

def get_btc_price() -> dict:
    """Ticker 24h de BTC-USDT-SWAP en OKX."""
    url    = f"{OKX_BASE}/api/v5/market/ticker"
    params = {"instId": "BTC-USDT-SWAP"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    last  = float(d["last"])
    open_ = float(d["open24h"])
    return {
        "price":        last,
        "price_change": round((last - open_) / open_ * 100, 4),
        "high_24h":     float(d["high24h"]),
        "low_24h":      float(d["low24h"]),
        "volume_24h":   float(d["volCcy24h"]),  # en USDT
    }


def get_ohlcv(bar: str = "4H", limit: int = 50) -> list:
    """
    Velas OHLCV de BTC-USDT-SWAP en OKX.
    bar: "1H", "4H", "1D"
    """
    url    = f"{OKX_BASE}/api/v5/market/candles"
    params = {"instId": "BTC-USDT-SWAP", "bar": bar, "limit": limit}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    raw = r.json()["data"]
    candles = []
    for k in reversed(raw):   # OKX devuelve de más reciente a más antiguo
        candles.append({
            "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).isoformat(),
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        })
    return candles


def get_order_book() -> dict:
    """Top 10 bids/asks de BTC-USDT-SWAP."""
    url    = f"{OKX_BASE}/api/v5/market/books"
    params = {"instId": "BTC-USDT-SWAP", "sz": 10}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return {
        "bids": [[float(p[0]), float(p[1])] for p in d["bids"]],
        "asks": [[float(p[0]), float(p[1])] for p in d["asks"]],
    }


def get_funding_rate() -> dict:
    """Funding rate actual e histórico de BTC-USDT-SWAP en OKX."""
    # Actual
    url    = f"{OKX_BASE}/api/v5/public/funding-rate"
    params = {"instId": "BTC-USDT-SWAP"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    current = float(r.json()["data"][0]["fundingRate"])

    # Histórico (últimas 3)
    url2    = f"{OKX_BASE}/api/v5/public/funding-rate-history"
    params2 = {"instId": "BTC-USDT-SWAP", "limit": 3}
    r2 = requests.get(url2, params=params2, headers=HEADERS, timeout=10)
    r2.raise_for_status()
    history = [float(x["fundingRate"]) for x in r2.json()["data"]]

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
        "symbol":         "BTC-USDT-SWAP",
        "source":         "OKX",
        "timeframe_main": "4H",
    }

    print("  → Precio BTC (OKX)…")
    market_data["price_ticker"] = get_btc_price()
    time.sleep(0.3)

    print("  → Velas 4H (OKX)…")
    market_data["ohlcv_4h"] = get_ohlcv("4H", 50)
    time.sleep(0.3)

    print("  → Velas 1H (OKX)…")
    market_data["ohlcv_1h"] = get_ohlcv("1H", 24)
    time.sleep(0.3)

    print("  → Order book (OKX)…")
    market_data["order_book"] = get_order_book()
    time.sleep(0.3)

    print("  → Funding rate (OKX)…")
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
