# -*- coding: utf-8 -*-
import os
import json
import time
import uuid
import asyncio
import logging
from datetime import datetime
from aiohttp import web

import requests
import urllib3
import telegram
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═══════════════ تنظیمات اصلی ═══════════════
BOT_NAME = "نبض بازار"
TOKEN = "8970486954:AAHlvEcS0VklJu-Umh3-mBoEfc5C8aTdKMA"
ADMIN_ID = 1040234076
BOT_USERNAME = "Nabzbazaarrbot"
PREMIUM_CONTACT = "@Singer963"

DATA_FILE = "data.json"
LOG_FILE = "bot.log"
CHECK_INTERVAL = 60
MARKET_CACHE_TTL = 120
ITEMS_PER_PAGE = 5

INITIAL_FREE_QUOTA = 3
PREMIUM_PRICE = 50000

session = requests.Session()
session.verify = False
HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36"}

# ═══════════════ دارایی‌ها ═══════════════
ASSETS = {
    "BTC": {"type": "crypto", "name": "بیت‌کوین", "cg": "bitcoin"},
    "ETH": {"type": "crypto", "name": "اتریوم", "cg": "ethereum"},
    "USDT": {"type": "crypto", "name": "تتر", "cg": "tether"},
    "PAXG": {"type": "crypto", "name": "طلا", "cg": "pax-gold"},
    "XAG": {"type": "metal", "name": "نقره", "cg": "kinesis-silver"},
    "BNB": {"type": "crypto", "name": "بایننس", "cg": "binancecoin"},
    "SOL": {"type": "crypto", "name": "سولانا", "cg": "solana"},
    "XRP": {"type": "crypto", "name": "ریپل", "cg": "ripple"},
    "TON": {"type": "crypto", "name": "تون", "cg": "the-open-network"},
    "DOGE": {"type": "crypto", "name": "دوج", "cg": "dogecoin"},
}

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger(__name__)

# ═══════════════ ابزارها ═══════════════
FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
def to_fa(text):
    if text is None: return "-"
    return str(text).translate(FA_DIGITS)

def fmt_price(price):
    if price is None: return "نامشخص"
    if price >= 1000: return to_fa(f"{price:,.2f} دلار")
    if price >= 1: return to_fa(f"{price:,.4f} دلار")
    return to_fa(f"{price:,.6f} دلار")

def fmt_toman(price):
    if price is None: return ""
    if price >= 1_000_000_000: return to_fa(f"{price:,.0f} تومان")
    if price >= 1_000_000: return to_fa(f"{price:,.1f} تومان")
    if price >= 1000: return to_fa(f"{price:,.2f} تومان")
    return to_fa(f"{price:,.4f} تومان")

def fmt_change(ch):
    if ch is None: return "⚪️ -"
    emoji = "🟢" if ch >= 0 else "🔴"
    sign = "+" if ch >= 0 else ""
    return to_fa(f"{emoji} {sign}{ch:.2f}%")

