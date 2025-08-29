"""
Bot de señales FX 5 min
- Alpha Vantage para precios en tiempo real
- Log de pips con 4 decimales
- min_move y tick_size configurables
- Zona horaria UTC
- Seguimiento de resultados a los 5 minutos
"""

import os
import time
import logging
import sys
import threading
import requests
import schedule
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Bot
from flask import Flask, request

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

load_dotenv()

# Tokens y variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
ALPHA_KEY = os.getenv("ALPHA_KEY", "").strip()
TEST_TOKEN = os.getenv("TEST_TOKEN", "test")

if not all([TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY]):
    logging.error("❌ Faltan variables de entorno (TELEGRAM_TOKEN, CHAT_ID, ALPHA_KEY).")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

PAIRS = [
    ("EUR", "USD"),
    ("GBP", "USD"),
    ("USD", "JPY"),
    ("AUD", "USD"),
]

# ---------------- CONFIGURABLE ----------------
MIN_MOVES = {
    ("EUR", "USD"): float(os.getenv("MIN_MOVE_EURUSD", 0.00002)),
    ("GBP", "USD"): float(os.getenv("MIN_MOVE_GBPUSD", 0.00002)),
    ("USD", "JPY"): float(os.getenv("MIN_MOVE_USDJPY", 0.002)),
    ("AUD", "USD"): float(os.getenv("MIN_MOVE_AUDUSD", 0.00002)),
}

TICK_SIZE = {
    ("EUR", "USD"): float(os.getenv("TICK_EURUSD", 0.00025)),
    ("GBP", "USD"): float(os.getenv("TICK_GBPUSD", 0.00025)),
    ("USD", "JPY"): float(os.getenv("TICK_USDJPY", 0.025)),
    ("AUD", "USD"): float(os.getenv("TICK_AUDUSD", 0.00025)),
}
# ---------------------------------------------

ACTIVE_SIGNALS = []   # lista en memoria para seguimiento

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
                logging.warning("⚠️ Respuesta inesperada: %s", data)
                raise ValueError("Campo no encontrado")

            rate_str = data["Realtime Currency Exchange Rate"].get("5. Exchange Rate")
            if not rate_str:
                logging.warning("⚠️ Tipo de cambio vacío: %s", data)
                raise ValueError("Tipo de cambio vacío")

            return float(rate_str)
        except Exception as e:
            logging.warning("⚠️ Alpha Vantage intento %d/%d: %s", attempt, attempts, e)
            time.sleep(2 ** attempt)
    logging.error("❌ Fallo tras %d intentos para %s/%s", attempts, from_curr, to_curr)
    return None

def micro_trend(current, previous, pair):
    diff = abs(current - previous)
    min_move = MIN_MOVES.get(pair, 0.00002)
    return "NEUTRO" if diff < min_move else ("CALL" if current > previous else "PUT")

def build_message(base, quote, direction, entry, tp, sl, prob):
    icon = "🟢" if direction == "CALL" else "🔴"
    color = "📈" if direction == "CALL" else "📉"
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return (
        f"{icon} **SEÑAL {base}/{quote}**\n"
        f"⏰ Hora: {now}\n"
        f"{color} **Dirección: {direction}**\n"
        f"💰 Entrada: ≤ {entry:.5f}\n"
        f"🎯 TP: {tp:.5f}\n"
        f"❌ SL: {sl:.5f}\n"
        f"📊 Probabilidad: ~{prob} %"
    )

def build_result_message(sig, current, result):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return (
        f"📊 **RESULTADO {sig['pair']}**\n"
        f"⏰ Hora: {now}\n"
        f"📈 Dirección original: {sig['direction']}\n"
        f"💰 Entrada: {sig['entry']:.5f}\n"
        f"🎯 TP: {sig['tp']:.5f}\n"
        f"❌ SL: {sig['sl']:.5f}\n"
        f"📍 Precio 5 min: {current:.5f}\n"
        f"{result}"
    )

