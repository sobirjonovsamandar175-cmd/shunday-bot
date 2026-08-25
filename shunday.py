# -*- coding: utf-8 -*-
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client
import re
import asyncio
import random
import string
import time
import logging
import html
import ssl as _ssl
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)
from telegram.error import TelegramError
from telethon import TelegramClient, events
from fragment_api import (
    STARS_PACKAGES,
    _get_ton_balance,
    get_fragment_stars_prices_bulk,
    api_buy_stars,
    api_buy_premium,
    fragment_wallet_command,
    fragment_cookie_status_command,
)

# PTBUserWarning ogohlantirishini yashirish
warnings.filterwarnings("ignore", category=PTBUserWarning)

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)

# ==================== ASOSIY SOZLAMALAR ====================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT")
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "@online_quiz_tests")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "1738809395").split(",") if x.strip().isdigit()]
ORDER_CHANNEL = os.getenv("ORDER_CHANNEL", "https://t.me/online_quiz_tests")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_HOLDER = os.getenv("CARD_HOLDER", "Sobirjonov Samandar")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(f"SUPABASE_URL yoki SUPABASE_KEY topilmadi: {BASE_DIR / '.env'}")
if not TOKEN:
    raise RuntimeError(f"BOT_TOKEN topilmadi: {BASE_DIR / '.env'}")
if not API_ID or not API_HASH:
    logger.warning("API_ID/API_HASH .env ichida yo'q — Telethon/Humo listener kerak bo'lsa to'ldiring.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
STARS_MIN_QTY = 50
DB_NAME = "SUPABASE"

_SSL_CTX = _ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = _ssl.CERT_NONE

telethon_client = TelegramClient('humo_userbot_session', API_ID, API_HASH)

# ==================== MA'LUMOTLAR BAZASI ====================
# Supabase DB adapter: eski botdagi sqlite3.execute(...) chaqiruvlarini
# minimal o'zgarish bilan Supabase REST API orqali bajaradi.

class _SupabaseCursor:
    def __init__(self):
        self.rows = []
        self.lastrowid = None

    def _set(self, rows):
        self.rows = rows or []
        return self

    def execute(self, sql, params=()):
        q = " ".join(str(sql).split()).strip().lower()
        p = tuple(params or ())
        self.rows = []
        self.lastrowid = None

        # Schema statements are executed separately in Supabase SQL Editor.
        if q.startswith(("create table", "pragma", "alter table")):
            return self

        # settings
        if q.startswith("insert or ignore into settings") or q.startswith("insert or replace into settings"):
            key, value = p
            supabase.table("settings").upsert({"key": key, "value": int(value)}, on_conflict="key").execute()
            return self
        if q.startswith("select value from settings"):
            key = p[0]
            data = supabase.table("settings").select("value").eq("key", key).limit(1).execute().data or []
            return self._set([(data[0].get("value"),)] if data else [])

        # users
        if q.startswith("select user_id from users where username"):
            username = p[0]
            data = supabase.table("users").select("user_id").eq("username", username).limit(1).execute().data or []
            return self._set([(data[0]["user_id"],)] if data else [])
        if q.startswith("select user_id from users where user_id"):
            uid = p[0]
            data = supabase.table("users").select("user_id").eq("user_id", uid).limit(1).execute().data or []
            return self._set([(data[0]["user_id"],)] if data else [])
        if q.startswith("select user_id from users"):
            data = supabase.table("users").select("user_id").execute().data or []
            return self._set([(x["user_id"],) for x in data])
        if q.startswith("select balance, incognito from users"):
            uid = p[0]
            data = supabase.table("users").select("balance,incognito").eq("user_id", uid).limit(1).execute().data or []
            return self._set([(data[0].get("balance", 0) or 0, data[0].get("incognito", 0) or 0)] if data else [])
        if q.startswith("select balance from users"):
            uid = p[0]
            data = supabase.table("users").select("balance").eq("user_id", uid).limit(1).execute().data or []
            return self._set([(data[0].get("balance", 0) or 0,)] if data else [])
        if q.startswith("select incognito from users"):
            uid = p[0]
            data = supabase.table("users").select("incognito").eq("user_id", uid).limit(1).execute().data or []
            return self._set([(data[0].get("incognito", 0) or 0,)] if data else [])
        if q.startswith("insert or ignore into users"):
            uid = p[0]
            supabase.table("users").upsert({"user_id": uid, "balance": 0, "incognito": 0}, on_conflict="user_id").execute()
            return self
        if q.startswith("insert into users"):
            uid, username = p
            supabase.table("users").insert({"user_id": uid, "username": username, "balance": 0, "incognito": 0}).execute()
            return self
        if q.startswith("update users set username"):
            username, uid = p
            supabase.table("users").update({"username": username}).eq("user_id", uid).execute()
            return self
        if q.startswith("update users set incognito = case"):
            uid = p[0]
            data = supabase.table("users").select("incognito").eq("user_id", uid).limit(1).execute().data or []
            if data:
                new_value = 0 if int(data[0].get("incognito", 0) or 0) else 1
                supabase.table("users").update({"incognito": new_value}).eq("user_id", uid).execute()
            return self
        if q.startswith("update users set balance = balance +"):
            amount, uid = p
            # Atomic balance change is preferred through the SQL RPC function.
            try:
                supabase.rpc("increment_user_balance", {"p_user_id": int(uid), "p_amount": int(amount)}).execute()
            except Exception:
                data = supabase.table("users").select("balance").eq("user_id", uid).limit(1).execute().data or []
                if not data:
                    supabase.table("users").upsert({"user_id": uid, "balance": int(amount), "incognito": 0}, on_conflict="user_id").execute()
                else:
                    new_balance = int(data[0].get("balance", 0) or 0) + int(amount)
                    supabase.table("users").update({"balance": new_balance}).eq("user_id", uid).execute()
            return self

        # orders
        if q.startswith("select count(*), coalesce(sum(price)"):
            uid = p[0]
            data = supabase.table("orders").select("price").eq("user_id", uid).eq("status", "Bajarildi").execute().data or []
            return self._set([(len(data), sum(int(x.get("price", 0) or 0) for x in data))])
        if q.startswith("select order_id, service_type, detail, price, status from orders"):
            uid = p[0]
            data = supabase.table("orders").select("order_id,service_type,detail,price,status").eq("user_id", uid).order("order_id", desc=True).limit(5).execute().data or []
            return self._set([(x.get("order_id"), x.get("service_type"), x.get("detail"), x.get("price"), x.get("status")) for x in data])
        if q.startswith("select user_id, price, status from orders"):
            oid = p[0]
            data = supabase.table("orders").select("user_id,price,status").eq("order_id", oid).limit(1).execute().data or []
            return self._set([(data[0].get("user_id"), data[0].get("price"), data[0].get("status"))] if data else [])
        if q.startswith("insert into orders"):
            uid, service_type, target, detail, qty, price = p
            data = supabase.table("orders").insert({"user_id": uid, "service_type": service_type, "target": target, "detail": detail, "qty": qty, "price": price, "status": "Kutilmoqda"}).execute().data or []
            self.lastrowid = data[0].get("order_id") if data else None
            return self
        if q.startswith("update orders set status"):
            status, oid = p
            supabase.table("orders").update({"status": status}).eq("order_id", oid).execute()
            return self
        if q.startswith("select u.username, u.incognito"):
            orders = supabase.table("orders").select("user_id,price").eq("status", "Bajarildi").execute().data or []
            totals = {}
            for x in orders:
                uid = x.get("user_id")
                totals[uid] = totals.get(uid, 0) + int(x.get("price", 0) or 0)
            ids = [x for x in totals if x is not None]
            users = supabase.table("users").select("user_id,username,incognito").in_("user_id", ids).execute().data if ids else []
            users = users.data if hasattr(users, "data") else (users or [])
            um = {x["user_id"]: x for x in users}
            result = [(um.get(uid, {}).get("username"), um.get(uid, {}).get("incognito", 0), total) for uid, total in totals.items()]
            result.sort(key=lambda x: x[2], reverse=True)
            return self._set(result[:5])

        # pending payments / payments
        if q.startswith("update pending_payments set status = 'expired'"):
            created = p[0]
            supabase.table("pending_payments").update({"status": "expired"}).eq("status", "pending").lt("expires_at", created).execute()
            return self
        if q.startswith("insert into pending_payments"):
            uid, base, exact, code, created, expires = p
            data = supabase.table("pending_payments").insert({"user_id": uid, "base_amount": base, "exact_amount": exact, "code": code, "created_at": created, "expires_at": expires, "status": "pending"}).execute().data or []
            self.lastrowid = data[0].get("payment_id") if data else None
            return self
        if q.startswith("select payment_id, user_id, exact_amount from pending_payments"):
            amount, now = p
            data = supabase.table("pending_payments").select("payment_id,user_id,exact_amount").eq("exact_amount", amount).eq("status", "pending").gte("expires_at", now).order("payment_id").limit(1).execute().data or []
            return self._set([(data[0]["payment_id"], data[0]["user_id"], data[0]["exact_amount"])] if data else [])
        if q.startswith("select status, exact_amount, expires_at from pending_payments"):
            pid = p[0]
            data = supabase.table("pending_payments").select("status,exact_amount,expires_at").eq("payment_id", pid).limit(1).execute().data or []
            return self._set([(data[0].get("status"), data[0].get("exact_amount"), data[0].get("expires_at"))] if data else [])
        if q.startswith("update pending_payments set status = 'completed'"):
            pid = p[0]
            supabase.table("pending_payments").update({"status": "completed"}).eq("payment_id", pid).execute()
            return self
        if q.startswith("update pending_payments set status = 'cancelled'"):
            pid = p[0]
            supabase.table("pending_payments").update({"status": "cancelled"}).eq("payment_id", pid).execute()
            return self
        if q.startswith("insert into payments"):
            uid, amount = p
            supabase.table("payments").insert({"user_id": uid, "amount": amount, "status": "Tasdiqlandi"}).execute()
            return self

        # statistics
        if q.startswith("select count(*) from users"):
            r = supabase.table("users").select("user_id", count="exact").limit(1).execute()
            return self._set([(int(r.count or 0),)])
        if q.startswith("select count(*) from orders"):
            r = supabase.table("orders").select("order_id", count="exact").limit(1).execute()
            return self._set([(int(r.count or 0),)])
        if q.startswith("select sum(amount) from payments"):
            data = supabase.table("payments").select("amount").eq("status", "Tasdiqlandi").execute().data or []
            return self._set([(sum(int(x.get("amount", 0) or 0) for x in data),)])

        raise RuntimeError(f"Supabase DB query qo'llab-quvvatlanmadi: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

class _SupabaseConnection:
    def cursor(self):
        return _SupabaseCursor()
    def commit(self):
        return None
    def close(self):
        return None

def db_connect(*args, **kwargs):
    return _SupabaseConnection()

def init_db():
    # Jadvallar Supabase SQL Editor orqali bir marta yaratiladi.
    defaults = {
        "price_star": 190,
        "premium_3": 145000,
        "premium_6": 195000,
        "premium_12": 340000,
    }
    for key, value in defaults.items():
        try:
            supabase.table("settings").upsert({"key": key, "value": value}, on_conflict="key").execute()
        except Exception as e:
            logger.warning("Supabase settings init xatosi: %s", e)

init_db()

def get_all_user_ids():
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_setting(key, default=0):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else default

def get_stars_rate():
    return int(get_setting("price_star", 190))

def set_setting(key, value):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, incognito FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else (0, 0)

def update_balance(user_id, amount):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, incognito) VALUES (?, 0, 0)", (user_id,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def toggle_incognito(user_id):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET incognito = CASE WHEN incognito = 1 THEN 0 ELSE 1 END WHERE user_id = ?", (user_id,))
    cursor.execute("SELECT incognito FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return res

def save_user(user_id, username):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    clean_username = username.replace("@", "") if username else None
    
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone()
    
    is_new = False
    if not exists:
        cursor.execute("INSERT INTO users (user_id, username, balance, incognito) VALUES (?, ?, 0, 0)", (user_id, clean_username))
        is_new = True
    else:
        if clean_username:
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (clean_username, user_id))
            
    conn.commit()
    conn.close()
    return is_new

