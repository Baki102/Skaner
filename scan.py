"""Skaner intraday (świece 1-minutowe, USA).
🟢 zielony = odbicie już widać
🟡 żółty   = możliwe odbicie (stabilizacja), jeszcze niepotwierdzone
To sygnały techniczne, nie rekomendacje."""
import os
import time
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ====== EDYTUJ ======
TICKERS = ["PLTR", "SOFI", "AMD", "INTC", "SNAP", "F"]
MAX_PRICE = None        # np. 5 = tylko spółki do 5 USD; None = bez limitu
MIN_DROP = 0.03         # spadek min. 3% od maksimum z ostatnich 60 minut
RSI_LOW = 35            # RSI(9) zszedł poniżej tej wartości w ostatnich 15 min
COOLDOWN_MIN = 30       # ten sam sygnał dla tej samej spółki nie częściej niż co 30 min
# ====================

ET = ZoneInfo("America/New_York")
sent = {}  # ticker -> (poziom, czas)


def rsi(close, n=9):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down)


def fetch(t):
    try:
        return yf.Ticker(t).history(period="1d", interval="1m",
                                    prepost=False, auto_adjust=True)
    except Exception as e:
        print(f"{t}: błąd pobierania {e}")
        return None


def classify(df):
    """Zwraca (poziom, opis) albo None. poziom: 'green' / 'yellow'."""
    if df is None or len(df) < 25:
        return None
    c, v = df["Close"], df["Volume"]
    price = float(c.iloc[-1])
    if MAX_PRICE and price > MAX_PRICE:
        return None

    drop = price / float(c.iloc[-60:].max()) - 1
    r = rsi(c)
    rsi_min = float(r.iloc[-15:].min())
    rsi_now, rsi_prev = float(r.iloc[-1]), float(r.iloc[-4])
    last15 = c.iloc[-15:]
    low_pos = int(last15.values.argmin())          # 14 = ostatnia świeca
    bounce = price / float(last15.min()) - 1
    sma10 = float(c.rolling(10).mean().iloc[-1])
    base = float(v.iloc[:-5].mean()) or 1.0
    vol_ratio = float(v.iloc[-5:].mean()) / base

    if drop > -MIN_DROP or rsi_min > RSI_LOW:      # nie było spadku/wyprzedania
        return None

    info = (f"{price:.2f} USD | od szczytu {drop*100:.1f}% | "
            f"od dołka +{bounce*100:.1f}% | RSI {rsi_now:.0f} | wol. x{vol_ratio:.1f}")

    green = (price > sma10 and c.iloc[-1] > c.iloc[-2] > c.iloc[-3]
             and bounce >= 0.005 and low_pos <= 11 and vol_ratio >= 1.3)
    if green:
        return "green", info
    yellow = low_pos <= 9 and rsi_now > rsi_prev and bounce >= 0.001
    if yellow:
        return "yellow", info
    return None


def send(text):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text)
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("Telegram błąd:", e)


def should_send(t, level):
    now = time.time()
    prev = sent.get(t)
    if prev is None:
        return True
    if prev[0] == "yellow" and level == "green":   # awans żółty -> zielony
        return True
    return prev[0] != level or now - prev[1] > COOLDOWN_MIN * 60


def main():
    stop = None
    s = os.getenv("STOP_AT_UTC")                   # np. "18:30"
    if s:
        h, m = map(int, s.split(":"))
        stop = dt.datetime.now(dt.timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)

    empty_rounds = 0
    while True:
        started = time.time()
        now_et = dt.datetime.now(ET)
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if now_et >= close_et:
            break
        if stop and dt.datetime.now(dt.timezone.utc) >= stop:
            break
        if now_et < open_et + dt.timedelta(minutes=1):
            time.sleep(30)
            continue

        got_data = False
        for t in TICKERS:
            df = fetch(t)
            if df is not None and not df.empty:
                got_data = True
            res = classify(df)
            if res and should_send(t, res[0]):
                level, info = res
                icon, label = (("🟢", "ODBICIE WIDOCZNE") if level == "green"
                               else ("🟡", "możliwe odbicie, niepotwierdzone"))
                send(f"{icon} {t}: {label}\n{info}\n(sygnał techniczny, nie rekomendacja)")
                sent[t] = (level, time.time())

        empty_rounds = 0 if got_data else empty_rounds + 1
        if empty_rounds >= 10:                     # święto / brak danych
            print("Brak danych, kończę.")
            break
        time.sleep(max(5, 60 - (time.time() - started)))


if __name__ == "__main__":
    main()
