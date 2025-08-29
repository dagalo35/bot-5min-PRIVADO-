import os
import time
import logging
import sys
import threading
import requests
import schedule
from dotenv import load_dotenv
from telegram import Bot
from flask import Flask

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID        = os.getenv("CHAT_ID", "").strip()

if not all([TELEGRAM_TOKEN, CHAT_ID]):
    logging.error("❌ Faltan variables de entorno: TELEGRAM_TOKEN o CHAT_ID.")
    sys.exit(1)

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    logging.error("❌ CHAT_ID debe ser un número entero.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# ---------- CONFIGURACIÓN DE DIVISAS ----------
PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("AUD", "USD"),
]

def get_price(from_curr="EUR", to_curr="USD", attempts=3):
    url = f"https://api.exchangerate-api.com/v4/latest/{from_curr}"
    for _ in range(attempts):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            rate = data["rates"].get(to_curr)
            if rate is None:
                logging.warning("⚠️ Par no encontrado: %s/%s", from_curr, to_curr)
                return None
            return float(rate)
        except Exception:
            logging.exception("❌ Error obteniendo precio desde exchangerate-api")
            time.sleep(2)
    return None

def micro_trend(prices):
    if len(prices) < 3:
        return "NEUTRO"
    if prices[-1] > prices[-2] > prices[-3]:
        return "CALL"
    if prices[-1] < prices[-2] < prices[-3]:
        return "PUT"
    return "NEUTRO"

def send_signals():
    for base, quote in PAIRS:
        logging.info("🔍 Analizando %s/%s...", base, quote)
        prices = []
        for _ in range(3):
            p = get_price(from_curr=base, to_curr=quote)
            if p is None:
                logging.warning("⚠️ Precio inválido para %s/%s, saltando...", base, quote)
                break
            prices.append(p)
            time.sleep(1)
        else:
            direction = micro_trend(prices)
            if direction == "NEUTRO":
                logging.info("➖ Sin señal para %s/%s (NEUTRO)", base, quote)
                continue

            entry = prices[-1]
            # Ajuste de tick según la divisa
            tick_size = 0.00025 if "JPY" not in quote else 0.025
            tp = entry - tick_size if direction == "PUT" else entry + tick_size
            sl = entry + tick_size if direction == "PUT" else entry - tick_size

            msg = (f"🔔 Señal {base}/{quote} 5 min\n"
                   f"⏰ Hora: {time.strftime('%H:%M:%S')}\n"
                   f"📊 Dirección: {direction}\n"
                   f"💰 Entrada: ≤ {entry:.5f}\n"
                   f"🎯 TP: {tp:.5f}\n"
                   f"❌ SL: {sl:.5f}")

            try:
                bot.send_message(chat_id=CHAT_ID, text=msg)
                logging.info("✅ Señal enviada: %s/%s -> %s", base, quote, direction)
            except Exception:
                logging.exception("❌ Error enviando mensaje para %s/%s", base, quote)

# ---------- HEALTH WEB SERVER ----------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test_signal():
    def _send():
        try:
            bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba de señal funcionando (exchangerate-api)")
            logging.info("✅ Test enviado a Telegram")
        except Exception:
            logging.exception("❌ Error en /test")
    threading.Thread(target=_send, daemon=True).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    logging.info("🌐 Escuchando en el puerto %s", port)
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    logging.info("🚀 Bot arrancado (exchangerate-api.com)")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("❌ Error en run_pending")
        time.sleep(1)