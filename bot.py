"""
Bot FX 5 min – Perú (v4-light + Twelve Data)
Worker / sin Flask / sin cron externo
Compatible con python-telegram-bot 13.x
"""
import os
import json
import time
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import schedule
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ---------------- CONFIG ----------------------
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID          = int(os.getenv("CHAT_ID", "0"))
TWELVE_API_KEY   = os.getenv("TWELVE_API_KEY", "").strip()

logging.info("TOKEN: %s  CHAT_ID: %s  TWELVE_KEY: %s",
             bool(TELEGRAM_TOKEN), bool(CHAT_ID), bool(TWELVE_API_KEY))

if not all([TELEGRAM_TOKEN, CHAT_ID, TWELVE_API_KEY]):
    logging.error("Faltan variables de entorno")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
TZ_PERU   = ZoneInfo("America/Lima")
SIGNAL_F  = "signals.json"
lock      = threading.Lock()

try:
    with open(SIGNAL_F) as f:
        ACTIVE_S = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ACTIVE_S = []

# ---------------- UTILS -----------------------
def now_peru():
    return datetime.now(TZ_PERU)

def save():
    with lock:
        with open(SIGNAL_F, "w") as f:
            json.dump(ACTIVE_S, f, default=str, indent=2)

def sma(lst, n):
    return sum(lst[-n:]) / n if len(lst) >= n else None

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    deltas = [c - p for p, c in zip(closes, closes[1:])]
    gains  = [d if d > 0 else 0 for d in deltas[-n:]]
    losses = [-d if d < 0 else 0 for d in deltas[-n:]]
    avg_gain = sma(gains, n)
    avg_loss = sma(losses, n)
    if not avg_gain or not avg_loss or avg_loss == 0:
        return None
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(closes, n=14):
    if len(closes) < n + 1:
        return None
    trs = [abs(c - p) for p, c in zip(closes, closes[1:])]
    return sma(trs, n)

def get_price_series(symbol, interval="5min", count=21):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": count,
        "apikey": TWELVE_API_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if "values" in data and data["values"]:
            closes = [float(d["close"]) for d in data["values"]][::-1]
            return closes[-count:]
    except Exception as e:
        logging.warning("Twelve Data falló para %s: %s", symbol, e)
    return None

# ---------------- LOGIC -----------------------
def send_signals():
    dt = now_peru()
    logging.info("Ejecutando send_signals – hora %s", dt.strftime("%H:%M:%S"))

    for pair in ["EUR/USD", "GBP/USD", "AUD/USD", "USD/JPY"]:
        symbol = pair
        with lock:
            if any(s["pair"] == symbol for s in ACTIVE_S):
                logging.debug("%s ya tiene señal activa", symbol)
                continue

        closes = get_price_series(symbol)
        if not closes or len(closes) < 15:
            logging.debug("Datos insuficientes para %s", symbol)
            continue

        current = closes[-1]
        rsi_val = rsi(closes)
        atr_val = atr(closes)

        if rsi_val is None or atr_val is None:
            logging.debug("Indicadores nulos para %s", symbol)
            continue

        direction = "BUY" if rsi_val < 30 else "SELL" if rsi_val > 70 else None
        if not direction:
            continue

        tick = 0.01 if "JPY" in symbol else 0.0001
        tp = round((current + atr_val * 1.5 * (1 if direction == "BUY" else -1)) / tick) * tick
        sl = round((current - atr_val * 1.0 * (1 if direction == "BUY" else -1)) / tick) * tick

        icon = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"{icon} **SEÑAL {symbol}**\n"
            f"⏰ Hora: {dt.strftime('%H:%M:%S')}\n"
            f"📊 Acción: {direction}\n"
            f"💰 Entrada: ≤ {current:.5f}\n"
            f"🎯 TP: {tp:.5f}\n"
            f"❌ SL: {sl:.5f}\n"
            f"📈 RSI: {rsi_val:.1f}"
        )
        try:
            m = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            with lock:
                ACTIVE_S.append({
                    "pair": symbol,
                    "direction": direction,
                    "entry": current,
                    "tp": tp,
                    "sl": sl,
                    "created_at": dt.isoformat(),
                    "message_id": m.message_id
                })
                save()
            logging.info("Señal enviada %s %s", symbol, direction)
        except Exception:
            logging.exception("Error enviando señal")

def check_results():
    now = now_peru()
    still = []
    with lock:
        signals_to_check = list(ACTIVE_S)

    for sig in signals_to_check:
        if (now - datetime.fromisoformat(sig["created_at"])).total_seconds() < 300:
            still.append(sig)
            continue

        pair = sig["pair"]
        url = "https://api.twelvedata.com/quote"
        params = {"symbol": pair, "apikey": TWELVE_API_KEY}
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            current = float(data["close"])
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
        except Exception as e:
            logging.exception("Error verificando resultado")
            still.append(sig)

    with lock:
        ACTIVE_S[:] = still
        save()

# ---------------- MAIN ------------------------
if __name__ == "__main__":
    logging.info("🚀 Bot FX v4-light + Twelve Data arrancado (Worker)")
    # Programa las tareas internas
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logging.exception("Error en el loop principal: %s", e)
            time.sleep(5)