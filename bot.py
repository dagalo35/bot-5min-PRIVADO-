"""
Bot FX 5 min – Perú v2
- ATR dinámico para TP/SL
- RSI como filtro de momentum
- Evita horas de noticias (hard-coded para simplificar)
- Persistencia robusta
"""
import os
import json
import time
import logging
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import schedule
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator
from flask import Flask, request
from telegram import Bot
from dotenv import load_dotenv

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()

# ---------------- CREDENCIALES ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY]):
    logging.error("❌ Falta configuración.")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# ---------------- CONFIG ----------------------
TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_FILE = "signals_v2.json"
CANDLES_FILE = "candles.json"  # cache de velas históricas
MIN_RSI = 30
MAX_RSI = 70
ATR_MULTIPLIER_TP = 1.5
ATR_MULTIPLIER_SL = 1.0
LOOKBACK = 14  # períodos para ATR y RSI
NEWS_TIMES = [
    # Ejemplo: evitar entre 08:30-09:30 y 14:00-15:00 hora de Lima
    (8, 30, 9, 30),
    (14, 0, 15, 0)
]

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("AUD", "USD"),
    ("USD", "JPY")
]

# ---------------- PERSISTENCIA ----------------
def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, default=str, indent=2)

ACTIVE_SIGNALS = load_json(SIGNAL_FILE, [])
CANDLES_CACHE = load_json(CANDLES_FILE, {})

# ---------------- UTILS -----------------------
def now_peru():
    return datetime.now(TZ_PERU)

def is_news_time(dt):
    t = dt.time()
    for h1, m1, h2, m2 in NEWS_TIMES:
        start = t.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = t.replace(hour=h2, minute=m2, second=0, microsecond=0)
        if start <= t <= end:
            return True
    return False

def get_alpha_series(from_curr, to_curr, interval="5min", outputsize=LOOKBACK + 1):
    """
    Descarga las últimas velas de 5 min vía Alpha Vantage FX_INTRADAY
    Devuelve pd.Series de precios de cierre.
    """
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_curr,
        "to_symbol": to_curr,
        "interval": interval,
        "outputsize": str(outputsize),
        "apikey": ALPHA_KEY
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    key = f"Time Series FX ({interval})"
    if key not in data:
        logging.warning("No se encontró serie %s/%s", from_curr, to_curr)
        return None
    raw = data[key]
    df = pd.DataFrame(raw).T.astype(float)
    closes = df.loc[:, "4. close"].sort_index()
    return closes.iloc[-outputsize:]

def calc_indicators(series):
    """
    Calcula ATR y RSI sobre la serie de precios.
    Devuelve (atr, rsi) del último punto.
    """
    if len(series) < LOOKBACK + 1:
        return None, None
    atr = AverageTrueRange(
        high=series, low=series, close=series, window=LOOKBACK
    ).average_true_range().iloc[-1]
    rsi = RSIIndicator(series, window=LOOKBACK).rsi().iloc[-1]
    return atr, rsi

def build_signal_msg(base, quote, direction, entry, tp, sl, rsi, atr):
    icon = "🟢" if direction == "BUY" else "🔴"
    now = now_peru().strftime("%H:%M:%S")
    return (
        f"{icon} **SEÑAL {base}/{quote}**\n"
        f"⏰ Hora: {now}\n"
        f"📊 Acción: {direction}\n"
        f"💰 Entrada: ≤ {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}  (ATR*{ATR_MULTIPLIER_TP:.1f})\n"
        f"❌ SL: {sl:.5f}  (ATR*{ATR_MULTIPLIER_SL:.1f})\n"
        f"📈 RSI: {rsi:.1f}"
    )

def build_result_msg(sig, current):
    direction = sig["direction"]
    entry, tp, sl = sig["entry"], sig["tp"], sig["sl"]
    result = (
        "✅ GANADA" if (direction == "BUY" and current >= tp) or
                      (direction == "SELL" and current <= tp) else
        "❌ PERDIDA" if (direction == "BUY" and current <= sl) or
                       (direction == "SELL" and current >= sl) else
        "⚖️ EMPATE"
    )
    now = now_peru().strftime("%H:%M:%S")
    return (
        f"📊 **RESULTADO {sig['pair']}**\n"
        f"⏰ Hora: {now}\n"
        f"📍 Precio: {current:.5f}\n"
        f"{result}"
    )

# ---------------- LÓGICA ----------------------
def send_signals():
    dt = now_peru()
    if is_news_time(dt):
        logging.info("Saltando señales – hora de noticias")
        return

    for base, quote in PAIRS:
        pair = f"{base}/{quote}"
        if any(s["pair"] == pair for s in ACTIVE_SIGNALS):
            continue

        closes = get_alpha_series(base, quote)
        if closes is None:
            continue
        atr, rsi = calc_indicators(closes)
        if atr is None or rsi is None:
            continue

        last_close = closes.iloc[-1]
        direction = None
        if rsi < MIN_RSI:
            direction = "BUY"
        elif rsi > MAX_RSI:
            direction = "SELL"
        else:
            continue  # RSI neutro

        tick_size = 0.01 if quote == "JPY" else 0.0001
        tp = last_close + atr * ATR_MULTIPLIER_TP * (-1 if direction == "SELL" else 1)
        sl = last_close - atr * ATR_MULTIPLIER_SL * (-1 if direction == "SELL" else 1)

        # Normalizar TP/SL a múltiplos de tick
        tp = round(tp / tick_size) * tick_size
        sl = round(sl / tick_size) * tick_size

        msg = build_signal_msg(base, quote, direction, last_close, tp, sl, rsi, atr)
        try:
            sent = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            ACTIVE_SIGNALS.append({
                "pair": pair,
                "direction": direction,
                "entry": last_close,
                "tp": tp,
                "sl": sl,
                "created_at": dt.isoformat(),
                "message_id": sent.message_id
            })
            save_json(SIGNAL_FILE, ACTIVE_SIGNALS)
            logging.info("Señal enviada %s %s", pair, direction)
        except Exception:
            logging.exception("Error enviando señal %s", pair)

def check_results():
    still_active = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (now_peru() - datetime.fromisoformat(sig["created_at"])).total_seconds()
        if elapsed < 300:
            still_active.append(sig)
            continue

        base, quote = sig["pair"].split("/")
        current = get_price_latest(base, quote)
        if current is None:
            still_active.append(sig)
            continue

        msg = build_result_msg(sig, current)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown",
                             reply_to_message_id=sig["message_id"])
        except Exception:
            logging.exception("Error enviando resultado %s", sig["pair"])

    ACTIVE_SIGNALS[:] = still_active
    save_json(SIGNAL_FILE, ACTIVE_SIGNALS)

def get_price_latest(from_curr, to_curr):
    """Último precio usando mismo endpoint de Alpha Vantage (rápido)"""
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_curr,
        "to_currency": to_curr,
        "apikey": ALPHA_KEY
    }
    try:
        r = requests.get("https://www.alphavantage.co/query", params=params, timeout=10)
        r.raise_for_status()
        return float(r.json()["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
    except Exception as e:
        logging.warning("Error obteniendo precio: %s", e)
        return None

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
    threading.Thread(
        target=lambda: bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba OK")
    ).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ---------------- MAIN ------------------------
if __name__ == "__main__":
    logging.info("🚀 Bot FX v2 arrancado – ATR + RSI")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        schedule.run_pending()
        time.sleep(1)