def get_user_stats(user_id):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders WHERE user_id = ? AND status = 'Bajarildi'", (user_id,))
    orders_count, total_spent = cursor.fetchone()
    conn.close()
    return orders_count, total_spent

def set_order_status(order_id, status):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
    conn.commit()
    conn.close()

def md_escape(text):
    if text is None:
        return ""
    return html.escape(str(text))

def get_premium_options():
    return [
        (3, get_setting("premium_3", 145000)),
        (6, get_setting("premium_6", 195000)),
        (12, get_setting("premium_12", 340000)),
    ]

# ==================== TO'LOV YARATISH ====================
def create_payment(user_id, amount):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    created_at = int(time.time())
    
    cursor.execute("UPDATE pending_payments SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", (created_at,))

    random_add = random.randint(1, 5)
    exact_amount = amount + random_add

    code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    expires_at = created_at + 300  # 5 daqiqa

    cursor.execute("""
        INSERT INTO pending_payments (user_id, base_amount, exact_amount, code, created_at, expires_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (user_id, amount, exact_amount, code, created_at, expires_at))
    
    payment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return payment_id, exact_amount, code, created_at, expires_at

# ==================== TELETHON LISTENER (AUTO-PAYMENT) ====================
async def process_humo_incoming_payment(amount_received, context_bot=None):
    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    now = int(time.time())

    cursor.execute("""
        SELECT payment_id, user_id, exact_amount FROM pending_payments 
        WHERE exact_amount = ? AND status = 'pending' AND expires_at >= ?
        ORDER BY payment_id ASC LIMIT 1
    """, (amount_received, now))
    row = cursor.fetchone()

    if row:
        pay_id, user_id, exact_amount = row
        cursor.execute("UPDATE pending_payments SET status = 'completed' WHERE payment_id = ?", (pay_id,))
        cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, incognito) VALUES (?, 0, 0)", (user_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (exact_amount, user_id))
        cursor.execute("INSERT INTO payments (user_id, amount, status) VALUES (?, ?, 'Tasdiqlandi')", (user_id, exact_amount))
        conn.commit()
        conn.close()

        if context_bot:
            try:
                await context_bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 <b>To‘lov qabul qilindi!</b>\n\n💰 Hisobingizga <b>{exact_amount:,} so‘m</b> qo‘shildi.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")

            try:
                log_msg = f"💳 <b>YANGI TO'LOV TASDIQLANDI!</b>\n\n👤 User ID: <code>{user_id}</code>\n💰 Summa: <b>{exact_amount:,} so'm</b>"
                await context_bot.send_message(chat_id=ADMIN_GROUP, text=log_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Guruhga xabarnoma yuborishda xato: {e}")
        return True
    
    conn.close()
    return False

@telethon_client.on(events.NewMessage(chats='HUMOcardbot'))
async def humo_card_bot_listener(event):
    text = event.raw_text
    # SMS'dan aniq o'tkazma summasini ajratish
    match = re.search(r'([\d\s\.,\xa0]+)\s*UZS', text, re.IGNORECASE)
    if match:
        raw_sum = match.group(1)
        clean_sum_str = raw_sum.replace(' ', '').replace('\xa0', '').replace('.', '')
        
        if ',' in clean_sum_str:
            clean_sum_str = clean_sum_str.split(',')[0]
            
        if clean_sum_str.isdigit():
            clean_sum = int(clean_sum_str)
            logger.info(f"📥 @HUMOcardbot'dan to'lov xabari keldi: {clean_sum} UZS")
            bot_instance = getattr(telethon_client, 'ptb_bot', None)
            await process_humo_incoming_payment(clean_sum, context_bot=bot_instance)

# ==================== HOLATLAR ====================
(
    MAIN,
    STARS_TARGET, STARS_QTY_INPUT,
    GIFT_TARGET, GIFT_CHOOSE,
    PREMIUM_TARGET, PREMIUM_CHOOSE,
    PAY_AMOUNT, PAY_CHECK,
    ADMIN_MAIN, SET_PRICE_CHOOSE, SET_PRICE_VALUE, 
    ADMIN_PAY_ID, ADMIN_PAY_SUM,
    ADMIN_SUB_ID, ADMIN_SUB_SUM,
    ADMIN_BROADCAST_MSG
) = range(17)

# ==================== MENYULAR ====================
main_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("⭐ Stars olish", callback_data="main_stars")],
    [
        InlineKeyboardButton("✈️ Premium olish", callback_data="main_premium"),
        InlineKeyboardButton("🎁 Gift olish", callback_data="main_gift")
    ],
    [InlineKeyboardButton("🌐 Telegram akkaunt sotib olish", callback_data="main_accounts")],
    [
        InlineKeyboardButton("💳 Balans to'ldirish", callback_data="main_balance"),
        InlineKeyboardButton("👤 Profil", callback_data="main_profile")
    ],
    [InlineKeyboardButton("ℹ️ Yordam", callback_data="main_help")]
])

target_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("👤 O‘zim uchun", callback_data="target_self")],
    [InlineKeyboardButton("◄ Orqaga", callback_data="target_back")]
])

gift_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("🧸 2,600 so'm", callback_data="gift_2600_🧸"), InlineKeyboardButton("💖 2,600 so'm", callback_data="gift_2600_💖")],
    [InlineKeyboardButton("🎁 4,325 so'm", callback_data="gift_4325_🎁"), InlineKeyboardButton("🌹 4,325 so'm", callback_data="gift_4325_🌹")],
    [InlineKeyboardButton("🎂 8,650 so'm", callback_data="gift_8650_🎂"), InlineKeyboardButton("💐 8,650 so'm", callback_data="gift_8650_💐")],
    [InlineKeyboardButton("🚀 8,650 so'm", callback_data="gift_8650_🚀"), InlineKeyboardButton("🏆 17,300 so'm", callback_data="gift_17300_🏆")],
    [InlineKeyboardButton("💍 17,300 so'm", callback_data="gift_17300_💍"), InlineKeyboardButton("💎 17,300 so'm", callback_data="gift_17300_💎")],
    [InlineKeyboardButton("◄ Orqaga", callback_data="target_back")]
])

confirm_menu = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✅ Tasdiqlash", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data="confirm_no")
    ]
])

admin_menu = ReplyKeyboardMarkup([
    ["📊 Statistika", "⚙️ Narxlarni o'zgartirish"],
    ["💎 Stars narxlari (Fragment)", "📢 Xabar yuborish"],
    ["💳 ID orqali balans to'ldirish", "🔻 ID orqali balans ayirish"],
    ["🏠 Asosiy menyu"]
], resize_keyboard=True)

def build_profile_menu(incognito_status):
    inc_text = "O'chirilgan" if incognito_status == 0 else "Yoqilgan"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Buyurtmalarim", callback_data="prof_orders")],
        [InlineKeyboardButton("🏆 TOP foydalanuvchilar", callback_data="prof_top")],
        [InlineKeyboardButton(f"🙈 Maxfiy rejim: {inc_text}", callback_data="prof_incognito")],
        [InlineKeyboardButton("💬 Buyurtmalar kanali", url=ORDER_CHANNEL)],
        [InlineKeyboardButton("↩️ Orqaga", callback_data="prof_back")]
    ])

def build_premium_menu():
    duration_label = {3: "3 oy", 6: "6 oy", 12: "1 yil"}
    rows = []
    labels = {}
    for months, price in get_premium_options():
        label = f"✈️ {duration_label[months]} — {price:,} so'm"
        labels[f"prem_{months}"] = (months, price, duration_label[months])
        rows.append([InlineKeyboardButton(label, callback_data=f"prem_{months}")])
    rows.append([InlineKeyboardButton("◄ Orqaga", callback_data="target_back")])
    return InlineKeyboardMarkup(rows), labels

# ==================== BOT START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    uname = update.message.from_user.username
    is_new = save_user(uid, uname)

    if is_new:
        user_tag = f"@{uname}" if uname else "Mavjud emas"
        new_user_msg = (
            f"👤 <b>Yangi obunachi botga qo'shildi!</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"👤 Ism: {md_escape(update.message.from_user.full_name)}\n"
            f"🔗 Username: {user_tag}"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_GROUP, text=new_user_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Guruhga xabar yuborishda xato: {e}")

    await context.bot.set_my_commands([BotCommand("start", "🤖 Botni qayta ishga tushirish / Menyu")])
    balance, _ = get_user_data(uid)

    remove_msg = await update.message.reply_text("🔄", reply_markup=ReplyKeyboardRemove())
    try:
        await remove_msg.delete()
    except Exception:
        pass

    await update.message.reply_text(
        f"👑 <b>Xush kelibsiz!</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {md_escape(update.message.from_user.full_name)}\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"💰 <b>Balans:</b> {balance:,} so'm\n\n"
        f"👇 Kerakli xizmatni tanlang:",
        parse_mode="HTML",
        reply_markup=main_menu
    )
    context.user_data.clear()
    return MAIN

# ==================== INLINE ASOSIY MENYU ====================
async def render_profile(query_or_msg, uid, is_edit=False):
    balance, incognito = get_user_data(uid)
    orders_count, total_spent = get_user_stats(uid)
    inc_status_text = "O'chirilgan" if incognito == 0 else "Yoqilgan"

    text = (
        f"👤 <b>Profil</b>\n\n"
        f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
        f"💳 <b>Balans:</b> {balance:,} so'm\n"
        f"💎 <b>Buyurtmalar:</b> {orders_count} ta\n"
        f"💰 <b>Sarflangan:</b> {total_spent:,} so'm\n"
        f"🙈 <b>Maxfiy rejim:</b> 👤 {inc_status_text}"
    )
    markup = build_profile_menu(incognito)

    if is_edit:
        await query_or_msg.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await query_or_msg.reply_text(text, parse_mode="HTML", reply_markup=markup)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "main_stars":
        context.user_data["current_service"] = "stars"
        await query.message.edit_text(
            "🔎 <b>Stars xarid qilish</b>\n\nStars yuborilishi kerak bo'lgan foydalanuvchi username'ini kiriting:\n✍️ Misol: @Sobirjonov_uz",
            reply_markup=target_menu,
            parse_mode="HTML"
        )
        return STARS_TARGET

    elif data == "main_premium":
        context.user_data["current_service"] = "premium"
        await query.message.edit_text(
            "🔎 <b>Telegram Premium</b>\n\nQabul qiluvchi foydalanuvchi username'ini kiriting:\n✍️ Misol: @Sobirjonov_uz",
            reply_markup=target_menu,
            parse_mode="HTML"
        )
        return PREMIUM_TARGET

    elif data == "main_gift":
        context.user_data["current_service"] = "gift"
        await query.message.edit_text(
            "🔎 <b>Telegram Gift</b>\n\nGift yubormoqchi bo'lgan foydalanuvchi username'ini kiriting:\n✍️ Misol: @Sobirjonov_uz",
            reply_markup=target_menu,
            parse_mode="HTML"
        )
        return GIFT_TARGET

    elif data == "main_accounts":
        await query.message.reply_text(
            "🌐 <b>Telegram akkaunt sotib olish</b>\n\nBu xizmat tez orada ishga tushadi.",
            parse_mode="HTML",
            reply_markup=main_menu
        )
        return MAIN

    elif data == "main_balance":
        cancel_inline = InlineKeyboardMarkup([[InlineKeyboardButton("◄ Orqaga", callback_data="target_back")]])
        await query.message.edit_text(
            f"💰 <b>Balansni to'ldirish</b>\n\nQuyidagi miqdorni kiriting:\n\n⬇️ Minimal: <b>1 000 so'm</b>\n⬆️ Maksimal: <b>2 500 000 so'm</b>",
            parse_mode="HTML",
            reply_markup=cancel_inline
        )
        return PAY_AMOUNT

    elif data == "main_profile":
        await render_profile(query.message, uid, is_edit=True)
        return MAIN

    elif data == "main_help":
        await query.message.edit_text(
            "ℹ️ <b>Yordam</b>\n\n🤖 Stars, Premium va Gift sotib olish uchun balansni to'ldiring.\n❓ Savollar bo'yicha: @sobirjonov_uz",
            parse_mode="HTML",
            reply_markup=main_menu
        )
        return MAIN

    return MAIN

# ==================== PROFIL HANDLERLARI ====================
async def profile_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "prof_incognito":
        toggle_incognito(uid)
        await render_profile(query.message, uid, is_edit=True)

    elif data == "prof_orders":
        conn = db_connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, service_type, detail, price, status FROM orders WHERE user_id = ? ORDER BY order_id DESC LIMIT 5", (uid,))
        orders = cursor.fetchall()
        conn.close()

        if not orders:
            await query.message.reply_text("📜 Sizda hali buyurtmalar mavjud emas.")
            return

        msg = "📜 <b>Sizning oxirgi buyurtmalaringiz:</b>\n\n"
        for oid, stype, det, pr, st in orders:
            msg += f"🆔 №{oid} | {md_escape(stype)} ({md_escape(det)}) | {pr:,} so'm | Holat: <b>{st}</b>\n"
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "prof_top":
        conn = db_connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.username, u.incognito, SUM(o.price) as total 
            FROM orders o JOIN users u ON o.user_id = u.user_id 
            WHERE o.status = 'Bajarildi' 
            GROUP BY o.user_id ORDER BY total DESC LIMIT 5
        """)
        top_users = cursor.fetchall()
        conn.close()

        msg = "🏆 <b>TOP 5 Xaridorlar:</b>\n\n"
        for idx, (uname, inc, total) in enumerate(top_users, 1):
            display_name = "🙈 Yashirin" if inc else f"@{md_escape(uname)}" if uname else "Foydalanuvchi"
            msg += f"{idx}. {display_name} — {total:,} so'm\n"
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "prof_back":
        balance, _ = get_user_data(uid)
        await query.message.edit_text(
            f"👑 <b>Xush kelibsiz!</b>\n\n🆔 <b>ID:</b> <code>{uid}</code>\n💰 <b>Balans:</b> {balance:,} so'm\n\n👇 Kerakli xizmatni tanlang:",
            parse_mode="HTML",
            reply_markup=main_menu
        )

