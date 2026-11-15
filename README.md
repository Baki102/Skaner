# Skaner intraday: 🟢 odbicie widoczne / 🟡 możliwe odbicie

Co minutę (9:30-16:00 czasu nowojorskiego) sprawdza spółki z listy TICKERS w scan.py.
Powiadomienia przychodzą na Telegram.

## Konfiguracja
1. Telegram: @BotFather -> /newbot -> skopiuj TOKEN. Napisz coś do bota, potem otwórz
   https://api.telegram.org/bot<TOKEN>/getUpdates i skopiuj "chat":{"id": ...} (CHAT_ID).
2. GitHub: nowe repozytorium **publiczne** (w prywatnym darmowe minuty szybko się kończą).
   Token jest bezpieczny w Secrets, publiczny jest tylko kod i lista spółek.
3. Wgraj pliki (z folderem .github/workflows).
4. Settings -> Secrets and variables -> Actions: dodaj TELEGRAM_TOKEN i TELEGRAM_CHAT_ID.
5. Actions -> "Skaner intraday" -> Run workflow (test w godzinach sesji USA).

## Uwagi
- GitHub potrafi opóźnić start o kilkanaście minut, więc początek sesji bywa pominięty.
- Dane z Yahoo mogą być opóźnione o ok. 1-2 minuty.
- GitHub wyłącza harmonogram po 60 dniach bez aktywności w repo (kliknij Enable w Actions).
