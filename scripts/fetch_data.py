"""
fetch_data.py
Obtiene datos de precio BTC desde Binance y liquidaciones desde Coinglass.
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

# ─────────────────────────────────────────────
# 1. BINANCE — Precio actual y velas OHLCV
# ─────────────────────────────────────────────

def get_btc_price() -> dict:
    """Precio actual de BTCUSDT en Binance."""
    url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    d = r.json()
    return {
        "price":         float(d["lastPrice"]),
        "price_change":  float(d["priceChangePercent"]),
        "high_24h":      float(d["highPrice"]),
        "low_24h":       float(d["lowPrice"]),
        "volume_24h":    float(d["quoteVolume"]),   # en USDT
    }


def get_ohlcv(interval: str = "4h", limit: int = 50) -> list[dict]:
    """Velas OHLCV de BTCUSDT — últimas `limit` velas del intervalo indicado."""
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol=BTCUSDT&interval={interval}&limit={limit}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    candles = []
    for k in r.json():
        candles.append({
            "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
        })
    return candles


def get_order_book_depth() -> dict:
    """Top 10 bids/asks para detectar muros de compra/venta."""
    url = "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=10"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    d = r.json()
    return {
        "bids": [[float(p), float(q)] for p, q in d["bids"]],
        "asks": [[float(p), float(q)] for p, q in d["asks"]],
    }


def get_funding_rate() -> dict:
    """Funding rate actual del perpetuo BTCUSDT en Binance Futures."""
    url = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=3"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    rates = r.json()
    return {
        "current_funding_rate": float(rates[-1]["fundingRate"]),
        "recent_funding_rates": [float(x["fundingRate"]) for x in rates],
    }


# ─────────────────────────────────────────────
# 2. COINGLASS — Liquidaciones y Open Interest
# ─────────────────────────────────────────────

COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"
COINGLASS_HEADERS = {
    "coinglassSecret": COINGLASS_API_KEY,
    "Content-Type": "application/json",
}


def get_liquidations() -> dict:
    """
    Liquidaciones de BTC en las últimas 24 h.
    Endpoint: /indicator/liquidation_history
    Requiere API key de Coinglass (plan gratuito incluye este endpoint).
    """
    if not COINGLASS_API_KEY:
        print("⚠️  COINGLASS_API_KEY no configurada — omitiendo liquidaciones")
        return {"error": "No API key provided"}

    url = f"{COINGLASS_BASE}/indicator/liquidation_history"
    params = {"symbol": "BTC", "interval": "h4", "limit": 12}  # últimas 12 velas de 4h
    r = requests.get(url, headers=COINGLASS_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    if not data.get("success"):
        return {"error": data.get("msg", "Unknown Coinglass error")}

    liq_list = data.get("data", [])
    return {
        "liquidation_history_4h": [
            {
                "time":       datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "long_liq":   x.get("longLiquidationUsd", 0),
                "short_liq":  x.get("shortLiquidationUsd", 0),
                "total_liq":  x.get("longLiquidationUsd", 0) + x.get("shortLiquidationUsd", 0),
            }
            for x in liq_list
        ]
    }


def get_open_interest() -> dict:
    """Open Interest agregado de todos los exchanges para BTC."""
    if not COINGLASS_API_KEY:
        return {"error": "No API key provided"}

    url = f"{COINGLASS_BASE}/indicator/open_interest"
    params = {"symbol": "BTC", "interval": "h4", "limit": 12}
    r = requests.get(url, headers=COINGLASS_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    data = r.json()
    if not data.get("success"):
        return {"error": data.get("msg")}

    oi_list = data.get("data", [])
    return {
        "open_interest_4h": [
            {
                "time":            datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "open_interest":   x.get("openInterest", 0),
                "oi_change_pct":   x.get("openInterestChangePercent", 0),
            }
            for x in oi_list
        ]
    }


def get_long_short_ratio() -> dict:
    """Ratio Long/Short de las principales cuentas de traders."""
    if not COINGLASS_API_KEY:
        return {"error": "No API key provided"}

    url = f"{COINGLASS_BASE}/indicator/top_long_short_account_ratio"
    params = {"symbol": "BTCUSDT", "interval": "h4", "limit": 12}
    r = requests.get(url, headers=COINGLASS_HEADERS, params=params, timeout=10)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    data = r.json()
    if not data.get("success"):
        return {"error": data.get("msg")}

    ls_list = data.get("data", [])
    return {
        "long_short_ratio_4h": [
            {
                "time":        datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).isoformat(),
                "long_pct":    x.get("longPercent", 0),
                "short_pct":   x.get("shortPercent", 0),
                "ratio":       x.get("longShortRatio", 0),
            }
            for x in ls_list
        ]
    }


# ─────────────────────────────────────────────
# 3. Construir y guardar el payload completo
# ─────────────────────────────────────────────

def main():
    print("🔄 Obteniendo datos de mercado…")
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    market_data = {
        "fetched_at":       timestamp,
        "symbol":           "BTCUSDT",
        "timeframe_main":   "4h",
    }

    print("  → Precio BTC (Binance)…")
    market_data["price_ticker"] = get_btc_price()
    time.sleep(0.3)

    print("  → Velas 4h (Binance)…")
    market_data["ohlcv_4h"] = get_ohlcv("4h", 50)
    time.sleep(0.3)

    print("  → Velas 1h (Binance)…")
    market_data["ohlcv_1h"] = get_ohlcv("1h", 24)
    time.sleep(0.3)

    print("  → Order book…")
    market_data["order_book"] = get_order_book_depth()
    time.sleep(0.3)

    print("  → Funding rate…")
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
