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
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

if not all([TELEGRAM_TOKEN, ALPHA_KEY, CHAT_ID]):
    logging.error("❌ Faltan variables de entorno: TELEGRAM_TOKEN, ALPHA_KEY o CHAT_ID.")
    sys.exit(1)

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    logging.error("❌ CHAT_ID debe ser un número entero.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

def get_price(attempts=3, backoff=2):
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": "EUR",
        "to_currency": "USD",
        "apikey": ALPHA_KEY
    }
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "Realtime Currency Exchange Rate" not in data:
                logging.warning("⚠️ Respuesta inesperada: %s", data)
                return None
            return float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = (i + 1) * backoff
                logging.warning("⏳ Rate-limit 429, esperando %ss", wait)
                time.sleep(wait)
                continue
            logging.exception("❌ HTTPError")
            return None
        except Exception:
            logging.exception("❌ Error obteniendo precio")
            return None
    logging.error("❌ No se pudo obtener precio tras %s intentos", attempts)
    return None

def micro_trend(prices):
    if len(prices) < 3:
        return "NEUTRO"
    if prices[-1] > prices[-2] > prices[-3]:
        return "CALL"
    if prices[-1] < prices[-2] < prices[-3]:
        return "PUT"
    return "NEUTRO"

def send_signal():
    logging.info("🔍 Iniciando ciclo de análisis de señal...")
    prices = []
    for i in range(3):
        p = get_price()
        if p is None:
            logging.warning("⚠️ Precio inválido, abortando ciclo")
            return
        prices.append(p)
        if i < 2:
            time.sleep(1)

    direction = micro_trend(prices)
    if direction == "NEUTRO":
        logging.info("➖ Sin señal (NEUTRO)")
        return

    entry = prices[-1]
    tp = entry - 0.00025 if direction == "PUT" else entry + 0.00025
    sl = entry + 0.00025 if direction == "PUT" else entry - 0.00025

    msg = (f"🔔 Señal EUR/USD 5 min\n"
           f"⏰ Hora: {time.strftime('%H:%M:%S')}\n"
           f"📊 Dirección: {direction}\n"
           f"💰 Entrada: ≤ {entry:.5f}\n"
           f"🎯 TP: {tp:.5f}\n"
           f"❌ SL: {sl:.5f}")

    try:
        logging.info("📤 Enviando señal a Telegram: %s", direction)
        bot.send_message(chat_id=CHAT_ID, text=msg)
        logging.info("✅ Señal enviada: %s", direction)
    except Exception:
        logging.exception("❌ Error enviando mensaje")

# ---------- HEALTH WEB SERVER ----------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test_signal():
    def _send():
        try:
            bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba de señal funcionando")
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
    logging.info("🚀 Bot arrancado")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signal)
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("❌ Error en run_pending")
        time.sleep(1)