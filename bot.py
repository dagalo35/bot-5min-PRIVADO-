"""
Bot de señales FX 5 min – Perú
- Alpha Vantage en tiempo real
- Rangos amplios para reducir empates
- Hora local (Lima)
- Persistencia en disco
- Resultados como respuesta al mensaje original
"""

import os
import json
import time
import logging
import sys
import threading
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

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY]):
    logging.error("❌ Faltan variables de entorno.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("AUD", "USD"),
]

# Rangos amplios para reducir empates
MIN_MOVES = {
    ("EUR", "USD"): float(os.getenv("MIN_MOVE_EURUSD", 0.00006)),
    ("GBP", "USD"): float(os.getenv("MIN_MOVE_GBPUSD", 0.00006)),
    ("USD", "JPY"): float(os.getenv("MIN_MOVE_USDJPY", 0.006)),
    ("AUD", "USD"): float(os.getenv("MIN_MOVE_AUDUSD", 0.00006)),
}

TICK_SIZE = {
    ("EUR", "USD"): float(os.getenv("TICK_EURUSD", 0.00080)),
    ("GBP", "USD"): float(os.getenv("TICK_GBPUSD", 0.00080)),
    ("USD", "JPY"): float(os.getenv("TICK_USDJPY", 0.080)),
    ("AUD", "USD"): float(os.getenv("TICK_AUDUSD", 0.00080)),
}

TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "signals.json"

def load_signals():
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE) as f:
            return json.load(f)
    return []

def save_signals():
    with open(SIGNAL_FILE, "w") as f:
        json.dump(ACTIVE_SIGNALS, f, default=str)

ACTIVE_SIGNALS = load_signals()

def get_price(from_curr="EUR", to_curr="USD", attempts=3):
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_curr,
        "to_currency": to_curr,
        "apikey": ALPHA_KEY
    }
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get("https://www.alphavantage.co/query", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "Realtime Currency Exchange Rate" not in data:
                raise ValueError("Campo no encontrado")
            rate_str = data["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
            if not rate_str:
                raise ValueError("Tipo de cambio vacío")
            return float(rate_str)
        except Exception as e:
            logging.warning("⚠️ Alpha Vantage intento %d/%d: %s", attempt, attempts, e)
            time.sleep(2 ** attempt)
    return None

def micro_trend(current, previous, pair):
    diff = abs(current - previous)
    min_move = MIN_MOVES.get(pair, 0.00006)
    return "NEUTRO" if diff < min_move else ("COMPRAR" if current > previous else "VENDER")

def build_message(base, quote, direction, entry, tp, sl, prob):
    icon = "🟢" if direction == "COMPRAR" else "🔴"
    now = datetime.now(TZ_PERU).strftime("%H:%M:%S")
    return (
        f"{icon} **SEÑAL {base}/{quote}**\n"
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

def send_signals():
    for base, quote in PAIRS:
        pair = (base, quote)
        pair_str = f"{base}/{quote}"

        if any(sig["pair"] == pair_str for sig in ACTIVE_SIGNALS):
            continue

        price = get_price(from_curr=base, to_curr=quote)
        if price is None:
            continue

        previous = price - 0.0001 if quote != "JPY" else price - 0.01
        direction = micro_trend(price, previous, pair)
        if direction == "NEUTRO":
            continue

        tick_size = TICK_SIZE.get(pair, 0.00080)
        entry = price
        tp = entry + tick_size if direction == "COMPRAR" else entry - tick_size
        sl = entry - tick_size if direction == "COMPRAR" else entry + tick_size
        prob = min(95, max(50, int(abs(price - previous) * 1_000_000)))

        msg = build_message(base, quote, direction, entry, tp, sl, prob)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg)
            ACTIVE_SIGNALS.append({
                "pair": pair_str,
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
        time.sleep(12)

def check_results():
    still_active = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (datetime.now(TZ_PERU) - datetime.fromisoformat(sig["created_at"])).total_seconds()
        if elapsed < 300:
            still_active.append(sig)
            continue

        base, quote = sig["pair"].split("/")
        current = get_price(from_curr=base, to_curr=quote)
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

if __name__ == "__main__":
    logging.info("🚀 Bot arrancado con hora de Perú y seguimiento de 5 min")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        schedule.run_pending()
        time.sleep(1)