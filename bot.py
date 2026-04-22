#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# بوت متجر بايثون المتطور (النسخة العملاقة المطورة بالكامل)
# تمت إضافة جميع الميزات المطلوبة دون حذف أي شيء من الكود الأصلي
# الميزات المضافة: تحويل النقاط، دعم لغتين، تصنيفات متقدمة، إشعارات، صيانة، إحصائيات، حظر، طلبات معلقة، مسابقات، حماية، إعادة تشغيل ذاتي، دفع عبر Asia
# ============================================================

import subprocess, sys, os

# تثبيت تلقائي للمكتبات
required = ["python-telegram-bot>=20.0", "aiosqlite", "aiohttp", "nest-asyncio", "beautifulsoup4", "qrcode", "pillow"]
for pkg in required:
    try:
        pkg_name = pkg.split(">=")[0].replace("-", "_")
        __import__(pkg_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

import asyncio
import logging
import random
import string
import json
import time
import hashlib
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any

import nest_asyncio
nest_asyncio.apply()

import aiosqlite
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, ContextTypes
)
from telegram.error import TimedOut, NetworkError, RetryAfter
from bs4 import BeautifulSoup

# ========== الإعدادات الأساسية ==========
TOKEN = "8397067040:AAFSUdSHYurOna8UTfxvuXBTnPCH9p63HMQ"
ADMIN_ID = 7947679527
ADMIN_USERNAME = "@ggzh9"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
DB_PATH = "store_bot.db"

# ========== إعدادات الأداء ومعالجة الأخطاء ==========
MAX_CONCURRENT_REQUESTS = 100
CACHE_TTL = 300
RETRY_LIMIT = 3
RETRY_DELAY = 1

# قاموس لتتبع طلبات المستخدمين (Rate Limiting)
user_requests = {}

# ========== دالة لإعادة المحاولة الذكية ==========
async def send_with_retry(context, chat_id, text=None, reply_markup=None, parse_mode=None, document=None, caption=None, photo=None):
    for attempt in range(RETRY_LIMIT):
        try:
            if document:
                return await context.bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30
                )
            elif photo:
                return await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30
                )
            else:
                return await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30
                )
        except (TimedOut, NetworkError, RetryAfter) as e:
            logger.warning(f"إعادة محاولة {attempt+1}/{RETRY_LIMIT} بسبب {e}")
            if attempt < RETRY_LIMIT - 1:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            else:
                logger.error(f"فشل الإرسال بعد {RETRY_LIMIT} محاولات")
                raise
    return None

# ========== دالة التحقق من معدل الطلبات (Rate Limiting) ==========
async def check_rate_limit(user_id: int, limit_per_minute: int = 30) -> bool:
    now = time.time()
    if user_id not in user_requests:
        user_requests[user_id] = []
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < 60]
    if len(user_requests[user_id]) >= limit_per_minute:
        return False
    user_requests[user_id].append(now)
    return True

# ========== إعدادات الصيانة ==========
maintenance_mode = False
maintenance_message = "🔧 البوت في وضع الصيانة حالياً، يرجى المحاولة لاحقاً."

# ========== تهيئة قاعدة البيانات (شاملة لكل الجداول) ==========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        
        # المستخدمين (مع إضافة لغة وحظر)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_date TEXT,
                points INTEGER DEFAULT 0,
                referrer_id INTEGER DEFAULT NULL,
                last_active TEXT,
                language TEXT DEFAULT 'ar',
                is_banned INTEGER DEFAULT 0,
                ban_reason TEXT DEFAULT NULL
            )
        """)
        # التحقق من وجود الأعمدة الجديدة
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "last_active" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
        if "referrer_id" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL")
        if "language" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ar'")
        if "is_banned" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        if "ban_reason" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT NULL")
        
        # الأدوات (الخدمات) مع إضافة صورة الفئة
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                tool_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                file_id TEXT,
                is_available INTEGER DEFAULT 1,
                created_date TEXT,
                category TEXT DEFAULT 'عام',
                category_image TEXT,
                min_limit INTEGER DEFAULT 1,
                max_limit INTEGER DEFAULT 9999,
                api_url TEXT,
                api_key TEXT
            )
        """)
        
        # الطلبات
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tool_id INTEGER,
                quantity INTEGER DEFAULT 1,
                total_price INTEGER,
                order_date TEXT,
                status TEXT DEFAULT 'completed'
            )
        """)
        
        # الطلبات المعلقة (يدوية)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tool_id INTEGER,
                tool_name TEXT,
                quantity INTEGER DEFAULT 1,
                total_price INTEGER,
                request_date TEXT,
                status TEXT DEFAULT 'pending',
                admin_note TEXT
            )
        """)
        
        # قنوات جمع النقاط
        await db.execute("""
            CREATE TABLE IF NOT EXISTS points_channels (
                channel_id TEXT PRIMARY KEY,
                channel_username TEXT,
                channel_name TEXT,
                points INTEGER DEFAULT 10
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_rewards (
                user_id INTEGER,
                channel_id TEXT,
                rewarded INTEGER DEFAULT 0,
                reward_date TEXT,
                PRIMARY KEY (user_id, channel_id)
            )
        """)
        
        # أكواد النقاط
        await db.execute("""
            CREATE TABLE IF NOT EXISTS points_codes (
                code TEXT PRIMARY KEY,
                points INTEGER,
                created_by INTEGER,
                used_by INTEGER DEFAULT NULL,
                used_date TEXT DEFAULT NULL,
                is_used INTEGER DEFAULT 0
            )
        """)
        
        # إعدادات الإحالة
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_settings (
                id INTEGER PRIMARY KEY CHECK (id=1),
                points_per_referral INTEGER DEFAULT 50
            )
        """)
        await db.execute("INSERT OR IGNORE INTO referral_settings (id, points_per_referral) VALUES (1, 50)")
        
        # جداول الاشتراك الإجباري (قنوات، بوتات، حسابات تواصل)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forced_channels (
                channel_id TEXT PRIMARY KEY,
                channel_username TEXT,
                channel_name TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forced_bots (
                bot_id TEXT PRIMARY KEY,
                bot_username TEXT,
                bot_name TEXT,
                invite_link TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forced_social (
                social_id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                account_url TEXT,
                account_name TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_bot_activation (
                user_id INTEGER,
                bot_id TEXT,
                attempts INTEGER DEFAULT 0,
                activated INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, bot_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_social_follow (
                user_id INTEGER,
                social_id INTEGER,
                confirmed INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, social_id)
            )
        """)
        
        # أنشطة تجميع النقاط
        await db.execute("""
            CREATE TABLE IF NOT EXISTS point_collections (
                collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                points INTEGER,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_collection_rewards (
                user_id INTEGER,
                collection_id INTEGER,
                rewarded INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, collection_id)
            )
        """)
        
        # الأقسام (مع إضافة صورة)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                image_id TEXT,
                priority INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("INSERT OR IGNORE INTO categories (name, priority) VALUES ('عام', 0)")
        
        # إعدادات عامة
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # إعدادات افتراضية
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('site_url', '')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('site_token', '')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_transfer', '20')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('free_mode', 'on')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_gift', 'on')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('gift_points', '10')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('share_points', '5')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_name', 'متجر بايثون')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_channel', '')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('terms_text', 'شروط الاستخدام: لا تشارك الأدوات مع الآخرين')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('buy_text', 'لشراء الرصيد تواصل مع المطور @ggzh9')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('prize_text', 'جوائزنا: 100 نقطة لكل دعوة')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('asia_api_key', '')")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('asia_merchant_id', '')")
        
        # جدول إحصائيات الدعوات
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                date TEXT,
                FOREIGN KEY(referrer_id) REFERENCES users(user_id),
                FOREIGN KEY(referred_id) REFERENCES users(user_id)
            )
        """)
        
        # جدول الهدية اليومية
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_gifts (
                user_id INTEGER PRIMARY KEY,
                last_gift_date TEXT
            )
        """)
        
        # جدول تحويل النقاط
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transfer_log (
                transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user INTEGER,
                to_user INTEGER,
                amount INTEGER,
                date TEXT
            )
        """)
        
        # جدول الإشعارات
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                date TEXT,
                is_read INTEGER DEFAULT 0
            )
        """)
        
        # جدول إعدادات الإشعارات للمستخدمين
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_notification_settings (
                user_id INTEGER PRIMARY KEY,
                new_tools INTEGER DEFAULT 1,
                promotions INTEGER DEFAULT 1
            )
        """)
        
        # جدول المسابقات
        await db.execute("""
            CREATE TABLE IF NOT EXISTS contests (
                contest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                answer TEXT,
                points INTEGER,
                created_by INTEGER,
                created_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS contest_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contest_id INTEGER,
                user_id INTEGER,
                answer TEXT,
                date TEXT,
                is_correct INTEGER DEFAULT 0
            )
        """)
        
        # جدول إحصائيات البوت
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_stats (
                stat_date TEXT PRIMARY KEY,
                total_users INTEGER,
                total_orders INTEGER,
                total_points_spent INTEGER
            )
        """)
        
        # جدول طلبات الدفع عبر Asia
        await db.execute("""
            CREATE TABLE IF NOT EXISTS asia_payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount INTEGER,
                status TEXT DEFAULT 'pending',
                transaction_id TEXT,
                request_date TEXT,
                completed_date TEXT
            )
        """)
        
        await db.commit()
        logger.info("✅ تم تهيئة قاعدة البيانات بنجاح")

# ========== دوال المستخدمين ==========
async def add_user(user_id: int, username: str, full_name: str, referrer_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        exists = await cursor.fetchone()
        if not exists:
            await db.execute(
                "INSERT INTO users (user_id, username, full_name, joined_date, points, referrer_id, last_active) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (user_id, username or "", full_name or "", datetime.now().isoformat(), referrer_id, datetime.now().isoformat())
            )
            await db.commit()
            if referrer_id:
                cur = await db.execute("SELECT points_per_referral FROM referral_settings WHERE id=1")
                points = (await cur.fetchone())[0]
                await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, referrer_id))
                await db.commit()
                await db.execute("INSERT INTO referral_log (referrer_id, referred_id, date) VALUES (?, ?, ?)",
                                 (referrer_id, user_id, datetime.now().isoformat()))
                await db.commit()
        else:
            await db.execute("UPDATE users SET username=?, full_name=?, last_active=? WHERE user_id=?", 
                           (username or "", full_name or "", datetime.now().isoformat(), user_id))
            await db.commit()

async def is_user_banned(user_id: int) -> Tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_banned, ban_reason FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row and row[0] == 1:
            return True, row[1] or "تم حظرك من استخدام البوت"
        return False, ""

async def ban_user(user_id: int, reason: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, user_id))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=0, ban_reason=NULL WHERE user_id=?", (user_id,))
        await db.commit()