# ==================== COMMON TARGET HANDLER ====================
async def target_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "target_back":
        balance, _ = get_user_data(uid)
        await query.message.edit_text(
            f"👑 <b>Xush kelibsiz!</b>\n\n🆔 <b>ID:</b> <code>{uid}</code>\n💰 <b>Balans:</b> {balance:,} so'm\n\n👇 Kerakli xizmatni tanlang:",
            parse_mode="HTML",
            reply_markup=main_menu
        )
        return MAIN

    if data == "target_self":
        username = query.from_user.username
        if not username:
            await query.message.reply_text("❌ Profilingizda username o'rnatilmagan!")
            return MAIN
        target = f"@{username}"
        context.user_data["target"] = target

        service = context.user_data.get("current_service")
        if service == "stars":
            return await render_stars_qty_menu(query.message, context, target, is_edit=True)
        elif service == "premium":
            return await render_premium_menu(query.message, context, target, uid, is_edit=True)
        elif service == "gift":
            await query.message.edit_text("🎁 Kerakli giftni tanlang:", reply_markup=gift_menu)
            return GIFT_CHOOSE

    return MAIN

# ==================== STARS OLISH HANDLERLARI ====================
async def render_stars_qty_menu(msg_obj, context, target, is_edit=False):
    rate = get_stars_rate()
    context.user_data["rate"] = rate

    p50 = 50 * rate
    p100 = 100 * rate
    p150 = 150 * rate

    stars_inline = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⭐ 50 — {p50:,} so'm", callback_data="stars_qty_50")],
        [InlineKeyboardButton(f"⭐ 100 — {p100:,} so'm", callback_data="stars_qty_100")],
        [InlineKeyboardButton(f"⭐ 150 — {p150:,} so'm", callback_data="stars_qty_150")],
        [InlineKeyboardButton("✏️ Boshqa qiymat kiritish", callback_data="stars_qty_custom")],
        [InlineKeyboardButton("↩️ Orqaga", callback_data="stars_back")]
    ])

    text = (
        f"<b>Telegram Stars buyurtma</b>\n\n"
        f"👤 <b>Qabul qiluvchi:</b> {md_escape(target)}\n\n"
        f"Minimal: 50 | Maksimal: 5000\n\n"
        f"Kerakli Stars miqdorini tanlang:"
    )

    if is_edit:
        await msg_obj.edit_text(text, parse_mode="HTML", reply_markup=stars_inline)
    else:
        await msg_obj.reply_text(text, parse_mode="HTML", reply_markup=stars_inline)
    return MAIN