# ═══════════════ دیتابیس ═══════════════
def load_data():
    if not os.path.exists(DATA_FILE): return {"users": {}, "alerts": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {"users": {}, "alerts": []}

def save_data(data):
    with open(DATA_FILE + ".tmp", "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(DATA_FILE + ".tmp", DATA_FILE)

def is_premium(user):
    if user.get("premium") is True: return True
    if user.get("premium_until", 0) > time.time(): return True
    return False

def ensure_user(data, uid, username=None, referrer_id=None):
    uid = str(uid)
    if uid not in data["users"]:
        data["users"][uid] = {
            "premium": False, "premium_until": 0,
            "joined": datetime.utcnow().isoformat(), "username": username or "",
            "free_quota": INITIAL_FREE_QUOTA, 
            "total_referrals": 0, "available_referrals": 0,
            "referred_by": str(referrer_id) if referrer_id else None
        }
        if referrer_id and str(referrer_id) in data["users"] and str(referrer_id) != uid:
            ref = data["users"][str(referrer_id)]
            ref["total_referrals"] = ref.get("total_referrals", 0) + 1
            ref["available_referrals"] = ref.get("available_referrals", 0) + 1
    else:
        if "premium_until" not in data["users"][uid]: data["users"][uid]["premium_until"] = 0
        if "free_quota" not in data["users"][uid]: data["users"][uid]["free_quota"] = INITIAL_FREE_QUOTA
        if "total_referrals" not in data["users"][uid]: data["users"][uid]["total_referrals"] = 0
        if "available_referrals" not in data["users"][uid]: data["users"][uid]["available_referrals"] = 0
            
    return data["users"][uid]

def get_quota_status(user):
    if is_premium(user): return True, ""
    if user.get("free_quota", 0) > 0: return True, ""
    return False, (
        "🔒 <b>سهمیه رایگان شما به پایان رسیده است.</b>\n\n"
        "برای ادامه استفاده از ربات، یکی از راه‌های زیر را انتخاب کنید:\n\n"
        f"🎁 دعوت دوستان و دریافت جایزه: /invite\n"
        f"⭐ خرید اشتراک ویژه: {PREMIUM_CONTACT}"
    )

# ═══════════════ دریافت داده‌ها ═══════════════
_market_cache = {"data": None, "timestamp": 0}
_usdt_toman_cache = {"rate": None, "timestamp": 0}
_usdt_history_cache = {"data": None, "timestamp": 0}

def _sync_get_usdt_toman():
    try:
        r = session.get("https://raw.githubusercontent.com/iran-market/iran-market.github.io/main/data/popular.json", headers=HEADERS, timeout=8).json()
        for item in r.get("data", []):
            if item.get("symbol") == "USD_IRR_FREE":
                val = float(item.get("price", 0))
                if val > 0: return val
    except: pass
    
    try:
        r = session.get("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=irr", headers=HEADERS, timeout=8).json()
        if isinstance(r, dict) and "tether" in r and "irr" in r["tether"]:
            return float(r["tether"]["irr"]) / 10.0
    except: pass
    return 70000.0

async def get_usdt_toman():
    global _usdt_toman_cache
    if time.time() - _usdt_toman_cache["timestamp"] < 300: 
        return _usdt_toman_cache["rate"]
    rate = await asyncio.to_thread(_sync_get_usdt_toman)
    _usdt_toman_cache = {"rate": rate, "timestamp": time.time()}
    return rate

def _sync_get_usdt_history():
    try:
        r = session.get("https://api.coingecko.com/api/v3/coins/tether/market_chart?vs_currency=irr&days=365&interval=daily", headers=HEADERS, timeout=10).json()
        if isinstance(r, dict):
            prices = r.get("prices", [])
            if prices: return [float(p[1]) / 10.0 for p in prices]
    except: pass
    return []

async def get_usdt_toman_history():
    global _usdt_history_cache
    if _usdt_history_cache["data"] and time.time() - _usdt_history_cache["timestamp"] < 3600:
        return _usdt_history_cache["data"]
    history = await asyncio.to_thread(_sync_get_usdt_history)
    if history:
        _usdt_history_cache = {"data": history, "timestamp": time.time()}
        return history
    return []

def _sync_get_coingecko_batch(cg_ids):
    try:
        params = {
            "vs_currency": "usd", 
            "ids": ",".join(cg_ids), 
            "price_change_percentage": "24h,30d,200d,1y",
            "order": "market_cap_desc"
        }
        r = session.get("https://api.coingecko.com/api/v3/coins/markets", params=params, headers=HEADERS, timeout=15).json()
        if isinstance(r, list):
            return {coin["id"]: coin for coin in r}
        else:
            return {}
    except Exception as e:
        print(f"❌ [CoinGecko Batch]: {e}")
        return {}

async def get_all_assets_data(force_refresh=False):
    global _market_cache
    if not force_refresh and _market_cache["data"] and (time.time() - _market_cache["timestamp"] < MARKET_CACHE_TTL):
        return _market_cache["data"]
    
    toman_rate = await get_usdt_toman()
    usdt_history = await get_usdt_toman_history()
    
    cg_ids = [info["cg"] for info in ASSETS.values()]
    cg_data = await asyncio.to_thread(_sync_get_coingecko_batch, cg_ids)
    
    if not cg_data and _market_cache["data"]:
        return _market_cache["data"]
        
    result = []
    for symbol, info in ASSETS.items():
        coin = cg_data.get(info["cg"])
        data = {
            "symbol": symbol, "name": info["name"], "price_toman": None, "price_usd": None,
            "ch_1h": None, "ch_24h": None, "ch_7d": None, "ch_30d": None, "ch_200d": None, "ch_1y": None
        }
        
        if coin:
            price_usd = coin["current_price"]
            data["price_usd"] = price_usd
            data["price_toman"] = price_usd * toman_rate
            
            ch_24h = coin.get("price_change_percentage_24h_in_currency")
            ch_30d = coin.get("price_change_percentage_30d_in_currency")
            ch_200d = coin.get("price_change_percentage_200d_in_currency")
            ch_1y = coin.get("price_change_percentage_1y_in_currency")
            
            if usdt_history and len(usdt_history) >= 365:
                def calc_toman_change(usd_ch, period_index):
                    if usd_ch is None or period_index > len(usdt_history): return None
                    usdt_old = usdt_history[-period_index]
                    if usdt_old == 0: return usd_ch
                    price_old_usd = price_usd / (1 + usd_ch/100)
                    price_old_toman = price_old_usd * usdt_old
                    return ((data["price_toman"] - price_old_toman) / price_old_toman) * 100
                
                data["ch_1h"] = calc_toman_change(ch_24h, 1)
                data["ch_24h"] = calc_toman_change(ch_24h, 1)
                data["ch_7d"] = calc_toman_change(ch_24h, 7) 
                data["ch_30d"] = calc_toman_change(ch_30d, 30)
                data["ch_200d"] = calc_toman_change(ch_200d, 200)
                data["ch_1y"] = calc_toman_change(ch_1y, 365)
            else:
                data["ch_24h"] = ch_24h
                data["ch_30d"] = ch_30d
                data["ch_200d"] = ch_200d
                data["ch_1y"] = ch_1y
                
        result.append(data)
        
    _market_cache = {"data": result, "timestamp": time.time()}
    return result

# ═══════════════ کیبوردها ═══════════════
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 قیمت‌های بازار")],
        [KeyboardButton("💰 قیمت لحظه‌ای")],
        [KeyboardButton("🔔 ساخت هشدار"), KeyboardButton("📋 هشدارهای من")],
        [KeyboardButton("⭐ اشتراک ویژه"), KeyboardButton("🎁 دعوت دوستان")],
        [KeyboardButton("📖 راهنما و قوانین")]
    ], resize_keyboard=True)

