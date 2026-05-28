import os
import sqlite3
import uuid
import logging
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CASHBACK_10000 = int(os.getenv("CASHBACK_10000", "10000"))
CASHBACK_7000 = int(os.getenv("CASHBACK_7000", "7000"))
CASHBACK_5000 = int(os.getenv("CASHBACK_5000", "5000"))

# Bir kunda bir xaridor necha marta cashback so'rashi mumkin
CASHBACK_DAILY_LIMIT = int(os.getenv("CASHBACK_DAILY_LIMIT", "3"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_NAME = "cashback_bot.db"
UZ_TZ = timezone(timedelta(hours=5))

# Faqat chek so'rash uchun pending (kichik, xotirada saqlash mumkin)
pending_check_requests = {}  # complaint_id -> telegram_id
pending_admin_replies = {}   # complaint_id -> {text, lang}


# =========================
# DATABASE
# =========================

def now_text():
    return datetime.now(UZ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.now(UZ_TZ).strftime("%Y-%m-%d")


def db():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            language TEXT DEFAULT 'uz',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashback_requests (
            request_id TEXT PRIMARY KEY,
            telegram_id INTEGER,
            username TEXT,
            full_name TEXT,
            language TEXT,
            card_number TEXT,
            card_owner TEXT,
            status TEXT,
            amount INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id TEXT PRIMARY KEY,
            telegram_id INTEGER,
            username TEXT,
            full_name TEXT,
            language TEXT,
            complaint_type TEXT,
            negative_review TEXT,
            has_negative_screenshot TEXT,
            complaint_text TEXT,
            has_problem_image TEXT,
            status TEXT,
            admin_reply TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def add_user(user, lang="uz"):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (user.id,))
    exists = cur.fetchone()
    if exists:
        cur.execute(
            "UPDATE users SET full_name = ?, username = ? WHERE telegram_id = ?",
            (user.full_name or "", user.username or "", user.id)
        )
    else:
        cur.execute(
            "INSERT INTO users (telegram_id, full_name, username, language, created_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.full_name or "", user.username or "", lang, now_text())
        )
    conn.commit()
    conn.close()


def set_user_language(telegram_id, lang):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET language = ? WHERE telegram_id = ?", (lang, telegram_id))
    conn.commit()
    conn.close()


def get_user_language(telegram_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT language FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] in ["uz", "ru"]:
        return row[0]
    return "uz"


def save_cashback_request(request_id, message, lang, card_number, card_owner):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cashback_requests (
            request_id, telegram_id, username, full_name, language,
            card_number, card_owner, status, amount, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name or "",
            lang,
            card_number,
            card_owner,
            "pending",
            0,
            now_text(),
            now_text()
        )
    )
    conn.commit()
    conn.close()


def update_cashback_status(request_id, status, amount=None):
    conn = db()
    cur = conn.cursor()
    if amount is not None:
        cur.execute(
            "UPDATE cashback_requests SET status = ?, amount = ?, updated_at = ? WHERE request_id = ?",
            (status, amount, now_text(), request_id)
        )
    else:
        cur.execute(
            "UPDATE cashback_requests SET status = ?, updated_at = ? WHERE request_id = ?",
            (status, now_text(), request_id)
        )
    conn.commit()
    conn.close()


def get_cashback_request(request_id):
    """DB dan cashback arizasini olish — pending dict o'rniga"""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cashback_requests WHERE request_id = ?", (request_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["request_id", "telegram_id", "username", "full_name", "language",
            "card_number", "card_owner", "status", "amount", "created_at", "updated_at"]
    return dict(zip(cols, row))


def get_daily_cashback_count(telegram_id):
    """Bugun ushbu foydalanuvchi nechta cashback so'ragan"""
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM cashback_requests WHERE telegram_id = ? AND DATE(created_at) = ?",
        (telegram_id, today_text())
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


def save_complaint(complaint_id, message, lang, data):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO complaints (
            complaint_id, telegram_id, username, full_name, language,
            complaint_type, negative_review, has_negative_screenshot,
            complaint_text, has_problem_image, status, admin_reply,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            complaint_id,
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name or "",
            lang,
            data.get("complaint_type", ""),
            data.get("negative_review", ""),
            data.get("has_negative_screenshot", "yoq"),
            data.get("complaint_text", ""),
            data.get("has_problem_image", "yoq"),
            "pending",
            "",
            now_text(),
            now_text()
        )
    )
    conn.commit()
    conn.close()


def update_complaint_status(complaint_id, status, admin_reply=""):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE complaints SET status = ?, admin_reply = ?, updated_at = ? WHERE complaint_id = ?",
        (status, admin_reply, now_text(), complaint_id)
    )
    conn.commit()
    conn.close()


def get_complaint(complaint_id):
    """DB dan shikoyatni olish — pending dict o'rniga"""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM complaints WHERE complaint_id = ?", (complaint_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["complaint_id", "telegram_id", "username", "full_name", "language",
            "complaint_type", "negative_review", "has_negative_screenshot",
            "complaint_text", "has_problem_image", "status", "admin_reply",
            "created_at", "updated_at"]
    return dict(zip(cols, row))


# =========================
# HISOBOT (STATISTIKA)
# =========================

