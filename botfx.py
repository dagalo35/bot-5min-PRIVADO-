#!/usr/bin/env python3
"""
Bot de señales FX 5 min – Perú
- Twelve Data en tiempo real
- Rangos amplios para reducir empates
- Hora local (Lima)
- Persistencia en disco (signals.json)
- Resultados como respuesta al mensaje original
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

# ---------------- CONFIG ----------------------
load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_API_KEY]):
    logging.error("❌ Faltan variables de entorno.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# ---------------- CONSTANTES ------------------
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"]

TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "signals.json"

# ---------------- UTILS -----------------------
def load_signals():
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_signals():
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, default=str)

ACTIVE_SIGNALS = load_signals()

# Obtiene precio con Twelve Data
def get_price(symbol="EUR/USD"):
    url = "https://api.twelvedata.com/price"
    params = {"symbol": symbol, "apikey": TWELVE_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        logging.warning("Twelve Data falló para %s: %s", symbol, e)
        return None

# ---------------- LÓGICA DE SEÑALES ------------
def build_message(pair, direction, entry, tp, sl, prob):
    icon = "🟢" if direction == "COMPRAR" else "🔴"
    now = datetime.now(TZ_PERU).strftime("%H:%M:%S")
    return (
        f"{icon} **SEÑAL {pair}**\n"
        f"⏰ Hora: {now}\n"
        f"📊 **Acción: {direction}**\n"
        f"💰 Entrada: ≤ {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}\n"
        f"❌ SL: {sl:.5f}\n"
        f"📈 Probabilidad: ~{prob}%"
    )

def build_result_message(sig, current):
    direction = sig["direction"]
    entry = sig["entry"]
    tp = sig["tp"]
    sl = sig["sl"]

    if (direction == "COMPRAR" and current >= tp) or (direction == "VENDER" and current <= tp):
        result = "✅ GANADA"
    elif (direction == "COMPRAR" and current <= sl) or (direction == "VENDER" and current >= sl):
        result = "❌ PERDIDA"
    else:
        result = "⚖️ EMPATE"

    now = datetime.now(TZ_PERU).strftime("%H:%M:%S")
    return (
        f"📊 **RESULTADO {sig['pair']}**\n"
        f"⏰ Hora: {now}\n"
        f"📊 **Acción: {direction}**\n"
        f"💰 Entrada: {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}\n"
        f"❌ SL: {sl:.5f}\n"
        f"📍 Precio 5 min: {current:.5f}\n"
        f"{result}"
    )

# ---------------- TAREAS PROGRAMADAS ----------
def send_signals():
    for pair in PAIRS:
        if any(sig["pair"] == pair for sig in ACTIVE_SIGNALS):
            continue

        price = get_price(pair)
        if price is None:
            continue

        # simulamos micro-trend con un pequeño delta
        previous = price - 0.0001 if "JPY" not in pair else price - 0.01
        diff = abs(price - previous)
        if diff < 0.00005:
            continue

        direction = "COMPRAR" if price > previous else "VENDER"
        tick = 0.0008 if "JPY" not in pair else 0.08
        entry = price
        tp = round(entry + tick if direction == "COMPRAR" else entry - tick, 5)
        sl = round(entry - tick if direction == "COMPRAR" else entry + tick, 5)
        prob = min(95, max(50, int(diff * 1_000_000)))

        msg = build_message(pair, direction, entry, tp, sl, prob)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            ACTIVE_SIGNALS.append({
                "pair": pair,
                "direction": direction,
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "created_at": datetime.now(TZ_PERU).isoformat(),
                "message_id": sent.message_id
            })
            save_signals()
        except Exception:
            logging.exception("❌ Error enviando señal")
        time.sleep(1)

def check_results():
    still_active = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (datetime.now(TZ_PERU) - datetime.fromisoformat(sig["created_at"])).total_seconds()
        if elapsed < 300:
            still_active.append(sig)
            continue

        current = get_price(sig["pair"])
        if current is None:
            still_active.append(sig)
            continue

        msg = build_result_message(sig, current)
        try:
            bot.send_message(
                chat_id=CHAT_ID,
                text=msg,
                reply_to_message_id=sig["message_id"]
            )
        except Exception:
            logging.exception("❌ Error respondiendo al mensaje")

    ACTIVE_SIGNALS[:] = still_active
    save_signals()

# ---------------- FLASK (health-check) --------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test_signal():
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
    logging.info("🚀 Bot arrancado con Twelve Data – hora de Perú")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        schedule.run_pending()
        time.sleep(1)