import os
import time
import logging
import sys
import requests
import schedule
from dotenv import load_dotenv
from telegram import Bot

# ---------- CONFIGURACIÓN DE LOG ----------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ---------- CARGA DE VARIABLES ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALPHA_KEY      = os.getenv("ALPHA_KEY", "").strip()
CHAT_ID        = os.getenv("CHAT_ID", "").strip()
PAIR           = "EURUSD"

# Validación rápida
if not all([TELEGRAM_TOKEN, ALPHA_KEY, CHAT_ID]):
    logging.error("Faltan variables de entorno.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# ---------- FUNCIÓN PARA OBTENER PRECIO ----------
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
                logging.warning("Respuesta inesperada: %s", data)
                return None

            return float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])

        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = (i + 1) * backoff
                logging.warning("Rate-limit 429, esperando %ss", wait)
                time.sleep(wait)
                continue
            logging.exception("HTTPError")
            return None

        except Exception as e:
            logging.exception("Error obteniendo precio")
            return None

    logging.error("No se pudo obtener precio tras %s intentos", attempts)
    return None

# ---------- FUNCIÓN DE TENDENCIA ----------
def micro_trend(prices):
    if len(prices) < 3:
        return "NEUTRO"
    if prices[-1] > prices[-2] > prices[-3]:
        return "CALL"
    if prices[-1] < prices[-2] < prices[-3]:
        return "PUT"
    return "NEUTRO"

# ---------- FUNCIÓN PRINCIPAL ----------
def send_signal():
    prices = []
    for _ in range(3):
        p = get_price()
        if p is None:
            logging.warning("Precio inválido, abortando ciclo")
            return
        prices.append(p)
        if _ < 2:  # Pequeña pausa entre intentos
            time.sleep(1)

    direction = micro_trend(prices)
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

    try:
        bot.send_message(chat_id=CHAT_ID, text=msg)
        logging.info("Señal enviada: %s", direction)
    except Exception:
        logging.exception("Error enviando mensaje")

# ---------- SCHEDULER ----------
schedule.every(5).minutes.do(send_signal)

# ---------- BUCLE INFINITO ----------
if __name__ == "__main__":
    logging.info("Bot arrancado")
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("Error en run_pending")
        time.sleep(1)