def get_report_stats():
    conn = db()
    cur = conn.cursor()
    today = today_text()

    # Hafta boshlangani (dushanba)
    now = datetime.now(UZ_TZ)
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

    # Oy boshlangani
    month_start = now.strftime("%Y-%m-01")

    def cashback_stats(date_from, date_to=None):
        if date_to:
            cur.execute(
                """SELECT COUNT(*), COALESCE(SUM(amount), 0)
                   FROM cashback_requests
                   WHERE status IN ('paid') AND DATE(created_at) BETWEEN ? AND ?""",
                (date_from, date_to)
            )
        else:
            cur.execute(
                """SELECT COUNT(*), COALESCE(SUM(amount), 0)
                   FROM cashback_requests
                   WHERE status IN ('paid') AND DATE(created_at) = ?""",
                (date_from,)
            )
        return cur.fetchone()

    def pending_cashback(date_from=None):
        if date_from:
            cur.execute(
                "SELECT COUNT(*) FROM cashback_requests WHERE status = 'pending' AND DATE(created_at) = ?",
                (date_from,)
            )
        else:
            cur.execute("SELECT COUNT(*) FROM cashback_requests WHERE status = 'pending'")
        return cur.fetchone()[0]

    def complaint_stats(date_from, date_to=None):
        if date_to:
            cur.execute(
                "SELECT COUNT(*) FROM complaints WHERE DATE(created_at) BETWEEN ? AND ?",
                (date_from, date_to)
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM complaints WHERE DATE(created_at) = ?",
                (date_from,)
            )
        return cur.fetchone()[0]

    # Bugungi
    today_paid_count, today_paid_sum = cashback_stats(today)
    today_complaints = complaint_stats(today)
    today_pending = pending_cashback(today)

    # Haftalik
    week_paid_count, week_paid_sum = cashback_stats(week_start, today)
    week_complaints = complaint_stats(week_start, today)

    # Oylik
    month_paid_count, month_paid_sum = cashback_stats(month_start, today)
    month_complaints = complaint_stats(month_start, today)

    # Umumiy pending
    total_pending_cashback = pending_cashback()

    cur.execute("SELECT COUNT(*) FROM complaints WHERE status = 'pending'")
    total_pending_complaints = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    conn.close()

    return {
        "today": {
            "paid_count": today_paid_count,
            "paid_sum": today_paid_sum,
            "complaints": today_complaints,
            "pending_cashback": today_pending,
        },
        "week": {
            "paid_count": week_paid_count,
            "paid_sum": week_paid_sum,
            "complaints": week_complaints,
        },
        "month": {
            "paid_count": month_paid_count,
            "paid_sum": month_paid_sum,
            "complaints": month_complaints,
        },
        "total": {
            "pending_cashback": total_pending_cashback,
            "pending_complaints": total_pending_complaints,
            "users": total_users,
        }
    }


def format_report(stats):
    def fmt(n):
        return f"{n:,}".replace(",", " ")

    t = stats["today"]
    w = stats["week"]
    m = stats["month"]
    total = stats["total"]

    return (
        "📊 Hisobot\n"
        f"🕗 {now_text()}\n\n"

        "📅 Bugun:\n"
        f"  💳 To'langan cashback: {t['paid_count']} ta — {fmt(t['paid_sum'])} so'm\n"
        f"  ⏳ Kutayotgan cashback: {t['pending_cashback']} ta\n"
        f"  📝 Shikoyatlar: {t['complaints']} ta\n\n"

        "📆 Shu hafta:\n"
        f"  💳 To'langan cashback: {w['paid_count']} ta — {fmt(w['paid_sum'])} so'm\n"
        f"  📝 Shikoyatlar: {w['complaints']} ta\n\n"

        "🗓 Shu oy:\n"
        f"  💳 To'langan cashback: {m['paid_count']} ta — {fmt(m['paid_sum'])} so'm\n"
        f"  📝 Shikoyatlar: {m['complaints']} ta\n\n"

        "📌 Umumiy kutayotganlar:\n"
        f"  ⏳ Cashback: {total['pending_cashback']} ta\n"
        f"  ⏳ Shikoyat: {total['pending_complaints']} ta\n"
        f"  👥 Jami foydalanuvchilar: {total['users']} ta"
    )


# =========================
# STATES
# =========================

class CashbackState(StatesGroup):
    waiting_screenshot = State()
    waiting_card = State()
    waiting_card_owner = State()


class ComplaintState(StatesGroup):
    waiting_negative_screenshot = State()
    waiting_text = State()
    waiting_problem_image = State()


class AdminReplyState(StatesGroup):
    waiting_custom_reply = State()
    waiting_template_edit = State()
    waiting_check_photo = State()


# =========================
# TEXTS
# =========================

UZ_START_TEXT = (
    "Assalomu alaykum 😊\n\n"
    "Bu bot orqali siz cashback olish uchun ariza yuborishingiz yoki mahsulot bo'yicha shikoyat qoldirishingiz mumkin.\n\n"
    "Kerakli bo'limni tanlang:"
)

RU_START_TEXT = (
    "Здравствуйте 😊\n\n"
    "Через этот бот вы можете подать заявку на cashback или оставить жалобу по товару.\n\n"
    "Выберите нужный раздел:"
)

UZ_CASHBACK_RULES = (
    "💰 Cashback olish shartlari\n\n"
    "Cashback faqat 5⭐ sharh qoldirilgan holatda beriladi.\n\n"
    "To'lov miqdori:\n\n"
    "1️⃣ Rasmli + 5⭐ ijobiy sharh uchun — 10 000 so'm\n\n"
    "2️⃣ Rasmsiz + 5⭐ ijobiy so'zli sharh uchun — 7 000 so'm\n\n"
    "3️⃣ Faqat 5⭐ baho uchun — 5 000 so'm\n\n"
    "📌 Cashback olish uchun 5⭐ sharh qoldirganingiz skrinshotini yuboring."
)

RU_CASHBACK_RULES = (
    "💰 Условия получения cashback\n\n"
    "Cashback выплачивается только за отзыв 5⭐.\n\n"
    "Сумма выплаты:\n\n"
    "1️⃣ Отзыв 5⭐ с фото и положительным текстом — 10 000 сум\n\n"
    "2️⃣ Отзыв 5⭐ без фото, но с положительным текстом — 7 000 сум\n\n"
    "3️⃣ Только оценка 5⭐ — 5 000 сум\n\n"
    "📌 Чтобы получить cashback, отправьте скриншот вашего отзыва 5⭐."
)

UZ_DELIVERY_TEXT = (
    "🚚 Yetkazib berish bo'yicha ma'lumot\n\n"
    "Buyurtmani yetkazib berish muddati va yetkazib berish jarayoni Uzum tomonidan boshqariladi.\n\n"
    "Sotuvchi yetkazib berish vaqtini tezlashtira olmaydi yoki kuryer ishiga aralasha olmaydi.\n\n"
    "Iltimos, buyurtmangiz holatini Uzum ilovasi orqali tekshiring:\n\n"
    "Uzum ilovasi → Buyurtmalarim → Kerakli buyurtma → Yetkazib berish holati\n\n"
    "Agar buyurtmangiz kechikayotgan bo'lsa, Uzum qo'llab-quvvatlash xizmatiga murojaat qiling."
)

RU_DELIVERY_TEXT = (
    "🚚 Информация по доставке\n\n"
    "Сроки и процесс доставки заказов контролируются сервисом Uzum.\n\n"
    "Продавец не может ускорить доставку или вмешиваться в работу курьера.\n\n"
    "Пожалуйста, проверьте статус заказа в приложении Uzum:\n\n"
    "Приложение Uzum → Мои заказы → Нужный заказ → Статус доставки\n\n"
    "Если заказ задерживается, обратитесь в службу поддержки Uzum."
)

