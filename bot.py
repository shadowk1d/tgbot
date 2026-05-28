from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import db
from lang import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

BOT_USERNAME: str = ""   # filled in main()
_channel_edit_ts: dict[int, float] = {}  # lot_id → last edit timestamp (throttle)
_mg_buffer: dict[str, list] = {}          # media_group_id → [(type, file_id)]
_mg_tasks:  dict[str, asyncio.Task] = {}  # media_group_id → flush task

# ── FSM States ─────────────────────────────────────────────────────────────────

class RegisterUser(StatesGroup):
    full_name = State()
    company   = State()
    country   = State()
    phone     = State()


class CreateLot(StatesGroup):
    title         = State()
    reg_number    = State()
    description   = State()
    photos        = State()
    start_price   = State()
    reserve_price = State()
    bid_step      = State()
    starts_at     = State()
    end_time      = State()
    confirm       = State()


class BroadcastState(StatesGroup):
    text = State()


class BidState(StatesGroup):
    custom_amount = State()


# ── Helpers ────────────────────────────────────────────────────────────────────

def p(amount: float) -> str:
    """Format price in GBP."""
    return f"{amount:,.0f} GBP".replace(",", "\u202f")


def fmt_time_left(end_str: str) -> str:
    try:
        end = datetime.fromisoformat(end_str)
    except Exception:
        return "—"
    diff = end - datetime.now()
    if diff.total_seconds() <= 0:
        return "⏹ Ended"
    total = int(diff.total_seconds())
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def _channel_post(lot: dict, bidder_num: int | None = None, deep_link: str = None) -> str:
    title   = lot["title"]
    reg     = lot.get("reg_number") or "—"
    desc    = lot.get("description") or ""
    price   = lot["current_price"]
    step    = lot["bid_step"]
    end     = lot["end_time"]

    leader_str = f"👤 Leader: <b>Bidder #{bidder_num}</b>" if bidder_num else "👤 No bids yet"
    link_line  = f'\n\n🔨 <a href="{deep_link}"><b>Make a bid</b></a>' if deep_link else ""

    return (
        f"🚗 <b>{title}</b>\n"
        f"🔢 Reg: {reg}\n\n"
        f"{desc}\n\n"
        f"💰 Current bid: <b>{p(price)}</b>\n"
        f"{leader_str}\n"
        f"📈 Step: {p(step)}\n"
        f"⏱ Time left: {fmt_time_left(end)}"
        f"{link_line}"
    )


def _channel_post_closed(lot: dict) -> str:
    title  = lot["title"]
    reg    = lot.get("reg_number") or "—"
    desc   = lot.get("description") or ""
    if lot["status"] == "ended" and lot.get("winner_price"):
        status_line = f"🟢 <b>SOLD ✅ — Winning bid: {p(lot['winner_price'])}</b>"
    else:
        status_line = "🔴 <b>Lot closed — Not sold</b>"
    return (
        f"🚗 <b>{title}</b>\n"
        f"🔢 Reg: {reg}\n\n"
        f"{desc}\n\n"
        f"{status_line}"
    )


def _lot_card(lot: dict, bidder_num: int | None = None, my_bid: float = None) -> str:
    title   = lot["title"]
    reg     = lot.get("reg_number") or "—"
    desc    = lot.get("description") or ""
    price   = lot["current_price"]
    step    = lot["bid_step"]
    end     = lot["end_time"]
    starts  = lot.get("starts_at")

    started = True
    if starts:
        try:
            started = datetime.fromisoformat(starts) <= datetime.now()
        except Exception:
            pass

    leader_str = f"Bidder #{bidder_num}" if bidder_num else "no bids"
    my_str = f"\n💼 Your bid: <b>{p(my_bid)}</b>" if my_bid else ""
    started_str = "" if started else "\n⚠️ <b>Auction has not started yet.</b>"

    return (
        f"🚗 <b>{title}</b> | {reg}\n\n"
        f"{desc}\n\n"
        f"💰 Current bid: <b>{p(price)}</b> ({leader_str})\n"
        f"📈 Step: {p(step)}\n"
        f"⏱ Ends: {fmt_time_left(end)}"
        f"{my_str}"
        f"{started_str}"
    )


def _is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


# ── Keyboards ──────────────────────────────────────────────────────────────────

