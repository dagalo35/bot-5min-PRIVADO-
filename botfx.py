#!/usr/bin/env python3
"""
Bot FX 5 min – Perú (Twelve Data)
- Logs paso a paso
- Filtro ATR + cambio mínimo
- Anti-spam 5 min
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
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ---------------- CONFIG ----------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_API_KEY]):
    logging.error("❌ Faltan variables.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]
TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "signals.json"

# ---------------- UTILS -----------------------
def now_peru():
    return datetime.now(TZ_PERU)

def load_signals():
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_signals():
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, default=str)

ACTIVE_SIGNALS = load_signals()

def get_price(symbol):
    url = "https://api.twelvedata.com/price"
    params = {"symbol": symbol, "apikey": TWELVE_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        logging.warning("Twelve Data falló (%s): %s", symbol, e)
        return None

def get_atr5(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": "5min", "outputsize": 6, "apikey": TWELVE_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        closes = [float(b["close"]) for b in data["values"]][::-1]
        if len(closes) < 5:
            return None
        trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        return sum(trs) / len(trs)
    except Exception:
        return None

# ---------------- LÓGICA ----------------------
def build_message(pair, direction, entry, tp, sl):
    icon = "🟢" if direction == "COMPRAR" else "🔴"
    now = now_peru().strftime("%H:%M:%S")
    return (
        f"{icon} **SEÑAL {pair}**\n"
        f"⏰ Hora: {now}\n"
        f"📊 Acción: {direction}\n"
        f"💰 Entrada: ≤ {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}\n"
        f"❌ SL: {sl:.5f}"
    )

def build_result(sig, current):
    direction = sig["direction"]
    result = (
        "✅ GANADA"
        if (direction == "COMPRAR" and current >= sig["tp"]) or
           (direction == "VENDER" and current <= sig["tp"])
        else "❌ PERDIDA"
        if (direction == "COMPRAR" and current <= sig["sl"]) or
           (direction == "VENDER" and current >= sig["sl"])
        else "⚖️ EMPATE"
    )
    now = now_peru().strftime("%H:%M:%S")
    return (
        f"📊 **RESULTADO {sig['pair']}**\n"
        f"⏰ Hora: {now}\n"
        f"📍 Precio 5 min: {current:.5f}\n"
        f"{result}"
    )

# ---------------- TAREAS ----------------------
def send_signals():
    logging.info("🔍 Mandando señales...")
    for pair in PAIRS:
        # Anti-spam 5 min
        if any(sig["pair"] == pair and
               (now_peru() - datetime.fromisoformat(sig["created_at"]).replace(tzinfo=TZ_PERU)).total_seconds() < 300
               for sig in ACTIVE_SIGNALS):
            continue

        logging.info("📡 Recibiendo datos para %s", pair)
        price = get_price(pair)
        if price is None:
            continue

        atr = get_atr5(pair)
        if atr is None or atr < 0.00005:
            logging.debug("Volatilidad baja %s", pair)
            continue

        min_change = 0.00015 if "JPY" not in pair else 0.015
        if atr < min_change:
            logging.debug("Cambio insuficiente %s", pair)
            continue

        direction = "COMPRAR" if price > price - atr else "VENDER"
        tick = 0.0002 if "JPY" not in pair else 0.02
        entry = price
        tp = round(entry + tick if direction == "COMPRAR" else entry - tick, 5)
        sl = round(entry - tick if direction == "COMPRAR" else entry + tick, 5)

        logging.info("✅ Cumple parámetros para %s", pair)
        msg = build_message(pair, direction, entry, tp, sl)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            ACTIVE_SIGNALS.append({
                "pair": pair,
                "direction": direction,
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "created_at": now_peru().isoformat(),
                "message_id": sent.message_id
            })
            save_signals()
            logging.info("📤 Enviando al bot: %s %s", pair, direction)
        except Exception:
            logging.exception("❌ Error enviando señal")

def check_results():
    still_active = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (now_peru() - datetime.fromisoformat(sig["created_at"]).replace(tzinfo=TZ_PERU)).total_seconds()
        if elapsed < 300:
            still_active.append(sig)
            continue
        current = get_price(sig["pair"])
        if current is None:
            still_active.append(sig)
            continue
        msg = build_result(sig, current)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg, reply_to_message_id=sig["message_id"])
        except Exception:
            logging.exception("❌ Error respondiendo")
    ACTIVE_SIGNALS[:] = still_active
    save_signals()

# ---------------- FLASK -----------------------
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

# ---------------- INICIO ----------------------
if __name__ == "__main__":
    logging.info("🚀 Bot arrancado con Twelve Data – hora Perú")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        schedule.run_pending()
        time.sleep(1)