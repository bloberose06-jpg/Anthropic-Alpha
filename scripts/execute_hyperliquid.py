"""
execute_hyperliquid.py

Lee el análisis de Claude y ejecuta la operación en Hyperliquid TESTNET.

Estrategia de órdenes:
  1. LIMIT order en el precio de entrada que sugiere Claude
  2. Espera fill (polling con timeout configurable)
  3. Solo al confirmar fill → coloca SL (stop-market) + TP(s) como reduce-only
  4. Si no hay fill antes del timeout → cancela la orden y no opera

Secrets de GitHub necesarios:
  HL_PRIVATE_KEY     — Clave privada de la wallet (con 0x)
  HL_WALLET_ADDRESS  — Dirección pública de la wallet
  DRY_RUN            — "true" para simular sin enviar órdenes
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import eth_account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# ─────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────
TESTNET_API_URL = constants.TESTNET_API_URL

SYMBOL            = "BTC"
POSITION_SIZE_USD = 100.0    # USD por operación

# Limit order: cuánto alejarse del precio Claude en % para favorecer fill rápido
# 0.0  = precio exacto de Claude
# 0.05 = 0.05% más agresivo (pagamos/vendemos un tick mejor para llenar antes)
ENTRY_AGGRESSION_PCT = 0.05

# Tiempo máximo esperando fill de la limit order (segundos)
FILL_TIMEOUT_SECONDS  = 300   # 5 minutos
POLL_INTERVAL_SECONDS = 10

DRY_RUN    = os.environ.get("DRY_RUN", "false").lower() == "true"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def load_analysis() -> dict:
    path = OUTPUT_DIR / "analysis.json"
    if not path.exists():
        raise FileNotFoundError("output/analysis.json no encontrado")
    return json.loads(path.read_text())


def get_mid_price(info: Info) -> float:
    mids = info.all_mids()
    if SYMBOL not in mids:
        raise ValueError(f"{SYMBOL} no encontrado en Hyperliquid")
    return float(mids[SYMBOL])


def get_sz_decimals(info: Info) -> int:
    for asset in info.meta()["universe"]:
        if asset["name"] == SYMBOL:
            return asset["szDecimals"]
    return 3


def calculate_size(usd: float, price: float, decimals: int) -> float:
    raw = usd / price
    factor = 10 ** decimals
    return round(round(raw * factor) / factor, decimals)


def validate_signal(analysis: dict) -> tuple:
    signal = analysis.get("signal")
    if signal not in ("LONG", "SHORT"):
        return False, f"Señal {signal} — no operar"
    if analysis.get("confidence", 0) < 6:
        return False, f"Confianza {analysis['confidence']}/10 < 6 — skip"
    for field in ("entry", "stop_loss", "take_profit_1"):
        if not analysis.get(field):
            return False, f"Campo '{field}' faltante"
    return True, "OK"


def calculate_limit_price(entry: float, is_buy: bool, aggression_pct: float) -> float:
    """
    Ajusta el precio ligeramente para favorecer el fill sin alejarse demasiado:
    - LONG:  precio un poco más alto  (agresivo como comprador)
    - SHORT: precio un poco más bajo  (agresivo como vendedor)
    Si aggression_pct = 0 usa exactamente el precio de Claude.
    """
    if aggression_pct == 0:
        return round(entry, 1)
    factor = aggression_pct / 100
    if is_buy:
        return round(entry * (1 + factor), 1)
    else:
        return round(entry * (1 - factor), 1)


def get_position_size(info: Info, address: str) -> float:
    """Tamaño de la posición actual en BTC (positivo=long, negativo=short)."""
    for pos in info.user_state(address).get("assetPositions", []):
        item = pos.get("position", {})
        if item.get("coin") == SYMBOL:
            return float(item.get("szi", 0))
    return 0.0


def get_open_order_by_id(info: Info, address: str, oid: int):
    for order in info.open_orders(address):
        if order.get("oid") == oid:
            return order
    return None


def wait_for_fill(info, exchange, address, oid, expected_size, is_buy, timeout) -> tuple:
    """
    Polling hasta fill o timeout.
    Retorna (filled: bool, filled_size: float).
    """
    deadline = time.time() + timeout
    print(f"  ⏳ Esperando fill de limit order oid={oid} (timeout: {timeout}s)…")

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)

        open_order = get_open_order_by_id(info, address, oid)
        pos_size   = get_position_size(info, address)

        # Orden ya no está en open_orders
        if open_order is None:
            if (is_buy and pos_size > 0) or (not is_buy and pos_size < 0):
                filled_sz = abs(pos_size)
                print(f"  ✅ Fill confirmado: {filled_sz:.5f} BTC")
                return True, filled_sz
            else:
                print("  ⚠️  Orden cerrada sin posición detectable")
                return False, 0.0

        remaining_secs = int(deadline - time.time())
        print(f"  ⏳ Pendiente…  ({remaining_secs}s restantes)")

    # Timeout → cancelar
    print(f"  ⏰ Timeout alcanzado — cancelando orden {oid}")
    if not DRY_RUN:
        exchange.cancel(SYMBOL, oid)
    return False, 0.0


def cancel_open_orders(exchange, info, address):
    btc_orders = [o for o in info.open_orders(address) if o.get("coin") == SYMBOL]
    if not btc_orders:
        return
    print(f"  🗑️  Cancelando {len(btc_orders)} orden(es) abiertas…")
    for o in btc_orders:
        if not DRY_RUN:
            exchange.cancel(SYMBOL, o["oid"])
        else:
            print(f"     [DRY RUN] cancel oid={o['oid']}")


def close_existing_position(exchange, info, address):
    size = get_position_size(info, address)
    if size == 0:
        return
    print(f"  🔄 Cerrando posición existente ({size:+.5f} BTC)…")
    if not DRY_RUN:
        exchange.market_close(SYMBOL)
    else:
        print(f"     [DRY RUN] market_close({SYMBOL})")
    time.sleep(1.5)


# ─────────────────────────────────────────────────────────────
# Ejecución completa
# ─────────────────────────────────────────────────────────────

def execute_trade(analysis: dict, info: Info, exchange: Exchange, address: str) -> dict:
    signal  = analysis["signal"]
    entry   = float(analysis["entry"])
    sl      = float(analysis["stop_loss"])
    tp1     = float(analysis["take_profit_1"])
    tp2     = float(analysis["take_profit_2"]) if analysis.get("take_profit_2") else None
    is_buy  = signal == "LONG"

    sz_dec    = get_sz_decimals(info)
    mid_price = get_mid_price(info)
    size      = calculate_size(POSITION_SIZE_USD, mid_price, sz_dec)
    limit_px  = calculate_limit_price(entry, is_buy, ENTRY_AGGRESSION_PCT)

    print(f"\n  {'─'*50}")
    print(f"  Señal:          {signal}")
    print(f"  Mid actual:     ${mid_price:,.2f}")
    print(f"  Precio Claude:  ${entry:,.2f}")
    print(f"  Limit price:    ${limit_px:,.2f}  (+{ENTRY_AGGRESSION_PCT}% aggr.)")
    print(f"  Stop Loss:      ${sl:,.2f}")
    print(f"  TP1:            ${tp1:,.2f}")
    if tp2:
        print(f"  TP2:            ${tp2:,.2f}")
    print(f"  Tamaño:         {size} BTC  (~${POSITION_SIZE_USD} USD)")
    print(f"  {'─'*50}")

    results = {}

    # ── DRY RUN ────────────────────────────────────────────────
    if DRY_RUN:
        tp1_sz = round(size / 2, sz_dec) if tp2 else size
        tp2_sz = round(size - tp1_sz, sz_dec) if tp2 else None
        print("\n  🔍 [DRY RUN] Órdenes que se enviarían:")
        print(f"     1. LIMIT {'BUY' if is_buy else 'SELL'}  {size} BTC @ ${limit_px:,.2f}  GTC")
        print(f"        ↳ Esperar fill…")
        print(f"     2. STOP-MARKET {'SELL' if is_buy else 'BUY'}  {size} BTC  trigger=${sl:,.2f}  reduce-only")
        print(f"     3. LIMIT {'SELL' if is_buy else 'BUY'}  {tp1_sz} BTC @ ${tp1:,.2f}  reduce-only  (TP1)")
        if tp2 and tp2_sz:
            print(f"     4. LIMIT {'SELL' if is_buy else 'BUY'}  {tp2_sz} BTC @ ${tp2:,.2f}  reduce-only  (TP2)")
        return {"dry_run": True, "limit_px": limit_px, "size": size}

    # ── PASO 1: Limit order de entrada (GTC) ──────────────────
    print("\n  📤 [1/4] Enviando limit order de entrada…")
    r_entry = exchange.order(
        name=SYMBOL,
        is_buy=is_buy,
        sz=size,
        limit_px=limit_px,
        order_type={"limit": {"tif": "Gtc"}},
        reduce_only=False,
    )
    print(f"     → {r_entry}")
    results["entry_order"] = r_entry

    # Detectar si se llenó inmediatamente o quedó resting
    filled      = False
    filled_size = 0.0
    try:
        status = r_entry["response"]["data"]["statuses"][0]
        if "filled" in status:
            print("  ⚡ Llenada inmediatamente (taker)")
            filled      = True
            filled_size = size
        elif "resting" in status:
            oid = status["resting"]["oid"]
            filled, filled_size = wait_for_fill(
                info, exchange, address, oid, size, is_buy, FILL_TIMEOUT_SECONDS
            )
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Respuesta inesperada del exchange: {r_entry}") from e

    if not filled:
        print("\n  ⏸  Limit order sin fill — operación cancelada")
        results["filled"] = False
        return results

    results["filled"]      = True
    results["filled_size"] = filled_size
    actual_sz = filled_size or size
    time.sleep(1)

    # ── PASO 2: Stop Loss ──────────────────────────────────────
    print("  📤 [2/4] Colocando Stop Loss…")
    r_sl = exchange.order(
        name=SYMBOL,
        is_buy=not is_buy,
        sz=actual_sz,
        limit_px=sl,
        order_type={"trigger": {"triggerPx": sl, "isMarket": True, "tpsl": "sl"}},
        reduce_only=True,
    )
    print(f"     → {r_sl}")
    results["sl_order"] = r_sl
    time.sleep(0.5)

    # ── PASO 3 (+4): Take Profit(s) ───────────────────────────
    tp1_sz = round(actual_sz / 2, sz_dec) if tp2 else actual_sz

    print("  📤 [3/4] Colocando TP1…")
    r_tp1 = exchange.order(
        name=SYMBOL,
        is_buy=not is_buy,
        sz=tp1_sz,
        limit_px=tp1,
        order_type={"trigger": {"triggerPx": tp1, "isMarket": False, "tpsl": "tp"}},
        reduce_only=True,
    )
    print(f"     → {r_tp1}")
    results["tp1_order"] = r_tp1

    if tp2:
        time.sleep(0.5)
        tp2_sz = round(actual_sz - tp1_sz, sz_dec)
        print("  📤 [4/4] Colocando TP2…")
        r_tp2 = exchange.order(
            name=SYMBOL,
            is_buy=not is_buy,
            sz=tp2_sz,
            limit_px=tp2,
            order_type={"trigger": {"triggerPx": tp2, "isMarket": False, "tpsl": "tp"}},
            reduce_only=True,
        )
        print(f"     → {r_tp2}")
        results["tp2_order"] = r_tp2

    return results


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  🚀 HYPERLIQUID TESTNET — LIMIT ORDER EXECUTOR")
    print(f"  {'[DRY RUN]' if DRY_RUN else '[LIVE TESTNET]'}")
    print(f"  {datetime.now(tz=timezone.utc).isoformat()}")
    print(f"{'='*55}")

    analysis   = load_analysis()
    signal     = analysis.get("signal", "NEUTRAL")
    confidence = analysis.get("confidence", 0)

    print(f"\n  Señal Claude: {signal}  |  Confianza: {confidence}/10")
    print(f"  {analysis.get('summary', '')[:120]}")

    log = {
        "timestamp":  datetime.now(tz=timezone.utc).isoformat(),
        "signal":     signal,
        "confidence": confidence,
        "dry_run":    DRY_RUN,
        "analysis":   analysis,
        "execution":  None,
        "error":      None,
    }

    ok, reason = validate_signal(analysis)
    if not ok:
        print(f"\n  ⏸  Skip — {reason}")
        log["execution"] = {"skipped": True, "reason": reason}
    else:
        private_key = os.environ.get("HL_PRIVATE_KEY", "")
        wallet_addr = os.environ.get("HL_WALLET_ADDRESS", "")
        if not private_key or not wallet_addr:
            raise EnvironmentError("Faltan HL_PRIVATE_KEY y/o HL_WALLET_ADDRESS")

        account: LocalAccount = eth_account.Account.from_key(private_key)
        info     = Info(TESTNET_API_URL, skip_ws=True)
        exchange = Exchange(account, TESTNET_API_URL, account_address=wallet_addr)

        close_existing_position(exchange, info, wallet_addr)
        cancel_open_orders(exchange, info, wallet_addr)

        try:
            result = execute_trade(analysis, info, exchange, wallet_addr)
            log["execution"] = result
            status = "✅ Completada" if result.get("filled") else "⏸  Sin fill"
            print(f"\n  {status}")
        except Exception as e:
            print(f"\n  ❌ Error: {e}")
            log["error"] = str(e)
            raise

    out = OUTPUT_DIR / "execution_log.json"
    out.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(f"\n  💾 Log → {out}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
