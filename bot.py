"""
Bot FX 5 min – Perú (v2 light)
Sin pandas, sin ta.
Compatible con python-telegram-bot 13.x
"""
import os
import json
import time
import logging
import threading
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import schedule
from flask import Flask, request
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ---------------- CONFIG ----------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID        = int(os.getenv("CHAT_ID", "0"))
ALPHA_KEY      = os.getenv("ALPHA_KEY", "").strip()
TEST_TOKEN     = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY]):
    logging.error("Faltan variables de entorno")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

TZ_PERU   = ZoneInfo("America/Lima")
SIGNAL_F  = "signals.json"
ACTIVE_S  = json.load(open(SIGNAL_F)) if os.path.exists(SIGNAL_F) else []

# ---------------- UTILS -----------------------
def now_peru():
    return datetime.now(TZ_PERU)

def save():
    with open(SIGNAL_F, "w") as f:
        json.dump(ACTIVE_S, f, default=str, indent=2)

def is_news_time(dt):
    t = dt.time()
    # Ej: evitar 08:30–09:30 y 14:00–15:00 Lima
    ranges = [(8,30,9,30), (14,0,15,0)]
    for h1,m1,h2,m2 in ranges:
        start = t.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end   = t.replace(hour=h2, minute=m2, second=0, microsecond=0)
        if start <= t <= end:
            return True
    return False

def get_price_series(from_curr, to_curr, interval="5min", n=21):
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_curr,
        "to_symbol": to_curr,
        "interval": interval,
        "outputsize": str(n),
        "apikey": ALPHA_KEY
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    key = f"Time Series FX ({interval})"
    if key not in data:
        return None
    raw = data[key]
    closes = [float(v["4. close"]) for _, v in sorted(raw.items())]
    return closes[-n:]

def sma(lst, n):
    return sum(lst[-n:]) / n

def rsi(closes, n=14):
    deltas = [c - p for p, c in zip(closes, closes[1:])]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sma(gains, n)
    avg_loss = sma(losses, n)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(closes, n=14):
    # simplificado con True Range = |Close_t - Close_{t-1}|
    trs = [abs(c - p) for p, c in zip(closes, closes[1:])]
    return sma(trs, n)

# ---------------- LOGIC -----------------------
def send_signals():
    dt = now_peru()
    if is_news_time(dt):
        logging.info("Saltando – horario de noticias")
        return

    for base, quote in [("EUR","USD"), ("GBP","USD"), ("AUD","USD"), ("USD","JPY")]:
        pair = f"{base}/{quote}"
        if any(s["pair"] == pair for s in ACTIVE_S):
            continue

        closes = get_price_series(base, quote, n=21)
        if not closes or len(closes) < 15:
            continue

        current = closes[-1]
        rsi_val = rsi(closes)
        atr_val = atr(closes)

        direction = None
        if rsi_val < 30:
            direction = "BUY"
        elif rsi_val > 70:
            direction = "SELL"
        else:
            continue

        tick = 0.01 if quote == "JPY" else 0.0001
        tp = round((current + atr_val * 1.5) * (1 if direction == "BUY" else -1) / tick) * tick
        sl = round((current - atr_val * 1.0) * (1 if direction == "BUY" else -1) / tick) * tick

        icon = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"{icon} **SEÑAL {base}/{quote}**\n"
            f"⏰ Hora: {dt.strftime('%H:%M:%S')}\n"
            f"📊 Acción: {direction}\n"
            f"💰 Entrada: ≤ {current:.5f}\n"
            f"🎯 TP: {tp:.5f}\n"
            f"❌ SL: {sl:.5f}\n"
            f"📈 RSI: {rsi_val:.1f}"
        )
        try:
            m = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            ACTIVE_S.append({
                "pair": pair,
                "direction": direction,
                "entry": current,
                "tp": tp,
                "sl": sl,
                "created_at": dt.isoformat(),
                "message_id": m.message_id
            })
            save()
        except Exception as e:
            logging.exception("Error enviando señal")

def check_results():
    still = []
    for sig in ACTIVE_S:
        if (now_peru() - datetime.fromisoformat(sig["created_at"])).total_seconds() < 300:
            still.append(sig)
            continue
        base, quote = sig["pair"].split("/")
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": base,
            "to_currency": quote,
            "apikey": ALPHA_KEY
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            current = float(r.json()["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            result = "✅ GANADA" if (
                (sig["direction"] == "BUY" and current >= sig["tp"]) or
                (sig["direction"] == "SELL" and current <= sig["tp"])
            ) else "❌ PERDIDA" if (
                (sig["direction"] == "BUY" and current <= sig["sl"]) or
                (sig["direction"] == "SELL" and current >= sig["sl"])
            ) else "⚖️ EMPATE"
            msg = (
                f"📊 **RESULTADO {sig['pair']}**\n"
                f"⏰ Hora: {now_peru().strftime('%H:%M:%S')}\n"
                f"📍 Precio: {current:.5f}\n"
                f"{result}"
            )
            bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown",
                             reply_to_message_id=sig["message_id"])
        except Exception:
            still.append(sig)
    ACTIVE_S[:] = still
    save()

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
    logging.info("🚀 Bot FX v2-light arrancado")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        schedule.run_pending()
        time.sleep(1)