def get_coins_inline_keyboard(action_prefix):
    keyboard = []
    row = []
    for symbol, info in ASSETS.items():
        row.append(InlineKeyboardButton(f"{info['name']} ({symbol})", callback_data=f"{action_prefix}_{symbol}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    if action_prefix == "getprice": keyboard.append([InlineKeyboardButton("❌ بستن لیست", callback_data="back_close")])
    elif action_prefix == "setalert": keyboard.append([InlineKeyboardButton("⬅️ بازگشت به منوی اصلی", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

def get_referral_keyboard(available):
    keyboard = []
    if available >= 2: keyboard.append([InlineKeyboardButton("۲ دعوت = ۱ فرصت", callback_data="claim_ref_2")])
    if available >= 5: keyboard.append([InlineKeyboardButton("۵ دعوت = ۲ فرصت", callback_data="claim_ref_5")])
    if available >= 10: keyboard.append([InlineKeyboardButton("۱۰ دعوت = ۳ فرصت", callback_data="claim_ref_10")])
    if available >= 15: keyboard.append([InlineKeyboardButton("۱۵ دعوت = ۴ فرصت", callback_data="claim_ref_15")])
    if available >= 20: keyboard.append([InlineKeyboardButton("۲۰ دعوت = ۱ ماه اشتراک ویژه", callback_data="claim_ref_20")])
    
    keyboard.append([InlineKeyboardButton("⬅️ بستن", callback_data="back_close")])
    return InlineKeyboardMarkup(keyboard)

# ═══════════════ دستورات اصلی ═══════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    referrer_id = context.args[0].replace("ref_", "") if context.args and context.args[0].startswith("ref_") else None
    ensure_user(data, user.id, user.username, referrer_id)
    save_data(data)
    
    context.user_data.clear()
    
    await update.message.reply_text(
        f"👋 سلام! به <b>{BOT_NAME}</b> خوش آمدی.\n\n📊 من همراهِ هوشمند تو در بازار هستم. از دکمه‌های زیر استفاده کن:",
        reply_markup=get_main_keyboard(), parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        f"📖 <b>راهنما و قوانین {BOT_NAME}:</b>\n\n"
        "🔹 <b>سهمیه رایگان:</b> هر کاربر در بدو ورود ۳ فرصت رایگان دارد. (هر بار ساخت یا حذف هشدار = ۱ فرصت)\n\n"
        "🎁 <b>دعوت دوستان:</b>\n"
        "با دعوت دوستان، دعوت‌های آزاد دریافت می‌کنید. سپس از منوی «🎁 دعوت دوستان» می‌توانید دعوت‌های خود را به فرصت رایگان یا اشتراک ویژه تبدیل کنید.\n\n"
        "⭐ <b>اشتراک ویژه:</b> با خرید اشتراک، هشدار نامحدود و بدون مصرف فرصت خواهید داشت.\n\n"
        "🔹 با «📊 قیمت‌های بازار» لیست دارایی‌ها رو ببین.\n"
        "🔹 با «💰 قیمت لحظه‌ای» جزئیات یک ارز رو بگیر.",
        reply_markup=get_main_keyboard(), parse_mode="HTML"
    )

async def show_invite_ui(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit=False):
    query = update.callback_query
    uid = str(update.effective_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    avail = user.get("available_referrals", 0)
    total = user.get("total_referrals", 0)
    
    text = f"🎁 <b>دعوت دوستان</b>\n\nلینک اختصاصی شما:\n<code>{link}</code>\n\n"
    text += f"👥 کل دعوت‌های موفق: <b>{to_fa(total)} نفر</b>\n"
    text += f"💳 دعوت‌های آزاد (قابل استفاده): <b>{to_fa(avail)} نفر</b>\n\n"
    text += "🏆 <b>با استفاده از دعوت‌های آزاد، جایزه بگیرید:</b>\n"
    text += "• ۲ دعوت = ۱ فرصت رایگان\n"
    text += "• ۵ دعوت = ۲ فرصت رایگان\n"
    text += "• ۱۰ دعوت = ۳ فرصت رایگان\n"
    text += "• ۱۵ دعوت = ۴ فرصت رایگان\n"
    text += "• ۲۰ دعوت = ۱ ماه اشتراک ویژه\n\n"
    
    if avail >= 2:
        text += "👇 روی یکی از دکمه‌های زیر بزنید تا جایزه‌تان کم شود:"
    else:
        text += "⚠️ شما هنوز به حد نصاب (۲ دعوت) نرسیده‌اید. لینک خود را برای دوستانتان بفرستید."

    kb = get_referral_keyboard(avail)
    
    if is_edit and query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except telegram.error.BadRequest:
            pass
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_invite_ui(update, context, is_edit=False)

async def handle_claim_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    uid = str(query.from_user.id)
    cost = int(query.data.split("_")[-1])
    data = load_data()
    user = data["users"].get(uid, {})
    
    if user.get("available_referrals", 0) >= cost:
        user["available_referrals"] -= cost
        
        if cost == 2: user["free_quota"] += 1
        elif cost == 5: user["free_quota"] += 2
        elif cost == 10: user["free_quota"] += 3
        elif cost == 15: user["free_quota"] += 4
        elif cost == 20:
            current_until = user.get("premium_until", 0)
            user["premium_until"] = max(time.time(), current_until) + (30 * 86400)
            
        save_data(data)
        await query.answer("✅ جایزه با موفقیت دریافت و اعمال شد!", show_alert=True)
        await show_invite_ui(update, context, is_edit=True)
    else:
        await query.answer("❌ شما به اندازه کافی دعوت آزاد ندارید.", show_alert=True)

# ═══════════════ دکمه‌های بازگشت ═══════════════
async def handle_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    action = query.data
    if action == "back_close":
        context.user_data.clear(); await query.message.delete()
    elif action == "back_main":
        context.user_data.clear(); await query.edit_message_text("منوی اصلی بازگشت داده شد. 👇")
        await context.bot.send_message(chat_id=query.from_user.id, text="لطفاً از دکمه‌های زیر استفاده کنید:", reply_markup=get_main_keyboard())
    elif action == "back_coins_price":
        context.user_data.clear(); await query.edit_message_text("کدام ارز یا کالا رو می‌خوای چک کنی؟ 👇", reply_markup=get_coins_inline_keyboard("getprice"))
    elif action == "back_coins_set":
        context.user_data.clear(); context.user_data["step"] = "choose_coin"
        await query.edit_message_text("برای کدام ارز می‌خوای هشدار بسازی؟ 👇", reply_markup=get_coins_inline_keyboard("setalert"))

# ═══════════════ 1. بخش قیمت‌های بازار ═══════════════
async def show_market_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())
        return
        
    context.user_data.clear()
    msg_obj = await update.message.reply_text("⏳ در حال دریافت اطلاعات لحظه‌ای بازار...")
    await _render_market(msg_obj, page=0, force_refresh=True, is_edit=False)

async def handle_market_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await query.answer("🔒 سهمیه شما تمام شده است.", show_alert=True)
        return
        
    await query.answer()
    page = int(query.data.split("_")[-1])
    await _render_market(query.message, page=page, force_refresh=False, is_edit=True)

async def handle_refresh_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await query.answer("🔒 سهمیه شما تمام شده است.", show_alert=True)
        return
        
    await query.answer("⏳ در حال به‌روزرسانی...")
    await _render_market(query.message, page=0, force_refresh=True, is_edit=True)

async def _render_market(message, page=0, force_refresh=False, is_edit=False):
    data = await get_all_assets_data(force_refresh=force_refresh)
    total_items = len(data)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start_idx = page * ITEMS_PER_PAGE
    page_items = data[start_idx:start_idx + ITEMS_PER_PAGE]

    text = f"📊 <b>نمای کلی بازار</b> (صفحه {to_fa(page+1)} از {to_fa(total_pages)})\n"
    text += "🟦🟦🟦🟦🟦🟦🟦🟦🟦🟦\n\n"

    for idx, d in enumerate(page_items, start=start_idx + 1):
        text += f"<b>{to_fa(idx)}. {d['name']} ({d['symbol']})</b>\n"
        if d["price_toman"] is not None:
            price_line = f"💵 {fmt_price(d['price_usd'])}"
            if d["price_toman"]: price_line += f" | 🇮🇷 {fmt_toman(d['price_toman'])}"
            text += f"<code>{price_line}</code>\n"
            text += f"<code>⚡️ ساعتی: {fmt_change(d['ch_1h'])} | 📅 روزانه: {fmt_change(d['ch_24h'])}</code>\n"
            text += f"<code>🗓 هفتگی: {fmt_change(d['ch_7d'])} | 📆 ماهانه: {fmt_change(d['ch_30d'])}</code>\n"
            text += f"<code>📉 ۶ماهه: {fmt_change(d['ch_200d'])} | 📈 سالانه: {fmt_change(d['ch_1y'])}</code>\n"
        else:
            text += f"<code>⚠️ در حال دریافت اطلاعات...</code>\n"
        text += "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

    text += "\n🔗 <i>منبع: CoinGecko + بازار آزاد</i>"

    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ صفحه قبل", callback_data=f"mkt_pg_{page-1}"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("صفحه بعد ➡️", callback_data=f"mkt_pg_{page+1}"))
    
    keyboard = []
    if nav_buttons: keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="refresh_market")])
    keyboard.append([InlineKeyboardButton("📉 جزئیات یک ارز", callback_data="back_coins_price")])
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="back_close")])

    try:
        if is_edit: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else: await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except telegram.error.BadRequest: pass
    except Exception as e: log.error(f"خطا در مارکت: {e}")

