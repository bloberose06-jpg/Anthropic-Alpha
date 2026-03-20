# 🤖 BTC Trading Signal Bot — GitHub Actions

Analiza el mercado de BTC/USDT automáticamente usando datos en tiempo real y Claude AI para generar señales de trading con entrada, SL y TP.

## 📁 Estructura

```
btc-trading-bot/
├── .github/
│   └── workflows/
│       └── btc-analysis.yml      # Workflow principal (cron cada 4h)
├── scripts/
│   ├── fetch_data.py             # Obtiene datos de Binance + Coinglass
│   ├── analyze_with_claude.py    # Análisis con Claude AI
│   └── notify_telegram.py        # Notificación por Telegram
├── output/                       # Generado en runtime
│   ├── market_data.json
│   └── analysis.json
├── requirements.txt
└── README.md
```

---

## 🔐 Secrets requeridos en GitHub

Ve a tu repositorio → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Descripción | Requerido |
|---|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic (claude.ai/settings) | ✅ Sí |
| `COINGLASS_API_KEY` | API key de Coinglass (coinglass.com) | ⚠️ Recomendado |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram | ⚠️ Opcional |
| `TELEGRAM_CHAT_ID` | ID de tu chat/canal de Telegram | ⚠️ Opcional |

### Obtener API keys

**Anthropic:**
1. Ve a https://console.anthropic.com/settings/keys
2. Crea una nueva API key

**Coinglass:**
1. Ve a https://www.coinglass.com/pricing
2. El plan gratuito incluye liquidaciones y Open Interest

**Telegram Bot:**
1. Habla con [@BotFather](https://t.me/botfather) en Telegram
2. Crea un bot con `/newbot`
3. Copia el token
4. Para el `CHAT_ID`: habla con [@userinfobot](https://t.me/userinfobot)

---

## 🚀 Setup

```bash
# 1. Clona / crea el repositorio en GitHub
git init btc-trading-bot
cd btc-trading-bot

# 2. Sube los archivos
git add .
git commit -m "Initial BTC trading bot"
git push origin main

# 3. Configura los secrets en GitHub (ver tabla arriba)

# 4. El workflow se ejecuta automáticamente cada 4 horas
#    O manualmente: Actions → BTC Trading Signal Analyzer → Run workflow
```

---

## 📊 Ejemplo de señal generada

```json
{
  "signal": "LONG",
  "confidence": 7,
  "entry": 67500,
  "stop_loss": 65800,
  "take_profit_1": 70200,
  "take_profit_2": 73000,
  "risk_reward": "1:2.1",
  "leverage_suggested": 5,
  "summary": "BTC rompe resistencia clave en 67k con volumen creciente...",
  "technical_analysis": {
    "trend": "Alcista en 4h, consolidación en 1h",
    "key_levels": ["65800", "67000", "70200"],
    "pattern": "Bull flag breakout",
    "momentum": "RSI saliendo de sobrecompra en 4h"
  },
  "derivatives_analysis": {
    "funding_sentiment": "Funding positivo moderado (0.01%) — no extremo",
    "liquidation_context": "Cluster de liquidaciones shorts en 68k",
    "oi_trend": "OI creciendo con precio — confirma tendencia",
    "long_short_bias": "Ratio 55% long — sesgo moderado alcista"
  },
  "risk_factors": [
    "Resistencia fuerte en 70k zona psicológica",
    "Correlación con mercados tradicionales inestable"
  ],
  "invalidation": "Cierre de vela 4h por debajo de 66,200"
}
```

---

## ⏱️ Horarios de ejecución (UTC)

El bot corre a las: `00:00 · 04:00 · 08:00 · 12:00 · 16:00 · 20:00`

Para cambiar la frecuencia edita el cron en `.github/workflows/btc-analysis.yml`:
```yaml
- cron: "0 */4 * * *"   # cada 4 horas
- cron: "0 */1 * * *"   # cada hora
- cron: "0 8,16 * * *"  # solo a las 8h y 16h UTC
```

---

## ⚠️ Disclaimer

Este bot es solo para **fines educativos**. No constituye consejo financiero. 
Opera siempre con capital que puedas permitirte perder y gestiona el riesgo correctamente.
