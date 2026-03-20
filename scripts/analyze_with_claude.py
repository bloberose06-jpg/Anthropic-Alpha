"""
analyze_with_claude.py
Envía los datos de mercado a Claude y recibe un análisis detallado
con recomendación de entrada, SL y TP.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# Prompt del sistema — define el rol de Claude
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un analista cuantitativo experto en trading de criptomonedas, 
especializado en Bitcoin (BTC/USDT) en mercados de futuros perpetuos.

Tu análisis combina:
- Análisis técnico (velas OHLCV, soportes/resistencias, tendencias)
- Análisis de derivados (funding rate, open interest, liquidaciones, ratio L/S)
- Gestión de riesgo profesional (R:R mínimo 1:2, SL basado en estructura)

Reglas de trading que sigues:
1. Solo señalas una operación si la confluencia de factores es alta (≥3 señales alineadas)
2. El SL siempre se coloca DETRÁS de una estructura técnica relevante (mínimo/máximo previo)
3. El TP se coloca en la próxima zona de resistencia/soporte importante
4. Risk/Reward mínimo aceptable: 1:2
5. Si el mercado es ambiguo o de alta incertidumbre → señal NEUTRAL (sin operar)

Responde SIEMPRE en el siguiente formato JSON y nada más:
{
  "timestamp": "<ISO timestamp del análisis>",
  "signal": "LONG" | "SHORT" | "NEUTRAL",
  "confidence": <número del 1 al 10>,
  "entry": <precio de entrada sugerido o null>,
  "stop_loss": <precio de stop loss o null>,
  "take_profit_1": <primer target o null>,
  "take_profit_2": <segundo target o null>,
  "risk_reward": <ratio calculado o null>,
  "leverage_suggested": <apalancamiento sugerido 1-20 o null>,
  "summary": "<resumen de 2-3 oraciones del contexto de mercado>",
  "technical_analysis": {
    "trend": "<tendencia principal>",
    "key_levels": ["<nivel>", "..."],
    "pattern": "<patrón chartista detectado si existe>",
    "momentum": "<descripción del momentum>"
  },
  "derivatives_analysis": {
    "funding_sentiment": "<interpretación del funding rate>",
    "liquidation_context": "<análisis de liquidaciones recientes>",
    "oi_trend": "<tendencia del open interest>",
    "long_short_bias": "<sesgo del mercado según ratio L/S>"
  },
  "risk_factors": ["<factor de riesgo 1>", "..."],
  "invalidation": "<qué haría inválida esta tesis>"
}"""


def build_user_prompt(market_data: dict) -> str:
    """Construye el prompt con todos los datos de mercado."""
    price = market_data.get("price_ticker", {})
    funding = market_data.get("funding", {})
    liq = market_data.get("liquidations", {})
    oi = market_data.get("open_interest", {})
    ls = market_data.get("long_short", {})

    # Últimas 10 velas 4h (las más recientes al final)
    ohlcv_4h = market_data.get("ohlcv_4h", [])[-10:]
    ohlcv_1h = market_data.get("ohlcv_1h", [])[-12:]

    # Resumen de liquidaciones (últimas 6 velas)
    liq_history = liq.get("liquidation_history_4h", [])[-6:] if isinstance(liq, dict) else []
    oi_history  = oi.get("open_interest_4h", [])[-6:]        if isinstance(oi, dict)  else []
    ls_history  = ls.get("long_short_ratio_4h", [])[-6:]     if isinstance(ls, dict)  else []

    return f"""Analiza el mercado de BTC/USDT y genera una señal de trading.

═══════════════════════════════════════════
📊 DATOS DE PRECIO — {market_data.get('fetched_at')}
═══════════════════════════════════════════
Precio actual:   ${price.get('price', 'N/A'):,.2f}
Cambio 24h:      {price.get('price_change', 'N/A')}%
Máximo 24h:      ${price.get('high_24h', 'N/A'):,.2f}
Mínimo 24h:      ${price.get('low_24h', 'N/A'):,.2f}
Volumen 24h:     ${price.get('volume_24h', 0):,.0f} USDT

═══════════════════════════════════════════
🕯️ VELAS 4H (últimas 10)
═══════════════════════════════════════════
{json.dumps(ohlcv_4h, indent=2)}

═══════════════════════════════════════════
🕯️ VELAS 1H (últimas 12)
═══════════════════════════════════════════
{json.dumps(ohlcv_1h, indent=2)}

═══════════════════════════════════════════
💰 DERIVADOS
═══════════════════════════════════════════
Funding Rate actual:   {funding.get('current_funding_rate', 'N/A')}
Funding recientes:     {funding.get('recent_funding_rates', [])}

Long/Short ratio (4h):
{json.dumps(ls_history, indent=2)}

═══════════════════════════════════════════
💥 LIQUIDACIONES (Coinglass — 4h)
═══════════════════════════════════════════
{json.dumps(liq_history, indent=2) if liq_history else "No disponible (sin API key)"}

═══════════════════════════════════════════
📈 OPEN INTEREST (Coinglass — 4h)
═══════════════════════════════════════════
{json.dumps(oi_history, indent=2) if oi_history else "No disponible (sin API key)"}

Genera el JSON de análisis ahora."""


def main():
    # 1. Cargar datos de mercado
    data_path = OUTPUT_DIR / "market_data.json"
    if not data_path.exists():
        raise FileNotFoundError("No se encontró output/market_data.json — ejecuta fetch_data.py primero")

    market_data = json.loads(data_path.read_text())
    print(f"📂 Datos cargados — precio: ${market_data['price_ticker']['price']:,.2f}")

    # 2. Llamar a Claude
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    print("🧠 Enviando datos a Claude para análisis…")

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_user_prompt(market_data)}
        ],
    )

    raw_response = message.content[0].text.strip()

    # 3. Parsear el JSON de respuesta
    # Limpiar posibles backticks de markdown
    if raw_response.startswith("```"):
        raw_response = raw_response.split("```")[1]
        if raw_response.startswith("json"):
            raw_response = raw_response[4:]

    analysis = json.loads(raw_response.strip())

    # 4. Guardar resultado
    out_path = OUTPUT_DIR / "analysis.json"
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))

    # 5. Log en consola
    signal = analysis.get("signal", "NEUTRAL")
    confidence = analysis.get("confidence", 0)
    entry = analysis.get("entry")
    sl = analysis.get("stop_loss")
    tp1 = analysis.get("take_profit_1")
    tp2 = analysis.get("take_profit_2")
    rr = analysis.get("risk_reward")

    emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}.get(signal, "⚪")

    print(f"\n{'='*50}")
    print(f"  {emoji} SEÑAL: {signal}  |  Confianza: {confidence}/10")
    print(f"{'='*50}")
    if entry:
        print(f"  Entrada:      ${entry:,.2f}")
        print(f"  Stop Loss:    ${sl:,.2f}")
        print(f"  TP1:          ${tp1:,.2f}")
        print(f"  TP2:          ${tp2:,.2f}" if tp2 else "")
        print(f"  R:R ratio:    {rr}")
    print(f"\n  {analysis.get('summary', '')}")
    print(f"{'='*50}\n")
    print(f"✅ Análisis guardado en {out_path}")

    # Escribir el signal al env para el paso de Telegram
    with open(os.environ.get("GITHUB_ENV", "/dev/null"), "a") as f:
        f.write(f"SIGNAL={signal}\n")
        f.write(f"CONFIDENCE={confidence}\n")


if __name__ == "__main__":
    main()