async def stars_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not re.match(r"^@[A-Za-z0-9_]{5,32}$", text):
        await update.message.reply_text("❌ Username xato kiritildi (@ bilan, 5-32 belgi):")
        return STARS_TARGET
    context.user_data["target"] = text
    return await render_stars_qty_menu(update.message, context, text, is_edit=False)

async def stars_qty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "stars_back":
        await query.message.edit_text("🔎 Foydalanuvchi username'ini kiriting:", reply_markup=target_menu)
        return STARS_TARGET

    if data == "stars_qty_custom":
        await query.message.reply_text("✍️ Nechta Stars sotib olmoqchisiz? Raqamda kiriting:")
        return STARS_QTY_INPUT

    qty = int(data.split("_")[-1])
    return await process_stars_order(query.message, context, qty, is_edit=True)

async def stars_qty_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    clean_text = text.replace(' ', '').replace('.', '').replace(',', '')
    if not clean_text.isdigit():
        await update.message.reply_text("❌ Iltimos faqat raqam kiriting:")
        return STARS_QTY_INPUT
    qty = int(clean_text)
    if qty < STARS_MIN_QTY:
        await update.message.reply_text(f"❌ Minimal miqdor {STARS_MIN_QTY} ta. Qaytadan kiriting:")
        return STARS_QTY_INPUT

    return await process_stars_order(update.message, context, qty, is_edit=False)

