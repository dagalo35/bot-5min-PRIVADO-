#!/usr/bin/env python3
"""
Bot OTC 5 min – Perú (Twelve Data + divisas)
- Apuesta simple: arriba / abajo
- Cierre automático a los 5 min
- Dirección según última vela 5-min
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
from decimal import Decimal
from threading import Lock

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from telegram import Bot
from flask import Flask, request

load_dotenv()

# ---------- LOGGING ----------
handler = RotatingFileHandler("otc_bot.log", maxBytes=500_000, backupCount=2)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler, logging.StreamHandler(sys.stdout)],
)

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
try:
    CHAT_ID = int(os.getenv("CHAT_ID", "0"))
except ValueError:
    logging.error("❌ CHAT_ID debe ser un entero.")
    sys.exit(1)

TWELVE_KEY = os.getenv("TWELVE_KEY", "").strip()
if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_KEY]):
    logging.error("❌ Falta TELEGRAM_TOKEN, CHAT_ID o TWELVE_KEY.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# Mapeo Binance -> Twelve Data (FOREX)
PAIR_MAP = {
    "EURUSDT": "EUR/USD",
    "BRLUSDT": "USD/BRL",
    "PENUSDT": "USD/PEN",
}
OTC_PAIRS = list(PAIR_MAP.keys())

TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "otc_signals.json"
LOCK = Lock()

def now_peru():
    return datetime.now(TZ_PERU)

# ---------- PERSISTENCIA ----------
def load_signals():
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_signals():
    with LOCK:
        with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, default=str)

ACTIVE_SIGNALS = load_signals()

# ---------- VELAS 5-MIN (Twelve Data) ----------
def fetch_last_two_closes(symbol: str):
    """
    Devuelve (actual, anterior) usando Twelve Data.
    """
    td_symbol = PAIR_MAP.get(symbol)
    if not td_symbol:
        logging.warning("Par %s no mapeado para Twelve Data", symbol)
        return None, None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": td_symbol,
        "interval": "5min",
        "outputsize": 2,
        "apikey": TWELVE_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("values", [])
        if len(data) >= 2:
            # Twelve Data devuelve más reciente primero
            actual = Decimal(data[0]["close"])
            anterior = Decimal(data[1]["close"])
            return actual, anterior
    except Exception as e:
        logging.warning("Twelve Data falló %s: %s", symbol, e)
    return None, None

# ---------- MENSAJES ----------
def build_open(pair, direction, entry):
    icon = "🚀" if direction == "ARRIBA" else "📉"
    now = now_peru().strftime("%H:%M")
    return (
        f"{icon} **APUESTA {pair}**\n"
        f"⏰ Hora: {now}\n"
        f"📈 Dirección: {direction}\n"
        f"💰 Entrada: {entry:.4f}"
    )

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

# ---------- TAREAS ----------
def open_bets():
    with LOCK:
        opened = {sig["pair"] for sig in ACTIVE_SIGNALS}

    for pair in OTC_PAIRS:
        if pair in opened:
            continue
        actual, anterior = fetch_last_two_closes(pair)
        if actual is None or anterior is None:
            continue
        direction = "ARRIBA" if actual > anterior else "ABAJO"
        msg = build_open(pair, direction, actual)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            with LOCK:
                ACTIVE_SIGNALS.append(
                    {
                        "pair": pair,
                        "direction": direction,
                        "entry": float(actual),
                        "created_at": now_peru().isoformat(),
                        "message_id": sent.message_id,
                    }
                )
                save_signals()
            logging.info("📤 Apuesta abierta: %s %s", pair, direction)
        except Exception:
            logging.exception("❌ Error enviando apuesta")

def close_bets():
    now = now_peru()
    to_close = []
    with LOCK:
        for sig in list(ACTIVE_SIGNALS):
            elapsed = (
                now - datetime.fromisoformat(sig["created_at"]).replace(tzinfo=TZ_PERU)
            ).total_seconds()
            if elapsed >= 300:
                to_close.append(sig)

    for sig in to_close:
        actual, _ = fetch_last_two_closes(sig["pair"])
        if actual is None:
            continue
        direction = sig["direction"]
        result = (
            "GANASTE"
            if (direction == "ARRIBA" and actual > sig["entry"])
            or (direction == "ABAJO" and actual < sig["entry"])
            else "PERDISTE"
        )
        msg = build_close(sig["pair"], direction, sig["entry"], actual, result)
        try:
            bot.send_message(
                chat_id=CHAT_ID, text=msg, reply_to_message_id=sig["message_id"]
            )
            with LOCK:
                ACTIVE_SIGNALS.remove(sig)
        except Exception:
            logging.exception("❌ Error cerrando")
    save_signals()

# ---------- FLASK ----------
app = Flask(__name__)

@app.route("/")
def ok():
    with LOCK:
        return {"status": "ok", "signals": len(ACTIVE_SIGNALS)}, 200

@app.route("/test")
def test():
    if request.args.get("token") != os.getenv("TEST_TOKEN", "test"):
        return "Unauthorized", 401
    threading.Thread(
        target=lambda: bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba OK"),
        daemon=True,
    ).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ---------- INICIO ----------
if __name__ == "__main__":
    logging.info("🚀 Bot OTC 5 min – Perú (Twelve Data + divisas)")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(open_bets)
    schedule.every(30).seconds.do(close_bets)
    while True:
        schedule.run_pending()
        time.sleep(1)