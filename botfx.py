#!/usr/bin/env python3
"""
Bot OTC 5 min – Perú (Twelve Data + divisas OTC)
- Apuesta simple: arriba / abajo
- Cierre automático a los 5 min
- Dirección según última vela 1-min
"""

import os
import json
import time
import logging
import threading
import sys
import requests
import schedule
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Bot
from flask import Flask, request

load_dotenv()
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ----------------- CONFIG -----------------
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID         = int(os.getenv("CHAT_ID", "0"))
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "").strip()
TEST_TOKEN      = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_API_KEY]):
    logging.error("❌ Faltan variables.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# ✅ Divisas OTC disponibles en Twelve Data (plan free)
OTC_PAIRS = ["EUR/USD", "PEN/USD", "USD/BRL"]

TZ_PERU   = ZoneInfo("America/Lima")
SIGNAL_FILE = "otc_signals.json"

def now_peru():
    return datetime.now(TZ_PERU)

# ----------------- PERSISTENCIA -----------------
def load_signals():
    return json.load(open(SIGNAL_FILE)) if os.path.exists(SIGNAL_FILE) else []

def save_signals():
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, default=str)

ACTIVE_SIGNALS = load_signals()

# ----------------- VELAS 1-MIN -----------------
def fetch_last_two_closes(symbol, resolution=1):
    """
    Devuelve (actual, anterior) usando Twelve Data (candles 1-min).
    """
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": f"{resolution}min",
        "apikey": TWELVE_API_KEY,
        "outputsize": 2
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "ok" and len(data["values"]) >= 2:
            actual   = float(data["values"][0]["close"])
            anterior = float(data["values"][1]["close"])
            return actual, anterior
    except Exception as e:
        logging.warning("Twelve Data candle falló %s: %s", symbol, e)
    return None, None

# ----------------- MENSAJES -----------------
def build_open(pair, direction, entry):
    icon = "🚀" if direction == "ARRIBA" else "📉"
    now  = now_peru().strftime("%H:%M:%S")
    return f"{icon} **APUESTA {pair}**\n⏰ Hora: {now}\n📈 Dirección: {direction}\n💰 Entrada: {entry:.4f}"

def build_close(pair, direction, entry, current, result):
    icon = "🤑" if result == "GANASTE" else "😞"
    return (
        f"{icon} **RESULTADO {pair}**\n"
        f"⏰ Cierre: {now_peru().strftime('%H:%M:%S')}\n"
        f"📈 Dirección: {direction}\n"
        f"💰 Entrada: {entry:.4f}\n"
        f"📍 Cierre: {current:.4f}\n"
        f"**{result}**"
    )

# ----------------- TAREAS -----------------
def open_bets():
    for pair in OTC_PAIRS:
        actual, anterior = fetch_last_two_closes(pair)
        if actual is None or anterior is None:
            continue
        direction = "ARRIBA" if actual > anterior else "ABAJO"
        msg = build_open(pair, direction, actual)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            ACTIVE_SIGNALS.append({
                "pair": pair,
                "direction": direction,
                "entry": actual,
                "created_at": now_peru().isoformat(),
                "message_id": sent.message_id
            })
            save_signals()
            logging.info("📤 Apuesta abierta: %s %s", pair, direction)
        except Exception:
            logging.exception("❌ Error enviando apuesta")

def close_bets():
    for sig in ACTIVE_SIGNALS[:]:
        elapsed = (now_peru() - datetime.fromisoformat(sig["created_at"]).replace(tzinfo=TZ_PERU)).total_seconds()
        if elapsed < 300:
            continue

        actual, _ = fetch_last_two_closes(sig["pair"])
        if actual is None:
            continue

        direction = sig["direction"]
        result = (
            "GANASTE"
            if ((direction == "ARRIBA" and actual > sig["entry"]) or
                (direction == "ABAJO"  and actual < sig["entry"]))
            else "PERDISTE"
        )
        msg = build_close(sig["pair"], direction, sig["entry"], actual, result)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg, reply_to_message_id=sig["message_id"])
            ACTIVE_SIGNALS.remove(sig)
        except Exception:
            logging.exception("❌ Error cerrando")
    save_signals()

# ----------------- FLASK -----------------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test():
    if request.args.get("token") != TEST_TOKEN:
        return "Unauthorized", 401
    threading.Thread(lambda: bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba OK")).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ----------------- INICIO -----------------
if __name__ == "__main__":
    logging.info("🚀 Bot OTC 5 min – Perú (Twelve Data + divisas OTC)")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(open_bets)
    schedule.every(30).seconds.do(close_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)