UZ_COMPLAINT_PRE_TEXT = (
    "Sizda kelib chiqqan noqulayliklar uchun uzr so'raymiz.\n\n"
    "Muammoni 100% ijobiy hal qilishga yordam berishga harakat qilamiz.\n\n"
    "Iltimos, muammoni batafsil yozib yuboring."
)

RU_COMPLAINT_PRE_TEXT = (
    "Приносим извинения за доставленные неудобства.\n\n"
    "Мы постараемся помочь решить проблему максимально положительно.\n\n"
    "Пожалуйста, подробно опишите проблему."
)

ADMIN_TEMPLATES = {
    "apology": {
        "title_uz": "1️⃣ Uzr so'rash + ko'rib chiqamiz",
        "title_ru": "1️⃣ Извинение + рассмотрим",
        "uz": (
            "Assalomu alaykum.\n\n"
            "Sizda kelib chiqqan noqulayliklar uchun uzr so'raymiz. "
            "Murojaatingiz ko'rib chiqiladi va muammoni imkon qadar ijobiy hal qilishga yordam beramiz."
        ),
        "ru": (
            "Здравствуйте.\n\n"
            "Приносим извинения за доставленные неудобства. "
            "Ваше обращение будет рассмотрено, и мы постараемся помочь решить ситуацию максимально положительно."
        )
    },
    "order_screenshot": {
        "title_uz": "2️⃣ Buyurtma skrinshotini so'rash",
        "title_ru": "2️⃣ Попросить скриншот заказа",
        "uz": "Iltimos, buyurtmangiz skrinshotini yuboring. Buyurtma ma'lumotlari orqali holatni aniqlashtirib beramiz.",
        "ru": "Пожалуйста, отправьте скриншот вашего заказа. По данным заказа мы сможем уточнить ситуацию."
    },
    "product_photo": {
        "title_uz": "3️⃣ Mahsulot rasmini so'rash",
        "title_ru": "3️⃣ Попросить фото товара",
        "uz": "Iltimos, muammoli mahsulot rasmini yuboring. Shundan so'ng murojaatingizni batafsil ko'rib chiqamiz.",
        "ru": "Пожалуйста, отправьте фото проблемного товара. После этого мы подробнее рассмотрим ваше обращение."
    },
    "delivery": {
        "title_uz": "4️⃣ Yetkazib berish bo'yicha",
        "title_ru": "4️⃣ По доставке",
        "uz": (
            "Yetkazib berish jarayoni Uzum tomonidan boshqariladi. "
            "Sotuvchi yetkazib berish vaqtini tezlashtira olmaydi yoki kuryer ishiga aralasha olmaydi. "
            "Iltimos, buyurtma holatini Uzum ilovasidan tekshiring yoki Uzum qo'llab-quvvatlash xizmatiga murojaat qiling."
        ),
        "ru": (
            "Процесс доставки контролируется сервисом Uzum. "
            "Продавец не может ускорить доставку или вмешиваться в работу курьера. "
            "Пожалуйста, проверьте статус заказа в приложении Uzum или обратитесь в службу поддержки Uzum."
        )
    },
    "cashback": {
        "title_uz": "5️⃣ Cashback bo'yicha",
        "title_ru": "5️⃣ По cashback",
        "uz": "Cashback faqat shartlarga mos 5⭐ sharhlar uchun beriladi. Arizangiz tekshiriladi va shartlarga mos bo'lsa, to'lov amalga oshiriladi.",
        "ru": "Cashback выплачивается только за отзывы 5⭐, соответствующие условиям. Ваша заявка будет проверена, и если она соответствует условиям, выплата будет произведена."
    },
    "defect_return": {
        "title_uz": "6️⃣ Defekt mahsulot / qaytarish",
        "title_ru": "6️⃣ Дефектный товар / возврат",
        "uz": (
            "Assalomu alaykum.\n\n"
            "Sizda kelib chiqqan noqulayliklar uchun uzr so'raymiz. Sizga defekt mahsulot yetib borganga o'xshaydi.\n\n"
            "Iltimos, Uzum chat orqali biz bilan bog'laning. Biz sizga mahsulotni qaytarish uchun rozilik beramiz.\n\n"
            "Shundan so'ng mahsulotni olgan holatingizdagidek, barcha qismlari va elementlari bilan birga Uzum punktiga qaytarishingiz mumkin. "
            "Qaytarish vaqtida mahsulot defekt/brak ekanini ayting. Bu mahsulot qayta sotuvga chiqib ketmasligi uchun kerak.\n\n"
            "Mahsulot qabul qilingandan so'ng pulingiz Uzum tomonidan 100% qaytariladi.\n\n"
            "Noqulaylik uchun yana bir bor uzr so'raymiz."
        ),
        "ru": (
            "Здравствуйте.\n\n"
            "Приносим извинения за доставленные неудобства. Похоже, вам пришёл товар с дефектом.\n\n"
            "Пожалуйста, свяжитесь с нами через чат Uzum. Мы дадим согласие на возврат товара.\n\n"
            "После этого вы сможете вернуть товар в пункт Uzum в том же виде, в котором получили его, со всеми комплектующими и элементами. "
            "При возврате обязательно укажите, что товар имеет дефект/брак. Это нужно для того, чтобы товар не был повторно выставлен на продажу.\n\n"
            "После принятия возврата деньги будут возвращены вам со стороны Uzum в полном размере.\n\n"
            "Ещё раз приносим извинения за неудобства."
        )
    }
}


# =========================
# KEYBOARDS
# =========================

def lang_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🇺🇿 O'zbekcha", callback_data="lang:uz")
    kb.button(text="🇷🇺 Русский", callback_data="lang:ru")
    kb.adjust(1)
    return kb.as_markup()


def main_menu(lang="uz"):
    if lang == "ru":
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💰 Получить cashback")],
                [KeyboardButton(text="📝 Оставить жалобу")],
                [KeyboardButton(text="🌐 Изменить язык")],
            ],
            resize_keyboard=True
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Cashback olish")],
            [KeyboardButton(text="📝 Shikoyat qoldirish")],
            [KeyboardButton(text="🌐 Tilni o'zgartirish")],
        ],
        resize_keyboard=True
    )


def cashback_paid_menu(lang="uz"):
    """To'landi xabaridan keyin chek so'rash tugmasi"""
    if lang == "ru":
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🧾 Запросить чек")],
                [KeyboardButton(text="💰 Получить cashback")],
                [KeyboardButton(text="📝 Оставить жалобу")],
                [KeyboardButton(text="🌐 Изменить язык")],
            ],
            resize_keyboard=True
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧾 Chek so'rash")],
            [KeyboardButton(text="💰 Cashback olish")],
            [KeyboardButton(text="📝 Shikoyat qoldirish")],
            [KeyboardButton(text="🌐 Tilni o'zgartirish")],
        ],
        resize_keyboard=True
    )


