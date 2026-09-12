import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

# ============ SOZLAMALAR ============
# Render'da bularni kodga yozmaysiz — Dashboard > Environment bo'limida
# Environment Variable sifatida kiritasiz (xavfsizroq). Lokal sinov uchun
# osdan o'qiy olmasa, pastdagi standart qiymatlarni o'zgartirib qo'yaverishingiz mumkin.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "123456789").split(",")]
MOVIES_CHANNEL_ID = int(os.environ.get("MOVIES_CHANNEL_ID", "-1001234567890"))
FORCE_SUB_CHANNELS = [
    # {"id": -1001111111111, "username": "kino_kanal1", "title": "Kino Kanal 1"},
    # {"id": -1001222222222, "username": "kino_kanal2", "title": "Kino Kanal 2"},
]
DB_PATH = "kino_bot.db"
PORT = int(os.environ.get("PORT", 10000))  # Render shu orqali "tirikligini" tekshiradi

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER,
            title TEXT,
            added_date TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            joined_date TEXT
        )
    """)
    conn.commit()
    conn.close()


def db_add_movie(code: str, channel_id: int, message_id: int, title: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO movies (code, channel_id, message_id, title, added_date) VALUES (?, ?, ?, ?, ?)",
        (code, channel_id, message_id, title, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def db_get_movie(code: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT channel_id, message_id, title FROM movies WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    return row


def db_add_user(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, joined_date) VALUES (?, ?, ?)",
        (user_id, username or "", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def db_stats():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM movies")
    movies_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]
    conn.close()
    return movies_count, users_count


def db_all_user_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


# ============ MAJBURIY OBUNA (FORCE SUBSCRIBE) ============
async def check_subscription(user_id: int) -> list:
    """Obuna bo'lmagan kanallar ro'yxatini qaytaradi (bo'sh bo'lsa hammasiga obuna)."""
    not_subscribed = []
    for ch in FORCE_SUB_CHANNELS:
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(ch)
        except TelegramBadRequest:
            not_subscribed.append(ch)
    return not_subscribed


def subscribe_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"➕ {ch['title']}", url=f"https://t.me/{ch['username']}")]
        for ch in channels
    ]
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ HANDLERLAR ============
@router.message(Command("start"))
async def cmd_start(message: Message):
    db_add_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "🎬 Kino botiga xush kelibsiz!\n\n"
        "Film kodini yuboring va men sizga filmni jo'nataman.\n"
        "Masalan: <code>0001</code>",
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    movies_count, users_count = db_stats()
    await message.answer(f"📊 Statistika:\n🎞 Filmlar: {movies_count}\n👥 Foydalanuvchilar: {users_count}")


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject):
    """Kanaldagi videoni forward qilib, /add 0001 deb javob yozish orqali qo'shiladi."""
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: kanaldan videoni botga forward qiling va\n<code>/add KOD</code> deb javob bering.", parse_mode="HTML")
        return
    if not message.reply_to_message or not message.reply_to_message.forward_from_chat:
        await message.answer("❗️ Avval kinoni saqlangan kanaldan botga forward qiling, so'ng shu xabarga javob qilib /add KOD yozing.")
        return

    code = command.args.strip()
    fwd = message.reply_to_message
    title = fwd.caption or fwd.video.file_name if fwd.video else "Nomsiz"
    db_add_movie(code, fwd.forward_from_chat.id, fwd.forward_from_message_id, title)
    await message.answer(f"✅ Film qo'shildi!\nKod: <code>{code}</code>", parse_mode="HTML")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /broadcast Xabar matni")
        return
    text = command.args
    ids = db_all_user_ids()
    sent, failed = 0, 0
    status = await message.answer(f"Yuborilmoqda... 0/{len(ids)}")
    for i, uid in enumerate(ids, start=1):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            await status.edit_text(f"Yuborilmoqda... {i}/{len(ids)}")
        await asyncio.sleep(0.05)  # flood limitdan qochish
    await status.edit_text(f"✅ Yakunlandi. Yuborildi: {sent}, xato: {failed}")


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(callback):
    not_subscribed = await check_subscription(callback.from_user.id)
    if not_subscribed:
        await callback.answer("❗️ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        return
    await callback.message.edit_text("✅ Rahmat! Endi film kodini yuborishingiz mumkin.")
    await callback.answer()


@router.message(F.text)
async def handle_code(message: Message):
    """Foydalanuvchi film kodini yuborganda ishlaydi."""
    db_add_user(message.from_user.id, message.from_user.username)

    if FORCE_SUB_CHANNELS:
        not_subscribed = await check_subscription(message.from_user.id)
        if not_subscribed:
            await message.answer(
                "📢 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                reply_markup=subscribe_keyboard(not_subscribed),
            )
            return

    code = message.text.strip()
    movie = db_get_movie(code)
    if not movie:
        await message.answer("❌ Bunday kodli film topilmadi. Kodni tekshirib qayta urinib ko'ring.")
        return

    channel_id, message_id, title = movie
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=channel_id,
            message_id=message_id,
        )
    except TelegramBadRequest as e:
        logging.error(f"Copy error: {e}")
        await message.answer("⚠️ Filmni yuborishda xatolik yuz berdi. Admin bilan bog'laning.")


# ============ KEEP-ALIVE WEB SERVER (Render/UptimeRobot uchun) ============
async def handle_ping(request):
    return web.Response(text="Bot ishlayapti ✅")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Keep-alive server {PORT}-portda ishga tushdi")


# ============ ISHGA TUSHIRISH ============
async def main():
    db_init()
    await start_webserver()
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
