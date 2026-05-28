# =========================================
# UZUM CASHBACK & SHIKOYAT BOT
# Production Version
# Aiogram 3.x
# =========================================

import os
import sqlite3
import uuid
import logging
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================================
# LOAD ENV
# =========================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")

# =========================================
# LOGGING
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)

# =========================================
# BOT
# =========================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# =========================================
# DATABASE
# =========================================

DB_NAME = "uzum_bot.db"

UZ_TZ = timezone(timedelta(hours=5))

# =========================================
# CASHBACK SETTINGS
# =========================================

CASHBACK_10000 = 10000
CASHBACK_7000 = 7000
CASHBACK_5000 = 5000

DAILY_LIMIT = 3

# =========================================
# STATES
# =========================================

class CashbackState(StatesGroup):
    waiting_screenshot = State()
    waiting_card = State()
    waiting_card_owner = State()


class ComplaintState(StatesGroup):
    waiting_negative_review = State()
    waiting_negative_screenshot = State()
    waiting_text = State()
    waiting_problem_image = State()


class AdminState(StatesGroup):
    waiting_custom_reply = State()

    waiting_paid_check = State()
    waiting_requested_check = State()


# =========================================
# DB HELPERS
# =========================================

def db():
    return sqlite3.connect(DB_NAME)