async def get_user_points(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

async def update_user_points(user_id: int, delta: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET points = points + ? WHERE user_id=?", (delta, user_id))
        await db.commit()

async def get_all_users() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE is_banned=0")
        rows = await cur.fetchall()
        return [row[0] for row in rows]

async def get_total_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE is_banned=0")
        row = await cur.fetchone()
        return row[0] if row else 0

async def get_referral_stats(user_id: int) -> Tuple[int, List[Tuple]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT u.user_id, u.full_name, u.username, rl.date 
            FROM referral_log rl
            JOIN users u ON rl.referred_id = u.user_id
            WHERE rl.referrer_id = ?
            ORDER BY rl.date DESC
        """, (user_id,))
        rows = await cur.fetchall()
        return len(rows), rows

# ========== دوال اللغة ==========
async def get_user_language(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else "ar"

async def set_user_language(user_id: int, language: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET language=? WHERE user_id=?", (language, user_id))
        await db.commit()

# قاموس الترجمات
translations = {
    "ar": {
        "welcome": "🌟 مرحباً بك في متجر بايثون المتطور!\n\n✨ يمكنك شراء الأدوات باستخدام النقاط\n🎁 اجمع النقاط عبر الانضمام للقنوات أو دعوة الأصدقاء أو استخدام أكواد النقاط.\n👇 استخدم الأزرار أدناه.",
        "shop": "🛒 قائمة الأدوات:",
        "no_tools": "⚠️ لا توجد أدوات حالياً.",
        "points": "⭐ رصيد نقاطك: {} نقطة",
        "collect_points": "🎁 نتائج تجميع النقاط:\n{}\n\n✨ المجموع: {} نقطة\n⭐ رصيدك الجديد: {}",
        "no_new_points": "ℹ️ لا توجد نقاط جديدة.\n⭐ رصيدك: {}",
        "daily_gift": "🎁 تم استلام هديتك اليومية: +{} نقطة!\n⭐ رصيدك الآن: {}",
        "daily_gift_already": "⚠️ لقد استلمت هديتك اليومية بالفعل!\nعُد غداً للحصول على هدية جديدة.",
        "daily_gift_disabled": "🎁 الهدية اليومية معطلة حالياً.",
        "code_sent": "🎫 أرسل الكود:",
        "code_success": "✅ تم إضافة {} نقطة!",
        "code_invalid": "❌ كود غير صالح أو مستخدم.",
        "referral_link": "🔗 **رابط دعوتك:**\n{}\n\n🎁 كل مدعو يمنحك {} نقطة.{}",
        "no_referrals": "\n\n📊 لم يدعُ أحد بعد.",
        "purchases": "📦 **مشترياتك:**\n{}",
        "no_purchases": "📦 لم تشترِ أي شيء بعد.",
        "stats": "📊 **إحصائياتك:**\n⭐ النقاط: {}\n🛒 المشتريات: {}\n👥 المدعوون: {}",
        "about": "🤖 {}\nالإصدار 5.0\n© @ggzh9",
        "support": "📞 الدعم: {}",
        "back": "🔙 الرئيسية",
        "buy_success": "🎉 تم شراء {} (كمية {}) بنجاح!\n{}",
        "buy_fail_points": "❌ نقاطك غير كافية!\n⭐ رصيدك: {} نقطة\n💰 السعر الإجمالي: {} نقطة",
        "buy_fail_limit_min": "❌ الحد الأدنى للشراء هو {}.",
        "buy_fail_limit_max": "❌ الحد الأقصى للشراء هو {}.",
        "buy_fail_tool": "❌ الأداة غير موجودة.",
        "transfer_title": "💰 **تحويل نقاط**\nرصيدك الحالي: {} نقطة\n\nأرسل معرف المستخدم والمبلغ:\nمثال: `123456789 50`",
        "transfer_success": "✅ تم تحويل {} نقطة إلى المستخدم {}\nرصيدك الجديد: {} نقطة",
        "transfer_fail_self": "❌ لا يمكنك تحويل النقاط لنفسك.",
        "transfer_fail_balance": "❌ رصيدك غير كافي. رصيدك: {} نقطة",
        "transfer_fail_user": "❌ المستخدم غير موجود.",
        "transfer_fail_min": "❌ الحد الأدنى للتحويل هو {} نقطة.",
        "transfer_log": "📊 **سجل تحويلاتك:**\n{}",
        "no_transfers": "لا توجد تحويلات سابقة.",
        "maintenance": "🔧 البوت في وضع الصيانة حالياً، يرجى المحاولة لاحقاً.",
        "banned": "🚫 {}\n\nإذا كنت تعتقد أن هذا خطأ، تواصل مع الدعم.",
        "pending_order_sent": "✅ تم إرسال طلب الشراء اليدوي إلى الإدارة.\nسنقوم بالرد عليك قريباً.",
        "contest_question": "🏆 **مسابقة: {}**\n\nالسؤال: {}\n\n🎁 الجائزة: {} نقطة\n\nأرسل إجابتك كرسالة نصية:",
        "contest_correct": "✅ إجابة صحيحة! تم إضافة {} نقطة إلى رصيدك.",
        "contest_wrong": "❌ إجابة خاطئة. حاول مرة أخرى في المسابقات القادمة!",
        "contest_ended": "⏰ انتهت المسابقة، شكراً لمشاركتك!",
        "no_active_contest": "لا توجد مسابقات نشطة حالياً.",
        "category": "📂 **الأقسام:**",
        "select_category": "اختر القسم الذي تريده:",
    },
    "en": {
        "welcome": "🌟 Welcome to Python Store!\n\n✨ You can buy tools with points\n🎁 Collect points by joining channels, inviting friends, or using codes.\n👇 Use the buttons below.",
        "shop": "🛒 Tools List:",
        "no_tools": "⚠️ No tools available.",
        "points": "⭐ Your points balance: {}",
        "collect_points": "🎁 Points collection results:\n{}\n\n✨ Total: {} points\n⭐ Your new balance: {}",
        "no_new_points": "ℹ️ No new points.\n⭐ Your balance: {}",
        "daily_gift": "🎁 You received your daily gift: +{} points!\n⭐ Your new balance: {}",
        "daily_gift_already": "⚠️ You already received your daily gift!\nCome back tomorrow.",
        "daily_gift_disabled": "🎁 Daily gift is currently disabled.",
        "code_sent": "🎫 Send the code:",
        "code_success": "✅ Added {} points!",
        "code_invalid": "❌ Invalid or used code.",
        "referral_link": "🔗 **Your referral link:**\n{}\n\n🎁 Each referral gives you {} points.{}",
        "no_referrals": "\n\n📊 No referrals yet.",
        "purchases": "📦 **Your purchases:**\n{}",
        "no_purchases": "📦 You haven't purchased anything yet.",
        "stats": "📊 **Your stats:**\n⭐ Points: {}\n🛒 Purchases: {}\n👥 Referrals: {}",
        "about": "🤖 {}\nVersion 5.0\n© @ggzh9",
        "support": "📞 Support: {}",
        "back": "🔙 Main Menu",
        "buy_success": "🎉 Successfully purchased {} (quantity {})!\n{}",
        "buy_fail_points": "❌ Insufficient points!\n⭐ Your balance: {} points\n💰 Total price: {} points",
        "buy_fail_limit_min": "❌ Minimum purchase quantity is {}.",
        "buy_fail_limit_max": "❌ Maximum purchase quantity is {}.",
        "buy_fail_tool": "❌ Tool not found.",
        "transfer_title": "💰 **Transfer Points**\nYour balance: {} points\n\nSend user ID and amount:\nExample: `123456789 50`",
        "transfer_success": "✅ Transferred {} points to user {}\nYour new balance: {} points",
        "transfer_fail_self": "❌ You cannot transfer points to yourself.",
        "transfer_fail_balance": "❌ Insufficient balance. Your balance: {} points",
        "transfer_fail_user": "❌ User not found.",
        "transfer_fail_min": "❌ Minimum transfer amount is {} points.",
        "transfer_log": "📊 **Your transfer history:**\n{}",
        "no_transfers": "No previous transfers.",
        "maintenance": "🔧 Bot is under maintenance, please try again later.",
        "banned": "🚫 {}\n\nIf you think this is a mistake, contact support.",
        "pending_order_sent": "✅ Your manual purchase request has been sent to the admin.\nWe will reply to you soon.",
        "contest_question": "🏆 **Contest: {}**\n\nQuestion: {}\n\n🎁 Prize: {} points\n\nSend your answer as a text message:",
        "contest_correct": "✅ Correct answer! Added {} points to your balance.",
        "contest_wrong": "❌ Wrong answer. Try again in future contests!",
        "contest_ended": "⏰ The contest has ended, thank you for participating!",
        "no_active_contest": "No active contests at the moment.",
        "category": "📂 **Categories:**",
        "select_category": "Select a category:",
    }
}

async def get_text(user_id: int, key: str, *args) -> str:
    lang = await get_user_language(user_id)
    text = translations.get(lang, translations["ar"]).get(key, key)
    if args:
        return text.format(*args)
    return text

# ========== دوال الهدية اليومية ==========
async def can_get_daily_gift(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT last_gift_date FROM daily_gifts WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return True
        last_date = datetime.fromisoformat(row[0])
        return datetime.now().date() > last_date.date()

async def mark_daily_gift_received(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO daily_gifts (user_id, last_gift_date) VALUES (?, ?)",
                         (user_id, datetime.now().isoformat()))
        await db.commit()

# ========== دوال الأدوات ==========
async def get_categories() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT cat_id, name, image_id, priority FROM categories WHERE is_active=1 ORDER BY priority DESC, name")
        return await cur.fetchall()

async def update_category_image(cat_id: int, image_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE categories SET image_id=? WHERE cat_id=?", (image_id, cat_id))
        await db.commit()

async def get_tools_by_category(category: str) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tool_id, name, description, price, file_id FROM tools WHERE is_available=1 AND category=? ORDER BY tool_id", (category,))
        return await cur.fetchall()

async def get_all_tools(only_available=True) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        if only_available:
            cur = await db.execute("SELECT tool_id, name, description, price, file_id, category FROM tools WHERE is_available=1 ORDER BY tool_id")
        else:
            cur = await db.execute("SELECT tool_id, name, description, price, file_id, category FROM tools ORDER BY tool_id")
        return await cur.fetchall()

async def get_tool(tool_id: int) -> Optional[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tool_id, name, description, price, file_id, category, min_limit, max_limit, api_url, api_key FROM tools WHERE tool_id=?", (tool_id,))
        return await cur.fetchone()

async def add_tool(name: str, description: str, price: int, file_id: str, category: str = "عام", min_limit: int = 1, max_limit: int = 9999, api_url: str = "", api_key: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tools (name, description, price, file_id, created_date, category, min_limit, max_limit, api_url, api_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, price, file_id, datetime.now().isoformat(), category, min_limit, max_limit, api_url, api_key)
        )
        await db.commit()
        return cur.lastrowid

async def add_tools_batch(tools_list: List[Dict]) -> int:
    count = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for tool in tools_list:
            await db.execute(
                "INSERT INTO tools (name, description, price, file_id, created_date, category, min_limit, max_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tool['name'], tool['description'], tool['price'], tool['file_id'], datetime.now().isoformat(), tool.get('category', 'عام'), tool.get('min_limit', 1), tool.get('max_limit', 9999))
            )
            count += 1
        await db.commit()
    return count

async def update_tool(tool_id: int, name: str, description: str, price: int, file_id: str, category: str = None, min_limit: int = None, max_limit: int = None, api_url: str = None, api_key: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if category is not None:
            await db.execute("UPDATE tools SET name=?, description=?, price=?, file_id=?, category=?, min_limit=?, max_limit=?, api_url=?, api_key=? WHERE tool_id=?", 
                             (name, description, price, file_id, category, min_limit, max_limit, api_url, api_key, tool_id))
        else:
            await db.execute("UPDATE tools SET name=?, description=?, price=?, file_id=? WHERE tool_id=?", 
                             (name, description, price, file_id, tool_id))
        await db.commit()

async def update_tool_price(tool_id: int, new_price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tools SET price=? WHERE tool_id=?", (new_price, tool_id))
        await db.commit()

async def delete_tool(tool_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tools SET is_available=0 WHERE tool_id=?", (tool_id,))
        await db.commit()

async def create_order(user_id: int, tool_id: int, quantity: int = 1, total_price: int = None):
    tool = await get_tool(tool_id)
    if not tool:
        return False
    price = tool[3]
    total = price * quantity if total_price is None else total_price
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO orders (user_id, tool_id, quantity, total_price, order_date) VALUES (?, ?, ?, ?, ?)",
                         (user_id, tool_id, quantity, total, datetime.now().isoformat()))
        await db.commit()
        # تحديث الإحصائيات
        await update_bot_stats()
    return True

async def buy_tool_with_points(user_id: int, tool_id: int, quantity: int, context) -> Tuple[bool, str]:
    tool = await get_tool(tool_id)
    if not tool:
        return False, "❌ الأداة غير موجودة."
    _, name, desc, price, file_id, category, min_limit, max_limit, api_url, api_key = tool
    if quantity < min_limit:
        return False, f"❌ الحد الأدنى للشراء هو {min_limit}."
    if quantity > max_limit:
        return False, f"❌ الحد الأقصى للشراء هو {max_limit}."
    total_price = price * quantity
    points = await get_user_points(user_id)
    if points < total_price:
        return False, f"❌ نقاطك غير كافية!\n⭐ رصيدك: {points} نقطة\n💰 السعر الإجمالي: {total_price} نقطة"
    
    await update_user_points(user_id, -total_price)
    await create_order(user_id, tool_id, quantity, total_price)
    
    if file_id.startswith("http"):
        await send_with_retry(context, user_id, text=f"🎉 تم شراء {name} (كمية {quantity}) بنجاح!\n📥 رابط التحميل: {file_id}")
    else:
        await send_with_retry(context, user_id, document=file_id, caption=f"🎉 تم شراء {name} (كمية {quantity}) بنجاح!")
    
    # إرسال إشعار للمستخدمين الآخرين عن الأداة الجديدة (إذا كانت جديدة)
    await notify_users_new_tool(name, context)
    
    return True, f"✅ تم شراء {name} (كمية {quantity}) وخصم {total_price} نقطة"

# ========== دوال الإشعارات ==========
async def add_notification(user_id: int, message: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO notifications (user_id, message, date) VALUES (?, ?, ?)",
                         (user_id, message, datetime.now().isoformat()))
        await db.commit()

async def get_user_notifications(user_id: int) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, message, date, is_read FROM notifications WHERE user_id=? ORDER BY date DESC LIMIT 20", (user_id,))
        return await cur.fetchall()

async def mark_notification_read(notification_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
        await db.commit()

async def notify_users_new_tool(tool_name: str, context):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM user_notification_settings WHERE new_tools=1")
        users = await cur.fetchall()
        for user in users:
            try:
                await send_with_retry(context, user[0], f"🆕 أداة جديدة!\nتمت إضافة أداة جديدة: {tool_name}\nتفضل بزيارة المتجر لشرائها.")
            except:
                pass

async def set_user_notification_settings(user_id: int, new_tools: int = 1, promotions: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO user_notification_settings (user_id, new_tools, promotions) VALUES (?, ?, ?)",
                         (user_id, new_tools, promotions))
        await db.commit()

# ========== دوال تحويل النقاط ==========
async def transfer_points(from_user: int, to_user: int, amount: int) -> Tuple[bool, str]:
    if from_user == to_user:
        return False, "لا يمكنك التحويل لنفسك"
    
    min_transfer = int(await get_setting("min_transfer"))
    if amount < min_transfer:
        return False, f"الحد الأدنى للتحويل هو {min_transfer} نقطة"
    
    from_points = await get_user_points(from_user)
    if from_points < amount:
        return False, f"رصيدك غير كافي. رصيدك: {from_points} نقطة"
    
    async with aiosqlite.connect(DB_PATH) as db:
        # التحقق من وجود المستخدم
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (to_user,))
        if not await cur.fetchone():
            return False, "المستخدم غير موجود"
        
        await db.execute("UPDATE users SET points = points - ? WHERE user_id=?", (amount, from_user))
        await db.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, to_user))
        await db.execute("INSERT INTO transfer_log (from_user, to_user, amount, date) VALUES (?, ?, ?, ?)",
                         (from_user, to_user, amount, datetime.now().isoformat()))
        await db.commit()
    
    return True, f"تم تحويل {amount} نقطة"

async def get_transfer_history(user_id: int) -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT from_user, to_user, amount, date FROM transfer_log 
            WHERE from_user=? OR to_user=? 
            ORDER BY date DESC LIMIT 20
        """, (user_id, user_id))
        return await cur.fetchall()

# ========== دوال الطلبات المعلقة (يدوية) ==========
async def create_pending_order(user_id: int, tool_id: int, tool_name: str, quantity: int, total_price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO pending_orders (user_id, tool_id, tool_name, quantity, total_price, request_date) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, tool_id, tool_name, quantity, total_price, datetime.now().isoformat())
        )
        await db.commit()
        return cur.lastrowid

async def get_pending_orders(status: str = "pending") -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT order_id, user_id, tool_name, quantity, total_price, request_date, status FROM pending_orders WHERE status=? ORDER BY request_date DESC", (status,))
        return await cur.fetchall()

async def update_pending_order_status(order_id: int, status: str, admin_note: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE pending_orders SET status=?, admin_note=? WHERE order_id=?", (status, admin_note, order_id))
        await db.commit()

# ========== دوال المسابقات ==========
async def create_contest(question: str, answer: str, points: int, created_by: int, duration_hours: int = 24):
    end_date = datetime.now() + timedelta(hours=duration_hours)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO contests (question, answer, points, created_by, created_date, end_date) VALUES (?, ?, ?, ?, ?, ?)",
            (question, answer.lower(), points, created_by, datetime.now().isoformat(), end_date.isoformat())
        )
        await db.commit()
        return cur.lastrowid

async def get_active_contest() -> Optional[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT contest_id, question, answer, points, end_date FROM contests WHERE is_active=1 AND end_date > ?", (datetime.now().isoformat(),))
        return await cur.fetchone()

async def check_contest_answer(contest_id: int, user_id: int, answer: str) -> Tuple[bool, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        # التحقق من الإجابة
        cur = await db.execute("SELECT answer, points FROM contests WHERE contest_id=? AND is_active=1", (contest_id,))
        row = await cur.fetchone()
        if not row:
            return False, 0
        
        correct_answer = row[0]
        points = row[1]
        
        if answer.lower().strip() != correct_answer:
            return False, 0
        
        # التحقق من عدم إجابة المستخدم سابقاً
        cur = await db.execute("SELECT id FROM contest_answers WHERE contest_id=? AND user_id=?", (contest_id, user_id))
        if await cur.fetchone():
            return False, 0
        
        # تسجيل الإجابة الصحيحة
        await db.execute("INSERT INTO contest_answers (contest_id, user_id, answer, date, is_correct) VALUES (?, ?, ?, ?, 1)",
                         (contest_id, user_id, answer, datetime.now().isoformat()))
        await db.commit()
        
        # إضافة النقاط
        await update_user_points(user_id, points)
        
        return True, points

async def end_contest(contest_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE contests SET is_active=0 WHERE contest_id=?", (contest_id,))
        await db.commit()

# ========== دوال إحصائيات البوت ==========
async def update_bot_stats():
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        total_users = await get_total_users()
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE date(order_date) = date('now')")
        total_orders = (await cur.fetchone())[0]
        cur = await db.execute("SELECT SUM(total_price) FROM orders WHERE date(order_date) = date('now')")
        total_points = (await cur.fetchone())[0] or 0
        
        await db.execute("INSERT OR REPLACE INTO bot_stats (stat_date, total_users, total_orders, total_points_spent) VALUES (?, ?, ?, ?)",
                         (today, total_users, total_orders, total_points))
        await db.commit()

async def get_bot_stats() -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        # اليوم
        today = datetime.now().date().isoformat()
        cur = await db.execute("SELECT total_users, total_orders, total_points_spent FROM bot_stats WHERE stat_date=?", (today,))
        today_stats = await cur.fetchone()
        
        # الأسبوع
        week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
        cur = await db.execute("SELECT SUM(total_orders), SUM(total_points_spent) FROM bot_stats WHERE stat_date >= ?", (week_ago,))
        week_stats = await cur.fetchone()
        
        # الشهر
        month_ago = (datetime.now() - timedelta(days=30)).date().isoformat()
        cur = await db.execute("SELECT SUM(total_orders), SUM(total_points_spent) FROM bot_stats WHERE stat_date >= ?", (month_ago,))
        month_stats = await cur.fetchone()
        
        return {
            "today_users": today_stats[0] if today_stats else 0,
            "today_orders": today_stats[1] if today_stats else 0,
            "today_points": today_stats[2] if today_stats else 0,
            "week_orders": week_stats[0] if week_stats else 0,
            "week_points": week_stats[1] if week_stats else 0,
            "month_orders": month_stats[0] if month_stats else 0,
            "month_points": month_stats[1] if month_stats else 0,
        }

# ========== دوال الدفع عبر Asia (رصيد أسيا) ==========
async def create_asia_payment(user_id: int, amount: int) -> str:
    payment_id = hashlib.md5(f"{user_id}{amount}{time.time()}".encode()).hexdigest()[:16]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO asia_payments (payment_id, user_id, amount, request_date) VALUES (?, ?, ?, ?)",
            (payment_id, user_id, amount, datetime.now().isoformat())
        )
        await db.commit()
    return payment_id

async def generate_asia_qr(amount: int) -> BytesIO:
    # إنشاء رمز QR للدفع عبر Asia (مثال، يمكن تخصيصه حسب متطلبات بوابة Asia)
    payment_text = f"ASIA_PAY:{amount}:{int(time.time())}"
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(payment_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

async def verify_asia_payment(payment_id: str, transaction_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, status FROM asia_payments WHERE payment_id=?", (payment_id,))
        row = await cur.fetchone()
        if not row or row[2] != "pending":
            return False
        
        user_id, amount, _ = row
        await db.execute("UPDATE asia_payments SET status='completed', transaction_id=?, completed_date=? WHERE payment_id=?",
                         (transaction_id, datetime.now().isoformat(), payment_id))
        await db.commit()
        
        # إضافة النقاط للمستخدم
        await update_user_points(user_id, amount)
        
        return True

# ========== دوال القنوات لنظام النقاط ==========
async def add_points_channel(channel_id: str, channel_username: str, channel_name: str, points: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO points_channels (channel_id, channel_username, channel_name, points) VALUES (?, ?, ?, ?)",
                         (channel_id, channel_username, channel_name, points))
        await db.commit()

async def get_all_points_channels() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT channel_id, channel_username, channel_name, points FROM points_channels")
        return await cur.fetchall()

async def remove_points_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM points_channels WHERE channel_id=?", (channel_id,))
        await db.commit()

async def has_user_received_channel_reward(user_id: int, channel_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT rewarded FROM user_channel_rewards WHERE user_id=? AND channel_id=?", (user_id, channel_id))
        row = await cur.fetchone()
        return row is not None and row[0] == 1

async def mark_channel_reward(user_id: int, channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO user_channel_rewards (user_id, channel_id, rewarded, reward_date) VALUES (?, ?, 1, ?)",
                         (user_id, channel_id, datetime.now().isoformat()))
        await db.commit()

async def collect_channel_points(user_id: int, context) -> Tuple[int, List[str]]:
    total = 0
    collected = []
    channels = await get_all_points_channels()
    for cid, username, name, points in channels:
        if not await has_user_received_channel_reward(user_id, cid):
            try:
                member = await context.bot.get_chat_member(cid, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    await update_user_points(user_id, points)
                    await mark_channel_reward(user_id, cid)
                    total += points
                    collected.append(f"✅ {name or username}: +{points}")
            except:
                pass
    return total, collected

# ========== دوال أكواد النقاط والإحالة ==========
async def create_points_code(points: int, created_by: int) -> str:
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO points_codes (code, points, created_by) VALUES (?, ?, ?)", (code, points, created_by))
        await db.commit()
    return code

async def redeem_code(code: str, user_id: int) -> Tuple[bool, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT points, is_used FROM points_codes WHERE code=?", (code,))
        row = await cur.fetchone()
        if not row or row[1] == 1:
            return False, 0
        points = row[0]
        await db.execute("UPDATE points_codes SET used_by=?, used_date=?, is_used=1 WHERE code=?", (user_id, datetime.now().isoformat(), code))
        await db.execute("UPDATE users SET points = points + ? WHERE user_id=?", (points, user_id))
        await db.commit()
        return True, points

async def get_referral_points() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT points_per_referral FROM referral_settings WHERE id=1")
        row = await cur.fetchone()
        return row[0] if row else 50

async def set_referral_points(points: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE referral_settings SET points_per_referral=? WHERE id=1", (points,))
        await db.commit()

# ========== دوال الاشتراك الإجباري (تم الإصلاح) ==========
async def add_forced_channel(channel_id: str, channel_username: str, channel_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO forced_channels (channel_id, channel_username, channel_name) VALUES (?, ?, ?)",
                         (channel_id, channel_username, channel_name))
        await db.commit()

async def get_forced_channels() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT channel_id, channel_username, channel_name FROM forced_channels")
        return await cur.fetchall()

async def remove_forced_channel(channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM forced_channels WHERE channel_id=?", (channel_id,))
        await db.commit()

async def add_forced_bot(bot_id: str, bot_username: str, bot_name: str, invite_link: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO forced_bots (bot_id, bot_username, bot_name, invite_link) VALUES (?, ?, ?, ?)",
                         (bot_id, bot_username, bot_name, invite_link))
        await db.commit()

async def get_forced_bots() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT bot_id, bot_username, bot_name, invite_link FROM forced_bots")
        return await cur.fetchall()

async def remove_forced_bot(bot_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM forced_bots WHERE bot_id=?", (bot_id,))
        await db.commit()

async def update_user_bot_attempts(user_id: int, bot_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT attempts, activated FROM user_bot_activation WHERE user_id=? AND bot_id=?", (user_id, bot_id))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO user_bot_activation (user_id, bot_id, attempts, activated) VALUES (?, ?, 1, 0)", (user_id, bot_id))
            return 1
        elif row[1] == 1:
            return 0
        else:
            new_attempts = row[0] + 1
            await db.execute("UPDATE user_bot_activation SET attempts=? WHERE user_id=? AND bot_id=?", (new_attempts, user_id, bot_id))
            await db.commit()
            return new_attempts

async def mark_bot_activated(user_id: int, bot_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_bot_activation SET activated=1 WHERE user_id=? AND bot_id=?", (user_id, bot_id))
        await db.commit()

async def add_forced_social(platform: str, account_url: str, account_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO forced_social (platform, account_url, account_name) VALUES (?, ?, ?)",
                         (platform, account_url, account_name))
        await db.commit()

async def get_forced_social() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT social_id, platform, account_url, account_name FROM forced_social")
        return await cur.fetchall()

async def remove_forced_social(social_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM forced_social WHERE social_id=?", (social_id,))
        await db.commit()

async def confirm_social_follow(user_id: int, social_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO user_social_follow (user_id, social_id, confirmed) VALUES (?, ?, 1)", (user_id, social_id))
        await db.commit()

async def is_social_followed(user_id: int, social_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT confirmed FROM user_social_follow WHERE user_id=? AND social_id=?", (user_id, social_id))
        row = await cur.fetchone()
        return row is not None and row[0] == 1

async def check_user_channel_subscription(user_id: int, channel_id: str, context) -> bool:
    try:
        member = await context.bot.get_chat_member(channel_id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

async def check_user_bot_activation(user_id: int, bot_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT activated FROM user_bot_activation WHERE user_id=? AND bot_id=?", (user_id, bot_id))
        row = await cur.fetchone()
        return row is not None and row[0] == 1

async def check_all_forced_subscriptions(user_id: int, context) -> Tuple[bool, List[Dict]]:
    not_subscribed = []
    for cid, username, name in await get_forced_channels():
        if not await check_user_channel_subscription(user_id, cid, context):
            not_subscribed.append({"type": "channel", "id": cid, "username": username, "name": name})
    for bid, username, name, invite_link in await get_forced_bots():
        if not await check_user_bot_activation(user_id, bid):
            not_subscribed.append({"type": "bot", "id": bid, "username": username, "name": name, "invite_link": invite_link})
    for sid, platform, url, name in await get_forced_social():
        if not await is_social_followed(user_id, sid):
            not_subscribed.append({"type": "social", "id": sid, "platform": platform, "url": url, "name": name})
    return len(not_subscribed) == 0, not_subscribed

# ========== دوال أنشطة تجميع النقاط ==========
async def add_point_collection(name: str, points: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO point_collections (name, points) VALUES (?, ?)", (name, points))
        await db.commit()

async def get_point_collections() -> List[Tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT collection_id, name, points, is_active FROM point_collections WHERE is_active=1")
        return await cur.fetchall()

async def has_user_collection_reward(user_id: int, collection_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT rewarded FROM user_collection_rewards WHERE user_id=? AND collection_id=?", (user_id, collection_id))
        row = await cur.fetchone()
        return row is not None and row[0] == 1

async def mark_collection_reward(user_id: int, collection_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO user_collection_rewards (user_id, collection_id, rewarded) VALUES (?, ?, 1)", (user_id, collection_id))
        await db.commit()

async def collect_all_points(user_id: int, context) -> Tuple[int, List[str]]:
    total = 0
    collected = []
    ch_total, ch_msgs = await collect_channel_points(user_id, context)
    total += ch_total
    collected.extend(ch_msgs)
    for cid, name, points, _ in await get_point_collections():
        if not await has_user_collection_reward(user_id, cid):
            await update_user_points(user_id, points)
            await mark_collection_reward(user_id, cid)
            total += points
            collected.append(f"✅ {name}: +{points}")
    return total, collected

# ========== دوال الإعدادات العامة ==========
async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else ""

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# ========== دوال الرشق ==========
async def zefoy_views(link: str) -> Tuple[bool, str]:
    try:
        return False, f"🎵 **خدمة رشق مشاهدات تيك توك**\n\nللاستخدام، يرجى اتباع الخطوات التالية:\n\n1️⃣ اضغط على الرابط أدناه\n2️⃣ قم بحل الكابتشا إن ظهرت\n3️⃣ أدخل رابط الفيديو الذي تريد رشقه\n4️⃣ اختر عدد المشاهدات\n\n🔗 **رابط الخدمة:**\nhttps://zefoy.online/views/\n\n⚠️ ملاحظة: البوت لا يستطيع تجاوز الكابتشا، يجب تنفيذ الخطوات يدوياً."
    except Exception as e:
        logger.error(f"Zefoy error: {e}")
        return False, f"❌ حدث خطأ: {str(e)}"

# ========== لوحة المفاتيح (الأزرار) ==========
def get_main_keyboard(user_id: int = None):
    keyboard = [
        [InlineKeyboardButton("🛒 المتجر", callback_data="shop"),
         InlineKeyboardButton("⭐ نقاطي", callback_data="my_points")],
        [InlineKeyboardButton("🎁 تجميع النقاط", callback_data="collect_points"),
         InlineKeyboardButton("🎫 إدخال كود", callback_data="redeem_code")],
        [InlineKeyboardButton("🎁 هدية يومية", callback_data="daily_gift"),
         InlineKeyboardButton("🔗 رابط الدعوة", callback_data="referral_link")],
        [InlineKeyboardButton("📦 مشترياتي", callback_data="my_purchases"),
         InlineKeyboardButton("📊 إحصائياتي", callback_data="my_stats")],
        [InlineKeyboardButton("💰 تحويل نقاط", callback_data="transfer_points"),
         InlineKeyboardButton("🔔 إشعاراتي", callback_data="my_notifications")],
        [InlineKeyboardButton("🎵 رشق تيك توك", callback_data="zefoy_views"),
         InlineKeyboardButton("🏆 المسابقات", callback_data="contests")],
        [InlineKeyboardButton("🌐 تغيير اللغة", callback_data="change_language"),
         InlineKeyboardButton("ℹ️ عن البوت", callback_data="about")],
        [InlineKeyboardButton("📞 الدعم", callback_data="support")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🔧 لوحة التحكم", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_shop_keyboard(tools: List[Tuple], page: int = 0, per_page=6):
    start = page * per_page
    end = start + per_page
    page_tools = tools[start:end]
    keyboard = []
    for tool in page_tools:
        tid, name, desc, price, fid, cat = tool
        keyboard.append([InlineKeyboardButton(f"🟢 {name} — {price} 💎", callback_data=f"tool_{tid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"shop_page_{page-1}"))
    if end < len(tools):
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"shop_page_{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_tool_detail_keyboard(tool_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 شراء (نقاط)", callback_data=f"buy_{tool_id}")],
        [InlineKeyboardButton("📝 طلب شراء يدوي", callback_data=f"pending_buy_{tool_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="shop")]
    ])

def get_admin_panel_keyboard(total_users: int = 0):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة أداة", callback_data="admin_add_tool"),
         InlineKeyboardButton("✏️ تعديل أداة", callback_data="admin_edit_tool")],
        [InlineKeyboardButton("💰 تغيير سعر", callback_data="admin_change_price"),
         InlineKeyboardButton("🗑 حذف أداة", callback_data="admin_remove_tool")],
        [InlineKeyboardButton("📋 الطلبات", callback_data="admin_orders"),
         InlineKeyboardButton("📢 بث", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👥 المستخدمين", callback_data="admin_users"),
         InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_ban_user")],
        [InlineKeyboardButton("🎯 قنوات النقاط", callback_data="admin_points_channels"),
         InlineKeyboardButton("🎁 أنشطة التجميع", callback_data="admin_collections")],
        [InlineKeyboardButton("🎫 إنشاء كود", callback_data="admin_create_code"),
         InlineKeyboardButton("⚙️ نقاط الإحالة", callback_data="admin_set_referral")],
        [InlineKeyboardButton("🔒 الاشتراك الإجباري", callback_data="admin_forced_menu"),
         InlineKeyboardButton("📂 إدارة الأقسام", callback_data="admin_categories")],
        [InlineKeyboardButton("⚙️ إعدادات البوت", callback_data="admin_settings"),
         InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("🛒 طلبات يدوية", callback_data="admin_pending_orders"),
         InlineKeyboardButton("🏆 إنشاء مسابقة", callback_data="admin_create_contest")],
        [InlineKeyboardButton("💰 شحن رصيد Asia", callback_data="admin_asia_settings"),
         InlineKeyboardButton("🔧 وضع الصيانة", callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("📦 إضافة أدوات دفعة واحدة", callback_data="admin_add_batch_tools"),
         InlineKeyboardButton("🔙 الرئيسية", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_forced_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 قنوات إجبارية", callback_data="admin_forced_channels"),
         InlineKeyboardButton("🤖 بوتات إجبارية", callback_data="admin_forced_bots")],
        [InlineKeyboardButton("🌐 حسابات تواصل", callback_data="admin_forced_social"),
         InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_forced_subscription_keyboard(not_subscribed: List[Dict]):
    keyboard = []
    for item in not_subscribed:
        if item["type"] == "channel":
            keyboard.append([InlineKeyboardButton(f"📢 اشترك في {item['name'] or item['username']}", url=f"https://t.me/{item['username'].lstrip('@')}")])
        elif item["type"] == "bot":
            invite = item.get('invite_link', f"https://t.me/{item['username'].lstrip('@')}")
            keyboard.append([InlineKeyboardButton(f"🤖 تفعيل {item['name'] or item['username']}", url=invite)])
            keyboard.append([InlineKeyboardButton("✅ تم التفعيل", callback_data=f"confirm_bot_{item['id']}")])
        elif item["type"] == "social":
            emoji = {"instagram":"📸","twitter":"🐦","facebook":"📘","tiktok":"🎵","youtube":"📺"}.get(item.get("platform",""),"🌐")
            keyboard.append([InlineKeyboardButton(f"{emoji} تابع {item['name']}", url=item['url'])])
            keyboard.append([InlineKeyboardButton("✅ تم المتابعة", callback_data=f"confirm_social_{item['id']}")])
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_forced_subscription")])
    return InlineKeyboardMarkup(keyboard)

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton("🌐 رابط الموقع", callback_data="set_site_url"),
         InlineKeyboardButton("🔑 توكن الموقع", callback_data="set_site_token")],
        [InlineKeyboardButton("💰 حد التحويل", callback_data="set_min_transfer"),
         InlineKeyboardButton("🎁 الهدية اليومية", callback_data="toggle_gift")],
        [InlineKeyboardButton("⭐ نقاط المشاركة", callback_data="set_share_points"),
         InlineKeyboardButton("📝 شروط الاستخدام", callback_data="set_terms")],
        [InlineKeyboardButton("💵 نص الشراء", callback_data="set_buy_text"),
         InlineKeyboardButton("🏆 نص الجوائز", callback_data="set_prize_text")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_language_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_notifications_keyboard(notifications: List[Tuple]):
    keyboard = []
    for nid, msg, date, is_read in notifications[:10]:
        status = "✅" if is_read else "🆕"
        keyboard.append([InlineKeyboardButton(f"{status} {msg[:30]}...", callback_data=f"read_notif_{nid}")])
    keyboard.append([InlineKeyboardButton("🗑 مسح الكل", callback_data="clear_notifications")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_contest_keyboard(contest_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إرسال إجابة", callback_data=f"contest_answer_{contest_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
    ])

def get_admin_stats_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات اليوم", callback_data="admin_stats_today"),
         InlineKeyboardButton("📊 إحصائيات الأسبوع", callback_data="admin_stats_week")],
        [InlineKeyboardButton("📊 إحصائيات الشهر", callback_data="admin_stats_month"),
         InlineKeyboardButton("📊 تقرير كامل", callback_data="admin_stats_full")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])

# ========== معالجات البوت ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    if maintenance_mode and update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(maintenance_message)
        return
    
    user = update.effective_user
    
    # التحقق من الحظر
    banned, reason = await is_user_banned(user.id)
    if banned:
        await update.message.reply_text(await get_text(user.id, "banned", reason))
        return
    
    ref = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref = int(context.args[0][4:])
            if ref == user.id:
                ref = None
        except:
            pass
    await add_user(user.id, user.username, user.full_name, ref)
    
    is_subscribed, not_subscribed = await check_all_forced_subscriptions(user.id, context)
    if not is_subscribed and not_subscribed:
        keyboard = get_forced_subscription_keyboard(not_subscribed)
        await send_with_retry(context, user.id, 
            f"🌟 مرحباً {user.first_name}!\n\n⚠️ يجب استكمال المتطلبات التالية أولاً:\n" +
            "\n".join([f"• {item.get('name', item.get('username', ''))}" for item in not_subscribed]) +
            "\n\n✨ بعد الاستكمال، اضغط على زر التحقق",
            parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
        return
    
    keyboard = get_main_keyboard(user.id)
    await send_with_retry(context, user.id,
        await get_text(user.id, "welcome"),
        parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
    )

async def daily_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من الحظر
    banned, reason = await is_user_banned(user_id)
    if banned:
        await update.callback_query.answer(reason, show_alert=True)
        return
    
    gift_points = int(await get_setting("gift_points"))
    daily_gift_enabled = await get_setting("daily_gift")
    
    if daily_gift_enabled != "on":
        await update.callback_query.edit_message_text(await get_text(user_id, "daily_gift_disabled"), reply_markup=get_main_keyboard(user_id))
        return
    
    if await can_get_daily_gift(user_id):
        await update_user_points(user_id, gift_points)
        await mark_daily_gift_received(user_id)
        await update.callback_query.edit_message_text(await get_text(user_id, "daily_gift", gift_points, await get_user_points(user_id)), reply_markup=get_main_keyboard(user_id))
    else:
        await update.callback_query.edit_message_text(await get_text(user_id, "daily_gift_already"), reply_markup=get_main_keyboard(user_id))

async def check_forced_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_subscribed, not_subscribed = await check_all_forced_subscriptions(user.id, context)
    if is_subscribed:
        keyboard = get_main_keyboard(user.id)
        await update.callback_query.edit_message_text(f"✅ تم التحقق بنجاح!\n⭐ رصيدك: {await get_user_points(user.id)} نقطة", reply_markup=keyboard)
    else:
        keyboard = get_forced_subscription_keyboard(not_subscribed)
        await update.callback_query.edit_message_text("⚠️ لا يزال يتعين عليك:", reply_markup=keyboard)

async def confirm_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    bot_id = query.data.split("_")[2]
    
    # التحقق من المحاولات
    attempts = await update_user_bot_attempts(user.id, bot_id)
    if attempts >= 3:
        await query.answer("تجاوزت الحد الأقصى من المحاولات (3)", show_alert=True)
        return
    
    await mark_bot_activated(user.id, bot_id)
    is_subscribed, not_subscribed = await check_all_forced_subscriptions(user.id, context)
    if is_subscribed:
        keyboard = get_main_keyboard(user.id)
        await query.edit_message_text("✅ تم تفعيل البوت! مرحباً بك.", reply_markup=keyboard)
    else:
        keyboard = get_forced_subscription_keyboard(not_subscribed)
        await query.edit_message_text("✅ تم تفعيل البوت، لكن لا يزال هناك متطلبات أخرى.", reply_markup=keyboard)

async def confirm_social_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    social_id = int(query.data.split("_")[2])
    await confirm_social_follow(user.id, social_id)
    is_subscribed, not_subscribed = await check_all_forced_subscriptions(user.id, context)
    if is_subscribed:
        keyboard = get_main_keyboard(user.id)
        await query.edit_message_text("✅ تم تأكيد المتابعة! مرحباً بك.", reply_markup=keyboard)
    else:
        keyboard = get_forced_subscription_keyboard(not_subscribed)
        await query.edit_message_text("✅ تم تأكيد المتابعة، لكن لا يزال هناك متطلبات أخرى.", reply_markup=keyboard)

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    user_id = update.effective_user.id
    tools = await get_all_tools()
    if not tools:
        await update.callback_query.edit_message_text(await get_text(user_id, "no_tools"), reply_markup=get_main_keyboard(user_id))
        return
    await update.callback_query.edit_message_text(await get_text(user_id, "shop"), parse_mode=ParseMode.MARKDOWN, reply_markup=get_shop_keyboard(tools, page))

async def show_tool_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, tool_id: int):
    user_id = update.effective_user.id
    tool = await get_tool(tool_id)
    if not tool:
        await update.callback_query.answer("الأداة غير موجودة", show_alert=True)
        return
    _, name, desc, price, _, _, minl, maxl, _, _ = tool
    await update.callback_query.edit_message_text(f"🔧 {name}\n\n📝 {desc}\n💰 السعر: {price} نقطة\n📊 الكمية: {minl}-{maxl}", reply_markup=get_tool_detail_keyboard(tool_id))

async def buy_tool(update: Update, context: ContextTypes.DEFAULT_TYPE, tool_id: int):
    user_id = update.effective_user.id
    
    # التحقق من الحظر
    banned, reason = await is_user_banned(user_id)
    if banned:
        await update.callback_query.answer(reason, show_alert=True)
        return
    
    quantity = 1
    success, msg = await buy_tool_with_points(user_id, tool_id, quantity, context)
    if success:
        await update.callback_query.edit_message_text(msg, reply_markup=get_main_keyboard(user_id))
    else:
        await update.callback_query.answer(msg, show_alert=True)

async def pending_buy_tool(update: Update, context: ContextTypes.DEFAULT_TYPE, tool_id: int):
    user_id = update.effective_user.id
    
    # التحقق من الحظر
    banned, reason = await is_user_banned(user_id)
    if banned:
        await update.callback_query.answer(reason, show_alert=True)
        return
    
    tool = await get_tool(tool_id)
    if not tool:
        await update.callback_query.answer("الأداة غير موجودة", show_alert=True)
        return
    
    _, name, desc, price, _, _, minl, maxl, _, _ = tool
    quantity = 1
    total_price = price * quantity
    
    order_id = await create_pending_order(user_id, tool_id, name, quantity, total_price)
    await update.callback_query.edit_message_text(
        f"📝 **طلب شراء يدوي**\n\nالأداة: {name}\nالسعر: {price} نقطة\nالكمية: {quantity}\nالمجموع: {total_price} نقطة\n\n✅ تم إرسال طلبك إلى الإدارة، سيتم الرد عليك قريباً.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(user_id)
    )
    
    # إرسال إشعار للأدمن
    await send_with_retry(context, ADMIN_ID, f"🆕 طلب شراء يدوي جديد!\n\nالمستخدم: {user_id}\nالأداة: {name}\nالمبلغ: {total_price} نقطة\nرقم الطلب: {order_id}")

async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pts = await get_user_points(user_id)
    await update.callback_query.edit_message_text(await get_text(user_id, "points", pts), reply_markup=get_main_keyboard(user_id))

async def collect_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    total, collected = await collect_all_points(user_id, context)
    if collected:
        text = await get_text(user_id, "collect_points", "\n".join(collected), total, await get_user_points(user_id))
    else:
        text = await get_text(user_id, "no_new_points", await get_user_points(user_id))
    await update.callback_query.edit_message_text(text, reply_markup=get_main_keyboard(user_id))

async def redeem_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.callback_query.edit_message_text(await get_text(user_id, "code_sent"))
    return 1

async def redeem_code_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = update.message.text.strip().upper()
    success, pts = await redeem_code(code, user_id)
    if success:
        await send_with_retry(context, user_id, await get_text(user_id, "code_success", pts))
    else:
        await send_with_retry(context, user_id, await get_text(user_id, "code_invalid"))
    await send_with_retry(context, user_id, await get_text(user_id, "back"), reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

async def referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    pts = await get_referral_points()
    count, referrals = await get_referral_stats(user_id)
    stats_text = ""
    if count > 0:
        stats_text = f"\n\n📊 **إحصائيات دعواتك:**\n👥 عدد المدعوين: {count}\n"
        for i, (uid, name, uname, date) in enumerate(referrals[:10], 1):
            stats_text += f"{i}. {name} (@{uname}) - {date[:10]}\n"
        if count > 10:
            stats_text += f"... و{count-10} آخرين"
    else:
        stats_text = await get_text(user_id, "no_referrals")
    await update.callback_query.edit_message_text(
        await get_text(user_id, "referral_link", link, pts, stats_text),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(user_id)
    )

async def my_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tools.name, orders.total_price, orders.order_date FROM orders JOIN tools ON orders.tool_id=tools.tool_id WHERE orders.user_id=? ORDER BY orders.order_date DESC", (user_id,))
        rows = await cur.fetchall()
    if not rows:
        text = await get_text(user_id, "no_purchases")
    else:
        purchases_list = "\n".join([f"• {name} - {price} نقطة - {date[:10]}" for name, price, date in rows])
        text = await get_text(user_id, "purchases", purchases_list)
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard(user_id))

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,))
        orders = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM referral_log WHERE referrer_id=?", (user_id,))
        refs = (await cur.fetchone())[0]
    points = await get_user_points(user_id)
    await update.callback_query.edit_message_text(await get_text(user_id, "stats", points, orders, refs), parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard(user_id))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_name = await get_setting("bot_name")
    await update.callback_query.edit_message_text(await get_text(user_id, "about", bot_name), reply_markup=get_main_keyboard(user_id))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.callback_query.edit_message_text(await get_text(user_id, "support", ADMIN_USERNAME), reply_markup=get_main_keyboard(user_id))

async def zefoy_views_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    success, msg = await zefoy_views("")
    await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard(user_id))

async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🌐 اختر لغتك / Choose your language:", reply_markup=get_language_keyboard())
    return 1

async def set_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    lang = query.data.split("_")[1]
    await set_user_language(user_id, lang)
    await query.answer(f"تم تغيير اللغة إلى {'العربية' if lang == 'ar' else 'English'}")
    keyboard = get_main_keyboard(user_id if user_id == ADMIN_ID else None)
    await query.edit_message_text(await get_text(user_id, "welcome"), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    return ConversationHandler.END

# ========== معالجات تحويل النقاط ==========
async def transfer_points_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points = await get_user_points(user_id)
    await update.callback_query.edit_message_text(await get_text(user_id, "transfer_title", points), parse_mode=ParseMode.MARKDOWN)
    return 1

async def transfer_points_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        parts = update.message.text.split()
        to_user = int(parts[0])
        amount = int(parts[1])
        
        success, msg = await transfer_points(user_id, to_user, amount)
        if success:
            await send_with_retry(context, user_id, await get_text(user_id, "transfer_success", amount, to_user, await get_user_points(user_id)))
            # إشعار للمستلم
            await send_with_retry(context, to_user, f"💰 استلمت {amount} نقطة من المستخدم {user_id}")
        else:
            await send_with_retry(context, user_id, f"❌ {msg}")
    except:
        await send_with_retry(context, user_id, "❌ خطأ في التنسيق. استخدم: `123456789 50`", parse_mode=ParseMode.MARKDOWN)
    
    await send_with_retry(context, user_id, await get_text(user_id, "back"), reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

async def transfer_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = await get_transfer_history(user_id)
    if not history:
        text = await get_text(user_id, "no_transfers")
    else:
        lines = []
        for from_u, to_u, amount, date in history:
            if from_u == user_id:
                lines.append(f"📤 إلى {to_u}: -{amount} نقطة - {date[:10]}")
            else:
                lines.append(f"📥 من {from_u}: +{amount} نقطة - {date[:10]}")
        text = await get_text(user_id, "transfer_log", "\n".join(lines[:10]))
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard(user_id))

# ========== معالجات الإشعارات ==========
async def my_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    notifications = await get_user_notifications(user_id)
    if not notifications:
        await update.callback_query.edit_message_text("📭 لا توجد إشعارات.", reply_markup=get_main_keyboard(user_id))
        return
    await update.callback_query.edit_message_text("🔔 **إشعاراتي:**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_notifications_keyboard(notifications))

async def read_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notif_id = int(update.callback_query.data.split("_")[2])
    await mark_notification_read(notif_id)
    await my_notifications(update, context)

async def clear_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
        await db.commit()
    await update.callback_query.edit_message_text("🗑 تم مسح جميع الإشعارات.", reply_markup=get_main_keyboard(user_id))

# ========== معالجات المسابقات ==========
async def contests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    contest = await get_active_contest()
    if not contest:
        await update.callback_query.edit_message_text(await get_text(user_id, "no_active_contest"), reply_markup=get_main_keyboard(user_id))
        return
    
    contest_id, question, answer, points, end_date = contest
    await update.callback_query.edit_message_text(
        await get_text(user_id, "contest_question", f"#{contest_id}", question, points),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_contest_keyboard(contest_id)
    )

async def contest_answer_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contest_id = int(update.callback_query.data.split("_")[2])
    context.user_data['contest_id'] = contest_id
    await update.callback_query.edit_message_text("📝 أرسل إجابتك كرسالة نصية:")
    return 1

async def contest_answer_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    contest_id = context.user_data.get('contest_id')
    answer = update.message.text
    
    success, points = await check_contest_answer(contest_id, user_id, answer)
    if success:
        await send_with_retry(context, user_id, await get_text(user_id, "contest_correct", points))
    else:
        await send_with_retry(context, user_id, await get_text(user_id, "contest_wrong"))
    
    await send_with_retry(context, user_id, await get_text(user_id, "back"), reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

# ========== معالجات الدفع عبر Asia ==========
async def asia_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.callback_query.edit_message_text("💰 **شحن الرصيد عبر Asia**\n\nأرسل المبلغ الذي تريد شحنه (بالنقاط):\nمثال: `100`", parse_mode=ParseMode.MARKDOWN)
    return 1

async def asia_payment_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text.strip())
        if amount < 10:
            await send_with_retry(context, user_id, "⚠️ الحد الأدنى للشحن هو 10 نقاط.")
            return 1
        
        payment_id = await create_asia_payment(user_id, amount)
        qr_bio = await generate_asia_qr(amount)
        
        await send_with_retry(context, user_id, 
            f"💰 **طلب شحن {amount} نقطة**\n\n"
            f"🆔 معرف الدفع: `{payment_id}`\n\n"
            f"📱 استخدم التطبيق الخاص ببوابة Asia لمسح الرمز أو إدخال المعرف.\n\n"
            f"⚠️ بعد إتمام الدفع، سيتم إضافة الرصيد تلقائياً.",
            parse_mode=ParseMode.MARKDOWN,
            photo=InputFile(qr_bio, filename="qr.png"),
            caption=f"رمز QR للدفع - {amount} نقطة"
        )
        await send_with_retry(context, user_id, await get_text(user_id, "back"), reply_markup=get_main_keyboard(user_id))
    except:
        await send_with_retry(context, user_id, "❌ خطأ: أرسل رقماً صحيحاً.")
        return 1
    return ConversationHandler.END

# ========== لوحة تحكم الأدمن ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        if update.callback_query:
            await update.callback_query.answer("غير مصرح", show_alert=True)
        else:
            await update.message.reply_text("غير مصرح")
        return
    total_users = await get_total_users()
    text = f"🔧 **لوحة التحكم**\n👥 عدد المستخدمين: {total_users}"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_panel_keyboard(total_users))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_panel_keyboard(total_users))

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT orders.order_id, users.full_name, tools.name, orders.total_price, orders.order_date FROM orders JOIN users ON orders.user_id=users.user_id JOIN tools ON orders.tool_id=tools.tool_id ORDER BY orders.order_date DESC LIMIT 20")
        rows = await cur.fetchall()
    text = "📋 **آخر الطلبات:**\n" + "\n".join([f"#{oid} | {name} | {tool} | {price} نقطة | {date[:16]}" for oid, name, tool, price, date in rows]) if rows else "لا توجد طلبات."
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_panel_keyboard(await get_total_users()))

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
        cur = await db.execute("SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 10")
        top = await cur.fetchall()
    text = f"👥 **عدد المستخدمين:** {total}\n🏆 **ترتيب النقاط:**\n" + "\n".join([f"{i+1}. {name} - {pts} نقطة" for i,(name,_,pts) in enumerate(top)])
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_panel_keyboard(total))

async def admin_ban_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("🚫 **حظر مستخدم**\n\nأرسل معرف المستخدم وسبب الحظر:\nمثال: `123456789 سبب الحظر`")
    return 1

async def admin_ban_user_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    try:
        parts = update.message.text.split(maxsplit=1)
        user_id = int(parts[0])
        reason = parts[1] if len(parts) > 1 else "تم حظرك من قبل الإدارة"
        await ban_user(user_id, reason)
        await update.message.reply_text(f"✅ تم حظر المستخدم {user_id}")
        await send_with_retry(context, user_id, f"🚫 {reason}\n\nإذا كنت تعتقد أن هذا خطأ، تواصل مع الدعم.")
    except:
        await update.message.reply_text("❌ خطأ في التنسيق")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_unban_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("🔓 **إلغاء حظر مستخدم**\n\nأرسل معرف المستخدم:")
    return 1

async def admin_unban_user_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    try:
        user_id = int(update.message.text.strip())
        await unban_user(user_id)
        await update.message.reply_text(f"✅ تم إلغاء حظر المستخدم {user_id}")
        await send_with_retry(context, user_id, "✅ تم إلغاء حظرك، يمكنك استخدام البوت مرة أخرى.")
    except:
        await update.message.reply_text("❌ خطأ")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("📢 أرسل نص البث:")
    return 1

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    users = await get_all_users()
    success = 0
    for uid in users:
        try:
            await send_with_retry(context, uid, f"📢 إعلان:\n{update.message.text}")
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await update.message.reply_text(f"✅ تم الإرسال إلى {success} مستخدم.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_pending_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    orders = await get_pending_orders()
    if not orders:
        await update.callback_query.edit_message_text("لا توجد طلبات معلقة.", reply_markup=get_admin_panel_keyboard(await get_total_users()))
        return
    
    text = "🛒 **الطلبات اليدوية المعلقة:**\n\n"
    for oid, uid, tool_name, qty, price, date, status in orders:
        text += f"📋 طلب #{oid}\n👤 المستخدم: {uid}\n🔧 الأداة: {tool_name}\n💰 المبلغ: {price} نقطة\n📅 التاريخ: {date[:16]}\n\n"
    
    keyboard = []
    for oid, uid, tool_name, qty, price, date, status in orders:
        keyboard.append([InlineKeyboardButton(f"✅ قبول طلب #{oid}", callback_data=f"approve_order_{oid}")])
        keyboard.append([InlineKeyboardButton(f"❌ رفض طلب #{oid}", callback_data=f"reject_order_{oid}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def approve_pending_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    order_id = int(update.callback_query.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, tool_id, tool_name, quantity, total_price FROM pending_orders WHERE order_id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await update.callback_query.answer("الطلب غير موجود")
            return
        user_id, tool_id, tool_name, quantity, total_price = row
        
        # إضافة الطلب إلى جدول الطلبات المكتملة
        await db.execute("INSERT INTO orders (user_id, tool_id, quantity, total_price, order_date) VALUES (?, ?, ?, ?, ?)",
                         (user_id, tool_id, quantity, total_price, datetime.now().isoformat()))
        
        # تحديث حالة الطلب
        await db.execute("UPDATE pending_orders SET status='approved' WHERE order_id=?", (order_id,))
        await db.commit()
    
    # إرسال إشعار للمستخدم
    await send_with_retry(context, user_id, f"✅ تم قبول طلبك رقم #{order_id}!\nالأداة: {tool_name}\nتم إضافة المنتج إلى مشترياتك.")
    
    await update.callback_query.edit_message_text(f"✅ تم قبول الطلب #{order_id}", reply_markup=get_admin_panel_keyboard(await get_total_users()))

async def reject_pending_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    order_id = int(update.callback_query.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, tool_name FROM pending_orders WHERE order_id=?", (order_id,))
        row = await cur.fetchone()
        if row:
            user_id, tool_name = row
            await db.execute("UPDATE pending_orders SET status='rejected' WHERE order_id=?", (order_id,))
            await db.commit()
            await send_with_retry(context, user_id, f"❌ تم رفض طلبك رقم #{order_id}\nالأداة: {tool_name}\nيرجى التواصل مع الدعم للمزيد من المعلومات.")
    
    await update.callback_query.edit_message_text(f"❌ تم رفض الطلب #{order_id}", reply_markup=get_admin_panel_keyboard(await get_total_users()))

async def admin_create_contest_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("🏆 **إنشاء مسابقة جديدة**\n\nأرسل السؤال:")
    return 1

async def admin_create_contest_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contest_question'] = update.message.text
    await update.message.reply_text("أرسل الإجابة الصحيحة:")
    return 2

async def admin_create_contest_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contest_answer'] = update.message.text
    await update.message.reply_text("أرسل عدد النقاط للجائزة:")
    return 3

async def admin_create_contest_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        points = int(update.message.text)
        contest_id = await create_contest(
            context.user_data['contest_question'],
            context.user_data['contest_answer'],
            points,
            ADMIN_ID,
            24
        )
        await update.message.reply_text(f"✅ تم إنشاء المسابقة!\nالمعرف: {contest_id}\nالسؤال: {context.user_data['contest_question'][:50]}...\nالجائزة: {points} نقطة")
    except:
        await update.message.reply_text("❌ خطأ في إنشاء المسابقة")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("📊 **إحصائيات البوت**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_stats_keyboard())

async def admin_stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats = await get_bot_stats()
    text = f"📊 **إحصائيات اليوم**\n\n👥 المستخدمين: {stats['today_users']}\n🛒 الطلبات: {stats['today_orders']}\n💰 النقاط المنفقة: {stats['today_points']}"
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_stats_keyboard())

async def admin_stats_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats = await get_bot_stats()
    text = f"📊 **إحصائيات الأسبوع**\n\n🛒 الطلبات: {stats['week_orders']}\n💰 النقاط المنفقة: {stats['week_points']}"
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_stats_keyboard())

async def admin_stats_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats = await get_bot_stats()
    text = f"📊 **إحصائيات الشهر**\n\n🛒 الطلبات: {stats['month_orders']}\n💰 النقاط المنفقة: {stats['month_points']}"
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_stats_keyboard())

async def admin_stats_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    stats = await get_bot_stats()
    text = f"📊 **التقرير الكامل**\n\n📅 اليوم: {stats['today_orders']} طلب | {stats['today_points']} نقطة\n📅 الأسبوع: {stats['week_orders']} طلب | {stats['week_points']} نقطة\n📅 الشهر: {stats['month_orders']} طلب | {stats['month_points']} نقطة\n👥 إجمالي المستخدمين: {await get_total_users()}"
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_admin_stats_keyboard())

async def admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    if update.effective_user.id != ADMIN_ID: return
    maintenance_mode = not maintenance_mode
    status = "مفعل" if maintenance_mode else "معطل"
    await update.callback_query.answer(f"وضع الصيانة {status}", show_alert=True)
    await admin_panel(update, context)

async def admin_asia_settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("💰 **إعدادات بوابة Asia**\n\nأرسل API Key:")
    return 1

async def admin_asia_settings_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_setting("asia_api_key", update.message.text.strip())
    await update.message.reply_text("أرسل Merchant ID:")
    return 2

async def admin_asia_settings_merchant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_setting("asia_merchant_id", update.message.text.strip())
    await update.message.reply_text("✅ تم حفظ إعدادات Asia بنجاح!")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

# ========== باقي دوال الأدمن (محفوظة من الكود الأصلي) ==========
async def admin_points_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    channels = await get_all_points_channels()
    text = "🎯 **قنوات جمع النقاط:**\n" + "\n".join([f"{name or username}: {points} نقطة" for cid,username,name,points in channels]) if channels else "لا توجد قنوات."
    keyboard = [[InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_points_channel")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_points_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل معرف القناة ثم عدد النقاط:\nمثال: @channel 50")
    return 1

async def admin_add_points_channel_value(update: Update, context):
    try:
        parts = update.message.text.split()
        username = parts[0].strip()
        points = int(parts[1])
        if not username.startswith('@'): username = '@'+username
        chat = await context.bot.get_chat(username)
        await add_points_channel(str(chat.id), username, chat.title or username, points)
        await update.message.reply_text("✅ تم إضافة القناة.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    cats = await get_categories()
    text = "📂 **الأقسام:**\n" + "\n".join([f"{name}" for _,name,_,_ in cats]) if cats else "لا توجد أقسام."
    keyboard = [[InlineKeyboardButton("➕ إضافة قسم", callback_data="admin_add_category")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل اسم القسم الجديد:")
    return 1

async def admin_add_category_value(update: Update, context):
    name = update.message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        await db.commit()
    await update.message.reply_text(f"✅ تم إضافة القسم {name}.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("⚙️ **إعدادات البوت**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_settings_keyboard())

async def set_site_url_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل رابط موقع الرشق (مثال: https://example.com):")
    return 1
async def set_site_url_value(update: Update, context):
    await set_setting("site_url", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين رابط الموقع.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def set_site_token_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل توكن الموقع (API key):")
    return 1
async def set_site_token_value(update: Update, context):
    await set_setting("site_token", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين توكن الموقع.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def set_min_transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل أقل مبلغ لتحويل الرصيد (رقم):")
    return 1
async def set_min_transfer_value(update: Update, context):
    await set_setting("min_transfer", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين الحد الأدنى.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def toggle_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    current = await get_setting("daily_gift")
    new = "off" if current == "on" else "on"
    await set_setting("daily_gift", new)
    await update.callback_query.answer(f"تم {'تفعيل' if new=='on' else 'تعطيل'} الهدية اليومية", show_alert=True)
    await admin_settings(update, context)

async def set_share_points_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل عدد النقاط لكل مشاركة رابط:")
    return 1
async def set_share_points_value(update: Update, context):
    await set_setting("share_points", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين نقاط المشاركة.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def set_terms_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل نص شروط الاستخدام الجديد:")
    return 1
async def set_terms_value(update: Update, context):
    await set_setting("terms_text", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين شروط الاستخدام.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def set_buy_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل نص شراء الرصيد الجديد:")
    return 1
async def set_buy_text_value(update: Update, context):
    await set_setting("buy_text", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين نص الشراء.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def set_prize_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل نص الجوائز الجديد:")
    return 1
async def set_prize_text_value(update: Update, context):
    await set_setting("prize_text", update.message.text.strip())
    await update.message.reply_text("✅ تم تعيين نص الجوائز.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_add_tool_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل اسم الأداة:")
    return 1
async def admin_add_tool_name(update: Update, context):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("أرسل الوصف:")
    return 2
async def admin_add_tool_desc(update: Update, context):
    context.user_data['desc'] = update.message.text
    await update.message.reply_text("أرسل السعر (نقاط):")
    return 3
async def admin_add_tool_price(update: Update, context):
    context.user_data['price'] = int(update.message.text)
    await update.message.reply_text("أرسل file_id أو رابط:")
    return 4
async def admin_add_tool_file(update: Update, context):
    fid = update.message.document.file_id if update.message.document else update.message.text.strip()
    await add_tool(context.user_data['name'], context.user_data['desc'], context.user_data['price'], fid, "عام")
    await update.message.reply_text("✅ تمت الإضافة.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_edit_tool_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    tools = await get_all_tools()
    if not tools:
        await update.callback_query.edit_message_text("لا توجد أدوات", reply_markup=get_admin_panel_keyboard(await get_total_users()))
        return
    keyboard = [[InlineKeyboardButton(f"✏️ {name}", callback_data=f"edit_tool_{tid}")] for tid, name, _, _, _, _ in tools]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    await update.callback_query.edit_message_text("اختر أداة للتعديل:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_edit_tool_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_id = int(update.callback_query.data.split('_')[2])
    context.user_data['edit_tool_id'] = tool_id
    tool = await get_tool(tool_id)
    if tool:
        context.user_data['edit_tool_data'] = tool
        await update.callback_query.edit_message_text(f"✏️ تعديل الأداة: {tool[1]}\nأرسل الاسم الجديد (أو /skip):")
        return 1
    else:
        await update.callback_query.answer("الأداة غير موجودة")
        return ConversationHandler.END

async def edit_tool_name(update: Update, context):
    if update.message.text == '/skip':
        tool = context.user_data.get('edit_tool_data')
        context.user_data['edit_name'] = tool[1] if tool else ""
    else:
        context.user_data['edit_name'] = update.message.text
    await update.message.reply_text("أرسل الوصف الجديد (أو /skip):")
    return 2

async def edit_tool_desc(update: Update, context):
    if update.message.text == '/skip':
        tool = context.user_data.get('edit_tool_data')
        context.user_data['edit_desc'] = tool[2] if tool else ""
    else:
        context.user_data['edit_desc'] = update.message.text
    await update.message.reply_text("أرسل السعر الجديد (أو /skip):")
    return 3

async def edit_tool_price(update: Update, context):
    if update.message.text == '/skip':
        tool = context.user_data.get('edit_tool_data')
        context.user_data['edit_price'] = tool[3] if tool else 0
    else:
        context.user_data['edit_price'] = int(update.message.text)
    await update.message.reply_text("أرسل file_id جديد أو رابط (أو /skip):")
    return 4

async def edit_tool_file(update: Update, context):
    if update.message.text == '/skip':
        tool = context.user_data.get('edit_tool_data')
        fid = tool[4] if tool else ""
    else:
        fid = update.message.document.file_id if update.message.document else update.message.text.strip()
    await update_tool(context.user_data['edit_tool_id'], context.user_data['edit_name'], context.user_data['edit_desc'], context.user_data['edit_price'], fid)
    await update.message.reply_text("✅ تم التعديل.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_change_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    tools = await get_all_tools()
    if not tools:
        await update.callback_query.edit_message_text("لا توجد أدوات", reply_markup=get_admin_panel_keyboard(await get_total_users()))
        return
    keyboard = [[InlineKeyboardButton(f"💰 {name} ({price})", callback_data=f"price_tool_{tid}")] for tid, name, _, price, _, _ in tools]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    await update.callback_query.edit_message_text("اختر أداة لتغيير سعرها:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_price_tool_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_id = int(update.callback_query.data.split('_')[2])
    context.user_data['price_tool_id'] = tool_id
    await update.callback_query.edit_message_text("أرسل السعر الجديد:")
    return 1

async def change_price_value(update: Update, context):
    new_price = int(update.message.text)
    await update_tool_price(context.user_data['price_tool_id'], new_price)
    await update.message.reply_text("✅ تم تغيير السعر.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_remove_tool_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    tools = await get_all_tools()
    if not tools:
        await update.callback_query.edit_message_text("لا توجد أدوات", reply_markup=get_admin_panel_keyboard(await get_total_users()))
        return
    keyboard = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"delete_tool_{tid}")] for tid, name, _, _, _, _ in tools]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    await update.callback_query.edit_message_text("اختر أداة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_delete_tool_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tool_id = int(update.callback_query.data.split('_')[2])
    await delete_tool(tool_id)
    await update.callback_query.answer("تم حذف الأداة")
    await update.callback_query.edit_message_text("✅ تم حذف الأداة", reply_markup=get_admin_panel_keyboard(await get_total_users()))

async def admin_create_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل عدد النقاط للكود:")
    return 1
async def admin_create_code_value(update: Update, context):
    points = int(update.message.text)
    code = await create_points_code(points, ADMIN_ID)
    await update.message.reply_text(f"✅ الكود: `{code}`\nالقيمة: {points} نقطة", parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_set_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    current = await get_referral_points()
    await update.callback_query.edit_message_text(f"القيمة الحالية: {current}\nأرسل القيمة الجديدة:")
    return 1
async def admin_set_referral_value(update: Update, context):
    points = int(update.message.text)
    await set_referral_points(points)
    await update.message.reply_text(f"✅ تم تعيين نقاط الإحالة إلى {points}")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_forced_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("🔒 **إدارة الاشتراك الإجباري**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_forced_menu_keyboard())

async def admin_forced_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    channels = await get_forced_channels()
    text = "📢 **القنوات الإجبارية:**\n" + "\n".join([f"{name or username}" for cid,username,name in channels]) if channels else "لا توجد قنوات."
    keyboard = [[InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_forced_channel")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_forced_menu")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_forced_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل معرف القناة (مثال: @channel):")
    return 1
async def admin_add_forced_channel_value(update: Update, context):
    username = update.message.text.strip()
    if not username.startswith('@'): username = '@'+username
    chat = await context.bot.get_chat(username)
    await add_forced_channel(str(chat.id), username, chat.title or username)
    await update.message.reply_text("✅ تم إضافة القناة الإجبارية.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_forced_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    bots = await get_forced_bots()
    text = "🤖 **البوتات الإجبارية:**\n" + "\n".join([f"{name or username}" for bid,username,name,_ in bots]) if bots else "لا توجد بوتات."
    keyboard = [[InlineKeyboardButton("➕ إضافة بوت", callback_data="admin_add_forced_bot")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_forced_menu")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_forced_bot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل معرف البوت (مثال: @bot):")
    return 1
async def admin_add_forced_bot_value(update: Update, context):
    username = update.message.text.strip()
    if not username.startswith('@'): username = '@'+username
    bot_info = await context.bot.get_chat(username)
    await add_forced_bot(str(bot_info.id), username, bot_info.first_name or username, f"https://t.me/{username.lstrip('@')}")
    await update.message.reply_text("✅ تم إضافة البوت الإجباري.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_forced_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    social = await get_forced_social()
    text = "🌐 **حسابات التواصل:**\n" + "\n".join([f"{name} ({platform})" for sid,platform,url,name in social]) if social else "لا توجد حسابات."
    keyboard = [[InlineKeyboardButton("➕ إضافة حساب", callback_data="admin_add_forced_social")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_forced_menu")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_forced_social_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل المنصة، الرابط، الاسم (مفصولة بمسافة):\nمثال: instagram https://instagram.com/username حسابنا")
    return 1
async def admin_add_forced_social_value(update: Update, context):
    parts = update.message.text.split(maxsplit=2)
    platform = parts[0].lower()
    url = parts[1]
    name = parts[2] if len(parts)>2 else platform
    await add_forced_social(platform, url, name)
    await update.message.reply_text("✅ تم إضافة الحساب.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_collections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    cols = await get_point_collections()
    text = "🎁 **أنشطة تجميع النقاط:**\n" + "\n".join([f"{name}: +{points}" for cid,name,points,_ in cols]) if cols else "لا توجد أنشطة."
    keyboard = [[InlineKeyboardButton("➕ إضافة نشاط", callback_data="admin_add_collection")],[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_collection_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("أرسل اسم النشاط وعدد النقاط:\nمثال: تقييم البوت 50")
    return 1
async def admin_add_collection_value(update: Update, context):
    parts = update.message.text.rsplit(maxsplit=1)
    name = parts[0]
    points = int(parts[1])
    await add_point_collection(name, points)
    await update.message.reply_text("✅ تم إضافة النشاط.")
    await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
    return ConversationHandler.END

async def admin_add_batch_tools_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.callback_query.edit_message_text("📦 **إضافة أدوات دفعة واحدة**\nأرسل عدد الأدوات التي تريد إضافتها (1-50):")
    return 1

async def admin_add_batch_tools_count(update: Update, context):
    try:
        count = int(update.message.text)
        if count <= 0 or count > 50:
            await update.message.reply_text("⚠️ الرجاء إدخال عدد بين 1 و 50.")
            return 1
        context.user_data['batch_count'] = count
        context.user_data['batch_index'] = 0
        context.user_data['batch_tools'] = []
        await update.message.reply_text(f"📦 سيتم إضافة {count} أداة.\nأرسل بيانات الأداة رقم 1 بالتنسيق:\n`الاسم | الوصف | السعر (مؤقت) | file_id أو رابط`\n(يمكنك تعديل السعر لاحقاً)", parse_mode=ParseMode.MARKDOWN)
        return 2
    except:
        await update.message.reply_text("❌ خطأ: أرسل رقماً صحيحاً.")
        return 1

async def admin_add_batch_tools_data(update: Update, context):
    text = update.message.text
    parts = text.split('|')
    if len(parts) < 4:
        await update.message.reply_text("⚠️ التنسيق غير صحيح. استخدم: الاسم | الوصف | السعر | file_id/رابط")
        return 2
    name = parts[0].strip()
    description = parts[1].strip()
    try:
        price = int(parts[2].strip())
    except:
        price = 0
    file_id = parts[3].strip()
    
    context.user_data['batch_tools'].append({
        'name': name,
        'description': description,
        'price': price,
        'file_id': file_id,
        'category': 'عام'
    })
    context.user_data['batch_index'] += 1
    
    if context.user_data['batch_index'] < context.user_data['batch_count']:
        await update.message.reply_text(f"✅ تم حفظ الأداة {context.user_data['batch_index']}\nأرسل بيانات الأداة رقم {context.user_data['batch_index']+1}:")
        return 2
    else:
        count = await add_tools_batch(context.user_data['batch_tools'])
        await update.message.reply_text(f"✅ تم إضافة {count} أداة بنجاح!\nيمكنك الآن تعديل أسعارها من لوحة التحكم (تغيير سعر).")
        await update.message.reply_text("لوحة التحكم", reply_markup=get_admin_panel_keyboard(await get_total_users()))
        return ConversationHandler.END

# ========== المعالج الرئيسي ==========
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    # التحقق من الحظر
    banned, reason = await is_user_banned(user_id)
    if banned and data not in ["admin_panel", "back_to_main"] and user_id != ADMIN_ID:
        await query.answer(reason, show_alert=True)
        return
    
    # التحقق من وضع الصيانة
    if maintenance_mode and user_id != ADMIN_ID and data not in ["admin_panel", "admin_toggle_maintenance"]:
        await query.answer(maintenance_message, show_alert=True)
        return
    
    # التحقق من معدل الطلبات
    if not await check_rate_limit(user_id):
        await query.answer("⚠️太多请求，请稍后再试", show_alert=True)
        return
    
    if data == "back_to_main":
        await query.edit_message_text(await get_text(user_id, "back"), reply_markup=get_main_keyboard(user_id if user_id == ADMIN_ID else None))
    elif data == "shop":
        await shop(update, context, 0)
    elif data == "my_points":
        await my_points(update, context)
    elif data == "collect_points":
        await collect_points_callback(update, context)
    elif data == "daily_gift":
        await daily_gift(update, context)
    elif data == "redeem_code":
        await redeem_code_start(update, context)
    elif data == "referral_link":
        await referral_link(update, context)
    elif data == "my_purchases":
        await my_purchases(update, context)
    elif data == "my_stats":
        await my_stats(update, context)
    elif data == "about":
        await about(update, context)
    elif data == "support":
        await support(update, context)
    elif data == "zefoy_views":
        await zefoy_views_handler(update, context)
    elif data == "change_language":
        await change_language(update, context)
    elif data == "transfer_points":
        await transfer_points_start(update, context)
    elif data == "transfer_log":
        await transfer_log(update, context)
    elif data == "my_notifications":
        await my_notifications(update, context)
    elif data == "contests":
        await contests_menu(update, context)
    elif data.startswith("read_notif_"):
        await read_notification(update, context)
    elif data == "clear_notifications":
        await clear_notifications(update, context)
    elif data.startswith("lang_"):
        await set_language_callback(update, context)
    elif data.startswith("contest_answer_"):
        await contest_answer_start(update, context)
    elif data == "check_forced_subscription":
        await check_forced_subscription_callback(update, context)
    elif data.startswith("confirm_bot_"):
        await confirm_bot_callback(update, context)
    elif data.startswith("confirm_social_"):
        await confirm_social_callback(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_orders":
        await admin_orders(update, context)
    elif data == "admin_users":
        await admin_users(update, context)
    elif data == "admin_ban_user":
        await admin_ban_user_start(update, context)
    elif data == "admin_unban_user":
        await admin_unban_user_start(update, context)
    elif data == "admin_broadcast":
        await admin_broadcast_start(update, context)
    elif data == "admin_points_channels":
        await admin_points_channels(update, context)
    elif data == "admin_categories":
        await admin_categories(update, context)
    elif data == "admin_settings":
        await admin_settings(update, context)
    elif data == "admin_pending_orders":
        await admin_pending_orders(update, context)
    elif data == "admin_create_contest":
        await admin_create_contest_start(update, context)
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data == "admin_stats_today":
        await admin_stats_today(update, context)
    elif data == "admin_stats_week":
        await admin_stats_week(update, context)
    elif data == "admin_stats_month":
        await admin_stats_month(update, context)
    elif data == "admin_stats_full":
        await admin_stats_full(update, context)
    elif data == "admin_toggle_maintenance":
        await admin_toggle_maintenance(update, context)
    elif data == "admin_asia_settings":
        await admin_asia_settings_start(update, context)
    elif data.startswith("approve_order_"):
        await approve_pending_order(update, context)
    elif data.startswith("reject_order_"):
        await reject_pending_order(update, context)
    elif data.startswith("set_site_url"):
        await set_site_url_start(update, context)
    elif data.startswith("set_site_token"):
        await set_site_token_start(update, context)
    elif data.startswith("set_min_transfer"):
        await set_min_transfer_start(update, context)
    elif data == "toggle_gift":
        await toggle_gift(update, context)
    elif data == "set_share_points":
        await set_share_points_start(update, context)
    elif data == "set_terms":
        await set_terms_start(update, context)
    elif data == "set_buy_text":
        await set_buy_text_start(update, context)
    elif data == "set_prize_text":
        await set_prize_text_start(update, context)
    elif data == "admin_add_tool":
        await admin_add_tool_start(update, context)
    elif data == "admin_edit_tool":
        await admin_edit_tool_start(update, context)
    elif data == "admin_change_price":
        await admin_change_price_start(update, context)
    elif data == "admin_remove_tool":
        await admin_remove_tool_start(update, context)
    elif data == "admin_create_code":
        await admin_create_code_start(update, context)
    elif data == "admin_set_referral":
        await admin_set_referral_start(update, context)
    elif data == "admin_forced_menu":
        await admin_forced_menu(update, context)
    elif data == "admin_forced_channels":
        await admin_forced_channels(update, context)
    elif data == "admin_forced_bots":
        await admin_forced_bots(update, context)
    elif data == "admin_forced_social":
        await admin_forced_social(update, context)
    elif data == "admin_collections":
        await admin_collections(update, context)
    elif data == "admin_add_collection":
        await admin_add_collection_start(update, context)
    elif data == "admin_add_forced_channel":
        await admin_add_forced_channel_start(update, context)
    elif data == "admin_add_forced_bot":
        await admin_add_forced_bot_start(update, context)
    elif data == "admin_add_forced_social":
        await admin_add_forced_social_start(update, context)
    elif data == "admin_add_points_channel":
        await admin_add_points_channel_start(update, context)
    elif data == "admin_add_category":
        await admin_add_category_start(update, context)
    elif data == "admin_add_batch_tools":
        await admin_add_batch_tools_start(update, context)
    elif data.startswith("tool_"):
        await show_tool_detail(update, context, int(data.split('_')[1]))
    elif data.startswith("buy_"):
        await buy_tool(update, context, int(data.split('_')[1]))
    elif data.startswith("pending_buy_"):
        await pending_buy_tool(update, context, int(data.split('_')[2]))
    elif data.startswith("shop_page_"):
        await shop(update, context, int(data.split('_')[2]))
    elif data.startswith("edit_tool_"):
        await admin_edit_tool_select(update, context)
    elif data.startswith("price_tool_"):
        await admin_price_tool_select(update, context)
    elif data.startswith("delete_tool_"):
        await admin_delete_tool_callback(update, context)
    else:
        await query.answer("أمر غير معروف")

# ========== تشغيل البوت ==========
async def main():
    await init_db()
    # إعدادات التطبيق مع زيادة timeouts
    app = Application.builder().token(TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    
    # تعيين الأوامر
    await app.bot.set_my_commands([
        BotCommand("start", "بدء استخدام البوت"),
        BotCommand("admin", "لوحة تحكم الأدمن")
    ])
    
    # معالج الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    # محادثة إدخال الكود
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(redeem_code_start, pattern="^redeem_code$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_code_value)]},
        fallbacks=[]
    ))
    
    # محادثة تحويل النقاط
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(transfer_points_start, pattern="^transfer_points$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_points_value)]},
        fallbacks=[]
    ))
    
    # محادثة تغيير اللغة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(change_language, pattern="^change_language$")],
        states={1: [CallbackQueryHandler(set_language_callback, pattern="^lang_")]},
        fallbacks=[]
    ))
    
    # محادثة المسابقات
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(contest_answer_start, pattern="^contest_answer_")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, contest_answer_value)]},
        fallbacks=[]
    ))
    
    # محادثة الدفع عبر Asia
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(asia_payment_start, pattern="^asia_payment$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, asia_payment_amount)]},
        fallbacks=[]
    ))
    
    # محادثة البث
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة قناة نقاط
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_points_channel_start, pattern="^admin_add_points_channel$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_points_channel_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة قسم
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_category_start, pattern="^admin_add_category$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_category_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة أداة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_tool_start, pattern="^admin_add_tool$")],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_name)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_desc)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_price)],
            4: [MessageHandler(filters.TEXT | filters.Document.ALL, admin_add_tool_file)]
        },
        fallbacks=[]
    ))
    
    # محادثة تعديل أداة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_tool_start, pattern="^admin_edit_tool$")],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tool_name)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tool_desc)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tool_price)],
            4: [MessageHandler(filters.TEXT | filters.Document.ALL, edit_tool_file)]
        },
        fallbacks=[]
    ))
    
    # محادثة تغيير السعر
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_change_price_start, pattern="^admin_change_price$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_price_value)]},
        fallbacks=[]
    ))
    
    # محادثة إنشاء كود
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_create_code_start, pattern="^admin_create_code$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_code_value)]},
        fallbacks=[]
    ))
    
    # محادثة تعيين نقاط الإحالة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_referral_start, pattern="^admin_set_referral$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_referral_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة قناة إجبارية
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_forced_channel_start, pattern="^admin_add_forced_channel$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_forced_channel_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة بوت إجباري
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_forced_bot_start, pattern="^admin_add_forced_bot$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_forced_bot_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة حساب تواصل إجباري
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_forced_social_start, pattern="^admin_add_forced_social$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_forced_social_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة نشاط تجميع
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_collection_start, pattern="^admin_add_collection$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_collection_value)]},
        fallbacks=[]
    ))
    
    # محادثة إعدادات الموقع
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_site_url_start, pattern="^set_site_url$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_site_url_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_site_token_start, pattern="^set_site_token$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_site_token_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_min_transfer_start, pattern="^set_min_transfer$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_min_transfer_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_share_points_start, pattern="^set_share_points$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_share_points_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_terms_start, pattern="^set_terms$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_terms_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_buy_text_start, pattern="^set_buy_text$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_buy_text_value)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(set_prize_text_start, pattern="^set_prize_text$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_prize_text_value)]},
        fallbacks=[]
    ))
    
    # محادثة إضافة أدوات دفعة واحدة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_batch_tools_start, pattern="^admin_add_batch_tools$")],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_batch_tools_count)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_batch_tools_data)]
        },
        fallbacks=[]
    ))
    
    # محادثة حظر مستخدم
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_user_start, pattern="^admin_ban_user$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_user_value)]},
        fallbacks=[]
    ))
    
    # محادثة إلغاء حظر مستخدم
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_user_start, pattern="^admin_unban_user$")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_user_value)]},
        fallbacks=[]
    ))
    
    # محادثة إنشاء مسابقة
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_create_contest_start, pattern="^admin_create_contest$")],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_contest_question)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_contest_answer)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_contest_points)]
        },
        fallbacks=[]
    ))
    
    # محادثة إعدادات Asia
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_asia_settings_start, pattern="^admin_asia_settings$")],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_asia_settings_key)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_asia_settings_merchant)]
        },
        fallbacks=[]
    ))
    
    # معالج الأزرار
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    logger.info("🚀 تم تشغيل البوت بنجاح!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())