def complaint_type_keyboard(lang="uz"):
    kb = InlineKeyboardBuilder()
    if lang == "ru":
        buttons = [
            ("📦 Качество товара", "quality"),
            ("🚚 Доставка", "delivery"),
            ("📦 Упаковка", "packaging"),
            ("💰 По cashback", "cashback"),
            ("👤 Связь с продавцом", "seller_contact"),
            ("📝 Другой вопрос", "other"),
        ]
    else:
        buttons = [
            ("📦 Mahsulot sifati", "quality"),
            ("🚚 Yetkazib berish", "delivery"),
            ("📦 Qadoqlash", "packaging"),
            ("💰 Cashback bo'yicha", "cashback"),
            ("👤 Sotuvchi bilan aloqa", "seller_contact"),
            ("📝 Boshqa masala", "other"),
        ]
    for text, value in buttons:
        kb.button(text=text, callback_data=f"complaint_type:{value}")
    kb.adjust(1)
    return kb.as_markup()


def negative_review_keyboard(lang="uz"):
    kb = InlineKeyboardBuilder()
    if lang == "ru":
        kb.button(text="✅ Да, оставил(а)", callback_data="negative_review:yes")
        kb.button(text="❌ Нет, ещё не оставлял(а)", callback_data="negative_review:no")
    else:
        kb.button(text="✅ Ha, qoldirganman", callback_data="negative_review:yes")
        kb.button(text="❌ Yo'q, hali qoldirmadim", callback_data="negative_review:no")
    kb.adjust(1)
    return kb.as_markup()


def skip_keyboard(lang="uz"):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭ Пропустить" if lang == "ru" else "⏭ O'tkazib yuborish")]
        ],
        resize_keyboard=True
    )


