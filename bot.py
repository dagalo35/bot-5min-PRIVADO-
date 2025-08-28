# bot.py  (versión para verificar rápidamente)
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

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALPHA_KEY      = os.getenv("ALPHA_KEY", "").strip()
CHAT_ID        = os.getenv("CHAT_ID", "").strip()

if not all([TELEGRAM_TOKEN, ALPHA_KEY, CHAT_ID]):
    logging.error("Faltan variables de entorno.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

def get_price():
    url = "https://www.alphavantage.co/query"
    params = {"function": "CURRENCY_EXCHANGE_RATE", "from_currency": "EUR",
              "to_currency": "USD", "apikey": ALPHA_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return float(r.json()["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
    except Exception as e:
        logging.warning("Error obteniendo precio: %s", e)
        return None

def send_signal():
    prices = [get_price() for _ in range(3)]
    prices = [p for p in prices if p is not None]
    if len(prices) < 3:
        logging.info("Sin señal (NEUTRO)")
        return
    direction = "CALL" if prices[-1] > prices[-2] > prices[-3] else \
                "PUT"  if prices[-1] < prices[-2] < prices[-3] else "NEUTRO"
    if direction == "NEUTRO":
        logging.info("Sin señal (NEUTRO)")
        return
    entry = prices[-1]
    tp = entry - 0.00025 if direction == "PUT" else entry + 0.00025
    sl = entry + 0.00025 if direction == "PUT" else entry - 0.00025
    msg = (f"🔔 Señal EUR/USD 5 min\n"
           f"⏰ Hora: {time.strftime('%H:%M:%S')}\n"
           f"📊 Dirección: {direction}\n"
           f"💰 Entrada: ≤ {entry}\n"
           f"🎯 TP: {tp:.5f}\n"
           f"❌ SL: {sl:.5f}")
    bot.send_message(chat_id=CHAT_ID, text=msg)
    logging.info("Señal enviada: %s", direction)

# ----------  PARA PRUEBA: cada 10 segundos  ----------
schedule.every(10).seconds.do(send_signal)

app = Flask(__name__)
@app.route("/")
def health(): return "ok", 200
def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

if __name__ == "__main__":
    logging.info("Bot arrancado (modo prueba 10 s)")
    threading.Thread(target=run_web, daemon=True).start()
    while True:
        schedule.run_pending()
        time.sleep(1)