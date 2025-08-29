def get_price_series(from_curr, to_curr, interval="5min", n=21):
    """
    Descarga n velas de 5 min vía FX_INTRADAY.
    Si falla (límite, clave, etc.) usa CURRENCY_EXCHANGE_RATE como fallback.
    Devuelve lista de n valores iguales para que RSI/ATR no fallen.
    """
    # 1) Intentar FX_INTRADAY
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_curr,
        "to_symbol": to_curr,
        "interval": interval,
        "outputsize": str(n),
        "apikey": ALPHA_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        key = f"Time Series FX ({interval})"
        if key in data and data[key]:
            raw = data[key]
            closes = [float(v["4. close"]) for _, v in sorted(raw.items())]
            if len(closes) >= n:
                return closes[-n:]
    except Exception as e:
        logging.warning("FX_INTRADAY falló para %s/%s: %s", from_curr, to_curr, e)

    # 2) Fallback a precio actual
    fallback_url = "https://www.alphavantage.co/query"
    fallback_params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_curr,
        "to_currency": to_curr,
        "apikey": ALPHA_KEY
    }
    try:
        r = requests.get(fallback_url, params=fallback_params, timeout=10)
        r.raise_for_status()
        payload = r.json()
        current = float(payload["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
        logging.info("Fallback activado para %s/%s -> %.5f", from_curr, to_curr, current)
        return [current] * n   # lista plana
    except Exception as e:
        logging.error("Fallback también falló: %s", e)
        return None