def _with_main_menu(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Append a global menu shortcut to inline keyboards."""
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu")]])

def kb_main(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t(uid, "btn_lots"),    callback_data="lots:0")],
        [InlineKeyboardButton(text=t(uid, "btn_my_bids"), callback_data="my_bids")],
        [InlineKeyboardButton(text=t(uid, "btn_rules"),   callback_data="rules"),
         InlineKeyboardButton(text=t(uid, "btn_support"), callback_data="support")],
        [InlineKeyboardButton(text=t(uid, "btn_language"), callback_data="lang_toggle")],
    ]
    if _is_admin(uid):
        rows.append([InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Lot",       callback_data="admin_add_lot")],
        [InlineKeyboardButton(text="📂 Manage Lots",   callback_data="admin_lots:0")],
        [InlineKeyboardButton(text="📋 Pending Users", callback_data="admin_pending")],
        [InlineKeyboardButton(text="👥 All Users",     callback_data="admin_users:0")],
        [InlineKeyboardButton(text="📜 History",       callback_data="admin_history:0")],
        [InlineKeyboardButton(text="📢 Broadcast",     callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Back",          callback_data="menu")],
    ])


def kb_cancel() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ])


def kb_photos_done() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="✅ Done / Готово", callback_data="photos_done")],
        [InlineKeyboardButton(text="❌ Cancel",       callback_data="cancel")],
    ])


def kb_skip() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="⏭ Skip / Пропустить", callback_data="skip")],
        [InlineKeyboardButton(text="❌ Cancel",           callback_data="cancel")],
    ])


def kb_starts_at() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="🟢 Сейчас",      callback_data="cl_start:now")],
        [InlineKeyboardButton(text="🕐 Через час",   callback_data="cl_start:1h")],
        [InlineKeyboardButton(text="🕑 Через 2 часа", callback_data="cl_start:2h")],
        [InlineKeyboardButton(text="❌ Отмена",       callback_data="cancel")],
    ])


def kb_end_time() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="🕔 Через 5 минут", callback_data="cl_end:5m")],
        [InlineKeyboardButton(text="🕐 Через час",    callback_data="cl_end:1h")],
        [InlineKeyboardButton(text="🕑 Через 2 часа", callback_data="cl_end:2h")],
        [InlineKeyboardButton(text="🕒 Через 3 часа", callback_data="cl_end:3h")],
        [InlineKeyboardButton(text="❌ Отмена",        callback_data="cancel")],
    ])


def kb_confirm_lot() -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="✅ Publish to Channel", callback_data="lot_confirm_yes")],
        [InlineKeyboardButton(text="❌ Cancel",             callback_data="cancel")],
    ])


def kb_bid(lot_id: int, step: float, uid: int = 0) -> InlineKeyboardMarkup:
    s = int(step)
    return _with_main_menu([
        [
            InlineKeyboardButton(text=f"+{p(s*1)}", callback_data=f"bid_q:{lot_id}:1"),
            InlineKeyboardButton(text=f"+{p(s*5)}", callback_data=f"bid_q:{lot_id}:5"),
        ],
        [
            InlineKeyboardButton(text=f"+{p(s*10)}", callback_data=f"bid_q:{lot_id}:10"),
            InlineKeyboardButton(text=f"+{p(s*50)}", callback_data=f"bid_q:{lot_id}:50"),
        ],
        [InlineKeyboardButton(text=t(uid, "btn_custom_amount") if uid else "✏️ Custom amount", callback_data=f"bid_custom:{lot_id}")],
        [InlineKeyboardButton(text=t(uid, "btn_back_lots") if uid else "🔙 Back to lots",  callback_data="lots:0")],
    ])


def kb_admin_lot(lot_id: int) -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="🗑 Delete",    callback_data=f"admin_del:{lot_id}"),
         InlineKeyboardButton(text="⏹ End Early", callback_data=f"admin_end:{lot_id}")],
        [InlineKeyboardButton(text="🔙 Back",     callback_data="admin_lots:0")],
    ])


def kb_verify_user(tg_id: int) -> InlineKeyboardMarkup:
    return _with_main_menu([
        [InlineKeyboardButton(text="✅ Verify",   callback_data=f"uverify:{tg_id}"),
         InlineKeyboardButton(text="❌ Reject",   callback_data=f"ureject:{tg_id}")],
    ])


def kb_contact(uid: int = 0) -> ReplyKeyboardMarkup:
    label = t(uid, "reg_phone_btn") if uid else "📱 Send my phone"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=label, request_contact=True)],
            [KeyboardButton(text="🏠 Main Menu")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def kb_reply_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Main Menu")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _nav(page: int, total: int, prefix: str) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{page-1}"))
    row.append(InlineKeyboardButton(text=f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{page+1}"))
    return row


# ── Channel helpers ────────────────────────────────────────────────────────────

async def publish_lot_to_channel(bot: Bot, lot: dict) -> None:
    """Post lot to channel and save message_id."""
    if not config.CHANNEL_ID:
        return
    deep_link = f"https://t.me/{BOT_USERNAME}?start=lot_{lot['id']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔨 Make a bid", url=deep_link)]
    ])
    photos = lot.get("photos") or []
    videos = lot.get("videos") or []
    total_media = len(photos) + len(videos)
    try:
        if total_media > 1:
            # Send all as media group; embed bid link in caption
            text = _channel_post(lot, deep_link=deep_link)
            media_items = []
            for i, fid in enumerate(photos):
                cap = text if i == 0 else None
                media_items.append(InputMediaPhoto(
                    media=fid,
                    caption=cap,
                    parse_mode=ParseMode.HTML if cap else None,
                ))
            for i, fid in enumerate(videos):
                cap = text if (i == 0 and not photos) else None
                media_items.append(InputMediaVideo(
                    media=fid,
                    caption=cap,
                    parse_mode=ParseMode.HTML if cap else None,
                ))
            msgs = await bot.send_media_group(config.CHANNEL_ID, media_items)
            db.set_lot_channel_message(lot["id"], msgs[0].message_id)
        elif videos:
            text = _channel_post(lot)
            msg = await bot.send_video(
                config.CHANNEL_ID, videos[0],
                caption=text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            db.set_lot_channel_message(lot["id"], msg.message_id)
        elif photos:
            text = _channel_post(lot)
            msg = await bot.send_photo(
                config.CHANNEL_ID, photos[0],
                caption=text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            db.set_lot_channel_message(lot["id"], msg.message_id)
        else:
            text = _channel_post(lot)
            msg = await bot.send_message(
                config.CHANNEL_ID, text,
                parse_mode=ParseMode.HTML, reply_markup=kb
            )
            db.set_lot_channel_message(lot["id"], msg.message_id)
    except Exception as e:
        logger.warning("publish_lot_to_channel error: %s", e)


async def edit_channel_post(bot: Bot, lot: dict) -> None:
    """Edit existing channel post to reflect latest bid / countdown."""
    if not config.CHANNEL_ID or not lot.get("channel_message_id"):
        return
    # Throttle: no more than 1 edit per 5 seconds per lot (Telegram rate limit)
    import time
    now = time.monotonic()
    lot_id = lot["id"]
    last_edit = _channel_edit_ts.get(lot_id, 0)
    if now - last_edit < 5:
        return
    _channel_edit_ts[lot_id] = now
    last = db.get_last_bid(lot["id"])
    bidder_num = last["bidder_num"] if last else None
    deep_link = f"https://t.me/{BOT_USERNAME}?start=lot_{lot['id']}"
    photos = lot.get("photos") or []
    videos = lot.get("videos") or []
    multi = (len(photos) + len(videos)) > 1
    kb = None if multi else InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔨 Make a bid", url=deep_link)]
    ])
    text = _channel_post(lot, bidder_num, deep_link=deep_link if multi else None)
    try:
        if photos or videos:
            await bot.edit_message_caption(
                chat_id=config.CHANNEL_ID,
                message_id=lot["channel_message_id"],
                caption=text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        else:
            await bot.edit_message_text(
                text=text,
                chat_id=config.CHANNEL_ID,
                message_id=lot["channel_message_id"],
                parse_mode=ParseMode.HTML, reply_markup=kb
            )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_channel_post error: %s", e)


# ── Lot finalization ───────────────────────────────────────────────────────────

async def finalize_lot(bot: Bot, lot_id: int) -> None:
    lot = db.get_lot(lot_id)
    if not lot or lot["status"] != "active":
        return

    last = db.get_last_bid(lot_id)
    reserve = lot.get("reserve_price") or 0

    if last and last["amount"] >= reserve:
        winner_id    = last["user_id"]
        winner_price = last["amount"]
        db.end_lot(lot_id, winner_id, winner_price, "ended")
        lot = db.get_lot(lot_id)

        # Notify winner
        bidder_num = last.get("bidder_num") or db.get_bidder_num(lot_id, winner_id)
        try:
            await bot.send_message(
                winner_id,
                t(winner_id, "won", title=lot["title"], amount=p(winner_price)),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        # Notify admins
        for aid in config.ADMIN_IDS:
            try:
                winner_user = db.get_user(winner_id)
                uname = (winner_user or {}).get("username", "")
                full  = (winner_user or {}).get("full_name", "")
                reg   = lot.get("reg_number") or "—"
                await bot.send_message(
                    aid,
                    f"✅ Lot <b>{lot['title']}</b> sold!\n"
                    f"Reg: <b>{reg}</b>\n"
                    f"Bidder #{bidder_num} | {full} | @{uname} | <code>{winner_id}</code>\n"
                    f"Price: <b>{p(winner_price)}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
    else:
        # Reserve not met → unsold
        db.end_lot(lot_id, None, 0, "unsold")
        lot = db.get_lot(lot_id)
        for aid in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    aid,
                    f"❌ Lot <b>{lot['title']}</b> ended UNSOLD "
                    f"(reserve {p(reserve)} not met, "
                    f"top bid {p(last['amount']) if last else 'none'}).",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    # Update channel post to show closed state
    lot = db.get_lot(lot_id)
    if lot and lot.get("channel_message_id"):
        try:
            text = _channel_post_closed(lot)
            photos = lot.get("photos") or []
            videos = lot.get("videos") or []
            if photos or videos:
                await bot.edit_message_caption(
                    chat_id=config.CHANNEL_ID,
                    message_id=lot["channel_message_id"],
                    caption=text, parse_mode=ParseMode.HTML,
                )
            else:
                await bot.edit_message_text(
                    text=text,
                    chat_id=config.CHANNEL_ID,
                    message_id=lot["channel_message_id"],
                    parse_mode=ParseMode.HTML,
                )
        except Exception as e:
            logger.warning("finalize channel edit: %s", e)


# ── Router ─────────────────────────────────────────────────────────────────────

router = Router()


# ── /start ─────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    uid  = msg.from_user.id
    uname = msg.from_user.username or ""
    db.ensure_user(uid, uname)
    user = db.get_user(uid)

    # Deep-link: /start lot_42
    args = msg.text.split(maxsplit=1)[1] if " " in (msg.text or "") else ""
    if args.startswith("lot_"):
        try:
            lot_id = int(args[4:])
        except ValueError:
            lot_id = None
        if lot_id:
            await _open_lot_for_bidding(msg, state, lot_id, user)
            return

    await msg.answer(
        t(uid, "welcome"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(uid),
    )
    await msg.answer("Quick actions:", reply_markup=kb_reply_main_menu())


async def _open_lot_for_bidding(msg: Message, state: FSMContext,
                                 lot_id: int, user: dict | None) -> None:
    lot = db.get_lot(lot_id)
    if not lot or lot["status"] != "active":
        await msg.answer("❌ Lot not found or auction is closed.")
        return

    uid = msg.from_user.id
    status = (user or {}).get("status", "new")

    if status == "new":
        # Start registration flow
        await state.set_state(RegisterUser.full_name)
        await state.update_data(pending_lot=lot_id)
        await msg.answer(
            t(uid, "reg_required"),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_cancel(),
        )
        return

    if status == "pending":
        await msg.answer(
            t(uid, "reg_pending"),
            parse_mode=ParseMode.HTML,
        )
        return

    if status == "blocked":
        await msg.answer("⛔ Your account has been blocked. Contact support.")
        return

    # Verified — show lot card
    starts = lot.get("starts_at")
    if starts:
        try:
            if datetime.fromisoformat(starts) > datetime.now():
                await msg.answer(
                    f"⏳ <b>Auction for {lot['title']} has not started yet.</b>\n"
                    f"Starts: {starts}",
                    parse_mode=ParseMode.HTML,
                )
                return
        except Exception:
            pass

    last = db.get_last_bid(lot_id)
    bidder_num = last["bidder_num"] if last else None
    my_bids = db.get_user_bids_on_active_lots(uid)
    my_bid  = next((b["my_best_bid"] for b in my_bids if b["id"] == lot_id), None)

    await msg.answer(
        _lot_card(lot, bidder_num, my_bid),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_bid(lot_id, lot["bid_step"], uid),
    )


# ── Registration FSM ───────────────────────────────────────────────────────────

@router.message(RegisterUser.full_name)
async def reg_full_name(msg: Message, state: FSMContext) -> None:
    uid  = msg.from_user.id
    name = msg.text.strip() if msg.text else ""
    if len(name) < 2:
        await msg.answer(t(uid, "reg_enter_name"), reply_markup=kb_cancel())
        return
    await state.update_data(full_name=name)
    await state.set_state(RegisterUser.company)
    await msg.answer(t(uid, "reg_enter_company"), parse_mode=ParseMode.HTML,
                     reply_markup=kb_cancel())


@router.message(RegisterUser.company)
async def reg_company(msg: Message, state: FSMContext) -> None:
    uid     = msg.from_user.id
    company = msg.text.strip() if msg.text else ""
    if len(company) < 1:
        await msg.answer(t(uid, "reg_enter_company_err"), reply_markup=kb_cancel())
        return
    await state.update_data(company=company)
    await state.set_state(RegisterUser.country)
    await msg.answer(t(uid, "reg_enter_country"), parse_mode=ParseMode.HTML,
                     reply_markup=kb_cancel())


@router.message(RegisterUser.country)
async def reg_country(msg: Message, state: FSMContext) -> None:
    uid     = msg.from_user.id
    country = msg.text.strip() if msg.text else ""
    if len(country) < 2:
        await msg.answer(t(uid, "reg_enter_country_err"), reply_markup=kb_cancel())
        return
    await state.update_data(country=country)
    await state.set_state(RegisterUser.phone)
    await msg.answer(
        t(uid, "reg_enter_phone"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_contact(uid),
    )


@router.message(RegisterUser.phone, F.contact)
async def reg_phone_contact(msg: Message, state: FSMContext) -> None:
    phone = msg.contact.phone_number
    await _finish_registration(msg, state, phone)


@router.message(RegisterUser.phone, F.text)
async def reg_phone_text(msg: Message, state: FSMContext) -> None:
    uid   = msg.from_user.id
    phone = (msg.text or "").strip()
    if len(phone) < 7:
        await msg.answer(t(uid, "reg_phone_err"), reply_markup=kb_contact(uid))
        return
    await _finish_registration(msg, state, phone)


async def _finish_registration(msg: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    full_name   = data.get("full_name", "")
    company     = data.get("company", "")
    country     = data.get("country", "")
    pending_lot = data.get("pending_lot")
    uid   = msg.from_user.id
    uname = msg.from_user.username or ""

    db.register_user(uid, full_name, company, country, phone, pending_lot)
    await state.clear()

    await msg.answer(
        t(uid, "reg_submitted"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_reply_main_menu(),
    )

    # Notify admins
    lot_info = f" (wanted to bid on lot #{pending_lot})" if pending_lot else ""
    for aid in config.ADMIN_IDS:
        try:
            await msg.bot.send_message(
                aid,
                f"👤 <b>New registration request</b>{lot_info}\n\n"
                f"Name: {full_name}\n"
                f"Company: {company}\n"
                f"Country: {country}\n"
                f"Phone: {phone}\n"
                f"Username: @{uname}\n"
                f"TG ID: <code>{uid}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_verify_user(uid),
            )
        except Exception:
            pass


# ── Admin user verification ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("uverify:"))
async def cb_verify_user(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        await cq.answer("Not authorized", show_alert=True)
        return
    tg_id = int(cq.data.split(":")[1])
    db.set_user_status(tg_id, "verified")
    user = db.get_user(tg_id)
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("✅ User verified")
    # Notify user
    try:
        pending_lot = (user or {}).get("pending_lot")
        text = t(tg_id, "reg_approved")
        markup = None
        if pending_lot:
            lot = db.get_lot(pending_lot)
            if lot and lot["status"] == "active":
                deep = f"https://t.me/{BOT_USERNAME}?start=lot_{pending_lot}"
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"🔨 Bid on {lot['title']}", url=deep)]
                ])
        await cq.bot.send_message(tg_id, text, parse_mode=ParseMode.HTML,
                                  reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith("ureject:"))
async def cb_reject_user(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        await cq.answer("Not authorized", show_alert=True)
        return
    tg_id = int(cq.data.split(":")[1])
    db.set_user_status(tg_id, "blocked")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("❌ User rejected")
    try:
        await cq.bot.send_message(
            tg_id,
            t(tg_id, "reg_rejected"),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ── Pending users list ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_pending")
async def cb_admin_pending(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    users = db.get_pending_users()
    if not users:
        await cq.message.edit_text("✅ No pending registration requests.",
                                   reply_markup=kb_admin())
        return
    await cq.message.edit_text(
        f"📋 <b>Pending verifications: {len(users)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin(),
    )
    for u in users:
        uname = u.get("username") or "—"
        await cq.message.answer(
            f"👤 <b>{u.get('full_name','—')}</b>\n"
            f"Company: {u.get('company','—')}\n"
            f"Country: {u.get('country','—')}\n"
            f"Phone: {u.get('phone','—')}\n"
            f"@{uname} | <code>{u['tg_id']}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_verify_user(u["tg_id"]),
        )
    await cq.answer()


# ── /menu ──────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    db.ensure_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(t(msg.from_user.id, "menu_title"), reply_markup=kb_main(msg.from_user.id))
    await msg.answer("Quick actions:", reply_markup=kb_reply_main_menu())


@router.message(F.text == "🏠 Main Menu")
async def cmd_menu_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    db.ensure_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(t(msg.from_user.id, "menu_title"), reply_markup=kb_main(msg.from_user.id))
    await msg.answer("Quick actions:", reply_markup=kb_reply_main_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = cq.from_user.id
    text = t(uid, "menu_title")
    try:
        await cq.message.edit_text(text, reply_markup=kb_main(uid))
    except Exception:
        await cq.message.answer(text, reply_markup=kb_main(uid))
    await cq.message.answer("Quick actions:", reply_markup=kb_reply_main_menu())
    await cq.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = cq.from_user.id
    text = t(uid, "cancelled")
    try:
        await cq.message.edit_text(text, reply_markup=kb_main(uid))
    except Exception:
        await cq.message.answer(text, reply_markup=kb_main(uid))
    await cq.message.answer("Quick actions:", reply_markup=kb_reply_main_menu())
    await cq.answer()


# ── Language toggle ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "lang_toggle")
async def cb_lang_toggle(cq: CallbackQuery) -> None:
    uid = cq.from_user.id
    current = db.get_user(uid)
    cur_lang = (current or {}).get("language", "ru") if current else "ru"
    new_lang = "en" if cur_lang == "ru" else "ru"
    db.set_user_language(uid, new_lang)
    await cq.message.edit_text(
        t(uid, "language_changed"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(uid),
    )
    await cq.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(cq: CallbackQuery) -> None:
    await cq.answer()


# ── Rules / Support ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "rules")
async def cb_rules(cq: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="menu")]
    ])
    try:
        await cq.message.edit_text(config.RULES_TEXT, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass
    await cq.answer()


@router.callback_query(F.data == "support")
async def cb_support(cq: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="menu")]
    ])
    await cq.message.edit_text(config.SUPPORT_TEXT, parse_mode=ParseMode.HTML, reply_markup=kb)
    await cq.answer()


# ── Active lots list ───────────────────────────────────────────────────────────

PAGE_SIZE = 3


@router.callback_query(F.data.startswith("lots:"))
async def cb_lots(cq: CallbackQuery) -> None:
    page = int(cq.data.split(":")[1])
    lots = db.get_active_lots()
    if not lots:
        await cq.message.edit_text(t(cq.from_user.id, "lot_no_active"),
                                   reply_markup=kb_main(cq.from_user.id))
        await cq.answer()
        return

    total_pages = max(1, (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = lots[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    for lot in chunk:
        last = db.get_last_bid(lot["id"])
        bn   = last["bidder_num"] if last else None
        label = (
            f"🚗 {lot['title']} | {p(lot['current_price'])} "
            f"{'(Bidder #'+str(bn)+')' if bn else '(no bids)'} "
            f"| {fmt_time_left(lot['end_time'])}"
        )
        rows.append([InlineKeyboardButton(text=label[:64],
                                          callback_data=f"lot_view:{lot['id']}")])

    nav = _nav(page, total_pages, "lots")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="menu")])

    await cq.message.edit_text(
        f"🏷 <b>Active Lots</b> (page {page+1}/{total_pages}):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("lot_view:"))
async def cb_lot_view(cq: CallbackQuery, state: FSMContext) -> None:
    lot_id = int(cq.data.split(":")[1])
    lot = db.get_lot(lot_id)
    if not lot or lot["status"] != "active":
        await cq.answer("Lot not found or closed.", show_alert=True)
        return

    uid = cq.from_user.id
    user = db.get_user(uid)
    status = (user or {}).get("status", "new")

    if status not in ("verified",):
        await cq.answer("You must be a verified member to bid.", show_alert=True)
        return

    last = db.get_last_bid(lot_id)
    bidder_num = last["bidder_num"] if last else None
    my_bids = db.get_user_bids_on_active_lots(uid)
    my_bid  = next((b["my_best_bid"] for b in my_bids if b["id"] == lot_id), None)

    await cq.message.edit_text(
        _lot_card(lot, bidder_num, my_bid),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_bid(lot_id, lot["bid_step"], uid),
    )
    await cq.answer()


# ── My bids ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_bids")
async def cb_my_bids(cq: CallbackQuery) -> None:
    uid = cq.from_user.id
    user = db.get_user(uid)
    if not user or user.get("status") not in ("verified",):
        await cq.answer("You must be verified to view bids.", show_alert=True)
        return
    rows_data = db.get_user_bids_on_active_lots(uid)
    if not rows_data:
        await cq.message.edit_text(t(uid, "no_bids_yet"),
                                   reply_markup=kb_main(uid))
        await cq.answer()
        return

    lines = ["📋 <b>Your active bids:</b>\n"]
    for b in rows_data:
        is_leading = b["leader_user_id"] == uid
        icon = "🟢" if is_leading else "🔴"
        reg = f" | {b['reg_number']}" if b.get("reg_number") else ""
        lines.append(
            f"{icon} <b>{b['title']}</b>{reg}\n"
            f"  Your bid: {p(b['my_best_bid'])} | "
            f"Current: {p(b['current_price'])} | "
            f"Ends: {fmt_time_left(b['end_time'])}"
        )
    await cq.message.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                               reply_markup=kb_main(uid))
    await cq.answer()


# ── Bidding ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bid_q:"))
async def cb_bid_quick(cq: CallbackQuery) -> None:
    _, lot_id_s, mult_s = cq.data.split(":")
    lot_id = int(lot_id_s)
    mult   = int(mult_s)

    uid = cq.from_user.id
    user = db.get_user(uid)
    if not user or user.get("status") != "verified":
        await cq.answer("You must be verified to bid.", show_alert=True)
        return

    lot = db.get_lot(lot_id)
    if not lot or lot["status"] != "active":
        await cq.answer("Auction is closed.", show_alert=True)
        return

    step   = lot["bid_step"]
    amount = lot["current_price"] + step * mult

    await _do_bid(cq, lot, uid, amount)


@router.callback_query(F.data.startswith("bid_custom:"))
async def cb_bid_custom(cq: CallbackQuery, state: FSMContext) -> None:
    lot_id = int(cq.data.split(":")[1])
    uid = cq.from_user.id
    user = db.get_user(uid)
    if not user or user.get("status") != "verified":
        await cq.answer("You must be verified to bid.", show_alert=True)
        return

    lot = db.get_lot(lot_id)
    if not lot or lot["status"] != "active":
        await cq.answer("Auction is closed.", show_alert=True)
        return

    await state.set_state(BidState.custom_amount)
    await state.update_data(lot_id=lot_id)
    await cq.message.answer(
        f"✏️ Enter your bid amount in GBP.\n"
        f"Current: <b>{p(lot['current_price'])}</b> | Step: <b>{p(lot['bid_step'])}</b>\n"
        f"Must be a multiple of {p(lot['bid_step'])} and above current price.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cancel(),
    )
    await cq.answer()


@router.message(BidState.custom_amount)
async def handle_custom_bid(msg: Message, state: FSMContext) -> None:
    uid = msg.from_user.id
    try:
        amount = float(msg.text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        await msg.answer(t(uid, "bid_invalid"), reply_markup=kb_cancel())
        return

    data   = await state.get_data()
    lot_id = data.get("lot_id")
    lot    = db.get_lot(lot_id)

    if not lot or lot["status"] != "active":
        await state.clear()
        await msg.answer(t(uid, "lot_not_found"))
        return

    await state.clear()

    await _place_bid(msg.bot, lot, uid, amount, reply_msg=msg)


async def _do_bid(cq: CallbackQuery, lot: dict, uid: int, amount: float) -> None:
    await _place_bid(cq.bot, lot, uid, amount, cq=cq)


async def _place_bid(bot: Bot, lot: dict, uid: int, amount: float,
                     cq: CallbackQuery = None, reply_msg: Message = None) -> None:
    lot_id = lot["id"]

    # Anti-sniping: if < 2 min remaining → extend by 2 min
    try:
        end_dt = datetime.fromisoformat(lot["end_time"])
        now_dt = datetime.now()
        if (end_dt - now_dt).total_seconds() < 120:
            new_end = (end_dt + timedelta(minutes=2)).isoformat(timespec="seconds")
            db.update_lot_end_time(lot_id, new_end)
    except Exception:
        pass

    bid_result = db.place_bid(lot_id, uid, amount)
    if not bid_result.get("ok"):
        reason = bid_result.get("reason")
        if reason == "too_low":
            current = p(float(bid_result.get("current_price", lot.get("current_price", 0))))
            msg_text = t(uid, "bid_too_low", current=current)
            alert_text = f"❌ Bid must be above {current}"
        elif reason == "not_multiple":
            step = float(bid_result.get("step", lot.get("bid_step", 0)))
            msg_text = t(uid, "bid_not_multiple", step=int(step))
            alert_text = f"❌ Bid must be a multiple of {p(step)}"
        else:
            msg_text = t(uid, "lot_not_found")
            alert_text = "❌ Auction is closed"

        if cq:
            await cq.answer(alert_text, show_alert=True)
        elif reply_msg:
            await reply_msg.answer(msg_text, parse_mode=ParseMode.HTML, reply_markup=kb_cancel())
        return

    bidder_num = bid_result["bidder_num"]
    prev_leader = bid_result.get("prev_leader")
    lot = db.get_lot(lot_id)  # reload

    if cq:
        await cq.message.edit_text(
            _lot_card(lot, bidder_num, amount),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_bid(lot_id, lot["bid_step"], uid),
        )
        await cq.answer(t(uid, "bid_accepted_short", amount=p(amount)))
    elif reply_msg:
        bid_text = t(uid, "bid_accepted", title=lot["title"], amount=p(amount), num=bidder_num)
        await reply_msg.answer(bid_text, parse_mode=ParseMode.HTML,
                               reply_markup=kb_bid(lot_id, lot["bid_step"], uid))

    # Update channel post
    await edit_channel_post(bot, lot)

    # Notify previous leader
    if prev_leader and prev_leader != uid:
        try:
            await bot.send_message(
                prev_leader,
                t(prev_leader, "bid_outbid", title=lot["title"], amount=p(amount)),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🔨 Bid again",
                        url=f"https://t.me/{BOT_USERNAME}?start=lot_{lot_id}",
                    )]
                ]),
            )
        except Exception:
            pass


# ── Admin panel ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin")
async def cb_admin(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        await cq.answer("Not authorized", show_alert=True)
        return
    await cq.message.edit_text("⚙️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML,
                               reply_markup=kb_admin())
    await cq.answer()


# ── Create lot FSM ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_add_lot")
async def cb_admin_add_lot(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    await state.set_state(CreateLot.title)
    await cq.message.edit_text("🚗 Enter lot <b>title</b> (car name/model):",
                               parse_mode=ParseMode.HTML, reply_markup=kb_cancel())
    await cq.answer()


@router.message(CreateLot.title)
async def cl_title(msg: Message, state: FSMContext) -> None:
    v = (msg.text or "").strip()
    if not v:
        return await msg.answer("Title cannot be empty:", reply_markup=kb_cancel())
    await state.update_data(title=v)
    await state.set_state(CreateLot.reg_number)
    await msg.answer("🔢 Enter <b>registration number</b>:", parse_mode=ParseMode.HTML,
                     reply_markup=kb_skip())


@router.callback_query(CreateLot.reg_number, F.data == "skip")
async def cl_reg_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(reg_number="")
    await state.set_state(CreateLot.description)
    await cq.message.edit_text("📝 Enter <b>description</b>:", parse_mode=ParseMode.HTML,
                               reply_markup=kb_cancel())
    await cq.answer()


@router.message(CreateLot.reg_number)
async def cl_reg_number(msg: Message, state: FSMContext) -> None:
    await state.update_data(reg_number=(msg.text or "").strip())
    await state.set_state(CreateLot.description)
    await msg.answer("📝 Enter <b>description</b>:", parse_mode=ParseMode.HTML,
                     reply_markup=kb_cancel())


@router.message(CreateLot.description)
async def cl_description(msg: Message, state: FSMContext) -> None:
    v = (msg.text or "").strip()
    if not v:
        return await msg.answer("Description cannot be empty:", reply_markup=kb_cancel())
    await state.update_data(description=v)
    await state.set_state(CreateLot.photos)
    await msg.answer(
        "📸 Send <b>photos or videos</b> (up to 10 total), then press Done:",
        parse_mode=ParseMode.HTML, reply_markup=kb_photos_done(),
    )


@router.message(CreateLot.photos, F.photo)
async def cl_photo(msg: Message, state: FSMContext) -> None:
    file_id = msg.photo[-1].file_id
    mg_id   = msg.media_group_id
    if mg_id:
        if mg_id not in _mg_buffer:
            _mg_buffer[mg_id] = []
        _mg_buffer[mg_id].append(("photo", file_id))
        if mg_id in _mg_tasks:
            _mg_tasks[mg_id].cancel()

        async def _flush(mid: str, st: FSMContext, m: Message) -> None:
            await asyncio.sleep(0.6)
            items = _mg_buffer.pop(mid, [])
            _mg_tasks.pop(mid, None)
            data = await st.get_data()
            photos = data.get("photos", [])
            videos = data.get("videos", [])
            added = 0
            for kind, fid in items:
                if len(photos) + len(videos) < 10:
                    (photos if kind == "photo" else videos).append(fid)
                    added += 1
            await st.update_data(photos=photos, videos=videos)
            total = len(photos) + len(videos)
            await m.answer(f"✅ {added} photo(s) added. Total: {total}/10. Send more or press Done.",
                           reply_markup=kb_photos_done())

        _mg_tasks[mg_id] = asyncio.create_task(_flush(mg_id, state, msg))
    else:
        data = await state.get_data()
        photos = data.get("photos", [])
        videos = data.get("videos", [])
        if len(photos) + len(videos) < 10:
            photos.append(file_id)
            await state.update_data(photos=photos)
            await msg.answer(f"✅ Photo added. Total: {len(photos)}/10. Send more or press Done.",
                             reply_markup=kb_photos_done())


@router.message(CreateLot.photos, F.video)
async def cl_video(msg: Message, state: FSMContext) -> None:
    file_id = msg.video.file_id
    mg_id   = msg.media_group_id
    if mg_id:
        if mg_id not in _mg_buffer:
            _mg_buffer[mg_id] = []
        _mg_buffer[mg_id].append(("video", file_id))
        if mg_id in _mg_tasks:
            _mg_tasks[mg_id].cancel()

        async def _flush_v(mid: str, st: FSMContext, m: Message) -> None:
            await asyncio.sleep(0.6)
            items = _mg_buffer.pop(mid, [])
            _mg_tasks.pop(mid, None)
            data = await st.get_data()
            photos = data.get("photos", [])
            videos = data.get("videos", [])
            added = 0
            for kind, fid in items:
                if len(photos) + len(videos) < 10:
                    (photos if kind == "photo" else videos).append(fid)
                    added += 1
            await st.update_data(photos=photos, videos=videos)
            total = len(photos) + len(videos)
            await m.answer(f"✅ {added} video(s) added. Total: {total}/10. Send more or press Done.",
                           reply_markup=kb_photos_done())

        _mg_tasks[mg_id] = asyncio.create_task(_flush_v(mg_id, state, msg))
    else:
        data = await state.get_data()
        photos = data.get("photos", [])
        videos = data.get("videos", [])
        if len(photos) + len(videos) < 10:
            videos.append(file_id)
            await state.update_data(videos=videos)
            await msg.answer(f"✅ Video added. Total: {len(photos) + len(videos)}/10. Send more or press Done.",
                             reply_markup=kb_photos_done())


@router.callback_query(CreateLot.photos, F.data == "photos_done")
async def cb_photos_done(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateLot.start_price)
    await cq.message.edit_text("💰 Enter <b>start price</b> (GBP):",
                               parse_mode=ParseMode.HTML, reply_markup=kb_cancel())
    await cq.answer()


@router.message(CreateLot.start_price)
async def cl_start_price(msg: Message, state: FSMContext) -> None:
    try:
        v = float(msg.text.replace(",", "."))
        assert v > 0
    except Exception:
        return await msg.answer("Enter a valid positive number:", reply_markup=kb_cancel())
    await state.update_data(start_price=v)
    await state.set_state(CreateLot.reserve_price)
    await msg.answer(
        "🔒 Enter <b>reserve price</b> (GBP, hidden from bidders).\n"
        "If top bid &lt; reserve → lot unsold. Or skip:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_skip(),
    )


@router.callback_query(CreateLot.reserve_price, F.data == "skip")
async def cl_reserve_skip(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(reserve_price=0)
    await state.set_state(CreateLot.bid_step)
    await cq.message.edit_text(
        "📈 Enter <b>bid step</b> (GBP, default 10):", parse_mode=ParseMode.HTML,
        reply_markup=kb_skip(),
    )
    await cq.answer()


@router.message(CreateLot.reserve_price)
async def cl_reserve_price(msg: Message, state: FSMContext) -> None:
    try:
        v = float(msg.text.replace(",", "."))
        assert v >= 0
    except Exception:
        return await msg.answer("Enter a valid number:", reply_markup=kb_skip())
    await state.update_data(reserve_price=v)
    await state.set_state(CreateLot.bid_step)
    await msg.answer("📈 Enter <b>bid step</b> (GBP, default 10):", parse_mode=ParseMode.HTML,
                     reply_markup=kb_skip())


@router.callback_query(CreateLot.bid_step, F.data == "skip")
async def cl_bid_step_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(bid_step=10)
    await state.set_state(CreateLot.starts_at)
    await cq.message.edit_text(
        "🕐 Выберите <b>время старта</b> аукциона:",
        parse_mode=ParseMode.HTML, reply_markup=kb_starts_at(),
    )
    await cq.answer()


@router.message(CreateLot.bid_step)
async def cl_bid_step(msg: Message, state: FSMContext) -> None:
    try:
        v = float(msg.text.replace(",", "."))
        assert v > 0
    except Exception:
        return await msg.answer("Enter a valid positive number:", reply_markup=kb_skip())
    await state.update_data(bid_step=v)
    await state.set_state(CreateLot.starts_at)
    await msg.answer(
        "🕐 Выберите <b>время старта</b> аукциона:",
        parse_mode=ParseMode.HTML, reply_markup=kb_starts_at(),
    )


@router.callback_query(CreateLot.starts_at, F.data.startswith("cl_start:"))
async def cl_starts_at_btn(cq: CallbackQuery, state: FSMContext) -> None:
    choice = cq.data.split(":")[1]
    now = datetime.now()
    if choice == "now":
        starts_at = ""
    elif choice == "1h":
        starts_at = (now + timedelta(hours=1)).isoformat(timespec="seconds")
    else:
        starts_at = (now + timedelta(hours=2)).isoformat(timespec="seconds")
    await state.update_data(starts_at=starts_at)
    await state.set_state(CreateLot.end_time)
    await cq.message.edit_text(
        "⏰ Выберите <b>время окончания</b> аукциона:",
        parse_mode=ParseMode.HTML, reply_markup=kb_end_time(),
    )
    await cq.answer()


@router.callback_query(CreateLot.end_time, F.data.startswith("cl_end:"))
async def cl_end_time_btn(cq: CallbackQuery, state: FSMContext) -> None:
    choice = cq.data.split(":")[1]
    now = datetime.now()
    if choice == "5m":
        end_dt = now + timedelta(minutes=5)
    elif choice == "1h":
        end_dt = now + timedelta(hours=1)
    elif choice == "2h":
        end_dt = now + timedelta(hours=2)
    else:
        end_dt = now + timedelta(hours=3)
    end_time = end_dt.isoformat(timespec="seconds")
    await state.update_data(end_time=end_time)
    await state.set_state(CreateLot.confirm)

    data = await state.get_data()
    reserve = data.get("reserve_price") or 0
    reserve_str = p(reserve) if reserve else "Not set"
    starts_str  = data.get("starts_at") or "Сейчас"

    preview = (
        f"📋 <b>Lot preview:</b>\n\n"
        f"Title: <b>{data['title']}</b>\n"
        f"Reg #: {data.get('reg_number') or '—'}\n"
        f"Description: {data['description']}\n"
        f"Photos: {len(data.get('photos', []))} | Videos: {len(data.get('videos', []))}\n"
        f"Start price: <b>{p(data['start_price'])}</b>\n"
        f"Reserve: <b>{reserve_str}</b>\n"
        f"Bid step: <b>{p(data.get('bid_step', 10))}</b>\n"
        f"Starts: {starts_str}\n"
        f"Ends: {end_time}\n\n"
        f"Publish to channel?"
    )
    await cq.message.edit_text(preview, parse_mode=ParseMode.HTML, reply_markup=kb_confirm_lot())
    await cq.answer()


@router.callback_query(CreateLot.confirm, F.data == "lot_confirm_yes")
async def cb_confirm_lot(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    lot_id = db.create_lot(
        title         = data["title"],
        reg_number    = data.get("reg_number", ""),
        description   = data["description"],
        parts         = data.get("parts", []),
        photos        = data.get("photos", []),
        videos        = data.get("videos", []),
        start_price   = data["start_price"],
        reserve_price = data.get("reserve_price", 0),
        bid_step      = data.get("bid_step", 10),
        starts_at     = data.get("starts_at", ""),
        end_time      = data["end_time"],
    )

    lot = db.get_lot(lot_id)
    await publish_lot_to_channel(cq.bot, lot)
    lot = db.get_lot(lot_id)  # reload with channel_message_id

    channel_ok = "✅ Published to channel." if lot.get("channel_message_id") else "⚠️ Channel post failed — check CHANNEL_ID."

    await cq.message.edit_text(
        f"✅ Lot <b>#{lot_id}: {lot['title']}</b> created!\n{channel_ok}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin(),
    )
    await cq.answer("Lot created!")


# ── Admin manage lots ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_lots:"))
async def cb_admin_lots(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    page = int(cq.data.split(":")[1])
    lots = db.get_active_lots()
    if not lots:
        await cq.message.edit_text("📭 No active lots.", reply_markup=kb_admin())
        await cq.answer()
        return

    total = max(1, (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE)
    page  = max(0, min(page, total - 1))
    chunk = lots[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    rows = []
    for lot in chunk:
        rows.append([InlineKeyboardButton(
            text=f"📦 #{lot['id']} {lot['title']} | {p(lot['current_price'])}",
            callback_data=f"admin_lot:{lot['id']}"
        )])
    nav = _nav(page, total, "admin_lots")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin")])

    await cq.message.edit_text(f"📂 <b>Active Lots</b> ({len(lots)} total)",
                               parse_mode=ParseMode.HTML,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("admin_lot:"))
async def cb_admin_lot_view(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    lot_id = int(cq.data.split(":")[1])
    lot = db.get_lot(lot_id)
    if not lot:
        return await cq.answer("Not found", show_alert=True)

    last = db.get_last_bid(lot_id)
    reserve = lot.get("reserve_price") or 0
    text = (
        f"📦 <b>Lot #{lot_id}: {lot['title']}</b>\n"
        f"Reg #: {lot.get('reg_number') or '—'}\n"
        f"Status: {lot['status']}\n"
        f"Start: {p(lot['start_price'])} | Reserve: {p(reserve)}\n"
        f"Current: <b>{p(lot['current_price'])}</b>\n"
        f"Step: {p(lot['bid_step'])}\n"
        f"Ends: {lot['end_time']}\n"
        f"Bids: {last['id'] if last else 0}\n"
        f"Leader: Bidder #{last['bidder_num'] if last else '—'}"
    )
    await cq.message.edit_text(text, parse_mode=ParseMode.HTML,
                               reply_markup=kb_admin_lot(lot_id))
    await cq.answer()


@router.callback_query(F.data.startswith("admin_del:"))
async def cb_admin_delete(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    lot_id = int(cq.data.split(":")[1])
    db.delete_lot(lot_id)
    await cq.message.edit_text(f"🗑 Lot #{lot_id} deleted.", reply_markup=kb_admin())
    await cq.answer("Deleted")


@router.callback_query(F.data.startswith("admin_end:"))
async def cb_admin_end_early(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    lot_id = int(cq.data.split(":")[1])
    await finalize_lot(cq.bot, lot_id)
    await cq.message.edit_text(f"⏹ Lot #{lot_id} ended.", reply_markup=kb_admin())
    await cq.answer("Ended")


# ── Admin history ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_history:"))
async def cb_admin_history(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    page = int(cq.data.split(":")[1])
    lots = db.get_ended_lots()
    if not lots:
        await cq.message.edit_text("📭 No ended lots.", reply_markup=kb_admin())
        await cq.answer()
        return

    total = max(1, (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE)
    page  = max(0, min(page, total - 1))
    chunk = lots[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    lines = [f"📜 <b>History</b> (page {page+1}/{total}):\n"]
    for lot in chunk:
        status_icon = "✅" if lot["status"] == "ended" else "❌"
        winner = f"Winner: {p(lot['winner_price'])}" if lot.get("winner_price") else "Unsold"
        lines.append(f"{status_icon} #{lot['id']} <b>{lot['title']}</b> — {winner}")

    rows = []
    nav  = _nav(page, total, "admin_history")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin")])

    await cq.message.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


# ── Admin: all users ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_users:"))
async def cb_admin_users(cq: CallbackQuery) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    page = int(cq.data.split(":")[1])
    users = db.get_all_users()
    if not users:
        await cq.message.edit_text("💭 No users yet.", reply_markup=kb_admin())
        return await cq.answer()

    total = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
    page  = max(0, min(page, total - 1))
    chunk = users[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    lines = [f"👥 <b>Users</b> ({len(users)} total, page {page+1}/{total}):\n"]
    for u in chunk:
        uname = f"@{u['username']}" if u.get("username") else "—"
        name  = u.get("full_name") or "—"
        status = u.get("status") or ""
        icon = "✅" if status == "verified" else ("⏳" if status == "pending" else "🔵")
        lines.append(f"{icon} <code>{u['tg_id']}</code> {uname} | {name}")

    rows = []
    nav  = _nav(page, total, "admin_users")
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin")])

    await cq.message.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


# ── Admin broadcast ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cq.from_user.id):
        return await cq.answer()
    await state.set_state(BroadcastState.text)
    await cq.message.edit_text("📢 Enter broadcast message:", reply_markup=kb_cancel())
    await cq.answer()


@router.message(BroadcastState.text)
async def handle_broadcast(msg: Message, state: FSMContext) -> None:
    if not _is_admin(msg.from_user.id):
        return
    await state.clear()
    user_ids = db.get_all_user_ids()
    sent = 0
    for uid in user_ids:
        try:
            await msg.bot.send_message(uid, msg.text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            pass
    await msg.answer(f"📢 Sent to {sent}/{len(user_ids)} users.",
                     reply_markup=kb_main(msg.from_user.id))


# ── Scheduler jobs ─────────────────────────────────────────────────────────────

async def job_check_expired(bot: Bot) -> None:
    for lot in db.get_expired_lots():
        await finalize_lot(bot, lot["id"])


async def job_notify_ending_soon(bot: Bot) -> None:
    for lot in db.get_lots_ending_soon(minutes=6):
        db.mark_notified_ending_soon(lot["id"])
        user_ids = db.get_all_user_ids()
        for uid in user_ids:
            try:
                await bot.send_message(
                    uid,
                    f"⏰ <b>Ending soon!</b> Lot <b>{lot['title']}</b>\n"
                    f"Bid: {p(lot['current_price'])} | {fmt_time_left(lot['end_time'])} left",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="🔨 Bid now",
                            url=f"https://t.me/{BOT_USERNAME}?start=lot_{lot['id']}"
                        )]
                    ]),
                )
            except Exception:
                pass


async def job_update_countdown(bot: Bot) -> None:
    """Edit channel posts to keep countdown fresh (every 10 sec)."""
    now = datetime.now().isoformat(timespec="seconds")
    for lot in db.get_active_lots():
        if lot.get("end_time", "") <= now:
            # Expired but not finalized yet — finalize immediately
            await finalize_lot(bot, lot["id"])
        elif lot.get("channel_message_id"):
            _channel_edit_ts.pop(lot["id"], None)  # force update from scheduler
            await edit_channel_post(bot, lot)
            await asyncio.sleep(1)  # rate-limit guard


# ── Single-instance lock ───────────────────────────────────────────────────────

def _ensure_single_instance() -> None:
    """Kill any previous bot instance using a PID lock file."""
    import os, signal
    pid_file = Path(__file__).parent / "data" / "bot.pid"
    pid_file.parent.mkdir(exist_ok=True)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid != os.getpid():
                try:
                    import psutil  # type: ignore
                    p = psutil.Process(old_pid)
                    p.kill()
                    logger.info("Killed previous bot instance (PID %s)", old_pid)
                except Exception:
                    pass  # process already gone
        except Exception:
            pass
    pid_file.write_text(str(os.getpid()))


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    global BOT_USERNAME
    import os
    os.makedirs("data", exist_ok=True)
    config.validate()
    _ensure_single_instance()
    db.init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    me = await bot.get_me()
    BOT_USERNAME = me.username
    logger.info("Bot: @%s  | Admins: %s | Channel: %s",
                BOT_USERNAME, config.ADMIN_IDS, config.CHANNEL_ID)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Suppress "message is not modified" and similar harmless Telegram errors
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import ErrorEvent

    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        exception = event.exception
        if isinstance(exception, TelegramBadRequest):
            err = str(exception).lower()
            if "message is not modified" in err or "query is too old" in err:
                return True  # suppress silently
        logger.error("Unhandled error: %s", exception)
        return False

    async def job_db_keepalive():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, db.keepalive)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(job_check_expired,      "interval", seconds=30,  args=[bot])
    scheduler.add_job(job_notify_ending_soon, "interval", seconds=300, args=[bot])
    scheduler.add_job(job_update_countdown,   "interval", seconds=10,  args=[bot])
    scheduler.add_job(job_db_keepalive,       "interval", seconds=240)
    scheduler.start()

    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
