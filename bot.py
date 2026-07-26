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
CASHBACK_3000 = int(os.getenv("CASHBACK_3000", "3000"))

PRODUCTS = {
    "pods_pro_2": {"uz": "🎧 Pods Pro 2", "ru": "🎧 Pods Pro 2", "options": {"photo_text": 10000, "text_only": 7000, "stars_only": 5000}},
    "pods_pro_3": {"uz": "🎧 Pods Pro 3", "ru": "🎧 Pods Pro 3", "options": {"photo_text": 10000, "text_only": 7000, "stars_only": 5000}},
    "smart_watch": {"uz": "⌚ Smart Watch", "ru": "⌚ Smart Watch", "options": {"photo_text": 10000, "text_only": 7000, "stars_only": 5000}},
    "charger_20w_2pin": {"uz": "🔌 20W 2-pin zaryadlovchi", "ru": "🔌 Зарядное устройство 20W, 2-pin", "options": {"photo_text": 5000, "text_only": 3000}},
    "charger_20w_3pin": {"uz": "🔌 20W 3-pin zaryadlovchi", "ru": "🔌 Зарядное устройство 20W, 3-pin", "options": {"photo_text": 5000, "text_only": 3000}},
    "charger_25w": {"uz": "🔌 25W zaryadlovchi", "ru": "🔌 Зарядное устройство 25W", "options": {"photo_text": 5000, "text_only": 3000}},
    "samsung_charger": {"uz": "🔌 Samsung zaryadlovchi", "ru": "🔌 Зарядное устройство Samsung", "options": {"text_only": 3000}},
    "charger_20w_3pin_set": {"uz": "🔌 20W 3-pin to'plam: galovka + kabel", "ru": "🔌 Комплект 20W 3-pin: блок + кабель", "options": {"photo_text": 10000, "text_only": 7000, "stars_only": 5000}},
    "charger_35w_typec": {"uz": "🔌 35W Type-C → Type-C zaryadlovchi", "ru": "🔌 Зарядное устройство 35W Type-C → Type-C", "options": {"photo_text": 5000, "text_only": 3000}},
}

REVIEW_LABELS = {
    "photo_text": {"uz": "5⭐ + matnli sharh + rasm", "ru": "5⭐ + текстовый отзыв + фото"},
    "text_only": {"uz": "5⭐ + matnli sharh, rasmsiz", "ru": "5⭐ + текстовый отзыв без фото"},
    "stars_only": {"uz": "faqat 5⭐", "ru": "только 5⭐"},
}