async def process_stars_order(msg_obj, context, qty, is_edit=False):
    rate = context.user_data.get("rate") or get_stars_rate()
    price = qty * rate

    context.user_data["qty"] = qty
    context.user_data["price"] = price
    context.user_data["detail"] = f"{qty} ta Stars"
    context.user_data["service"] = "Telegram Stars"
    context.user_data["order_mode"] = "api_stars"
    context.user_data["link"] = context.user_data["target"]

    text = (
        f"📋 <b>Buyurtma tafsilotlari:</b>\n\n"
        f"🛍 Xizmat: Telegram Stars\n"
        f"👤 Kimga: {md_escape(context.user_data['target'])}\n"
        f"⭐ Miqdor: {qty} ta\n"
        f"💰 Narx: {price:,} so'm\n\n"
        f"Xaridni tasdiqlaysizmi?"
    )

    if is_edit:
        await msg_obj.edit_text(text, parse_mode="HTML", reply_markup=confirm_menu)
    else:
        await msg_obj.reply_text(text, parse_mode="HTML", reply_markup=confirm_menu)
    return MAIN

# ==================== PREMIUM VA GIFT HANDLERLARI ====================
async def render_premium_menu(msg_obj, context, target, uid, is_edit=False):
    menu, labels = build_premium_menu()
    context.user_data["premium_options"] = labels
    balance, _ = get_user_data(uid)

    text = (
        f"✈️ <b>Telegram Premium</b>\n\n"
        f"👤 Qabul qiluvchi: {md_escape(target)}\n"
        f"💳 Balansingiz: {balance:,} so'm\n\n"
        f"📅 Muddatni tanlang:"
    )

    if is_edit:
        await msg_obj.edit_text(text, parse_mode="HTML", reply_markup=menu)
    else:
        await msg_obj.reply_text(text, parse_mode="HTML", reply_markup=menu)
    return PREMIUM_CHOOSE

async def premium_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.message.from_user.id

    if not re.match(r"^@[A-Za-z0-9_]{5,32}$", text):
        await update.message.reply_text("❌ Username xato formatda kiritildi.")
        return PREMIUM_TARGET

    context.user_data["target"] = text
    return await render_premium_menu(update.message, context, text, uid, is_edit=False)

async def premium_choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    opts = context.user_data.get("premium_options", {})
    if data not in opts:
        return PREMIUM_CHOOSE

    months, price, duration_label = opts[data]

    context.user_data["detail"] = duration_label
    context.user_data["qty"] = 1
    context.user_data["price"] = price
    context.user_data["duration_months"] = months
    context.user_data["service"] = "Telegram Premium"
    context.user_data["order_mode"] = "api_premium"
    context.user_data["link"] = context.user_data["target"]

    await query.message.edit_text(
        f"📋 <b>Buyurtma tafsilotlari:</b>\n\n"
        f"🛍 Xizmat: Telegram Premium\n"
        f"👤 Kimga: {md_escape(context.user_data['target'])}\n"
        f"📅 Muddat: {md_escape(duration_label)}\n"
        f"💰 Narx: {price:,} so'm\n\n"
        f"Xaridni tasdiqlaysizmi?",
        parse_mode="HTML",
        reply_markup=confirm_menu
    )
    return MAIN

async def gift_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not re.match(r"^@[A-Za-z0-9_]{5,32}$", text):
        await update.message.reply_text("❌ Username xato kiritildi (@ bo'lishi shart):")
        return GIFT_TARGET

    context.user_data["target"] = text
    await update.message.reply_text("🎁 Kerakli giftni tanlang:", reply_markup=gift_menu)
    return GIFT_CHOOSE

async def gift_choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    parts = data.split("_")
    price = int(parts[1])
    emoji = parts[2]

    context.user_data["detail"] = emoji
    context.user_data["qty"] = 1
    context.user_data["price"] = price
    context.user_data["service"] = "Telegram Gift"
    context.user_data["order_mode"] = "manual"
    context.user_data["link"] = context.user_data["target"]

    await query.message.edit_text(
        f"📋 <b>Buyurtma tafsilotlari:</b>\n\n"
        f"🛍 Xizmat: Telegram Gift\n"
        f"👤 Kimga: {md_escape(context.user_data['target'])}\n"
        f"🎁 Gift: {emoji}\n"
        f"💰 Narx: {price:,} so'm\n\n"
        f"Xaridni tasdiqlaysizmi?",
        parse_mode="HTML",
        reply_markup=confirm_menu
    )
    return MAIN

