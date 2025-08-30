#!/usr/bin/env python3
"""
Bot OTC 5 min – Perú (Twelve Data)
- Apuesta simple: arriba / abajo
- Cierre automático a los 5 min
- Mensaje final ganaste / perdiste
"""

import os
import json
import time
import logging
import threading
import sys
import requests
import schedule
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Bot
from flask import Flask, request

load_dotenv()
logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# CONFIG
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_API_KEY]):
    logging.error("❌ Faltan variables.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
OTC_PAIRS = ["US30USD", "US100USD", "DE30EUR", "BTCUSD", "ETHUSD"]
TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "otc_signals.json"

def now_peru():
    return datetime.now(TZ_PERU)

def load_signals():
    return json.load(open(SIGNAL_FILE)) if os.path.exists(SIGNAL_FILE) else []

def save_signals():
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, default=str)

ACTIVE_SIGNALS = load_signals()

# Cache 30 s
CACHE_PRICE = {}
CACHE_LOCK = threading.Lock()

def get_price(symbol):
    with CACHE_LOCK:
        now_ts = int(time.time())
        if symbol in CACHE_PRICE and now_ts - CACHE_PRICE[symbol][0] < 30:
            return CACHE_PRICE[symbol][1]

    url = "https://api.twelvedata.com/price"
    params = {"symbol": symbol, "apikey": TWELVE_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        price = float(r.json()["price"])
        with CACHE_LOCK:
            CACHE_PRICE[symbol] = (now_ts, price)
        return price
    except Exception as e:
        logging.warning("Twelve Data falló (%s): %s", symbol, e)
        return None

# MENSAJES
def build_open(pair, direction, entry):
    icon = "🚀" if direction == "ARRIBA" else "📉"
    now = now_peru().strftime("%H:%M:%S")
    return f"{icon} **APUESTA {pair}**\n⏰ Hora: {now}\n📈 Dirección: {direction}\n💰 Entrada: {entry:.2f}"

def build_close(pair, direction, entry, current, result):
    icon = "🤑" if result == "GANASTE" else "😞"
    return (
        f"{icon} **RESULTADO {pair}**\n"
        f"⏰ Cierre: {now_peru().strftime('%H:%M:%S')}\n"
        f"📈 Dirección: {direction}\n"
        f"💰 Entrada: {entry:.2f}\n"
        f"📍 Cierre: {current:.2f}\n"
        f"**{result}**"
    )

# TAREAS
def open_bets():
    for pair in OTC_PAIRS:
        price = get_price(pair)
        if price is None:
            continue

        direction = "ARRIBA" if price > price - 0.001 else "ABAJO"
        msg = build_open(pair, direction, price)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            ACTIVE_SIGNALS.append({
                "pair": pair,
                "direction": direction,
                "entry": price,
                "created_at": now_peru().isoformat(),
                "message_id": sent.message_id
            })
            save_signals()
            logging.info("📤 Apuesta abierta: %s %s", pair, direction)
        except Exception:
            logging.exception("❌ Error enviando apuesta")

def close_bets():
    still_open = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (now_peru() - datetime.fromisoformat(sig["created_at"]).replace(tzinfo=TZ_PERU)).total_seconds()
        if elapsed < 300:
            still_open.append(sig)
            continue
        current = get_price(sig["pair"])
        if current is None:
            still_open.append(sig)
            continue

        direction = sig["direction"]
        result = "GANASTE" if (
            (direction == "ARRIBA" and current > sig["entry"]) or
            (direction == "ABAJO" and current < sig["entry"])
        ) else "PERDISTE"
        msg = build_close(sig["pair"], direction, sig["entry"], current, result)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg, reply_to_message_id=sig["message_id"])
        except Exception:
            logging.exception("❌ Error cerrando")
    ACTIVE_SIGNALS[:] = still_open
    save_signals()

# FLASK
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test():
    token = request.args.get("token")
    if token != TEST_TOKEN:
        return "Unauthorized", 401
    threading.Thread(target=lambda: bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba OK")).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# INICIO
if __name__ == "__main__":
    logging.info("🚀 Bot OTC 5 min – Perú")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(open_bets)
    schedule.every(30).seconds.do(close_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)