def now():
    return datetime.now(UZ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now(UZ_TZ).strftime("%Y-%m-%d")


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY,
        full_name TEXT,
        username TEXT,
        language TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cashback_requests(
        request_id TEXT PRIMARY KEY,
        telegram_id INTEGER,
        card_number TEXT,
        card_owner TEXT,
        amount INTEGER DEFAULT 0,
        status TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS complaints(
        complaint_id TEXT PRIMARY KEY,
        telegram_id INTEGER,
        complaint_type TEXT,
        text TEXT,
        status TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()


# =========================================
# USER
# =========================================

def add_user(user, lang="uz"):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO users(
        telegram_id,
        full_name,
        username,
        language,
        created_at
    )
    VALUES(?,?,?,?,?)
    """, (
        user.id,
        user.full_name,
        user.username,
        lang,
        now()
    ))

    conn.commit()
    conn.close()


def get_lang(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT language FROM users WHERE telegram_id=?",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    if row:
        return row[0]

    return "uz"


# =========================================
# KEYBOARDS
# =========================================

def lang_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(text="🇺🇿 O'zbekcha", callback_data="lang_uz")
    kb.button(text="🇷🇺 Русский", callback_data="lang_ru")

    kb.adjust(1)

    return kb.as_markup()


def menu(lang="uz"):

    if lang == "ru":
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💰 Получить cashback")],
                [KeyboardButton(text="📝 Оставить жалобу")],
                [KeyboardButton(text="🌐 Изменить язык")]
            ],
            resize_keyboard=True
        )

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Cashback olish")],
            [KeyboardButton(text="📝 Shikoyat qoldirish")],
            [KeyboardButton(text="🌐 Tilni o'zgartirish")]
        ],
        resize_keyboard=True
    )


# =========================================
# START
# =========================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "🌐 Tilni tanlang / Выберите язык",
        reply_markup=lang_keyboard()
    )


@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(callback: CallbackQuery):

    lang = callback.data.split("_")[1]

    add_user(callback.from_user, lang)

    if lang == "ru":
        text = (
            "Здравствуйте 😊\n\n"
            "Через этот бот вы можете:\n"
            "• Получить cashback\n"
            "• Оставить жалобу"
        )
    else:
        text = (
            "Assalomu alaykum 😊\n\n"
            "Bu bot orqali:\n"
            "• Cashback olishingiz\n"
            "• Shikoyat qoldirishingiz mumkin"
        )

    await callback.message.answer(
        text,
        reply_markup=menu(lang)
    )

    await callback.answer()


# =========================================
# CHANGE LANGUAGE
# =========================================

@dp.message(F.text.in_([
    "🌐 Tilni o'zgartirish",
    "🌐 Изменить язык"
]))
async def change_lang(message: Message):

    await message.answer(
        "🌐 Tilni tanlang",
        reply_markup=lang_keyboard()
    )


# =========================================
# CASHBACK START
# =========================================

@dp.message(F.text.in_([
    "💰 Cashback olish",
    "💰 Получить cashback"
]))
async def cashback_start(message: Message, state: FSMContext):

    lang = get_lang(message.from_user.id)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT COUNT(*)
    FROM cashback_requests
    WHERE telegram_id=?
    AND created_at LIKE ?
    """, (
        message.from_user.id,
        f"{today()}%"
    ))

    count = cur.fetchone()[0]

    conn.close()

    if count >= DAILY_LIMIT:

        if lang == "ru":
            text = "❌ Вы сегодня уже использовали лимит cashback"
        else:
            text = "❌ Siz bugungi cashback limitidan foydalandingiz"

        await message.answer(text)

        return

    if lang == "ru":
        text = (
           "💰 Условия получения cashback\n\n"
    "Cashback выплачивается только за отзыв 5⭐.\n\n"
    "Сумма выплаты:\n\n"
    "1️⃣ Отзыв 5⭐ с фото и положительным текстом — 10 000 сум\n\n"
    "2️⃣ Отзыв 5⭐ без фото, но с положительным текстом — 7 000 сум\n\n"
    "3️⃣ Только оценка 5⭐ — 5 000 сум\n\n"
    "📌 Чтобы получить cashback, отправьте скриншот вашего отзыва 5⭐."
        )
    else:
        text =  (
    "💰 Cashback olish shartlari\n\n"
    "Cashback faqat 5⭐ sharh qoldirilgan holatda beriladi.\n\n"
    "To‘lov miqdori:\n\n"
    "1️⃣ Rasmli + 5⭐ ijobiy sharh uchun — 10 000 so‘m\n\n"
    "2️⃣ Rasmsiz + 5⭐ ijobiy so‘zli sharh uchun — 7 000 so‘m\n\n"
    "3️⃣ Faqat 5⭐ baho uchun — 5 000 so‘m\n\n"
    "📌 Cashback olish uchun 5⭐ sharh qoldirganingiz skrinshotini yuboring."
)
    await message.answer(text)

    await state.set_state(CashbackState.waiting_screenshot)


# =========================================
# SCREENSHOT
# =========================================

@dp.message(CashbackState.waiting_screenshot)
async def cashback_ss(message: Message, state: FSMContext):

    lang = get_lang(message.from_user.id)

    if not message.photo and not message.document:

        if lang == "ru":
            text = "Сначала отправьте скриншот"
        else:
            text = "Avval screenshot yuboring"

        await message.answer(text)

        return

    await state.update_data(
        screenshot=message.message_id
    )

    if lang == "ru":
        text = "💳 Отправьте номер карты"
    else:
        text = "💳 Karta raqamingizni yuboring"

    await message.answer(text)

    await state.set_state(CashbackState.waiting_card)


# =========================================
# CARD
# =========================================

@dp.message(CashbackState.waiting_card)
async def cashback_card(message: Message, state: FSMContext):

    card = message.text.strip()

    digits = "".join(x for x in card if x.isdigit())

    if len(digits) < 16:

        await message.answer(
            "❌ Karta noto'g'ri"
        )

        return

    await state.update_data(
        card=card
    )

    await message.answer(
        "👤 Karta egasini yuboring"
    )

    await state.set_state(
        CashbackState.waiting_card_owner
    )


# =========================================
# CARD OWNER
# =========================================

@dp.message(CashbackState.waiting_card_owner)
async def cashback_owner(message: Message, state: FSMContext):

    data = await state.get_data()

    request_id = uuid.uuid4().hex[:8]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO cashback_requests(
        request_id,
        telegram_id,
        card_number,
        card_owner,
        status,
        created_at
    )
    VALUES(?,?,?,?,?,?)
    """, (
        request_id,
        message.from_user.id,
        data["card"],
        message.text,
        "pending",
        now()
    ))

    conn.commit()
    conn.close()

    await message.answer(
        "✅ Arizangiz qabul qilindi",
        reply_markup=menu(
            get_lang(message.from_user.id)
        )
    )

    if ADMIN_ID:

        kb = InlineKeyboardBuilder()

        kb.button(
            text="10 000",
            callback_data=f"approve:{request_id}:10000"
        )

        kb.button(
            text="7 000",
            callback_data=f"approve:{request_id}:7000"
        )

        kb.button(
            text="5 000",
            callback_data=f"approve:{request_id}:5000"
        )

        kb.button(
            text="❌ Rad etish",
            callback_data=f"reject:{request_id}"
        )

        kb.adjust(1)

        await bot.send_message(
            ADMIN_ID,
            (
                "💰 Yangi cashback\n\n"
                f"🆔 {request_id}\n"
                f"👤 {message.from_user.full_name}\n"
                f"💳 {data['card']}\n"
                f"👤 {message.text}"
            ),
            reply_markup=kb.as_markup()
        )

        await bot.forward_message(
            ADMIN_ID,
            message.chat.id,
            data["screenshot"]
        )

    await state.clear()


# =========================================
# APPROVE
# =========================================

@dp.callback_query(F.data.startswith("approve:"))
async def approve(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    _, request_id, amount = callback.data.split(":")

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE cashback_requests
    SET status='approved',
    amount=?
    WHERE request_id=?
    """, (
        amount,
        request_id
    ))

    conn.commit()

    cur.execute("""
    SELECT telegram_id
    FROM cashback_requests
    WHERE request_id=?
    """, (
        request_id,
    ))

    user_id = cur.fetchone()[0]

    conn.close()

    await bot.send_message(
        user_id,
        f"✅ Cashback tasdiqlandi\n\n💰 {amount} so'm"
    )

    kb = InlineKeyboardBuilder()

    kb.button(
        text="💳 To'landi",
        callback_data=f"paid:{request_id}"
    )

    await callback.message.answer(
        "Pul o'tkazilgandan keyin bosing",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# =========================================
# REJECT
# =========================================

@dp.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    request_id = callback.data.split(":")[1]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT telegram_id
    FROM cashback_requests
    WHERE request_id=?
    """, (
        request_id,
    ))

    user_id = cur.fetchone()[0]

    cur.execute("""
    UPDATE cashback_requests
    SET status='rejected'
    WHERE request_id=?
    """, (
        request_id,
    ))

    conn.commit()
    conn.close()

    await bot.send_message(
        user_id,
        "❌ Cashback rad etildi"
    )

    await callback.answer(
        "Rad etildi"
    )


# =========================================
# PAID
# =========================================

@dp.callback_query(F.data.startswith("paid:"))
async def paid(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id != ADMIN_ID:
        return

    request_id = callback.data.split(":")[1]

    await state.update_data(
        request_id=request_id
    )

    await callback.message.answer(
        "📸 Chek rasmini yuboring"
    )

    await state.set_state(
        AdminState.waiting_paid_check
    )

    await callback.answer()


# =========================================
# CHECK PHOTO
# =========================================

@dp.message(AdminState.waiting_paid_check)
async def paid_check(message: Message, state: FSMContext):

    data = await state.get_data()

    request_id = data["request_id"]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT telegram_id
    FROM cashback_requests
    WHERE request_id=?
    """, (
        request_id,
    ))

    user_id = cur.fetchone()[0]

    cur.execute("""
    UPDATE cashback_requests
    SET status='paid'
    WHERE request_id=?
    """, (
        request_id,
    ))

    conn.commit()
    conn.close()

    await bot.send_message(
        user_id,
        "💳 Cashback o'tkazildi"
    )

    if message.photo:

        await bot.send_photo(
            user_id,
            message.photo[-1].file_id
        )

    elif message.document:

        await bot.send_document(
            user_id,
            message.document.file_id
        )

    await message.answer(
        "✅ Yuborildi"
    )

    await state.clear()


# =========================================
# COMPLAINT START
# =========================================

@dp.message(F.text.in_([
    "📝 Shikoyat qoldirish",
    "📝 Оставить жалобу"
]))
async def complaint_start(message: Message):

    kb = InlineKeyboardBuilder()

    kb.button(
        text="📦 Mahsulot sifati",
        callback_data="c_quality"
    )

    kb.button(
        text="🚚 Yetkazib berish",
        callback_data="c_delivery"
    )

    kb.button(
        text="💰 Cashback",
        callback_data="c_cashback"
    )

    kb.button(
        text="📝 Boshqa",
        callback_data="c_other"
    )

    kb.adjust(1)

    await message.answer(
        "Muammo turini tanlang",
        reply_markup=kb.as_markup()
    )


# =========================================
# COMPLAINT TYPE
# =========================================

@dp.callback_query(F.data.startswith("c_"))
async def complaint_type(callback: CallbackQuery, state: FSMContext):

    complaint_type = callback.data.replace("c_", "")

    if complaint_type == "delivery":

        await callback.message.answer(
            "🚚 Yetkazib berish Uzum tomonidan boshqariladi"
        )

        await callback.answer()

        return

    await state.update_data(
        complaint_type=complaint_type
    )

    kb = InlineKeyboardBuilder()

    kb.button(
        text="✅ Ha",
        callback_data="negative_yes"
    )

    kb.button(
        text="❌ Yo'q",
        callback_data="negative_no"
    )

    kb.adjust(1)

    await callback.message.answer(
        "Salbiy sharh qoldirganmisiz?",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# =========================================
# NEGATIVE REVIEW
# =========================================

@dp.callback_query(F.data.startswith("negative_"))
async def negative(callback: CallbackQuery, state: FSMContext):

    answer = callback.data.split("_")[1]

    if answer == "yes":

        await callback.message.answer(
            "Screenshot yuboring"
        )

        await state.set_state(
            ComplaintState.waiting_negative_screenshot
        )

    else:

        await callback.message.answer(
            (
                "Sizda kelib chiqqan "
                "noqulaylik uchun uzr so'raymiz\n\n"
                "Muammoni yozing"
            )
        )

        await state.set_state(
            ComplaintState.waiting_text
        )

    await callback.answer()


# =========================================
# NEGATIVE SCREENSHOT
# =========================================

@dp.message(
    ComplaintState.waiting_negative_screenshot
)
async def negative_ss(message: Message, state: FSMContext):

    await state.update_data(
        negative_ss=message.message_id
    )

    await message.answer(
        "Muammoni yozing"
    )

    await state.set_state(
        ComplaintState.waiting_text
    )


# =========================================
# COMPLAINT TEXT
# =========================================

@dp.message(ComplaintState.waiting_text)
async def complaint_text(message: Message, state: FSMContext):

    await state.update_data(
        complaint_text=message.text
    )

    await message.answer(
        "Muammo rasmi bo'lsa yuboring\n\n"
        "Yoki o'tkazib yuboring"
    )

    await state.set_state(
        ComplaintState.waiting_problem_image
    )


# =========================================
# COMPLAINT IMAGE
# =========================================

@dp.message(ComplaintState.waiting_problem_image)
async def complaint_finish(message: Message, state: FSMContext):

    data = await state.get_data()

    complaint_id = uuid.uuid4().hex[:8]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO complaints(
        complaint_id,
        telegram_id,
        complaint_type,
        text,
        status,
        created_at
    )
    VALUES(?,?,?,?,?,?)
    """, (
        complaint_id,
        message.from_user.id,
        data["complaint_type"],
        data["complaint_text"],
        "pending",
        now()
    ))

    conn.commit()
    conn.close()

    await message.answer(
        "✅ Shikoyat qabul qilindi",
        reply_markup=menu(
            get_lang(message.from_user.id)
        )
    )

    if ADMIN_ID:

        kb = InlineKeyboardBuilder()

        kb.button(
            text="✉️ Javob yozish",
            callback_data=f"reply:{complaint_id}"
        )

        kb.adjust(1)

        await bot.send_message(
            ADMIN_ID,
            (
                "📝 Yangi shikoyat\n\n"
                f"🆔 {complaint_id}\n\n"
                f"{data['complaint_text']}"
            ),
            reply_markup=kb.as_markup()
        )

    await state.clear()


# =========================================
# ADMIN REPLY
# =========================================

@dp.callback_query(F.data.startswith("reply:"))
async def admin_reply(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id != ADMIN_ID:
        return

    complaint_id = callback.data.split(":")[1]

    await state.update_data(
        complaint_id=complaint_id
    )

    await callback.message.answer(
        "Javob yozing"
    )

    await state.set_state(
        AdminState.waiting_custom_reply
    )

    await callback.answer()


# =========================================
# SEND REPLY
# =========================================

@dp.message(AdminState.waiting_custom_reply)
async def send_reply(message: Message, state: FSMContext):

    data = await state.get_data()

    complaint_id = data["complaint_id"]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT telegram_id
    FROM complaints
    WHERE complaint_id=?
    """, (
        complaint_id,
    ))

    user_id = cur.fetchone()[0]

    await bot.send_message(
        user_id,
        (
            "✉️ Admin javobi:\n\n"
            f"{message.text}"
        )
    )

    cur.execute("""
    UPDATE complaints
    SET status='answered'
    WHERE complaint_id=?
    """, (
        complaint_id,
    ))

    conn.commit()
    conn.close()

    await message.answer(
        "✅ Yuborildi"
    )

    await state.clear()


# =========================================
# REPORT
# =========================================

def get_report():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT COUNT(*)
    FROM users
    """)

    users = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM cashback_requests
    WHERE status='paid'
    """)

    paid = cur.fetchone()[0]

    cur.execute("""
    SELECT COALESCE(SUM(amount),0)
    FROM cashback_requests
    WHERE status='paid'
    """)

    amount = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM complaints
    """)

    complaints = cur.fetchone()[0]

    conn.close()

    return (
        "📊 HISOBOT\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"💳 To'langan cashback: {paid}\n"
        f"💰 Jami summa: {amount} so'm\n"
        f"📝 Shikoyatlar: {complaints}"
    )


@dp.message(Command("admin"))
async def admin(message: Message):

    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        get_report()
    )


# =========================================
# DAILY REPORT
# =========================================

async def daily_report():

    if ADMIN_ID:

        await bot.send_message(
            ADMIN_ID,
            "📊 Kunlik hisobot\n\n" + get_report()
        )


# =========================================
# MAIN
# =========================================

async def main():

    init_db()

    scheduler = AsyncIOScheduler(
        timezone="Asia/Tashkent"
    )

    scheduler.add_job(
        daily_report,
        "cron",
        hour=20,
        minute=0
    )

    scheduler.start()

    logger.info("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":

    import asyncio

    asyncio.run(main())