# ==================== BUYURTMANI TASDIQLASH VA GURUHGA YUBORISH ====================
async def confirm_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # BadQuery va Timeout xatolarining oldini olish uchun DARHOL answer() beriladi:
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    uid = query.from_user.id

    if data == "confirm_no":
        await query.message.edit_text("❌ Buyurtma bekor qilindi.")
        await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)
        context.user_data.clear()
        return MAIN

    # 0 SO'MLIK BO'SH BUYURTMA QOLISHINI OLDINI OLISH VA TEKSHIRISH
    price = context.user_data.get("price", 0)
    service = context.user_data.get("service")
    target = context.user_data.get("link") or context.user_data.get("target")

    if not service or price <= 0 or not target:
        await query.message.edit_text("⚠️ Buyurtma ma'lumotlari topilmadi yoki sessiya vaqti tugadi. Iltimos qaytadan urining.")
        await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)
        context.user_data.clear()
        return MAIN

    balance, _ = get_user_data(uid)
    if balance < price:
        await query.message.edit_text(f"❌ Mablag' yetarli emas. Yana {price - balance:,} so'm kerak.")
        await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)
        return MAIN

    # Balansdan pul yechish
    update_balance(uid, -price)
    order_mode = context.user_data.get("order_mode", "manual")
    user_tag = f"@{query.from_user.username}" if query.from_user.username else query.from_user.full_name

    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (user_id, service_type, target, detail, qty, price) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, service, target, context.user_data.get("detail"), context.user_data.get("qty", 1), price)
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Avtomatik API orqali bajariladigan buyurtmalar
    if order_mode in ("api_stars", "api_premium"):
        try:
            if order_mode == "api_stars":
                resp = await api_buy_stars(target, context.user_data.get("qty"), order_id)
            else:
                resp = await api_buy_premium(target, context.user_data.get("duration_months"), order_id)
        except Exception as e:
            resp = {"success": False, "error": str(e)}

        success = bool(resp.get("success")) if isinstance(resp, dict) else False

        if not success:
            update_balance(uid, price)
            set_order_status(order_id, "Rad etildi")
            raw_err = (resp.get("error") if isinstance(resp, dict) else None) or "Noma'lum xatolik"

            # FOYDALANUVCHIGA:
            user_error_msg = f"❌ <b>№{order_id} buyurtmani amalga oshirib bo'lmadi va u bekor qilindi.</b>\n💰 <b>{price:,} so'm</b> balansingizga qaytarildi."
            await query.message.edit_text(user_error_msg, parse_mode="HTML")
            await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)

            # ADMINGA:
            admin_error_log = (
                f"🚨 <b>BUYURTMA XATOLIK BILAN BEKOR QILINDI (№{order_id})</b>\n\n"
                f"👤 Mijoz: {md_escape(user_tag)} (ID: <code>{uid}</code>)\n"
                f"🛍 Xizmat: {md_escape(service)}\n"
                f"🔗 Target: {md_escape(target)}\n"
                f"💰 Summa: {price:,} so'm (Qaytarildi)\n"
                f"⚠️ <b>Xatolik sababi:</b> <code>{md_escape(raw_err)}</code>"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_GROUP, text=admin_error_log, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Guruhga xatolik logini yuborishda xato: {e}")

            context.user_data.clear()
            return MAIN

        set_order_status(order_id, "Bajarildi")

        completed_admin_msg = (
            f"✅ <b>BUYURTMA MUVAFFAQIYATLI BAJARILDI (№{order_id})</b>\n\n"
            f"👤 Mijoz: {md_escape(user_tag)} (ID: <code>{uid}</code>)\n"
            f"🛍 Xizmat turi: {md_escape(service)}\n"
            f"🔗 Manzil/Target: {md_escape(target)}\n"
            f"📋 Detal: {md_escape(context.user_data.get('detail'))}\n"
            f"📈 Miqdor: {context.user_data.get('qty')}\n"
            f"💰 Yechilgan summa: {price:,} so'm\n"
            f"⚡ Holat: <b>Avtomatik Bajarildi</b>"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_GROUP, text=completed_admin_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Guruhga xabar yuborishda xato: {e}")

        await query.message.edit_text(f"✅ №{order_id} buyurtma muvaffaqiyatli bajarildi!")
        await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)
        context.user_data.clear()
        return MAIN

    # Qo'lda bajariladigan buyurtmalar (Gift va h.k.)
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Qabul qilish", callback_data=f"order_accept_{order_id}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"order_reject_{order_id}")
        ]
    ])

    admin_msg = (
        f"📦 <b>YANGI BUYURTMA (№{order_id})</b>\n\n"
        f"👤 Mijoz: {md_escape(user_tag)} (ID: <code>{uid}</code>)\n"
        f"🛍 Xizmat turi: {md_escape(service)}\n"
        f"🔗 Manzil/Target: {md_escape(target)}\n"
        f"📋 Detal: {md_escape(context.user_data.get('detail'))}\n"
        f"📈 Miqdor: {context.user_data.get('qty')}\n"
        f"💰 Yechilgan summa: {price:,} so'm"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_GROUP, text=admin_msg, reply_markup=buttons, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Guruhga yangi buyurtma yuborishda xato: {e}")

    await query.message.edit_text("✅ Buyurtma qabul qilindi! Admin tasdig'ini kuting.")
    await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)
    context.user_data.clear()
    return MAIN

# ==================== TO'LOV HANDLERLARI ====================
async def pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    clean_text = text.replace(' ', '').replace('.', '').replace(',', '').replace("'", "")

    if not clean_text.isdigit():
        await update.message.reply_text("❌ Iltimos summani faqat raqamda kiriting (Masalan: 15000 yoki 1 000):")
        return PAY_AMOUNT

    amount = int(clean_text)
    if amount < 1000 or amount > 2500000:
        await update.message.reply_text("❌ Summa 1 000 so'm va 2 500 000 so'm oralig'ida bo'lishi kerak.")
        return PAY_AMOUNT

    uid = update.message.from_user.id
    try:
        payment_id, exact_amount, code, created_at, expires_at = create_payment(uid, amount)
    except Exception as e:
        logger.error(f"To'lov yaratishda xato: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return MAIN

    start_str = datetime.fromtimestamp(created_at, ZoneInfo("Asia/Tashkent")).strftime("%H:%M:%S")
    end_str = datetime.fromtimestamp(expires_at, ZoneInfo("Asia/Tashkent")).strftime("%H:%M:%S")

    pay_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ To'lovni tekshirish", callback_data=f"check_pay_{payment_id}")],
        [InlineKeyboardButton("⚠️ Bekor qilish", callback_data=f"cancel_pay_{payment_id}")]
    ])

    msg_text = (
        f"✅ <b>To'lov so'rovi yaratildi!</b>\n\n"
        f"🏷 Buyurtma kodi: <code>{code}</code>\n"
        f"💰 To'lanadigan ANIQ summa: <b>{exact_amount:,} so'm</b>\n\n"
        f"💳 To'lov uchun karta:\n<code>{CARD_NUMBER}</code>\n"
        f"👤 Egasi: {CARD_HOLDER}\n\n"
        f"⚠️ <b>Eslatma:</b> Kartaga <b>aynan {exact_amount:,} so'm</b> o'tkazishingiz kerak. Boshqa summa o'tkazilsa avtomatik moslashtirilmaydi.\n\n"
        f"⚠️ <b>Kutilish muddati:</b> {start_str} — {end_str} (5 daqiqa)"
    )

    await update.message.reply_text(msg_text, parse_mode="HTML", reply_markup=pay_buttons)
    context.user_data.clear()
    return MAIN

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    payment_id = int(data.split("_")[-1])

    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT status, exact_amount, expires_at FROM pending_payments WHERE payment_id = ?", (payment_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await query.answer("❌ To'lov so'rovi topilmadi.", show_alert=True)
        return

    status, exact_amount, expires_at = row
    now = int(time.time())

    if status == 'completed':
        await query.answer("🎉 To'lov muvaffaqiyatli qabul qilindi!", show_alert=True)
        await query.message.edit_text(f"✅ Ushbu to'lov ({exact_amount:,} so'm) hisobingizga tushirildi.")
    elif status == 'cancelled':
        await query.answer("❌ Bu to'lov so'rovi bekor qilingan.", show_alert=True)
    elif now > expires_at:
        await query.answer("⏰ To'lov kutilish vaqti (5 daqiqa) tugagan.", show_alert=True)
    else:
        await query.answer(f"⏳ To'lov hali kelmadi. Kartaga aynan {exact_amount:,} so'm o'tkazganingizdan so'ng biroz kuting.", show_alert=True)

async def cancel_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    payment_id = int(data.split("_")[-1])

    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE pending_payments SET status = 'cancelled' WHERE payment_id = ?", (payment_id,))
    conn.commit()
    conn.close()

    await query.message.edit_text("⚠️ To'lov so'rovi bekor qilindi.")
    await query.message.reply_text("👇 Kerakli xizmatni tanlang:", reply_markup=main_menu)

# ==================== ADMIN PANEL HANDLERLARI (/sredo) ====================
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid not in ADMINS:
        return
    await update.message.reply_text("⚙️ <b>SREDO Admin Paneliga xush kelibsiz!</b>", parse_mode="HTML", reply_markup=admin_menu)
    return ADMIN_MAIN

