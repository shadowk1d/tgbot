import os
from pathlib import Path
from dotenv import load_dotenv

# Always load .env from the same directory as this file
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

_raw = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", ""))
ADMIN_IDS: list[int] = [int(x) for x in _raw.replace(" ", "").split(",") if x.isdigit()]

ADMIN_CONTACT: str = os.getenv("ADMIN_CONTACT", "@stm_export")

# Service chat for admin notifications (first admin by default)
ADMIN_CHAT_ID: int = ADMIN_IDS[0] if ADMIN_IDS else 0

# Telegram channel where lots are published (e.g. @hot_offer_cars or -100xxxxxxxxx)
CHANNEL_ID: str = os.getenv("CHANNEL_ID", os.getenv("CHANNEL_USERNAME", ""))

RULES_TEXT = (
    "📜 <b>AUCTION RULES & BUYING INSTRUCTIONS</b>\n\n"
    "1️⃣ Each lot is published with a starting price.\n"
    "2️⃣ Participants can place bids by increasing the current offer in the comments or as instructed in the post.\n"
    "3️⃣ Every lot has a limited auction time. A countdown timer will be visible while the auction is active.\n"
    "4️⃣ Once the timer expires, the highest bid wins automatically.\n"
    "5️⃣ The winner must contact us and complete payment within the agreed time.\n"
    "6️⃣ If the winner does not respond or complete payment, the lot may be offered to the next highest bidder or relisted.\n"
    "7️⃣ Please place bids responsibly. Fake bids or non-payment may result in removal from future auctions.\n"
    "8️⃣ By participating in the auction, you agree to these rules.\n\n"
    "Thank you for your trust and good luck with the bidding! 🏆"
)

SUPPORT_TEXT = (
    "🛠 <b>Support / Поддержка</b>\n\n"
    f"Contact admin / Обратитесь к администратору: {ADMIN_CONTACT}"
)


def validate() -> None:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_IDS (or ADMIN_ID)")
    if missing:
        raise RuntimeError("Missing required env vars: " + ", ".join(missing))
