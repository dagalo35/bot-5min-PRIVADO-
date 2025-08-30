#!/usr/bin/env python3
"""
Bot FX 5 min – Perú (v4-light + Twelve Data)
Worker / sin Flask / sin cron externo
Compatible con python-telegram-bot 13.x
"""
import os
import json
import time
import logging
import threading
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "").strip()

if not TELEGRAM_TOKEN or not CHAT_ID or not TWELVE_API_KEY:
    logging.error("Faltan variables de entorno")
    sys.exit(1)

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    logging.error("CHAT_ID debe ser un número entero")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
TZ_PERU = ZoneInfo("America/Lima")
SIGNAL_F = "signals.json"
lock = threading.Lock()

try:
    with open(SIGNAL_F, encoding="utf-8") as f:
        ACTIVE_S = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ACTIVE_S = []

# ---------------- UTILS -----------------------
def now_peru():
    return datetime.now(TZ_PERU)

def save():
    with lock:
        with open(SIGNAL_F, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_S, f, ensure_ascii=False, indent=2)

def sma(lst, n):
    return sum(lst[-n:]) / n if len(lst) >= n else None

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    deltas = [c - p for p, c in zip(closes, closes[1:])]
    gains = [d if d > 0 else 0 for d in deltas[-n:]]
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

CACHE = {}
CACHE_LOCK = threading.Lock()

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

def cached_price(symbol):
    with CACHE_LOCK:
        if symbol in CACHE and time.time() - CACHE[symbol][0] < 30:
            return CACHE[symbol][1]
    try:
        url = "https://api.twelvedata.com/price"
        r = requests.get(url, params={"symbol": symbol, "apikey": TWELVE_API_KEY}, timeout=10)
        r.raise_for_status()
        price = float(r.json()["price"])
        with CACHE_LOCK:
            CACHE[symbol] = (time.time(), price)
        return price
    except Exception as e:
        logging.warning("Error obteniendo precio live para %s: %s", symbol, e)
        return None

# ---------------- LOGIC -----------------------
SEND_LOCK = threading.Semaphore(1)
CHECK_LOCK = threading.Semaphore(1)

def send_signals():
    if not SEND_LOCK.acquire(blocking=False):
        return
    try:
        dt = now_peru()
        logging.info("Ejecutando send_signals – %s", dt.strftime("%H:%M:%S"))
        for pair in ["EUR/USD", "GBP/USD", "AUD/USD", "USD/JPY"]:
            logging.info("Mandando señal para %s", pair)
            with lock:
                if any(s["pair"] == pair for s in ACTIVE_S):
                    logging.debug("%s ya tiene señal activa", pair)
                    continue
            logging.info("Recibiendo datos de %s", pair)
            closes = get_price_series(pair)
            if not closes or len(closes) < 15:
                logging.debug("Datos insuficientes para %s", pair)
                continue
            current = Decimal(str(closes[-1]))
            logging.info("Verificando parámetros para %s", pair)
            rsi_val = rsi(closes)
            atr_val = atr(closes)
            if rsi_val is None or atr_val is None:
                logging.debug("Indicadores nulos para %s", pair)
                continue

            direction = "BUY" if rsi_val < 30 else "SELL" if rsi_val > 70 else None
            if not direction:
                continue

            tick = Decimal("0.01") if "JPY" in pair else Decimal("0.0001")
            atr_dec = Decimal(str(atr_val))
            tp = (current + atr_dec * Decimal("1.5") * (1 if direction == "BUY" else -1)).quantize(tick, rounding=ROUND_HALF_UP)
            sl = (current - atr_dec * Decimal("1.0") * (1 if direction == "BUY" else -1)).quantize(tick, rounding=ROUND_HALF_UP)

            icon = "🟢" if direction == "BUY" else "🔴"
            msg = (
                f"{icon} **SEÑAL {pair}**\n"
                f"⏰ Hora: {dt.strftime('%H:%M:%S')}\n"
                f"📊 Acción: {direction}\n"
                f"💰 Entrada: ≤ {current:.5f}\n"
                f"🎯 TP: {tp:.5f}\n"
                f"❌ SL: {sl:.5f}\n"
                f"📈 RSI: {rsi_val:.1f}"
            )
            logging.info("Enviando al bot: %s %s", pair, direction)
            try:
                m = bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                with lock:
                    ACTIVE_S.append({
                        "pair": pair,
                        "direction": direction,
                        "entry": float(current),
                        "tp": float(tp),
                        "sl": float(sl),
                        "created_at": dt.isoformat(),
                        "message_id": m.message_id
                    })
                    save()
                logging.info("Señal enviada %s %s", pair, direction)
            except Exception:
                logging.exception("Error enviando señal")
    finally:
        SEND_LOCK.release()

def check_results():
    if not CHECK_LOCK.acquire(blocking=False):
        return
    try:
        now = now_peru()
        cutoff = now - timedelta(hours=6)
        with lock:
            signals = [s for s in ACTIVE_S if datetime.fromisoformat(s["created_at"]) > cutoff]
        still = []
        for sig in signals:
            if (now - datetime.fromisoformat(sig["created_at"])).total_seconds() < 300:
                still.append(sig)
                continue
            current = cached_price(sig["pair"])
            if current is None:
                still.append(sig)
                continue

            result = None
            if (sig["direction"] == "BUY" and current >= sig["tp"]) or \
               (sig["direction"] == "SELL" and current <= sig["tp"]):
                result = "✅ GANADA"
            elif (sig["direction"] == "BUY" and current <= sig["sl"]) or \
                 (sig["direction"] == "SELL" and current >= sig["sl"]):
                result = "❌ PERDIDA"

            if result:
                msg = (
                    f"📊 **RESULTADO {sig['pair']}**\n"
                    f"⏰ Hora: {now.strftime('%H:%M:%S')}\n"
                    f"📍 Precio: {current:.5f}\n"
                    f"{result}"
                )
                try:
                    bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown",
                                     reply_to_message_id=sig["message_id"])
                except Exception as e:
                    logging.warning("No se pudo enviar resultado: %s", e)
            else:
                still.append(sig)
        with lock:
            ACTIVE_S[:] = still
            save()
    finally:
        CHECK_LOCK.release()

# ---------------- MAIN ------------------------
if __name__ == "__main__":
    logging.info("🚀 Bot FX v4-light + Twelve Data arrancado (Worker)")
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Apagando bot...")
            break
        except Exception as e:
            logging.exception("Error en el loop principal: %s", e)
            time.sleep(5)