async def admin_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.message.from_user.id
    if uid not in ADMINS:
        return

    if text == "📊 Statistika":
        conn = db_connect(DB_NAME)
        cursor = conn.cursor()
        users_count = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        orders_count = cursor.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        payments_sum = cursor.execute("SELECT SUM(amount) FROM payments WHERE status='Tasdiqlandi'").fetchone()[0] or 0
        conn.close()

        try:
            ton_bal = await asyncio.wait_for(_get_ton_balance(), timeout=8)
            ton_text = f"{ton_bal:.4f} TON"
        except Exception as e:
            logger.warning("Statistikada Fragment balansini olishda xato: %s", e)
            ton_text = "❌ Hozircha olinmadi"

        await update.message.reply_text(
            f"📊 <b>Bot Statistikasi:</b>\n\n"
            f"👥 Jami foydalanuvchilar: {users_count} ta\n"
            f"📦 Jami buyurtmalar: {orders_count} ta\n"
            f"💰 Tasdiqlangan jami tushum: {payments_sum:,} so'm\n"
            f"💎 Fragment Wallet Balansi: {ton_text}",
            parse_mode="HTML",
            reply_markup=admin_menu
        )
        return ADMIN_MAIN

    elif text == "⚙️ Narxlarni o'zgartirish":
        p_star = get_setting('price_star', 190)
        p3 = get_setting('premium_3', 145000)
        p6 = get_setting('premium_6', 195000)
        p12 = get_setting('premium_12', 340000)

        menu = ReplyKeyboardMarkup([
            [f"Stars narxi ({p_star} so'm/ta)"],
            [f"Premium 3 oy ({p3} so'm)", f"Premium 6 oy ({p6} so'm)"],
            [f"Premium 1 yil ({p12} so'm)"],
            ["◄ Orqaga"]
        ], resize_keyboard=True)
        await update.message.reply_text("⚙️ Qaysi narxni o'zgartirmoqchisiz?", reply_markup=menu)
        return SET_PRICE_CHOOSE

    elif text == "💎 Stars narxlari (Fragment)":
        wait_msg = await update.message.reply_text("⏳ Fragment'dan real narxlar olinmoqda...")
        try:
            prices = await asyncio.wait_for(
                get_fragment_stars_prices_bulk(STARS_PACKAGES), timeout=15
            )
            try:
                ton_bal = await asyncio.wait_for(_get_ton_balance(), timeout=8)
                ton_text = f"{ton_bal:.4f} TON"
            except Exception as e:
                logger.warning("Fragment wallet balansini olishda xato: %s", e)
                ton_text = "❌ Hozircha olinmadi"

            lines = ["💎 <b>Fragment'dagi REAL Stars narxlari (TON)</b>\n"]
            for qty in STARS_PACKAGES:
                p = prices.get(qty)
                if p is None:
                    lines.append(f"⭐ {qty:,} Stars — ❌ olinmadi")
                else:
                    lines.append(f"⭐ {qty:,} Stars — {p:.4f} TON")

            lines.append(f"\n💰 Bot wallet balansi: <b>{ton_text}</b>")

            await wait_msg.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.exception("Stars narxlarini olishda xato")
            await wait_msg.edit_text(f"❌ Xato: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return ADMIN_MAIN

    elif text == "📢 Xabar yuborish":
        await update.message.reply_text(
            "✍️ Barcha obunachilarga yuboriladigan xabarni kiriting (Matn, rasm yoki media formatda bo'lishi mumkin):",
            reply_markup=ReplyKeyboardMarkup([["◄ Orqaga"]], resize_keyboard=True)
        )
        return ADMIN_BROADCAST_MSG

    elif text == "💳 ID orqali balans to'ldirish":
        await update.message.reply_text("✍️ Foydalanuvchi ID raqamini kiriting:", reply_markup=ReplyKeyboardMarkup([["◄ Orqaga"]], resize_keyboard=True))
        return ADMIN_PAY_ID

    elif text == "🔻 ID orqali balans ayirish":
        await update.message.reply_text("✍️ Balansidan pul ayiriladigan foydalanuvchi ID raqamini kiriting:", reply_markup=ReplyKeyboardMarkup([["◄ Orqaga"]], resize_keyboard=True))
        return ADMIN_SUB_ID

    elif text == "🏠 Asosiy menyu":
        return await start(update, context)

# ==================== ADMIN BROADCAST HANDLER ====================
async def admin_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◄ Orqaga":
        await update.message.reply_text("Admin paneli:", reply_markup=admin_menu)
        return ADMIN_MAIN

    user_ids = get_all_user_ids()
    total = len(user_ids)
    sent = 0
    failed = 0

    progress_msg = await update.message.reply_text(f"⏳ Xabar yuborilmoqda... (0/{total})")

    for uid in user_ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1

    await progress_msg.edit_text(
        f"✅ <b>Xabar yuborish yakunlandi!</b>\n\n"
        f"📊 Jami foydalanuvchilar: {total} ta\n"
        f"🟢 Muvaffaqiyatli yetkazildi: {sent} ta\n"
        f"🔴 Etib bormadi (bloklagan): {failed} ta",
        parse_mode="HTML"
    )
    await update.message.reply_text("Admin paneli:", reply_markup=admin_menu)
    return ADMIN_MAIN

async def set_price_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◄ Orqaga":
        await update.message.reply_text("Admin paneli:", reply_markup=admin_menu)
        return ADMIN_MAIN

    if "Stars" in text:
        context.user_data["edit_key"] = "price_star"
    elif "3 oy" in text:
        context.user_data["edit_key"] = "premium_3"
    elif "6 oy" in text:
        context.user_data["edit_key"] = "premium_6"
    elif "1 yil" in text:
        context.user_data["edit_key"] = "premium_12"
    else:
        await update.message.reply_text("❌ Noto'g'ri tanlov.")
        return SET_PRICE_CHOOSE

    await update.message.reply_text("✍️ Yangi narxni kiriting:")
    return SET_PRICE_VALUE

async def set_price_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text.isdigit():
        await update.message.reply_text("❌ Narx faqat raqamlardan iborat bo'lishi kerak:")
        return SET_PRICE_VALUE

    key = context.user_data.get("edit_key")
    set_setting(key, int(text))
    await update.message.reply_text("✅ Narx muvaffaqiyatli yangilandi!", reply_markup=admin_menu)
    return ADMIN_MAIN

# ==================== BALANS QO'SHISH HANDLERLARI ====================
async def admin_pay_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◄ Orqaga":
        await update.message.reply_text("Admin paneli:", reply_markup=admin_menu)
        return ADMIN_MAIN
    if not text.isdigit():
        await update.message.reply_text("❌ ID faqat raqam bo'ladi:")
        return ADMIN_PAY_ID

    context.user_data["admin_target_id"] = int(text)
    await update.message.reply_text("✍️ Qancha summa qo'shmoqchisiz?:")
    return ADMIN_PAY_SUM

async def admin_pay_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text.isdigit():
        await update.message.reply_text("❌ Faqat raqam kiriting:")
        return ADMIN_PAY_SUM

    target_id = context.user_data.get("admin_target_id")
    summa = int(text)

    update_balance(target_id, summa)
    bal, _ = get_user_data(target_id)
    await update.message.reply_text(f"✅ ID: {target_id} balansiga {summa:,} so'm qo'shildi!\nJoriy balans: {bal:,} so'm", reply_markup=admin_menu)
    try:
        await context.bot.send_message(target_id, f"💳 Hisobingiz admin tomonidan {summa:,} so'mga to'ldirildi!\nJoriy balansingiz: {bal:,} so'm")
    except Exception:
        pass
    return ADMIN_MAIN

# ==================== BALANS AYIRISH HANDLERLARI ====================
async def admin_sub_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◄ Orqaga":
        await update.message.reply_text("Admin paneli:", reply_markup=admin_menu)
        return ADMIN_MAIN
    if not text.isdigit():
        await update.message.reply_text("❌ ID faqat raqam bo'ladi:")
        return ADMIN_SUB_ID

    context.user_data["admin_sub_target_id"] = int(text)
    await update.message.reply_text("✍️ Qancha summa ayirmoqchisiz?:")
    return ADMIN_SUB_SUM

async def admin_sub_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text.isdigit():
        await update.message.reply_text("❌ Faqat raqam kiriting:")
        return ADMIN_SUB_SUM

    target_id = context.user_data.get("admin_sub_target_id")
    summa = int(text)

    update_balance(target_id, -summa)
    bal, _ = get_user_data(target_id)
    await update.message.reply_text(f"✅ ID: {target_id} balansidan {summa:,} so'm ayirildi!\nJoriy balans: {bal:,} so'm", reply_markup=admin_menu)
    try:
        await context.bot.send_message(target_id, f"🔻 Hisobingizdan admin tomonidan {summa:,} so'm ayirildi.\nJoriy balansingiz: {bal:,} so'm")
    except Exception:
        pass
    return ADMIN_MAIN

# ==================== ADMIN INLINE HANDLER ====================
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data.split("_")
    turi = data[0]
    holat = data[1]
    idx = int(data[2])

    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    has_caption = bool(query.message.caption)

    if turi == "order":
        cursor.execute("SELECT user_id, price, status FROM orders WHERE order_id = ?", (idx,))
        order = cursor.fetchone()
        if not order or order[2] != 'Kutilmoqda':
            msg = "⚠️ Bu buyurtma ko'rib chiqilgan!"
            await query.edit_message_caption(caption=msg) if has_caption else await query.edit_message_text(text=msg)
            conn.close()
            return

        user_id, price, _ = order
        if holat == "accept":
            cursor.execute("UPDATE orders SET status = 'Bajarildi' WHERE order_id = ?", (idx,))
            msg = f"✅ №{idx} Buyurtma qabul qilindi."
            await query.edit_message_caption(caption=msg) if has_caption else await query.edit_message_text(text=msg)
            try:
                await context.bot.send_message(user_id, f"✅ №{idx} raqamli buyurtmangiz muvaffaqiyatli bajarildi!")
            except Exception:
                pass
        else:
            cursor.execute("UPDATE orders SET status = 'Rad etildi' WHERE order_id = ?", (idx,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id))
            msg = f"❌ №{idx} Buyurtma rad etildi, pul qaytarildi."
            await query.edit_message_caption(caption=msg) if has_caption else await query.edit_message_text(text=msg)
            try:
                await context.bot.send_message(user_id, f"❌ №{idx} raqamli buyurtmangiz rad etildi.\n💰 {price:,} so'm qaytarildi.")
            except Exception:
                pass

    conn.commit()
    conn.close()

# ==================== GURUH BALANS HANDLERI ====================
async def group_balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type not in ["group", "supergroup"]:
        return

    if update.message.from_user.id not in ADMINS:
        return

    text = update.message.text
    match = re.match(r"^\.([A-Za-z0-9_]+)\s+([+-]?\d+)$", text)
    if not match:
        return

    target = match.group(1)
    amount = int(match.group(2))

    conn = db_connect(DB_NAME)
    cursor = conn.cursor()
    user_id = None
    if target.isdigit():
        user_id = int(target)
    else:
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (target,))
        res = cursor.fetchone()
        if res:
            user_id = res[0]

    if user_id:
        cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, incognito) VALUES (?, 0, 0)", (user_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()

        bal, _ = get_user_data(user_id)
        ishora = "qo'shildi" if amount >= 0 else "ayirildi"
        await update.message.reply_text(f"✅ Muvaffaqiyatli! Foydalanuvchi: <code>{md_escape(target)}</code> balansidan {abs(amount):,} so'm {ishora}.\nJoriy balans: {bal:,} so'm", parse_mode="HTML")
        try:
            await context.bot.send_message(user_id, f"💳 Hisobingiz guruh orqali admin tomonidan {abs(amount):,} so'm {ishora}!\nJoriy balansingiz: {bal:,} so'm")
        except Exception:
            pass
    else:
        conn.close()
        await update.message.reply_text("❌ Foydalanuvchi bot bazasidan topilmadi.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Xatolik yuz berdi:", exc_info=context.error)