def cashback_admin_keyboard(request_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ 10 000 so'm", callback_data=f"cashback_approve:{request_id}:{CASHBACK_10000}")
    kb.button(text="✅ 7 000 so'm", callback_data=f"cashback_approve:{request_id}:{CASHBACK_7000}")
    kb.button(text="✅ 5 000 so'm", callback_data=f"cashback_approve:{request_id}:{CASHBACK_5000}")
    kb.button(text="❌ Rad etish", callback_data=f"cashback_reject:{request_id}")
    kb.adjust(1)
    return kb.as_markup()


def cashback_paid_keyboard(request_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 To'landi", callback_data=f"cashback_paid:{request_id}")
    return kb.as_markup()


def complaint_admin_keyboard(complaint_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="✉️ Javob yozish", callback_data=f"complaint_reply:{complaint_id}")
    kb.button(text="📋 Shablon javoblar", callback_data=f"complaint_templates:{complaint_id}")
    kb.button(text="✅ Ko'rib chiqildi", callback_data=f"complaint_done:{complaint_id}")
    kb.button(text="❌ Rad etish", callback_data=f"complaint_reject:{complaint_id}")
    kb.adjust(1)
    return kb.as_markup()


def template_list_keyboard(complaint_id):
    kb = InlineKeyboardBuilder()
    for key, item in ADMIN_TEMPLATES.items():
        kb.button(text=item["title_uz"], callback_data=f"tpl_choose:{complaint_id}:{key}")
    kb.adjust(1)
    return kb.as_markup()


def template_lang_keyboard(complaint_id, template_key, preferred_lang="uz"):
    kb = InlineKeyboardBuilder()
    if preferred_lang == "ru":
        kb.button(text="🇷🇺 Русский", callback_data=f"tpl_lang:{complaint_id}:{template_key}:ru")
        kb.button(text="🇺🇿 O'zbekcha", callback_data=f"tpl_lang:{complaint_id}:{template_key}:uz")
    else:
        kb.button(text="🇺🇿 O'zbekcha", callback_data=f"tpl_lang:{complaint_id}:{template_key}:uz")
        kb.button(text="🇷🇺 Русский", callback_data=f"tpl_lang:{complaint_id}:{template_key}:ru")
    kb.adjust(1)
    return kb.as_markup()


def send_template_keyboard(complaint_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Yuborish", callback_data=f"tpl_send:{complaint_id}")
    kb.button(text="✏️ O'zgartirish", callback_data=f"tpl_edit:{complaint_id}")
    kb.button(text="⬅️ Orqaga", callback_data=f"complaint_templates:{complaint_id}")
    kb.adjust(1)
    return kb.as_markup()


# =========================
# HELPERS
# =========================

def user_label(message_or_user):
    user = message_or_user.from_user if hasattr(message_or_user, "from_user") else message_or_user
    username = f"@{user.username}" if user.username else "username yo'q"
    return username


def complaint_type_name(value, lang="uz"):
    names = {
        "quality": ("📦 Mahsulot sifati", "📦 Качество товара"),
        "delivery": ("🚚 Yetkazib berish", "🚚 Доставка"),
        "packaging": ("📦 Qadoqlash", "📦 Упаковка"),
        "cashback": ("💰 Cashback bo'yicha", "💰 По cashback"),
        "seller_contact": ("👤 Sotuvchi bilan aloqa", "👤 Связь с продавцом"),
        "other": ("📝 Boshqa masala", "📝 Другой вопрос"),
    }
    uz, ru = names.get(value, (value, value))
    return ru if lang == "ru" else uz


async def send_admin_cashback_request(request_id, message, lang, card_number, card_owner):
    lang_text = "Русский" if lang == "ru" else "O'zbekcha"
    text = (
        "💰 Yangi cashback so'rovi\n\n"
        f"🆔 Ariza ID: {request_id}\n"
        f"👤 Xaridor: {user_label(message)}\n"
        f"🆔 Telegram ID: {message.from_user.id}\n"
        f"🌐 Til: {lang_text}\n\n"
        f"💳 Karta: {card_number}\n"
        f"👤 Karta egasi: {card_owner}\n\n"
        "📌 Cashback shartlari:\n"
        "- Rasmli + 5⭐ ijobiy sharh — 10 000 so'm\n"
        "- Rasmsiz + 5⭐ ijobiy so'zli sharh — 7 000 so'm\n"
        "- Faqat 5⭐ — 5 000 so'm\n\n"
        "Holat: ⏳ Tekshiruvda"
    )
    await bot.send_message(ADMIN_ID, text, reply_markup=cashback_admin_keyboard(request_id))


async def send_admin_complaint(complaint_id, message, lang, data):
    lang_text = "Русский" if lang == "ru" else "O'zbekcha"
    negative = data.get("negative_review", "no")
    negative_text = "Ha" if negative == "yes" else "Yo'q"
    if lang == "ru":
        negative_text = "Да" if negative == "yes" else "Нет"
    type_text = complaint_type_name(data.get("complaint_type", ""), lang)
    text = (
        "📝 Yangi shikoyat\n\n"
        f"🆔 Shikoyat ID: {complaint_id}\n"
        f"👤 Xaridor: {user_label(message)}\n"
        f"🆔 Telegram ID: {message.from_user.id}\n"
        f"🌐 Til: {lang_text}\n\n"
        f"📌 Shikoyat turi: {type_text}\n"
        f"⭐ Salbiy sharh qoldirganmi: {negative_text}\n"
        f"🖼 Salbiy sharh skrinshoti: {data.get('has_negative_screenshot', 'yoq')}\n"
        f"🖼 Muammo rasmi: {data.get('has_problem_image', 'yoq')}\n\n"
        f"💬 Shikoyat matni:\n{data.get('complaint_text', '')}\n\n"
        "Holat: ⏳ Ko'rib chiqilmoqda"
    )
    await bot.send_message(ADMIN_ID, text, reply_markup=complaint_admin_keyboard(complaint_id))


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    add_user(message.from_user)
    await message.answer(
        "🌐 Tilni tanlang / Выберите язык",
        reply_markup=lang_keyboard()
    )


@dp.callback_query(F.data.startswith("lang:"))
async def set_language(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = callback.data.split(":")[1]
    if lang not in ["uz", "ru"]:
        lang = "uz"
    add_user(callback.from_user, lang)
    set_user_language(callback.from_user.id, lang)
    if lang == "ru":
        await callback.message.answer(RU_START_TEXT, reply_markup=main_menu("ru"))
    else:
        await callback.message.answer(UZ_START_TEXT, reply_markup=main_menu("uz"))
    await callback.answer()


@dp.message(F.text.in_(["🌐 Tilni o'zgartirish", "🌐 Изменить язык"]))
async def change_language(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🌐 Tilni tanlang / Выберите язык",
        reply_markup=lang_keyboard()
    )


@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(f"Sizning Telegram ID: {message.from_user.id}")


# =========================
# CASHBACK
# =========================

@dp.message(F.text.in_(["💰 Cashback olish", "💰 Получить cashback"]))
async def cashback_start(message: Message, state: FSMContext):
    await state.clear()
    lang = get_user_language(message.from_user.id)

    # Kunlik limit tekshirish
    daily_count = get_daily_cashback_count(message.from_user.id)
    if daily_count >= CASHBACK_DAILY_LIMIT:
        await message.answer(
            f"⚠️ Siz bugun {CASHBACK_DAILY_LIMIT} ta cashback arizasi yubordingiz. Ertaga qayta urinib ko'ring."
            if lang == "uz"
            else f"⚠️ Вы сегодня отправили {CASHBACK_DAILY_LIMIT} заявки на cashback. Попробуйте завтра.",
            reply_markup=main_menu(lang)
        )
        return

    await message.answer(
        RU_CASHBACK_RULES if lang == "ru" else UZ_CASHBACK_RULES,
        reply_markup=main_menu(lang)
    )
    await state.set_state(CashbackState.waiting_screenshot)


@dp.message(CashbackState.waiting_screenshot)
async def cashback_screenshot(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    if not message.photo and not message.document:
        await message.answer(
            "Iltimos, 5⭐ sharh skrinshotini rasm yoki fayl ko'rinishida yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте скриншот отзыва 5⭐ в виде фото или файла."
        )
        return
    await state.update_data(screenshot_message_id=message.message_id)
    await message.answer(
        "2-qadam: Cashback tushadigan karta raqamingizni yuboring.\n\nMasalan:\n8600 1234 5678 9012"
        if lang == "uz"
        else "Шаг 2: Отправьте номер карты для получения cashback.\n\nНапример:\n8600 1234 5678 9012"
    )
    await state.set_state(CashbackState.waiting_card)


@dp.message(CashbackState.waiting_card)
async def cashback_card(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    card = message.text.strip()
    digits = "".join(ch for ch in card if ch.isdigit())
    if len(digits) < 16:
        await message.answer(
            "Karta raqami noto'g'ri ko'rinadi. Iltimos, 16 xonali karta raqamini yuboring."
            if lang == "uz"
            else "Номер карты выглядит неверно. Пожалуйста, отправьте 16-значный номер карты."
        )
        return
    await state.update_data(card_number=card)
    await message.answer(
        "3-qadam: Karta egasining ism-familiyasini yuboring.\n\nMasalan:\nKamolova Nigora"
        if lang == "uz"
        else "Шаг 3: Отправьте имя и фамилию владельца карты.\n\nНапример:\nKamolova Nigora"
    )
    await state.set_state(CashbackState.waiting_card_owner)


@dp.message(CashbackState.waiting_card_owner)
async def cashback_card_owner(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    data = await state.get_data()
    card_owner = message.text.strip()
    card_number = data.get("card_number", "")
    request_id = uuid.uuid4().hex[:8]

    save_cashback_request(request_id, message, lang, card_number, card_owner)

    await message.answer(
        "✅ Cashback so'rovingiz qabul qilindi.\n\nArizangiz admin tomonidan tekshiriladi."
        if lang == "uz"
        else "✅ Ваша заявка на cashback принята.\n\nЗаявка будет проверена администратором.",
        reply_markup=main_menu(lang)
    )

    if ADMIN_ID:
        try:
            await send_admin_cashback_request(request_id, message, lang, card_number, card_owner)
            screenshot_message_id = data.get("screenshot_message_id")
            if screenshot_message_id:
                await bot.forward_message(ADMIN_ID, message.chat.id, screenshot_message_id)
        except Exception as e:
            logger.error(f"Admin ga cashback yuborishda xato: {e}")

    await state.clear()


@dp.callback_query(F.data.startswith("cashback_approve:"))
async def cashback_approve(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    _, request_id, amount_text = callback.data.split(":")
    amount = int(amount_text)

    item = get_cashback_request(request_id)
    if not item:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return

    if item["status"] != "pending":
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    # Tasdiqlangan summani saqlash
    update_cashback_status(request_id, "approved", amount)

    lang = item["language"]
    amount_pretty = f"{amount:,}".replace(",", " ")

    try:
        await bot.send_message(
            item["telegram_id"],
            (
                "✅ Cashback so'rovingiz tasdiqlandi.\n\n"
                f"💰 Cashback miqdori: {amount_pretty} so'm\n"
                "Tez orada kartangizga o'tkazib beriladi."
            ) if lang == "uz" else (
                "✅ Ваша заявка на cashback одобрена.\n\n"
                f"💰 Сумма cashback: {amount_pretty} сум\n"
                "Скоро сумма будет переведена на вашу карту."
            ),
            reply_markup=main_menu(lang)
        )
    except Exception as e:
        logger.error(f"Foydalanuvchiga cashback tasdiqlandi xabarini yuborishda xato: {e}")

    await callback.message.answer(
        f"✅ Cashback tasdiqlandi: {amount_pretty} so'm\n\n"
        "Pul o'tkazilgandan keyin '💳 To'landi' tugmasini bosing.",
        reply_markup=cashback_paid_keyboard(request_id)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cashback_reject:"))
async def cashback_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    request_id = callback.data.split(":")[1]
    item = get_cashback_request(request_id)

    if not item:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return

    if item["status"] != "pending":
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    update_cashback_status(request_id, "rejected", 0)
    lang = item["language"]

    try:
        await bot.send_message(
            item["telegram_id"],
            "❌ Cashback so'rovingiz rad etildi." if lang == "uz" else "❌ Ваша заявка на cashback отклонена.",
            reply_markup=main_menu(lang)
        )
    except Exception as e:
        logger.error(f"Cashback rad xabarini yuborishda xato: {e}")

    await callback.message.answer("❌ Cashback arizasi rad etildi.")
    await callback.answer()


@dp.callback_query(F.data.startswith("cashback_paid:"))
async def cashback_paid(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    request_id = callback.data.split(":")[1]
    item = get_cashback_request(request_id)

    if not item:
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return

    if item["status"] == "paid":
        await callback.answer("Bu ariza allaqachon to'langan.", show_alert=True)
        return

    # Avval chek so'raymiz
    await state.update_data(cashback_request_id=request_id)
    await callback.message.answer("📸 Endi chek rasmini yuboring:")
    await state.set_state(AdminReplyState.waiting_check_photo)
    await callback.answer()


@dp.message(AdminReplyState.waiting_check_photo)
async def admin_check_photo(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if not message.photo and not message.document:
        await message.answer("Iltimos, chek rasmini yuboring (rasm ko'rinishida).")
        return

    data = await state.get_data()

    # Ikki xil holat:
    # 1) "To'landi" tugmasi bosilganda — cashback_request_id bor
    # 2) Xaridor chek so'raganda — check_target_telegram_id bor

    cashback_request_id = data.get("cashback_request_id")
    check_target_telegram_id = data.get("check_target_telegram_id")

    if cashback_request_id:
        # "To'landi" tugmasidan kelgan — DB dan ma'lumot olib status yangilanadi
        item = get_cashback_request(cashback_request_id)
        if not item:
            await message.answer("Ariza topilmadi.")
            await state.clear()
            return

        update_cashback_status(cashback_request_id, "paid", item["amount"])
        target_id = item["telegram_id"]
        lang = item["language"]

        try:
            await bot.send_message(
                target_id,
                "💳 Cashback to'lovingiz amalga oshirildi.\n\nQuyida to'lov cheki:"
                if lang == "uz"
                else "💳 Cashback выплачен.\n\nНиже чек об оплате:",
                reply_markup=cashback_paid_menu(lang)
            )
            if message.photo:
                await bot.send_photo(target_id, message.photo[-1].file_id)
            elif message.document:
                await bot.send_document(target_id, message.document.file_id)
        except Exception as e:
            logger.error(f"Chekni foydalanuvchiga yuborishda xato: {e}")

    elif check_target_telegram_id:
        # Xaridor chek so'raganda — faqat chekni yuboramiz
        lang = get_user_language(check_target_telegram_id)

        try:
            await bot.send_message(
                check_target_telegram_id,
                "🧾 So'ragan chekingiz:"
                if lang == "uz"
                else "🧾 Запрошенный вами чек:",
                reply_markup=main_menu(lang)
            )
            if message.photo:
                await bot.send_photo(check_target_telegram_id, message.photo[-1].file_id)
            elif message.document:
                await bot.send_document(check_target_telegram_id, message.document.file_id)
        except Exception as e:
            logger.error(f"Chek so'roviga javob yuborishda xato: {e}")

    else:
        await message.answer("Ma'lumot topilmadi.")
        await state.clear()
        return

    await message.answer("✅ Chek xaridorga yuborildi.")
    await state.clear()


# =========================
# CHEK SO'RASH (XARIDOR)
# =========================

@dp.message(F.text.in_(["🧾 Chek so'rash", "🧾 Запросить чек"]))
async def request_check(message: Message):
    lang = get_user_language(message.from_user.id)
    telegram_id = message.from_user.id

    # Oxirgi to'langan cashback arizasini topamiz
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """SELECT request_id FROM cashback_requests
           WHERE telegram_id = ? AND status = 'paid'
           ORDER BY updated_at DESC LIMIT 1""",
        (telegram_id,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        await message.answer(
            "❌ To'langan cashback arizasi topilmadi."
            if lang == "uz"
            else "❌ Оплаченная заявка на cashback не найдена.",
            reply_markup=main_menu(lang)
        )
        return

    request_id = row[0]
    pending_check_requests[telegram_id] = request_id

    if ADMIN_ID:
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
            await bot.send_message(
                ADMIN_ID,
                f"🧾 Xaridor chek so'ramoqda!\n\n"
                f"👤 Xaridor: {username}\n"
                f"🆔 Telegram ID: {telegram_id}\n"
                f"🆔 Ariza ID: {request_id}\n\n"
                "Chek rasmini yuborish uchun /chek_yuborish komandasi yozing yoki quyidagi tugmani bosing.",
                reply_markup=send_check_keyboard(telegram_id, request_id)
            )
        except Exception as e:
            logger.error(f"Admin ga chek so'rovi yuborishda xato: {e}")

    await message.answer(
        "✅ Chek so'rovingiz adminga yuborildi. Tez orada chek yuboriladi."
        if lang == "uz"
        else "✅ Запрос на чек отправлен администратору. Чек будет выслан в ближайшее время.",
        reply_markup=main_menu(lang)
    )


def send_check_keyboard(telegram_id, request_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="📸 Chek yuborish", callback_data=f"send_check:{telegram_id}:{request_id}")
    return kb.as_markup()


@dp.callback_query(F.data.startswith("send_check:"))
async def send_check_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    parts = callback.data.split(":")
    target_telegram_id = int(parts[1])
    request_id = parts[2]

    await state.update_data(
        check_target_telegram_id=target_telegram_id,
        check_request_id=request_id
    )
    await callback.message.answer("📸 Chek rasmini yuboring:")
    await state.set_state(AdminReplyState.waiting_check_photo)
    await callback.answer()


# =========================
# COMPLAINTS
# =========================

@dp.message(F.text.in_(["📝 Shikoyat qoldirish", "📝 Оставить жалобу"]))
async def complaint_start(message: Message, state: FSMContext):
    await state.clear()
    lang = get_user_language(message.from_user.id)
    await message.answer(
        "📝 Shikoyat qoldirish\n\nIltimos, murojaat turini tanlang:"
        if lang == "uz"
        else "📝 Оставить жалобу\n\nПожалуйста, выберите тип обращения:",
        reply_markup=complaint_type_keyboard(lang)
    )


@dp.callback_query(F.data.startswith("complaint_type:"))
async def complaint_type_selected(callback: CallbackQuery, state: FSMContext):
    lang = get_user_language(callback.from_user.id)
    complaint_type = callback.data.split(":")[1]

    if complaint_type == "delivery":
        await callback.message.answer(
            RU_DELIVERY_TEXT if lang == "ru" else UZ_DELIVERY_TEXT,
            reply_markup=main_menu(lang)
        )
        await callback.answer()
        return

    await state.update_data(
        complaint_type=complaint_type,
        complaint_type_name=complaint_type_name(complaint_type, lang)
    )

    await callback.message.answer(
        "Siz ushbu muammo bo'yicha Uzum'da salbiy sharh qoldirganmisiz?"
        if lang == "uz"
        else "Вы уже оставляли негативный отзыв по этой проблеме в Uzum?",
        reply_markup=negative_review_keyboard(lang)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("negative_review:"))
async def negative_review_selected(callback: CallbackQuery, state: FSMContext):
    lang = get_user_language(callback.from_user.id)
    answer = callback.data.split(":")[1]
    await state.update_data(negative_review=answer)

    if answer == "yes":
        await callback.message.answer(
            "Iltimos, Uzum'da qoldirgan salbiy sharhingiz skrinshotini yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте скриншот негативного отзыва, который вы оставили в Uzum."
        )
        await state.set_state(ComplaintState.waiting_negative_screenshot)
    else:
        await callback.message.answer(RU_COMPLAINT_PRE_TEXT if lang == "ru" else UZ_COMPLAINT_PRE_TEXT)
        await state.set_state(ComplaintState.waiting_text)

    await callback.answer()


@dp.message(ComplaintState.waiting_negative_screenshot)
async def negative_screenshot(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    if not message.photo and not message.document:
        await message.answer(
            "Iltimos, salbiy sharh skrinshotini rasm yoki fayl ko'rinishida yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте скриншот негативного отзыва в виде фото или файла."
        )
        return
    await state.update_data(
        negative_screenshot_message_id=message.message_id,
        has_negative_screenshot="bor"
    )
    await message.answer(RU_COMPLAINT_PRE_TEXT if lang == "ru" else UZ_COMPLAINT_PRE_TEXT)
    await state.set_state(ComplaintState.waiting_text)


@dp.message(ComplaintState.waiting_text)
async def complaint_text(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    await state.update_data(complaint_text=message.text.strip())
    await message.answer(
        "Agar muammoga oid rasm yoki skrinshot bo'lsa, yuboring.\n\nAgar rasm yo'q bo'lsa, '⏭ O'tkazib yuborish' tugmasini bosing."
        if lang == "uz"
        else "Если есть фото или скриншот по проблеме, отправьте его.\n\nЕсли фото нет, нажмите '⏭ Пропустить'.",
        reply_markup=skip_keyboard(lang)
    )
    await state.set_state(ComplaintState.waiting_problem_image)


@dp.message(ComplaintState.waiting_problem_image)
async def complaint_problem_image(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)

    skip_text = "⏭ O'tkazib yuborish" if lang == "uz" else "⏭ Пропустить"
    has_problem_image = "yoq"
    problem_image_message_id = None

    if message.text == skip_text:
        has_problem_image = "yoq"
    elif message.photo or message.document:
        has_problem_image = "bor"
        problem_image_message_id = message.message_id
    else:
        await message.answer(
            "Rasm/skrinshot yuboring yoki '⏭ O'tkazib yuborish' tugmasini bosing."
            if lang == "uz"
            else "Отправьте фото/скриншот или нажмите '⏭ Пропустить'."
        )
        return

    await state.update_data(
        has_problem_image=has_problem_image,
        problem_image_message_id=problem_image_message_id
    )

    final_data = await state.get_data()
    complaint_id = uuid.uuid4().hex[:8]

    save_complaint(complaint_id, message, lang, final_data)

    await message.answer(
        "✅ Shikoyatingiz qabul qilindi.\n\nMurojaatingiz admin tomonidan ko'rib chiqiladi. Javob shu bot orqali yuboriladi.\n\nRahmat!"
        if lang == "uz"
        else "✅ Ваша жалоба принята.\n\nОбращение будет рассмотрено администратором. Ответ придёт в этом боте.\n\nСпасибо!",
        reply_markup=main_menu(lang)
    )

    if ADMIN_ID:
        try:
            await send_admin_complaint(complaint_id, message, lang, final_data)
            negative_message_id = final_data.get("negative_screenshot_message_id")
            if negative_message_id:
                await bot.forward_message(ADMIN_ID, message.chat.id, negative_message_id)
            if problem_image_message_id:
                await bot.forward_message(ADMIN_ID, message.chat.id, problem_image_message_id)
        except Exception as e:
            logger.error(f"Admin ga shikoyat yuborishda xato: {e}")

    await state.clear()


# =========================
# ADMIN COMPLAINT ACTIONS
# =========================

@dp.callback_query(F.data.startswith("complaint_reply:"))
async def complaint_reply(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer("✉️ Xaridorga yuboriladigan javobni yozing:")
    await state.set_state(AdminReplyState.waiting_custom_reply)
    await callback.answer()


@dp.message(AdminReplyState.waiting_custom_reply)
async def admin_custom_reply(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    complaint_id = data.get("complaint_id")
    item = get_complaint(complaint_id)

    if not item:
        await message.answer("Shikoyat topilmadi.")
        await state.clear()
        return

    reply_text = message.text.strip()
    lang = item["language"]

    try:
        await bot.send_message(
            item["telegram_id"],
            (
                "✉️ Shikoyatingiz bo'yicha admin javobi:\n\n"
                f"{reply_text}"
            ) if lang == "uz" else (
                "✉️ Ответ администратора по вашей жалобе:\n\n"
                f"{reply_text}"
            ),
            reply_markup=main_menu(lang)
        )
    except Exception as e:
        logger.error(f"Admin javobini yuborishda xato: {e}")

    update_complaint_status(complaint_id, "answered", reply_text)
    await message.answer("✅ Javob xaridorga yuborildi.")
    await state.clear()


@dp.callback_query(F.data.startswith("complaint_done:"))
async def complaint_done(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    lang = item["language"]

    try:
        await bot.send_message(
            item["telegram_id"],
            "✅ Shikoyatingiz ko'rib chiqildi.\n\nMurojaatingiz uchun rahmat."
            if lang == "uz"
            else "✅ Ваша жалоба рассмотрена.\n\nСпасибо за обращение.",
            reply_markup=main_menu(lang)
        )
    except Exception as e:
        logger.error(f"Complaint done xabarini yuborishda xato: {e}")

    update_complaint_status(complaint_id, "done")
    await callback.message.answer("✅ Shikoyat ko'rib chiqildi deb belgilandi.")
    await callback.answer()


@dp.callback_query(F.data.startswith("complaint_reject:"))
async def complaint_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    lang = item["language"]

    try:
        await bot.send_message(
            item["telegram_id"],
            "❌ Shikoyatingiz rad etildi.\n\nAgar qo'shimcha ma'lumot bo'lsa, qayta murojaat qoldirishingiz mumkin."
            if lang == "uz"
            else "❌ Ваша жалоба отклонена.\n\nЕсли есть дополнительная информация, вы можете оставить обращение повторно.",
            reply_markup=main_menu(lang)
        )
    except Exception as e:
        logger.error(f"Complaint reject xabarini yuborishda xato: {e}")

    update_complaint_status(complaint_id, "rejected")
    await callback.message.answer("❌ Shikoyat rad etildi.")
    await callback.answer()


# =========================
# ADMIN TEMPLATES
# =========================

@dp.callback_query(F.data.startswith("complaint_templates:"))
async def complaint_templates(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    await callback.message.answer(
        "📋 Shablon javobni tanlang:",
        reply_markup=template_list_keyboard(complaint_id)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("tpl_choose:"))
async def template_choose(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    _, complaint_id, template_key = callback.data.split(":")
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    preferred_lang = item["language"]

    
        await callback.message.answer(
    "Xaridor tili: " + ("🇷🇺 Русский" if preferred_lang == "ru" else "🇺🇿 O'zbekcha") + "\n\n"
    "Qaysi tilda yuboramiz?",
    reply_markup=template_lang_keyboard(complaint_id, template_key, preferred_lang)
)
    await callback.answer()


@dp.callback_query(F.data.startswith("tpl_lang:"))
async def template_lang(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    _, complaint_id, template_key, lang = callback.data.split(":")
    item = get_complaint(complaint_id)

    if not item:
        await callback.answer("Shikoyat topilmadi.", show_alert=True)
        return

    text = ADMIN_TEMPLATES[template_key][lang]
    pending_admin_replies[complaint_id] = {"text": text, "lang": lang}

    await callback.message.answer(
        f"Shu javobni yuboramizmi?\n\n{text}",
        reply_markup=send_template_keyboard(complaint_id)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("tpl_send:"))
async def template_send(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]
    item = get_complaint(complaint_id)
    reply = pending_admin_replies.pop(complaint_id, None)

    if not item or not reply:
        await callback.answer("Ma'lumot topilmadi.", show_alert=True)
        return

    user_lang = item["language"]
    text = reply["text"]

    try:
        await bot.send_message(
            item["telegram_id"],
            (
                "✉️ Shikoyatingiz bo'yicha admin javobi:\n\n"
                f"{text}"
            ) if user_lang == "uz" else (
                "✉️ Ответ администратора по вашей жалобе:\n\n"
                f"{text}"
            ),
            reply_markup=main_menu(user_lang)
        )
    except Exception as e:
        logger.error(f"Shablon javobni yuborishda xato: {e}")

    update_complaint_status(complaint_id, "answered", text)
    await callback.message.answer("✅ Shablon javob xaridorga yuborildi.")
    await callback.answer()


@dp.callback_query(F.data.startswith("tpl_edit:"))
async def template_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    complaint_id = callback.data.split(":")[1]

    if complaint_id not in pending_admin_replies:
        await callback.answer("Shablon topilmadi.", show_alert=True)
        return

    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer("✏️ Tahrirlangan javob matnini yuboring:")
    await state.set_state(AdminReplyState.waiting_template_edit)
    await callback.answer()


@dp.message(AdminReplyState.waiting_template_edit)
async def template_edit_text(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    complaint_id = data.get("complaint_id")

    if complaint_id not in pending_admin_replies:
        await message.answer("Shablon topilmadi.")
        await state.clear()
        return

    pending_admin_replies[complaint_id]["text"] = message.text.strip()

    await message.answer(
        f"Shu javobni yuboramizmi?\n\n{message.text.strip()}",
        reply_markup=send_template_keyboard(complaint_id)
    )
    await state.clear()


# =========================
# ADMIN PANEL
# =========================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Siz admin emassiz.")
        return

    stats = get_report_stats()
    await message.answer(format_report(stats))


@dp.message(Command("hisobot"))
async def hisobot_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Siz admin emassiz.")
        return

    stats = get_report_stats()
    await message.answer(format_report(stats))


# =========================
# AVTOMATIK KUNLIK HISOBOT
# =========================

async def send_daily_report():
    if not ADMIN_ID:
        return
    try:
        stats = get_report_stats()
        report_text = "🕗 Kunlik avtomatik hisobot\n\n" + format_report(stats)
        await bot.send_message(ADMIN_ID, report_text)
        logger.info("Kunlik hisobot yuborildi.")
    except Exception as e:
        logger.error(f"Kunlik hisobotni yuborishda xato: {e}")


# =========================
# RUN
# =========================

async def main():
    init_db()

    scheduler = AsyncIOScheduler(timezone=str(UZ_TZ))
    scheduler.add_job(send_daily_report, "cron", hour=20, minute=0)
    scheduler.start()

    logger.info("Uzum Cashback Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
async def main():
    init_db()

    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    scheduler.add_job(send_daily_report, "cron", hour=20, minute=0)
    scheduler.start()

    logger.info("Uzum Cashback Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
