"""Skaner intraday USA: sam wybiera spółki do 5 USD i szuka odbić (świece 1-min).
🟢 = odbicie już widać
🟡 = możliwe odbicie, niepotwierdzone
To sygnały techniczne, nie rekomendacje."""
import io
import os
import time
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ====== USTAWIENIA ======
MAX_PRICE = 5.0           # tylko spółki do 5 USD
MIN_PRICE = 0.5           # poniżej tej ceny pomijamy (bardzo ryzykowne)
MIN_AVG_VOL = 500_000     # min. średni dzienny wolumen (płynność)
MAX_CANDIDATES = 200      # ile najbardziej płynnych spółek skanować co minutę
MIN_DROP = 0.03           # spadek min. 3% od maksimum z ostatnich 60 minut
RSI_LOW = 35              # RSI(9) zszedł poniżej tej wartości w ostatnich 15 min
COOLDOWN_MIN = 30         # ten sam sygnał dla spółki nie częściej niż co 30 min
FALLBACK = ["SOFI", "F", "SNAP", "NIO", "PLUG"]   # gdy nie uda się pobrać listy spółek
# ========================

ET = ZoneInfo("America/New_York")
sent = {}  # ticker -> (poziom, czas)


def rsi(close, n=9):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down)


def load_universe():
    """Lista akcji z NASDAQ/NYSE/AMEX (bez ETF-ów i testowych)."""
    syms = set()
    sources = [
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
        ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
    ]
    for url, col in sources:
        try:
            txt = requests.get(url, timeout=30).text
            df = pd.read_csv(io.StringIO(txt), sep="|", dtype=str)
            if "ETF" in df:
                df = df[df["ETF"] == "N"]
            if "Test Issue" in df:
                df = df[df["Test Issue"] == "N"]
            s = df[col].astype(str)
            s = s[s.str.fullmatch(r"[A-Z]{1,5}")]
            s = s[~((s.str.len() == 5) & s.str[-1].isin(["W", "U", "R"]))]  # warranty/jednostki
            syms.update(s)
        except Exception as e:
            print("Błąd pobierania listy:", url, e)
    return sorted(syms)


def pick_candidates(universe):
    """Z całej listy wybiera spółki 0.5-5 USD z dużym wolumenem."""
    rows = []
    for i in range(0, len(universe), 300):
        chunk = universe[i:i + 300]
        try:
            d = yf.download(chunk, period="5d", interval="1d", group_by="ticker",
                            threads=True, progress=False, auto_adjust=True)
        except Exception as e:
            print("Błąd paczki:", e)
            continue
        for t in chunk:
            try:
                x = d[t].dropna()
                if len(x) < 3:
                    continue
                price = float(x["Close"].iloc[-1])
                avg_vol = float(x["Volume"].mean())
                if MIN_PRICE <= price <= MAX_PRICE and avg_vol >= MIN_AVG_VOL:
                    rows.append((price * avg_vol, t))
            except Exception:
                continue
    rows.sort(reverse=True)
    return [t for _, t in rows[:MAX_CANDIDATES]]


def fetch_all(tickers):
    out = {}
    try:
        d = yf.download(tickers, period="1d", interval="1m", group_by="ticker",
                        threads=True, progress=False, prepost=False, auto_adjust=True)
    except Exception as e:
        print("Błąd pobierania świec:", e)
        return out
    for t in tickers:
        try:
            x = d[t].dropna(subset=["Close"])
            last = x.index[-1]
            if last.tzinfo is None:
                last = last.tz_localize(ET)
            if (pd.Timestamp.now(tz=ET) - last).total_seconds() < 900:
                out[t] = x
        except Exception:
            pass
    return out


def classify(df):
    """Zwraca (poziom, opis) albo None. poziom: 'green' / 'yellow'."""
    if df is None or len(df) < 25:
        return None
    c, v = df["Close"], df["Volume"]
    price = float(c.iloc[-1])
    if price > MAX_PRICE or price < MIN_PRICE:
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

    if drop > -MIN_DROP or rsi_min > RSI_LOW:
        return None

    info = (f"{price:.2f} USD | od szczytu {drop*100:.1f}% | "
            f"od dołka +{bounce*100:.1f}% | RSI {rsi_now:.0f} | wol. x{vol_ratio:.1f}")

    green = (price > sma10 and c.iloc[-1] > c.iloc[-2] > c.iloc[-3]
             and bounce >= 0.005 and low_pos <= 11 and vol_ratio >= 1.3)
    if green:
        return "green", info
    if low_pos <= 9 and rsi_now > rsi_prev and bounce >= 0.001:
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
    prev = sent.get(t)
    if prev is None:
        return True
    if prev[0] == "yellow" and level == "green":
        return True
    return prev[0] != level or time.time() - prev[1] > COOLDOWN_MIN * 60


def main():
    stop = None
    s = os.getenv("STOP_AT_UTC")
    if s:
        h, m = map(int, s.split(":"))
        stop = dt.datetime.now(dt.timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)

    universe = load_universe()
    print(f"Spółek na liście: {len(universe)}")
    candidates = pick_candidates(universe) if universe else []
    if not candidates:
        candidates = FALLBACK
    print(f"Do skanowania: {len(candidates)} spółek do {MAX_PRICE} USD")

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

        data = fetch_all(candidates)
        empty_rounds = 0 if data else empty_rounds + 1
        hits = []
        for t, df in data.items():
            res = classify(df)
            if res and should_send(t, res[0]):
                hits.append((res[0], t, res[1]))
                sent[t] = (res[0], time.time())
        if hits:
            hits.sort(key=lambda h: h[0] != "green")      # zielone najpierw
            lines = []
            for level, t, info in hits[:10]:
                icon = "🟢 ODBICIE WIDOCZNE" if level == "green" else "🟡 możliwe odbicie"
                lines.append(f"{icon}: {t}\n{info}")
            send("\n\n".join(lines) + "\n\n(sygnał techniczny, nie rekomendacja)")
        if empty_rounds >= 10:
            print("Brak danych, kończę.")
            break
        time.sleep(max(5, 60 - (time.time() - started)))


if __name__ == "__main__":
    main()
