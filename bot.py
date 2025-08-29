"""
Bot de señales FX 5 min
- Alpha Vantage para precios en tiempo real
- Log de pips con 4 decimales
- min_move y tick_size configurables
- Zona horaria UTC
"""

import os
import time
import logging
import sys
import threading
import requests
import schedule
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Bot
from flask import Flask

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()

# Tokens y variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID        = int(os.getenv("CHAT_ID", "0"))
ALPHA_KEY      = os.getenv("ALPHA_KEY", "").strip()

if not all([TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY]):
    logging.error("❌ Faltan variables de entorno (TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY).")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("AUD", "USD"),
]

# ---------------- CONFIGURABLE ----------------
MIN_MOVES = {
    ("EUR", "USD"): float(os.getenv("MIN_MOVE_EURUSD", 0.00002)),
    ("GBP", "USD"): float(os.getenv("MIN_MOVE_GBPUSD", 0.00002)),
    ("USD", "JPY"): float(os.getenv("MIN_MOVE_USDJPY", 0.002)),
    ("AUD", "USD"): float(os.getenv("MIN_MOVE_AUDUSD", 0.00002)),
}

TICK_SIZE = {
    ("EUR", "USD"): float(os.getenv("TICK_EURUSD", 0.00025)),
    ("GBP", "USD"): float(os.getenv("TICK_GBPUSD", 0.00025)),
    ("USD", "JPY"): float(os.getenv("TICK_USDJPY", 0.025)),
    ("AUD", "USD"): float(os.getenv("TICK_AUDUSD", 0.00025)),
}
# ---------------------------------------------

def get_price(from_curr="EUR", to_curr="USD", attempts=3):
    """
    Obtiene el tipo de cambio actual usando Alpha Vantage.
    https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE
    """
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_curr,
        "to_currency": to_curr,
        "apikey": ALPHA_KEY
    }
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get("https://www.alphavantage.co/query",
                             params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            rate = float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            return rate
        except Exception as e:
            logging.warning("⚠️ Alpha Vantage intento %d/%d: %s", attempt, attempts, e)
            time.sleep(2)
    logging.error("❌ Fallo tras %d intentos para %s/%s", attempts, from_curr, to_curr)
    return None

def micro_trend(prices, pair):
    if len(prices) < 2:
        return "NEUTRO"
    diff = abs(prices[-1] - prices[-2])
    min_move = MIN_MOVES.get(pair, 0.00002)
    return "NEUTRO" if diff < min_move else ("CALL" if prices[-1] > prices[-2] else "PUT")

def build_message(base, quote, direction, entry, tp, sl, prob):
    icon  = "🟢" if direction == "CALL" else "🔴"
    color = "📈" if direction == "CALL" else "📉"
    now   = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return (
        f"{icon} **SEÑAL {base}/{quote}**\n"
        f"⏰ Hora: {now}\n"
        f"{color} **Dirección: {direction}**\n"
        f"💰 Entrada: ≤ {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}\n"
        f"❌ SL: {sl:.5f}\n"
        f"📊 Probabilidad: ~{prob} %"
    )

def send_signals():
    for base, quote in PAIRS:
        pair = (base, quote)
        logging.info("🔍 Analizando %s/%s...", base, quote)
        prices = []
        for i in range(2):
            p = get_price(from_curr=base, to_curr=quote)
            if p is None:
                logging.warning("⚠️ Precio inválido para %s/%s, saltando...", base, quote)
                break
            prices.append(p)
            if i == 0:
                time.sleep(1)  # Alpha Vantage admite 1 llamada/s
        else:
            diff = abs(prices[-1] - prices[-2])
            pips = diff * 10_000 if "JPY" not in quote else diff * 100
            logging.info("Δ %s/%s: %.6f  (%.4f pips)", base, quote, diff, pips)

            direction = micro_trend(prices, pair)
            if direction == "NEUTRO":
                logging.info("➖ Sin señal para %s/%s (NEUTRO)", base, quote)
                continue

            entry     = prices[-1]
            tick_size = TICK_SIZE.get(pair, 0.00025)
            tp = entry - tick_size if direction == "PUT" else entry + tick_size
            sl = entry + tick_size if direction == "PUT" else entry - tick_size

            prob = min(95, max(50, int(diff * 1_000_000)))

            msg = build_message(base, quote, direction, entry, tp, sl, prob)
            try:
                bot.send_message(chat_id=CHAT_ID, text=msg)
                logging.info("✅ Señal enviada: %s/%s -> %s", base, quote, direction)
            except Exception:
                logging.exception("❌ Error enviando mensaje")

# ------------------- Flask --------------------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test_signal():
    def _send():
        try:
            bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba de señal funcionando")
            logging.info("✅ Test enviado")
        except Exception:
            logging.exception("❌ Error en /test")
    threading.Thread(target=_send, daemon=True).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    logging.info("🌐 Escuchando en el puerto %s", port)
    app.run(host="0.0.0.0", port=port)

# ------------------- Main --------------------
if __name__ == "__main__":
    logging.info("🚀 Bot arrancado con Alpha Vantage")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("❌ Error en run_pending")
        time.sleep(1)