# ═══════════════ 2. بخش قیمت لحظه‌ای ═══════════════
async def show_price_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())
        return
        
    context.user_data.clear()
    await update.message.reply_text("کدام ارز یا کالا رو می‌خوای چک کنی؟ 👇", reply_markup=get_coins_inline_keyboard("getprice"))

async def handle_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await query.answer("🔒 سهمیه شما تمام شده است.", show_alert=True)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت به منو", callback_data="back_main")]]))
        return
        
    await query.answer("⏳ دریافت...")
    symbol = query.data.split("_")[1]
    
    all_data = await get_all_assets_data(force_refresh=False)
    d = next((item for item in all_data if item["symbol"] == symbol), None)
    
    if not d or d["price_toman"] is None:
        await query.edit_message_text("⚠️ خطای موقت در دریافت اطلاعات. لطفاً یک‌بار دیگر تلاش کنید.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"getprice_{symbol}")]]))
        return
        
    text = f"💰 <b>{d['name']} ({symbol})</b>\n\n"
    price_line = f"💵 {fmt_price(d['price_usd'])}"
    if d['price_toman']: price_line += f" | 🇮🇷 {fmt_toman(d['price_toman'])}"
    text += f"<code>{price_line}</code>\n"
    
    text += "\n📈 <b>آمار تغییرات:</b>\n"
    text += f"<code>• ساعتی: {fmt_change(d['ch_1h'])}</code>\n"
    text += f"<code>• روزانه: {fmt_change(d['ch_24h'])}</code>\n"
    text += f"<code>• هفتگی: {fmt_change(d['ch_7d'])}</code>\n"
    text += f"<code>• ماهانه: {fmt_change(d['ch_30d'])}</code>\n"
    text += f"<code>• ۶ ماهه: {fmt_change(d['ch_200d'])}</code>\n"
    text += f"<code>• سالانه: {fmt_change(d['ch_1y'])}</code>\n"
    
    text += "\n🔗 <i>منبع: CoinGecko + بازار آزاد</i>"
    
    symbols_list = list(ASSETS.keys())
    current_idx = symbols_list.index(symbol)
    prev_symbol = symbols_list[(current_idx - 1) % len(symbols_list)]
    next_symbol = symbols_list[(current_idx + 1) % len(symbols_list)]
    
    keyboard = [
        [InlineKeyboardButton(f"⬅️ {ASSETS[prev_symbol]['name']}", callback_data=f"getprice_{prev_symbol}"),
         InlineKeyboardButton(f"{ASSETS[next_symbol]['name']} ➡️", callback_data=f"getprice_{next_symbol}")],
        [InlineKeyboardButton("🔄 به‌روزرسانی", callback_data=f"getprice_{symbol}")],
        [InlineKeyboardButton("⬅️ بازگشت به لیست ارزها", callback_data="back_coins_price")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ═══════════════ 3. بخش ساخت هشدار ═══════════════
async def start_set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())
        return
        
    context.user_data.clear(); context.user_data["step"] = "choose_coin"
    await update.message.reply_text("برای کدام ارز می‌خوای هشدار بسازی؟ 👇", reply_markup=get_coins_inline_keyboard("setalert"))

async def handle_add_alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    can_use, msg = get_quota_status(user)
    if not can_use:
        await query.answer("🔒 سهمیه شما تمام شده است.", show_alert=True)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت به منو", callback_data="back_main")]]))
        return
        
    await query.answer()
    context.user_data.clear(); context.user_data["step"] = "choose_coin"
    await query.edit_message_text("برای کدام ارز می‌خوای هشدار بسازی؟ 👇", reply_markup=get_coins_inline_keyboard("setalert"))

async def handle_set_alert_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    symbol = query.data.split("_")[1]
    context.user_data["alert_symbol"] = symbol; context.user_data["step"] = "choose_direction"
    
    all_data = await get_all_assets_data(force_refresh=False)
    d = next((item for item in all_data if item["symbol"] == symbol), None)
    
    if d and d["price_toman"]:
        price_line = f"💰 قیمت فعلی: <code>{fmt_price(d['price_usd'])}</code> | <code>{fmt_toman(d['price_toman'])}</code>"
    else:
        price_line = "⚠️ قیمت فعلی در دسترس نیست."

    keyboard = [
        [InlineKeyboardButton("⬆️ بالای قیمت مشخص", callback_data="dir_above")],
        [InlineKeyboardButton("⬇️ زیر قیمت مشخص", callback_data="dir_below")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="back_coins_set")]
    ]
    await query.edit_message_text(
        f"هشدار برای <b>{d['name'] if d else symbol}</b>.\n\n{price_line}\n\n"
        f"می‌خوای وقتی قیمت **بالای** یک عدد رفت خبرت کنم یا **زیر** یک عدد؟ 👇",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

async def handle_set_alert_dir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    direction = query.data.split("_")[1]
    context.user_data["alert_dir"] = direction; context.user_data["step"] = "enter_price"
    symbol = context.user_data["alert_symbol"]; name = ASSETS[symbol]["name"]
    word = "بالای" if direction == "above" else "زیر"
    
    keyboard = [[InlineKeyboardButton("⬅️ بازگشت به مرحله قبل", callback_data=f"setalert_{symbol}")]]
    
    await query.edit_message_text(
        f"عالی! حالا قیمت هدف رو <b>به دلار</b> بفرست.\n\n"
        f"مثال: اگر می‌خوای وقتی {name} {word} ۱۰۰,۰۰۰ دلار رفت خبرت کنم، فقط بنویس: <code>100000</code>",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

async def handle_enter_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step") != "enter_price": return
    text_input = update.message.text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
    try:
        target = float(text_input.replace(",", ""))
        if target <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط یک عدد معتبر بفرست.")
        return
    uid = str(update.effective_user.id); symbol = context.user_data["alert_symbol"]; direction = context.user_data["alert_dir"]
    data = load_data(); user = data["users"].get(uid, {})
    
    can_use, msg = get_quota_status(user)
    if not can_use:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())
        context.user_data.clear(); return
        
    if not is_premium(user):
        user["free_quota"] -= 1
        
    data["alerts"].append({"id": str(uuid.uuid4()), "uid": uid, "symbol": symbol, "target": target, "direction": direction, "active": True, "created": datetime.utcnow().isoformat()})
    save_data(data); context.user_data.clear()
    word = "بالای" if direction == "above" else "زیر"
    await update.message.reply_text(
        f"✅ هشدار ثبت شد!\nوقتی <b>{ASSETS[symbol]['name']}</b> {word} {fmt_price(target)} رفت، بهت خبر می‌دم.",
        reply_markup=get_main_keyboard(), parse_mode="HTML"
    )

# ═══════════════ 4. بخش هشدارهای من ═══════════════
def _build_alerts_menu(uid):
    data = load_data()
    mine = [a for a in data["alerts"] if a["uid"] == uid and a.get("active")]
    user = data["users"].get(uid, {})
    
    text = f"📋 <b>هشدارهای فعال تو:</b>\n\n"
    keyboard = []
    
    if mine:
        for i, a in enumerate(mine):
            word = "بالای" if a["direction"] == "above" else "زیر"
            name = ASSETS.get(a['symbol'], {}).get('name', a['symbol'])
            text += f"{to_fa(i+1)}. <b>{name}</b> {word} {fmt_price(a['target'])}\n"
            keyboard.append([InlineKeyboardButton(f"❌ حذف {name} ({word} {fmt_price(a['target'])})", callback_data=f"delalert_{a['id']}")])
    else:
        text += "📭 هیچ هشدار فعالی نداری.\nهمین حالا یکی بساز!"
        
    if not is_premium(user):
        text += f"\n🎯 فرصت رایگان باقی‌مانده: {to_fa(user.get('free_quota', 0))}"
        
    keyboard.append([InlineKeyboardButton("➕ افزودن هشدار جدید", callback_data="add_alert_start")])
    keyboard.append([InlineKeyboardButton("⬅️ بستن لیست", callback_data="back_close")])
    return text, InlineKeyboardMarkup(keyboard)

async def show_my_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = str(update.effective_user.id)
    text, kb = _build_alerts_menu(uid)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def handle_delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    data = load_data()
    user = data["users"].get(uid, {})
    
    can_use, msg = get_quota_status(user)
    if not can_use:
        await query.answer("🔒 برای حذف هشدار به فرصت رایگان نیاز دارید.", show_alert=True)
        return
        
    await query.answer("✅ حذف شد!")
    alert_id = query.data.split("_")[1]
    
    for a in data["alerts"]:
        if a["id"] == alert_id: 
            a["active"] = False
            if not is_premium(user):
                user["free_quota"] -= 1
            break
    save_data(data)
    
    text, kb = _build_alerts_menu(uid)
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except telegram.error.BadRequest:
        await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

# ═══════════════ 5. اشتراک ویژه ═══════════════
async def show_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); uid = str(update.effective_user.id)
    data = load_data(); user = data["users"].get(uid, {})
    if is_premium(user):
        await update.message.reply_text(f"⭐ تو کاربر ویژه‌ی {BOT_NAME} هستی. هشدار نامحدود داری!", reply_markup=get_main_keyboard()); return
    
    await update.message.reply_text(
        f"⭐ <b>اشتراک ویژه‌ی {BOT_NAME}:</b>\n\n"
        "• هشدار نامحدود\n• پشتیبانی اولویت‌دار\n\n"
        f"💰 قیمت: {to_fa(f'{PREMIUM_PRICE:,}')} تومان / ماه\n"
        f"📞 خرید: {PREMIUM_CONTACT}\n\n"
        f"🎁 <b>یا رایگان!</b> با ۲۰ دعوت به ربات.",
        reply_markup=get_main_keyboard(), parse_mode="HTML"
    )

# ═══════════════ دستورات ادمین ═══════════════
def is_admin(update): return (update.effective_user.id if update.effective_user else 0) == ADMIN_ID

async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not context.args: await update.message.reply_text("مثال: <code>/grant 123456789</code>", parse_mode="HTML"); return
    uid = context.args[0]; data = load_data()
    data["users"].setdefault(uid, {})["premium"] = True; save_data(data)
    await update.message.reply_text(f"✅ کاربر <code>{uid}</code> ویژه شد.", parse_mode="HTML")
    try: await context.bot.send_message(chat_id=int(uid), text=f"🎉 اشتراک ویژه‌ی {BOT_NAME} برایت فعال شد!")
    except: pass

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    data = load_data()
    await update.message.reply_text(f"📊 کل کاربران: {to_fa(len(data['users']))} | ویژه: {to_fa(sum(1 for u in data['users'].values() if is_premium(u)))} | هشدار فعال: {to_fa(sum(1 for a in data['alerts'] if a.get('active')))}", parse_mode="HTML")

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not context.args: await update.message.reply_text("مثال: <code>/broadcast سلام به همه</code>", parse_mode="HTML"); return
    msg = " ".join(context.args); data = load_data(); sent = 0
    await update.message.reply_text(f"⏳ در حال ارسال...")
    for uid in list(data["users"].keys()):
        try: await context.bot.send_message(chat_id=int(uid), text=msg); sent += 1; await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ ارسال شد به {to_fa(sent)} کاربر.")

# ═══════════════ سیستم هشدار (بک‌گراند) ═══════════════
async def check_alerts(app: Application):
    data = load_data(); active = [a for a in data["alerts"] if a.get("active")]
    if not active: return
    triggered_ids = []
    
    all_data = await get_all_assets_data(force_refresh=False)
    price_map_usd = {d["symbol"]: d["price_usd"] for d in all_data if d["price_usd"]}
    
    for a in active:
        price_usd = price_map_usd.get(a["symbol"])
        if price_usd is None: continue
        
        hit = (a["direction"] == "above" and price_usd >= a["target"]) or (a["direction"] == "below" and price_usd <= a["target"])
        if hit:
            word = "بالای" if a["direction"] == "above" else "زیر"
            name = ASSETS[a['symbol']]["name"]
            msg = f"🚨 <b>هشدار {BOT_NAME}!</b>\n\n<b>{name}</b> به {fmt_price(price_usd)} رسید.\nهدف تو بود: {word} {fmt_price(a['target'])}"
            try: await app.bot.send_message(chat_id=int(a["uid"]), text=msg, parse_mode="HTML", reply_markup=get_main_keyboard())
            except: pass
            triggered_ids.append(a["id"])
            
    if triggered_ids:
        data = load_data()
        for a in data["alerts"]:
            if a["id"] in triggered_ids: a["active"] = False
        save_data(data)

async def alert_worker(app: Application):
    while True:
        try: await check_alerts(app)
        except Exception as e: log.exception(f"خطای alert_worker: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# ═══════════════ راه‌اندازی (مخصوص Render) ═══════════════
async def health_check(request):
    return web.Response(text="Bot is running!")

async def main():
    print(f"📊 در حال راه‌اندازی {BOT_NAME} روی سرور...")
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("invite", cmd_invite))
    application.add_handler(CommandHandler("grant", cmd_grant))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast))

    application.add_handler(MessageHandler(filters.Text("📊 قیمت‌های بازار"), show_market_prices))
    application.add_handler(MessageHandler(filters.Text("💰 قیمت لحظه‌ای"), show_price_menu))
    application.add_handler(MessageHandler(filters.Text("🔔 ساخت هشدار"), start_set_alert))
    application.add_handler(MessageHandler(filters.Text("📋 هشدارهای من"), show_my_alerts))
    application.add_handler(MessageHandler(filters.Text("⭐ اشتراک ویژه"), show_premium))
    application.add_handler(MessageHandler(filters.Text("🎁 دعوت دوستان"), cmd_invite))
    application.add_handler(MessageHandler(filters.Text("📖 راهنما و قوانین"), cmd_help))

    application.add_handler(CallbackQueryHandler(handle_price_callback, pattern="^getprice_"))
    application.add_handler(CallbackQueryHandler(handle_set_alert_coin, pattern="^setalert_"))
    application.add_handler(CallbackQueryHandler(handle_set_alert_dir, pattern="^dir_"))
    application.add_handler(CallbackQueryHandler(handle_delete_alert, pattern="^delalert_"))
    application.add_handler(CallbackQueryHandler(handle_back_callback, pattern="^back_"))
    application.add_handler(CallbackQueryHandler(handle_add_alert_start, pattern="^add_alert_start$"))
    application.add_handler(CallbackQueryHandler(handle_market_page, pattern="^mkt_pg_"))
    application.add_handler(CallbackQueryHandler(handle_refresh_market, pattern="^refresh_market$"))
    application.add_handler(CallbackQueryHandler(handle_claim_referral, pattern="^claim_ref_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_enter_price))

    # تنظیم دستورات پیش‌فرض
    await application.bot.set_my_commands([
        BotCommand("start", "🚀 شروع / منوی اصلی"),
        BotCommand("invite", "🎁 دعوت دوستان"),
        BotCommand("help", "📖 راهنما")
    ])

    # راه‌اندازی حلقه هشدارها در پس‌زمینه
    asyncio.create_task(alert_worker(application))

    PORT = int(os.environ.get("PORT", 8080))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        
        app = web.Application()
        app.router.add_get("/", health_check) # برای UptimeRobot
        app.router.add_post("/webhook", application.webhook_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        
        print(f"✅ ربات با موفقیت روی پورت {PORT} اجرا شد.")
        await asyncio.Event().wait() # سرور را همیشه روشن نگه می‌دارد
    else:
        print("⚠️ RENDER_EXTERNAL_URL یافت نشد. ربات به حالت Polling می‌رود.")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
