"""
Translations for STM Export Auction Bot.
Usage:
    from lang import t, get_lang
    text = t(user_id, "welcome")
    text = t(user_id, "bid_accepted", amount="£500")
"""
from __future__ import annotations
import db

# ── Translation strings ────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    # ── General ──
    "welcome": {
        "en": "👋 Welcome to <b>STM Export Limited</b> Auction Bot!\n\nUse the menu below.",
        "ru": "👋 Добро пожаловать в аукцион-бот <b>STM Export Limited</b>!\n\nИспользуйте меню ниже.",
    },
    "menu_title": {
        "en": "📋 Menu:",
        "ru": "📋 Меню:",
    },
    "cancelled": {
        "en": "❌ Cancelled.",
        "ru": "❌ Отменено.",
    },
    "language_changed": {
        "en": "🌐 Language set to <b>English</b>.",
        "ru": "🌐 Язык изменён на <b>Русский</b>.",
    },

    # ── Menu buttons ──
    "btn_lots": {
        "en": "🔨 Active Lots",
        "ru": "🔨 Активные лоты",
    },
    "btn_my_bids": {
        "en": "📋 My Bids",
        "ru": "📋 Мои ставки",
    },
    "btn_rules": {
        "en": "📜 Rules",
        "ru": "📜 Правила",
    },
    "btn_support": {
        "en": "🛠 Support",
        "ru": "🛠 Поддержка",
    },
    "btn_language": {
        "en": "🌐 Switch to Russian",
        "ru": "🌐 Switch to English",
    },
    "btn_cancel": {
        "en": "❌ Cancel",
        "ru": "❌ Отмена",
    },
    "btn_skip": {
        "en": "⏭ Skip",
        "ru": "⏭ Пропустить",
    },
    "btn_back": {
        "en": "🔙 Back",
        "ru": "🔙 Назад",
    },
    "btn_back_lots": {
        "en": "🔙 Back to lots",
        "ru": "🔙 К лотам",
    },
    "btn_custom_amount": {
        "en": "✏️ Custom amount",
        "ru": "✏️ Своя сумма",
    },
    "btn_make_bid": {
        "en": "🔨 Make a bid / Сделать ставку",
        "ru": "🔨 Сделать ставку",
    },

    # ── Lot card ──
    "lot_no_bids": {
        "en": "no bids",
        "ru": "нет ставок",
    },
    "lot_not_started": {
        "en": "⚠️ <b>Auction has not started yet.</b>",
        "ru": "⚠️ <b>Аукцион ещё не начался.</b>",
    },
    "lot_current_bid": {
        "en": "💰 Current bid:",
        "ru": "💰 Текущая ставка:",
    },
    "lot_step": {
        "en": "📈 Step:",
        "ru": "📈 Шаг:",
    },
    "lot_ends": {
        "en": "⏱ Ends:",
        "ru": "⏱ Заканчивается:",
    },
    "lot_my_bid": {
        "en": "💼 Your bid:",
        "ru": "💼 Ваша ставка:",
    },
    "lot_not_found": {
        "en": "❌ Lot not found or auction is closed.",
        "ru": "❌ Лот не найден или аукцион закрыт.",
    },
    "lot_no_active": {
        "en": "There are no active lots at the moment.",
        "ru": "На данный момент нет активных лотов.",
    },
    "no_bids_yet": {
        "en": "You have not placed any bids yet.",
        "ru": "Вы ещё не делали ставок.",
    },

    # ── Registration ──
    "reg_required": {
        "en": "📝 <b>Registration required before bidding.</b>\n\nPlease enter your <b>full name</b> (first + last name):",
        "ru": "📝 <b>Для участия в аукционе необходима регистрация.</b>\n\nВведите ваше <b>полное имя</b> (имя и фамилия):",
    },
    "reg_pending": {
        "en": "⏳ <b>Your registration is pending approval.</b>\nYou will be notified once verified.",
        "ru": "⏳ <b>Ваша заявка на рассмотрении.</b>\nВы получите уведомление после проверки.",
    },
    "reg_blocked": {
        "en": "🚫 Your account has been blocked. Contact support.",
        "ru": "🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.",
    },
    "reg_enter_name": {
        "en": "Please enter your full name (min 2 chars):",
        "ru": "Введите полное имя (мин. 2 символа):",
    },
    "reg_enter_company": {
        "en": "🏢 Enter your <b>company name</b>:",
        "ru": "🏢 Введите название <b>компании</b>:",
    },
    "reg_enter_company_err": {
        "en": "Please enter company name:",
        "ru": "Введите название компании:",
    },
    "reg_enter_country": {
        "en": "🌍 Enter your <b>country</b>:",
        "ru": "🌍 Введите вашу <b>страну</b>:",
    },
    "reg_enter_country_err": {
        "en": "Please enter country name:",
        "ru": "Введите название страны:",
    },
    "reg_enter_phone": {
        "en": "📱 Please share your <b>phone number</b>:",
        "ru": "📱 Пожалуйста, поделитесь вашим <b>номером телефона</b>:",
    },
    "reg_phone_btn": {
        "en": "📱 Send my phone",
        "ru": "📱 Отправить мой номер",
    },
    "reg_phone_err": {
        "en": "Please send a valid phone number:",
        "ru": "Введите корректный номер телефона:",
    },
    "reg_submitted": {
        "en": "✅ <b>Registration submitted!</b>\nAn administrator will verify your account shortly.\nYou will receive a notification.",
        "ru": "✅ <b>Заявка отправлена!</b>\nАдминистратор проверит ваш аккаунт в ближайшее время.\nВы получите уведомление.",
    },
    "reg_approved": {
        "en": "✅ <b>Your registration has been approved!</b>\n\nYou can now place bids on lots.",
        "ru": "✅ <b>Ваша регистрация одобрена!</b>\n\nТеперь вы можете делать ставки на лоты.",
    },
    "reg_rejected": {
        "en": "❌ <b>Your registration has been rejected.</b>\nContact support for details.",
        "ru": "❌ <b>Ваша регистрация отклонена.</b>\nСвяжитесь с поддержкой для уточнения.",
    },
    "btn_bid_now": {
        "en": "🔨 Bid now",
        "ru": "🔨 Сделать ставку",
    },

    # ── Bidding ──
    "bid_accepted": {
        "en": "✅ <b>Bid accepted!</b>\nLot: <b>{title}</b>\nYour bid: <b>{amount}</b>\nYou are <b>Bidder #{num}</b>",
        "ru": "✅ <b>Ставка принята!</b>\nЛот: <b>{title}</b>\nВаша ставка: <b>{amount}</b>\nВы <b>Участник #{num}</b>",
    },
    "bid_accepted_short": {
        "en": "✅ Bid {amount} accepted!",
        "ru": "✅ Ставка {amount} принята!",
    },
    "bid_enter_amount": {
        "en": "✏️ Enter your bid amount (GBP):\nCurrent: <b>{current}</b>",
        "ru": "✏️ Введите сумму ставки (GBP):\nТекущая: <b>{current}</b>",
    },
    "bid_invalid": {
        "en": "❌ Invalid amount. Enter a number:",
        "ru": "❌ Неверная сумма. Введите число:",
    },
    "bid_too_low": {
        "en": "❌ Bid must be above {current}.",
        "ru": "❌ Ставка должна быть выше {current}.",
    },
    "bid_not_multiple": {
        "en": "❌ Bid must be a multiple of {step} GBP.",
        "ru": "❌ Ставка должна быть кратна {step} GBP.",
    },
    "bid_outbid": {
        "en": "📣 You have been outbid on lot <b>{title}</b>!\nNew bid: <b>{amount}</b>\nBid now to stay in the lead!",
        "ru": "📣 Вас перебили на лоте <b>{title}</b>!\nНовая ставка: <b>{amount}</b>\nСделайте ставку, чтобы снова стать лидером!",
    },
    "bid_ending_soon": {
        "en": "⏰ <b>Ending soon!</b> Lot <b>{title}</b>\nBid: {amount} | {time} left",
        "ru": "⏰ <b>Скоро завершается!</b> Лот <b>{title}</b>\nСтавка: {amount} | осталось {time}",
    },

    # ── Auction results ──
    "won": {
        "en": "🏆 <b>Congratulations! You won the auction!</b>\n\nLot: <b>{title}</b>\nYour bid: <b>{amount}</b>\n\nThe administrator will contact you shortly.",
        "ru": "🏆 <b>Поздравляем! Вы выиграли аукцион!</b>\n\nЛот: <b>{title}</b>\nВаша ставка: <b>{amount}</b>\n\nАдминистратор свяжется с вами в ближайшее время.",
    },
}


def get_lang(user_id: int) -> str:
    """Return 'en' or 'ru' for a given user."""
    user = db.get_user(user_id)
    if user:
        lang = user.get("language", "ru")
        return lang if lang in ("en", "ru") else "ru"
    return "ru"


def t(user_id: int, key: str, **fmt) -> str:
    """Get translated string for user. Falls back to English if key missing."""
    lang = get_lang(user_id)
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    if fmt:
        try:
            text = text.format(**fmt)
        except Exception:
            pass
    return text