# ==================== POST INIT VA POST SHUTDOWN ====================
async def post_init(application: Application):
    telethon_client.ptb_bot = application.bot
    await telethon_client.start()
    logger.info("⚡ Telethon Humo Listener muvaffaqiyatli ishga tushdi!")

async def post_shutdown(application: Application):
    if telethon_client.is_connected():
        await telethon_client.disconnect()
        logger.info("🛑 Telethon client o'chirildi.")

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)

    bot_app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("sredo", admin_start)
        ],
        states={
            MAIN: [
                CallbackQueryHandler(main_menu_callback, pattern=r"^main_(stars|premium|gift|accounts|balance|profile|help)$"),
                CallbackQueryHandler(profile_callback_handler, pattern=r"^prof_(orders|top|incognito|back)$"),
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                CallbackQueryHandler(stars_qty_callback, pattern=r"^stars_(qty_50|qty_100|qty_150|qty_custom|back)$"),
                CallbackQueryHandler(confirm_order_callback, pattern=r"^confirm_(yes|no)$"),
                CallbackQueryHandler(check_payment_callback, pattern=r"^check_pay_\d+$"),
                CallbackQueryHandler(cancel_payment_callback, pattern=r"^cancel_pay_\d+$")
            ],
            STARS_TARGET: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, stars_target)
            ],
            STARS_QTY_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, stars_qty_input)],
            PREMIUM_TARGET: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, premium_target)
            ],
            PREMIUM_CHOOSE: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                CallbackQueryHandler(premium_choose_callback, pattern=r"^prem_\d+$")
            ],
            GIFT_TARGET: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, gift_target)
            ],
            GIFT_CHOOSE: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_(self|back)$"),
                CallbackQueryHandler(gift_choose_callback, pattern=r"^gift_\d+_.+$")
            ],
            PAY_AMOUNT: [
                CallbackQueryHandler(target_callback_handler, pattern=r"^target_back$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, pay_amount)
            ],

            ADMIN_MAIN: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_main_handler)],
            ADMIN_BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_msg)],
            SET_PRICE_CHOOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price_choose)],
            SET_PRICE_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price_value)],
            ADMIN_PAY_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pay_id)],
            ADMIN_PAY_SUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pay_sum)],
            ADMIN_SUB_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_sub_id)],
            ADMIN_SUB_SUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_sub_sum)]
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("sredo", admin_start)
        ],
        per_chat=True,
        per_user=True,
        per_message=False
    )

    bot_app.add_handler(conv_handler)
    bot_app.add_handler(CommandHandler("wallet", fragment_wallet_command))
    bot_app.add_handler(CommandHandler("fragment_cookie_status", fragment_cookie_status_command))
    bot_app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^(order|pay)_(accept|reject)_\d+$"))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, group_balance_handler))
    bot_app.add_error_handler(error_handler)

    print("🤖 TELEGRAM BOT ISHGA TUSHMOQDA...")
    bot_app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        print("Bot to'xtatildi.")