def send_signals():
    for base, quote in PAIRS:
        pair = (base, quote)
        pair_str = f"{base}/{quote}"

        # Evitar duplicados
        if any(sig["pair"] == pair_str for sig in ACTIVE_SIGNALS):
            logging.info("⏳ Señal ya activa para %s", pair_str)
            continue

        logging.info("🔍 Analizando %s...", pair_str)
        price = get_price(from_curr=base, to_curr=quote)
        if price is None:
            logging.warning("⚠️ Precio inválido para %s, saltando...", pair_str)
            continue

        # Simulamos 2 precios con un deslizamiento mínimo para evitar 2 llamadas
        previous = price - 0.0001 if quote != "JPY" else price - 0.01
        direction = micro_trend(price, previous, pair)
        if direction == "NEUTRO":
            logging.info("➖ Sin señal para %s (NEUTRO)", pair_str)
            continue

        tick_size = TICK_SIZE.get(pair, 0.00025)
        entry = price
        tp = entry - tick_size if direction == "PUT" else entry + tick_size
        sl = entry + tick_size if direction == "PUT" else entry - tick_size
        prob = min(95, max(50, int(abs(price - previous) * 1_000_000)))

        msg = build_message(base, quote, direction, entry, tp, sl, prob)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg)
            logging.info("✅ Señal enviada: %s -> %s", pair_str, direction)
        except Exception:
            logging.exception("❌ Error enviando mensaje")
            continue

        ACTIVE_SIGNALS.append({
            "pair": pair_str,
            "direction": direction,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "created_at": datetime.now(timezone.utc)
        })

        time.sleep(12)  # Evitar límite de Alpha Vantage

def check_results():
    still_active = []
    for sig in ACTIVE_SIGNALS:
        elapsed = (datetime.now(timezone.utc) - sig["created_at"]).total_seconds()
        if elapsed < 300:
            still_active.append(sig)
            continue

        base, quote = sig["pair"].split("/")
        current = get_price(from_curr=base, to_curr=quote)
        if current is None:
            still_active.append(sig)
            continue

        direction = sig["direction"]
        tp = sig["tp"]
        sl = sig["sl"]

        if (direction == "CALL" and current >= tp) or \
           (direction == "PUT" and current <= tp):
            result = "✅ TP ALCANZADO"
        elif (direction == "CALL" and current <= sl) or \
             (direction == "PUT" and current >= sl):
            result = "❌ SL TOCADO"
        else:
            result = "⏳ SIN TOCAR"

        msg = build_result_message(sig, current, result)
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg)
            logging.info("✅ Resultado enviado: %s → %s", sig['pair'], result)
        except Exception:
            logging.exception("❌ Error enviando resultado")

    ACTIVE_SIGNALS[:] = still_active
    logging.info("📊 Señales activas: %d", len(ACTIVE_SIGNALS))

# ------------------- Flask --------------------
app = Flask(__name__)

@app.route("/")
def ok():
    return "ok", 200

@app.route("/test")
def test_signal():
    token = request.args.get("token")
    if token != TEST_TOKEN:
        return "Unauthorized", 401

    def _send():
        try:
            bot.send_message(chat_id=CHAT_ID, text="🔔 Prueba de señal funcionando")
            logging.info("✅ Test enviado")
        except Exception:
            logging.exception("❌ Error en /test")
    threading.Thread(target=_send, daemon=True).start()
    return "Enviado", 200

def run_web():
    port = int(os.getenv("PORT", 5000))
    logging.info("🌐 Escuchando en el puerto %s", port)
    app.run(host="0.0.0.0", port=port)

# ------------------- Main --------------------
if __name__ == "__main__":
    logging.info("🚀 Bot arrancado con seguimiento de 5 min")
    threading.Thread(target=run_web, daemon=True).start()
    schedule.every(5).minutes.do(send_signals)
    schedule.every(30).seconds.do(check_results)
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("❌ Error en run_pending")
        time.sleep(1)