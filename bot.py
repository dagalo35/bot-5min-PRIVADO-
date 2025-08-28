import os, time, requests, schedule
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN").strip()
ALPHA_KEY      = os.getenv("ALPHA_KEY").strip()
CHAT_ID        = os.getenv("CHAT_ID").strip()
PAIR           = "EURUSD"

bot = Bot(token=TELEGRAM_TOKEN.strip())

def get_price():
    url = "https://www.alphavantage.co/query?"
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": "EUR",
        "to_currency": "USD",
        "apikey": ALPHA_KEY
    }
    r = requests.get(url, params=params, timeout=10).json()
    try:
        return float(r["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
    except KeyError:
        return None

def micro_trend(prices):
    if len(prices) < 3:
        return "NEUTRO"
    if prices[-1] > prices[-2] > prices[-3]:
        return "CALL"
    elif prices[-1] < prices[-2] < prices[-3]:
        return "PUT"
    else:
        return "NEUTRO"

def send_signal():
    prices = [get_price() for _ in range(3)]
    prices = [p for p in prices if p is not None]
    direction = micro_trend(prices)
    if direction == "NEUTRO":
        return
    entry = prices[-1]
    tp = entry - 0.00025 if direction == "PUT" else entry + 0.00025
    sl = entry + 0.00025 if direction == "PUT" else entry - 0.00025
    msg = (f"🔔 Señal EUR/USD 5 min\n"
           f"⏰ Hora: {time.strftime('%H:%M:%S')}\n"
           f"📊 Dirección: {direction}\n"
           f"💰 Entrada: ≤ {entry}\n"
           f"🎯 TP: {tp:.5f}\n"
           f"❌ SL: {sl:.5f}")
    bot.send_message(chat_id=CHAT_ID, text=msg)

schedule.every(5).minutes.do(send_signal)

if __name__ == "__main__":
    while True:
        schedule.run_pending()
        time.sleep(1)