# Bir kunda bir xaridor necha marta cashback so'rashi mumkin
CASHBACK_DAILY_LIMIT = int(os.getenv("CASHBACK_DAILY_LIMIT", "3"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi.")

GUIDE_PHOTO_1 = "AgACAgIAAxkBAAIDMWo4_9ZToYgCec7qEF_Z-plLM0utAAKDGGsbR4DISfKxBgpz39MsAQADAgADeQADPAQ"
GUIDE_PHOTO_2 = "AgACAgIAAxkBAAIDM2o4_-KOnd6p4SsOp7OIIscJmwZpAAKEGGsbR4DISfITu_KkA6kFAQADAgADeQADPAQ"

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

    # Cashback tizimining yangi ustunlari. Eski baza ham saqlanib qoladi.
    cur.execute("PRAGMA table_info(cashback_requests)")
    existing_columns = {row[1] for row in cur.fetchall()}
    new_columns = {
        "product_key": "TEXT",
        "product_name": "TEXT",
        "review_type": "TEXT",
        "requested_amount": "INTEGER DEFAULT 0",
        "screenshot_file_id": "TEXT",
        "screenshot_unique_id": "TEXT",
        "admin_chat_id": "INTEGER",
        "admin_message_id": "INTEGER",
        "close_reason": "TEXT",
        "check_requested": "INTEGER DEFAULT 0",
    }
    for column, column_type in new_columns.items():
        if column not in existing_columns:
            cur.execute(f"ALTER TABLE cashback_requests ADD COLUMN {column} {column_type}")

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
    waiting_free_reply = State()
    waiting_cashback_reply = State()


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
    kb.button(text="✉️ Xabar yozish", callback_data=f"cashback_reply:{request_id}")
    kb.button(text="📸 Skrinshot noto'g'ri", callback_data=f"cashback_bad_screenshot:{request_id}")
    kb.button(text="🛍 Bizning do'konga tegishli emas", callback_data=f"cashback_wrong_shop:{request_id}")
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
# CASHBACK — YANGI TIZIM
# =========================

def money(amount):
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def products_keyboard(lang="uz"):
    kb = InlineKeyboardBuilder()
    for key, product in PRODUCTS.items():
        kb.button(text=product[lang], callback_data=f"cash_product:{key}")
    kb.adjust(1)
    return kb.as_markup()


def product_rules_text(product_key, lang):
    product = PRODUCTS[product_key]
    lines = [f"💰 {product[lang]}", ""]
    for review_type, amount in product["options"].items():
        lines.append(f"• {money(amount)} — {REVIEW_LABELS[review_type][lang]}")
    if lang == "ru":
        lines += [
            "", "📌 Отзыв должен быть опубликован.",
            "📸 Отправьте скриншот из раздела «Оставленные отзывы».",
            "📅 Заявку нужно отправить в течение 3 дней после публикации.",
            "⚠️ За один отзыв cashback выплачивается только один раз.",
        ]
    else:
        lines += [
            "", "📌 Sharh publikatsiya qilingan bo'lishi kerak.",
            "📸 «Qoldirilgan sharhlar» bo'limidan skrinshot yuboring.",
            "📅 Ariza publikatsiyadan keyin 3 kun ichida yuborilishi kerak.",
            "⚠️ Har bir sharh uchun cashback faqat bir marta beriladi.",
        ]
    return "\n".join(lines)


def rules_keyboard(product_key, lang):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я согласен с условиями" if lang == "ru" else "✅ Shartlarga roziman", callback_data=f"cash_accept:{product_key}")
    kb.button(text="📸 Как сделать скриншот?" if lang == "ru" else "📸 Skrinshotni qanday olish?", callback_data="cash_guide")
    kb.button(text="⬅️ Другой товар" if lang == "ru" else "⬅️ Boshqa mahsulot", callback_data="cash_products")
    kb.adjust(1)
    return kb.as_markup()


def review_type_keyboard(product_key, lang):
    kb = InlineKeyboardBuilder()
    for review_type, amount in PRODUCTS[product_key]["options"].items():
        kb.button(text=f"{REVIEW_LABELS[review_type][lang]} — {money(amount)}", callback_data=f"cash_review:{product_key}:{review_type}")
    kb.adjust(1)
    return kb.as_markup()


def get_cashback_request_new(request_id):
    conn = db(); cur = conn.cursor()
    cur.execute("PRAGMA table_info(cashback_requests)")
    cols = [r[1] for r in cur.fetchall()]
    cur.execute("SELECT * FROM cashback_requests WHERE request_id = ?", (request_id,))
    row = cur.fetchone(); conn.close()
    return dict(zip(cols, row)) if row else None


def update_cashback_new(request_id, **fields):
    fields["updated_at"] = now_text()
    sql = ", ".join(f"{k} = ?" for k in fields)
    conn = db(); conn.execute(f"UPDATE cashback_requests SET {sql} WHERE request_id = ?", (*fields.values(), request_id)); conn.commit(); conn.close()


def admin_cashback_caption(item):
    status_map = {"pending": "🟠 Tekshiruvda", "approved": "🟡 To'lov kutilmoqda", "paid": "🟢 Yakunlandi", "rejected": "🔴 Yopildi"}
    review = REVIEW_LABELS.get(item.get("review_type"), {}).get(item.get("language", "uz"), "-")
    extra = ""
    if item.get("status") in ["approved", "paid"]:
        extra += f"\n💵 Tasdiqlangan summa: {money(item.get('amount') or 0)}"
    if item.get("close_reason"):
        extra += f"\n📌 Sabab: {item['close_reason']}"
    username = f"@{item['username']}" if item.get("username") else "username yo'q"
    return (
        f"💰 Cashback arizasi\n\n"
        f"🆔 {item['request_id']}\n"
        f"📦 Mahsulot: {item.get('product_name') or '-'}\n"
        f"📝 Sharh turi: {review}\n"
        f"💰 So'ralgan summa: {money(item.get('requested_amount') or 0)}\n"
        f"👤 Ism: {item.get('full_name') or '-'}\n"
        f"📱 Telegram: {username}\n"
        f"💳 Karta: {item.get('card_number') or '-'}\n"
        f"👤 Karta egasi: {item.get('card_owner') or '-'}\n"
        f"📅 Sana: {item.get('created_at') or '-'}\n"
        f"📍 Holat: {status_map.get(item.get('status'), item.get('status'))}{extra}"
    )


def cashback_admin_keyboard_new(item):
    kb = InlineKeyboardBuilder()
    amounts = sorted(set(PRODUCTS[item["product_key"]]["options"].values()), reverse=True)
    for amount in amounts:
        kb.button(text=f"✅ {money(amount)}", callback_data=f"cash_approve:{item['request_id']}:{amount}")
    kb.button(text="🛍 Do'konga tegishli emas", callback_data=f"cash_close:{item['request_id']}:wrong_shop")
    kb.button(text="⏳ Publikatsiyadan keyin yuboring", callback_data=f"cash_close:{item['request_id']}:not_published")
    kb.button(text="📸 Skrinshot noto'g'ri", callback_data=f"cash_close:{item['request_id']}:bad_screenshot")
    kb.button(text="🔁 Avval cashback olingan", callback_data=f"cash_close:{item['request_id']}:duplicate")
    kb.button(text="📅 3 kunlik muddat o'tgan", callback_data=f"cash_close:{item['request_id']}:expired")
    kb.button(text="✉️ Xabar yozish", callback_data=f"cashback_reply:{item['request_id']}")
    kb.button(text="❌ Rad etish", callback_data=f"cash_close:{item['request_id']}:rejected")
    kb.adjust(1)
    return kb.as_markup()


def cashback_approved_keyboard_new(request_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 To'landi", callback_data=f"cash_paid:{request_id}")
    kb.button(text="✉️ Xabar yozish", callback_data=f"cashback_reply:{request_id}")
    kb.adjust(1)
    return kb.as_markup()


async def edit_admin_cashback_card(item, markup=None):
    try:
        await bot.edit_message_caption(chat_id=item["admin_chat_id"], message_id=item["admin_message_id"], caption=admin_cashback_caption(item), reply_markup=markup)
    except Exception as e:
        logger.error(f"Admin cashback xabarini tahrirlashda xato: {e}")


@dp.message(F.text.in_(["💰 Cashback olish", "💰 Получить cashback"]))
async def cashback_start(message: Message, state: FSMContext):
    await state.clear()
    lang = get_user_language(message.from_user.id)
    if get_daily_cashback_count(message.from_user.id) >= CASHBACK_DAILY_LIMIT:
        await message.answer(f"⚠️ Siz bugun {CASHBACK_DAILY_LIMIT} ta cashback arizasi yubordingiz. Ertaga qayta urinib ko'ring." if lang == "uz" else f"⚠️ Вы сегодня отправили {CASHBACK_DAILY_LIMIT} заявки. Попробуйте завтра.", reply_markup=main_menu(lang))
        return
    await message.answer("Mahsulotni tanlang:" if lang == "uz" else "Выберите товар:", reply_markup=products_keyboard(lang))


@dp.callback_query(F.data == "cash_products")
async def cash_products(callback: CallbackQuery, state: FSMContext):
    await state.clear(); lang = get_user_language(callback.from_user.id)
    await callback.message.edit_text("Mahsulotni tanlang:" if lang == "uz" else "Выберите товар:", reply_markup=products_keyboard(lang)); await callback.answer()


@dp.callback_query(F.data.startswith("cash_product:"))
async def cash_product(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[1]; lang = get_user_language(callback.from_user.id)
    await state.update_data(product_key=key)
    await callback.message.edit_text(product_rules_text(key, lang), reply_markup=rules_keyboard(key, lang)); await callback.answer()


@dp.callback_query(F.data == "cash_guide")
async def cash_guide(callback: CallbackQuery):
    lang = get_user_language(callback.from_user.id)
    guide_text = (
        "📸 Как сделать скриншот отзыва?\n\n1️⃣ Откройте приложение Uzum\n2️⃣ Перейдите в Профиль → Мои отзывы\n3️⃣ Дождитесь публикации\n4️⃣ Откройте раздел Оставленные отзывы\n5️⃣ Сделайте скриншот, где видны товар, 5⭐, текст и фото отзыва\n6️⃣ Отправьте его в течение 3 дней"
        if lang == "ru" else
        "📸 Sharh skrinshotini qanday olish kerak?\n\n1️⃣ Uzum ilovasini oching\n2️⃣ Profil → Sharhlarim ga kiring\n3️⃣ Sharh publikatsiya bo'lishini kuting\n4️⃣ Qoldirilgan sharhlar bo'limiga o'ting\n5️⃣ Mahsulot, 5⭐, matn va rasm ko'ringan sahifadan skrinshot oling\n6️⃣ Uni 3 kun ichida yuboring"
    )
    try:
        await bot.send_photo(callback.message.chat.id, GUIDE_PHOTO_1)
        await bot.send_photo(callback.message.chat.id, GUIDE_PHOTO_2, caption=guide_text)
    except Exception:
        await callback.message.answer(guide_text)
    await callback.answer()


@dp.callback_query(F.data.startswith("cash_accept:"))
async def cash_accept(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[1]; lang = get_user_language(callback.from_user.id)
    await state.update_data(product_key=key)
    await callback.message.edit_text("Sharh turini tanlang:" if lang == "uz" else "Выберите тип отзыва:", reply_markup=review_type_keyboard(key, lang)); await callback.answer()


@dp.callback_query(F.data.startswith("cash_review:"))
async def cash_review(callback: CallbackQuery, state: FSMContext):
    _, key, review_type = callback.data.split(":"); lang = get_user_language(callback.from_user.id)
    await state.update_data(product_key=key, review_type=review_type)
    await callback.message.edit_text("📸 Endi «Qoldirilgan sharhlar» bo'limidan skrinshot yuboring." if lang == "uz" else "📸 Теперь отправьте скриншот из раздела «Оставленные отзывы».")
    await state.set_state(CashbackState.waiting_screenshot); await callback.answer()


@dp.message(CashbackState.waiting_screenshot)
async def cashback_screenshot(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id)
    if message.photo:
        file_id = message.photo[-1].file_id; unique_id = message.photo[-1].file_unique_id
    elif message.document:
        file_id = message.document.file_id; unique_id = message.document.file_unique_id
    else:
        await message.answer("Iltimos, skrinshotni rasm yoki fayl ko'rinishida yuboring." if lang == "uz" else "Отправьте скриншот как фото или файл."); return
    await state.update_data(screenshot_file_id=file_id, screenshot_unique_id=unique_id)
    await message.answer("2-qadam: Karta raqamingizni yuboring.\nMasalan: 8600 1234 5678 9012" if lang == "uz" else "Шаг 2: Отправьте номер карты.")
    await state.set_state(CashbackState.waiting_card)


@dp.message(CashbackState.waiting_card)
async def cashback_card(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id); digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if len(digits) != 16:
        await message.answer("Karta raqami 16 ta raqamdan iborat bo'lishi kerak." if lang == "uz" else "Номер карты должен состоять из 16 цифр."); return
    card = " ".join(digits[i:i+4] for i in range(0,16,4)); await state.update_data(card_number=card)
    await message.answer("3-qadam: Karta egasining ism-familiyasini yuboring:" if lang == "uz" else "Шаг 3: Отправьте имя владельца карты:")
    await state.set_state(CashbackState.waiting_card_owner)


@dp.message(CashbackState.waiting_card_owner)
async def cashback_owner(message: Message, state: FSMContext):
    lang = get_user_language(message.from_user.id); data = await state.get_data(); request_id = uuid.uuid4().hex[:10]
    key=data["product_key"]; review=data["review_type"]; amount=PRODUCTS[key]["options"][review]
    conn=db(); cur=conn.cursor()
    cur.execute("""INSERT INTO cashback_requests (request_id,telegram_id,username,full_name,language,card_number,card_owner,status,amount,created_at,updated_at,product_key,product_name,review_type,requested_amount,screenshot_file_id,screenshot_unique_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (request_id,message.from_user.id,message.from_user.username or "",message.from_user.full_name or "",lang,data["card_number"],message.text.strip(),"pending",0,now_text(),now_text(),key,PRODUCTS[key][lang],review,amount,data["screenshot_file_id"],data["screenshot_unique_id"]))
    conn.commit(); conn.close(); item=get_cashback_request_new(request_id)
    sent=await bot.send_photo(ADMIN_ID,item["screenshot_file_id"],caption=admin_cashback_caption(item),reply_markup=cashback_admin_keyboard_new(item))
    update_cashback_new(request_id,admin_chat_id=sent.chat.id,admin_message_id=sent.message_id)
    await message.answer("✅ Cashback arizangiz qabul qilindi. Admin 24 soat ichida ko'rib chiqadi." if lang == "uz" else "✅ Заявка принята. Администратор рассмотрит её в течение 24 часов.",reply_markup=main_menu(lang))
    await state.clear()


@dp.callback_query(F.data.startswith("cash_approve:"))
async def cash_approve(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: await callback.answer("Ruxsat yo'q.",show_alert=True); return
    _,request_id,raw=callback.data.split(":"); amount=int(raw); item=get_cashback_request_new(request_id)
    if not item or item["status"]!="pending": await callback.answer("Ariza allaqachon ko'rib chiqilgan.",show_alert=True); return
    update_cashback_new(request_id,status="approved",amount=amount); item=get_cashback_request_new(request_id)
    await edit_admin_cashback_card(item,cashback_approved_keyboard_new(request_id))
    lang=item["language"]; review=REVIEW_LABELS[item["review_type"]][lang]
    await bot.send_message(item["telegram_id"],f"✅ Cashback arizangiz tasdiqlandi.\n\n{review} uchun {money(amount)} tasdiqlandi.\nTo'lov 24 soat ichida amalga oshiriladi." if lang=="uz" else f"✅ Заявка одобрена.\n\nЗа {review} одобрено {money(amount)}.\nВыплата будет произведена в течение 24 часов.")
    await callback.answer("Tasdiqlandi.")


CLOSE_LABELS={"wrong_shop":"Do'konga tegishli emas","not_published":"Sharh publikatsiya qilinmagan","bad_screenshot":"Skrinshot noto'g'ri","duplicate":"Avval cashback olingan","expired":"3 kunlik muddat o'tgan","rejected":"Rad etildi"}

@dp.callback_query(F.data.startswith("cash_close:"))
async def cash_close(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: await callback.answer("Ruxsat yo'q.",show_alert=True); return
    _,request_id,reason=callback.data.split(":"); item=get_cashback_request_new(request_id)
    if not item or item["status"] in ["paid","rejected"]: await callback.answer("Ariza yakunlangan.",show_alert=True); return
    update_cashback_new(request_id,status="rejected",amount=0,close_reason=CLOSE_LABELS[reason]); item=get_cashback_request_new(request_id); await edit_admin_cashback_card(item,None)
    uz={"wrong_shop":"❌ Sharh bizning do'konimiz mahsulotiga tegishli emas.","not_published":"⏳ Sharh publikatsiya bo'lgandan keyin yangi ariza yuboring.","bad_screenshot":"📸 Skrinshot noto'g'ri. To'g'ri skrinshot bilan yangi ariza yuboring.","duplicate":"🔁 Ushbu sharh uchun cashback avval berilgan.","expired":"📅 Sharh publikatsiyasidan keyin 3 kunlik muddat o'tgan.","rejected":"❌ Cashback arizangiz rad etildi."}
    ru={"wrong_shop":"❌ Отзыв относится не к товару нашего магазина.","not_published":"⏳ Отправьте новую заявку после публикации отзыва.","bad_screenshot":"📸 Скриншот неверный. Отправьте новую заявку.","duplicate":"🔁 Cashback за этот отзыв уже выплачен.","expired":"📅 После публикации прошло более 3 дней.","rejected":"❌ Заявка отклонена."}
    await bot.send_message(item["telegram_id"],(ru if item["language"]=="ru" else uz)[reason]+("\n\nℹ️ Эта заявка закрыта." if item["language"]=="ru" else "\n\nℹ️ Ushbu ariza yopildi."),reply_markup=main_menu(item["language"]))
    await callback.answer("Ariza yopildi.")


@dp.callback_query(F.data.startswith("cash_paid:"))
async def cash_paid(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: await callback.answer("Ruxsat yo'q.",show_alert=True); return
    request_id=callback.data.split(":")[1]; item=get_cashback_request_new(request_id)
    if not item or item["status"]!="approved": await callback.answer("Avval summani tasdiqlang.",show_alert=True); return
    update_cashback_new(request_id,status="paid"); item=get_cashback_request_new(request_id); await edit_admin_cashback_card(item,None)
    await bot.send_message(item["telegram_id"],f"✅ Cashback kartangizga o'tkazildi.\n\n💵 Summa: {money(item['amount'])}\n\nChek kerak bo'lsa menyudagi «Chek so'rash» tugmasini bosing." if item["language"]=="uz" else f"✅ Cashback переведён на карту.\n\n💵 Сумма: {money(item['amount'])}\n\nЕсли нужен чек, нажмите «Запросить чек».",reply_markup=cashback_paid_menu(item["language"]))
    await callback.answer("To'landi.")


# Cashback admin xabar yozishi — eski funksiyaning o'zi saqlanadi
@dp.callback_query(F.data.startswith("cashback_reply:"))
async def cashback_reply_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: await callback.answer("Ruxsat yo'q.", show_alert=True); return
    request_id=callback.data.split(":")[1]; await state.update_data(cashback_reply_request_id=request_id)
    await callback.message.answer("✉️ Cashback so'ragan xaridorga yuboriladigan xabarni yozing:"); await state.set_state(AdminReplyState.waiting_cashback_reply); await callback.answer()


@dp.message(AdminReplyState.waiting_cashback_reply)
async def admin_cashback_reply(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data=await state.get_data(); item=get_cashback_request_new(data.get("cashback_reply_request_id"))
    if not item: await message.answer("Cashback arizasi topilmadi."); await state.clear(); return
    await bot.send_message(item["telegram_id"],("✉️ Cashback bo'yicha admin xabari:\n\n" if item["language"]=="uz" else "✉️ Сообщение администратора по cashback:\n\n")+message.text,reply_markup=main_menu(item["language"]))
    await message.answer("✅ Xabar yuborildi."); await state.clear()


# =========================
# CHEK SO'RASH (XARIDOR)
# =========================

@dp.message(F.text.in_(["🧾 Chek so'rash", "🧾 Запросить чек"]))
async def request_check(message: Message):
    lang=get_user_language(message.from_user.id); conn=db(); cur=conn.cursor(); cur.execute("SELECT request_id FROM cashback_requests WHERE telegram_id=? AND status='paid' ORDER BY updated_at DESC LIMIT 1",(message.from_user.id,)); row=cur.fetchone(); conn.close()
    if not row: await message.answer("❌ To'langan cashback arizasi topilmadi." if lang=="uz" else "❌ Оплаченная заявка не найдена.",reply_markup=main_menu(lang)); return
    request_id=row[0]; kb=InlineKeyboardBuilder(); kb.button(text="📸 Chek yuborish",callback_data=f"send_check:{message.from_user.id}:{request_id}")
    await bot.send_message(ADMIN_ID,f"🧾 Xaridor chek so'ramoqda!\n\n👤 {message.from_user.full_name}\n🆔 {message.from_user.id}\n🆔 Ariza: {request_id}",reply_markup=kb.as_markup())
    await message.answer("✅ Chek so'rovingiz adminga yuborildi." if lang=="uz" else "✅ Запрос чека отправлен администратору.",reply_markup=main_menu(lang))


@dp.callback_query(F.data.startswith("send_check:"))
async def send_check_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: await callback.answer("Ruxsat yo'q.",show_alert=True); return
    _,telegram_id,request_id=callback.data.split(":"); await state.update_data(check_target_telegram_id=int(telegram_id),check_request_id=request_id)
    await callback.message.answer("📸 Chek rasmini yuboring:"); await state.set_state(AdminReplyState.waiting_check_photo); await callback.answer()


@dp.message(AdminReplyState.waiting_check_photo)
async def admin_check_photo(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    if not message.photo and not message.document: await message.answer("Chek rasmini yuboring."); return
    data=await state.get_data(); target=data.get("check_target_telegram_id")
    if not target: await message.answer("Ma'lumot topilmadi."); await state.clear(); return
    lang=get_user_language(target); await bot.send_message(target,"🧾 So'ragan chekingiz:" if lang=="uz" else "🧾 Запрошенный чек:",reply_markup=main_menu(lang))
    if message.photo: await bot.send_photo(target,message.photo[-1].file_id)
    else: await bot.send_document(target,message.document.file_id)
    await message.answer("✅ Chek xaridorga yuborildi."); await state.clear()


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

    lang_label = "🇷🇺 Русский" if preferred_lang == "ru" else "🇺🇿 O'zbekcha"
    await callback.message.answer(
        "Xaridor tili: " + lang_label + "\n\nQaysi tilda yuboramiz?",
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




@dp.message(Command("getid"))
async def getid_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📎 Rasmni yuboring — file_id olasiz:")


@dp.message(F.photo & (F.from_user.id == ADMIN_ID))
async def admin_photo_file_id(message: Message, state: FSMContext):
    current_state = await state.get_state()
    # Faqat hech qanday state yo'q bo'lganda file_id yuborsin
    if current_state is not None:
        return
    file_id = message.photo[-1].file_id
    await message.answer(f"📎 File ID:\n<code>{file_id}</code>", parse_mode="HTML")

# =========================
# ERKIN XABAR
# =========================

@dp.message(F.text & ~F.text.startswith("/"))
async def free_message(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        return

    # Tugmalar har doim ishlashi kerak
    known_buttons = [
        "💰 Cashback olish", "💰 Получить cashback",
        "📝 Shikoyat qoldirish", "📝 Оставить жалобу",
        "🌐 Tilni o'zgartirish", "🌐 Изменить язык",
        "🧾 Chek so'rash", "🧾 Запросить чек",
        "⏭ O'tkazib yuborish", "⏭ Пропустить",
    ]
    if message.text in known_buttons:
        return

    current_state = await state.get_state()
    if current_state is not None:
        return

    lang = get_user_language(message.from_user.id)

    known_buttons = [
        "💰 Cashback olish", "💰 Получить cashback",
        "📝 Shikoyat qoldirish", "📝 Оставить жалобу",
        "🌐 Tilni o'zgartirish", "🌐 Изменить язык",
        "🧾 Chek so'rash", "🧾 Запросить чек",
        "⏭ O'tkazib yuborish", "⏭ Пропустить",
    ]

    if message.text in known_buttons:
        return

    # Adminga xabar yuborish
    if ADMIN_ID:
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
            admin_text = (
                f"💬 Xaridor xabar yozdi\n\n"
                f"👤 Xaridor: {username}\n"
                f"🆔 Telegram ID: {message.from_user.id}\n\n"
                f"📝 Xabar:\n{message.text}"
            )
            kb = InlineKeyboardBuilder()
            kb.button(text="✉️ Javob berish", callback_data=f"free_reply:{message.from_user.id}")
            kb.adjust(1)
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=kb.as_markup())
        except Exception as e:
            logger.error(f"Erkin xabarni adminga yuborishda xato: {e}")

    await message.answer(
        "✅ Xabaringiz qabul qilindi. Tez orada javob beriladi."
        if lang == "uz"
        else "✅ Ваше сообщение принято. Ответ придёт в ближайшее время.",
        reply_markup=main_menu(lang)
    )


@dp.callback_query(F.data.startswith("free_reply:"))
async def free_reply_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    await state.update_data(free_reply_target_id=target_id)
    await callback.message.answer("✉️ Xaridorga yuboriladigan javobni yozing:")
    await state.set_state(AdminReplyState.waiting_free_reply)
    await callback.answer()


@dp.message(AdminReplyState.waiting_free_reply)
async def admin_free_reply(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    target_id = data.get("free_reply_target_id")

    if not target_id:
        await message.answer("Xaridor topilmadi.")
        await state.clear()
        return

    lang = get_user_language(target_id)

    try:
        await bot.send_message(
            target_id,
            f"✉️ Admin javobi:\n\n{message.text.strip()}",
            reply_markup=main_menu(lang)
        )
        await message.answer("✅ Javob xaridorga yuborildi.")
    except Exception as e:
        logger.error(f"Erkin javob yuborishda xato: {e}")
        await message.answer("❌ Xabarni yuborishda xato.")

    await state.clear()

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

    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    scheduler.add_job(send_daily_report, "cron", hour=20, minute=0)
    scheduler.start()

    logger.info("Uzum Cashback Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
