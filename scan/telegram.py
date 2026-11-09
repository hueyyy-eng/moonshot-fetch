"""Send a message to the owner's phone through their Telegram bot.

    python scan/telegram.py --test          # one test message

Needs two Actions secrets, passed in as environment variables:
    TELEGRAM_BOT_TOKEN   the token BotFather gave the owner's bot
    TELEGRAM_CHAT_ID     the owner's Telegram user id
This repo and its run logs are public: the token is never printed, and error messages are cleaned of it before
they reach the log. Returns True when Telegram accepted the message.
"""
from __future__ import annotations

import os
import sys

API = "https://api.telegram.org"


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("telegram: secret TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set - nothing sent")
        return False
    import requests
    try:
        r = requests.post(f"{API}/bot{token}/sendMessage", timeout=20,
                          json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})
        ok = r.status_code == 200 and (r.json() or {}).get("ok") is True
        if not ok:
            # Telegram's own error text ("chat not found", "bot was blocked by the user") is safe to show
            desc = ""
            try:
                desc = str((r.json() or {}).get("description") or "")
            except ValueError:
                pass
            print(f"telegram: not sent (HTTP {r.status_code}) {desc.replace(token, '<hidden>')[:200]}")
        return ok
    except Exception as e:  # noqa: BLE001 - the exception text can contain the URL, so it is cleaned first
        print(f"telegram: not sent ({type(e).__name__}: {str(e).replace(token, '<hidden>')[:160]})")
        return False


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = send("✅ Moonshot Scanner: Telegram is connected. Quick Solana alerts will arrive here "
                  "(score 60+ and on a list; quiet 11pm–7am Singapore time).")
        print("telegram test:", "sent" if ok else "FAILED")
        sys.exit(0 if ok else 1)
    print(__doc__)
