import logging
import os
import csv
import socket
import dns.resolver
import aiohttp
import phonenumbers
import sqlite3
import asyncio
import httpx
from phonenumbers import geocoder, carrier
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- Переменные окружения ---
TOKEN = os.getenv("TOKEN", "")
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
DB_PATH = "data.db"
USE_FTS = os.getenv("USE_FTS", "false").lower() == "true"  # Выбор режима поиска

if not TOKEN:
    raise RuntimeError("❌ Укажите переменную окружения TOKEN")

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Состояния пользователей ---
user_states: dict[int, str] = {}

# --- Функция скачивания базы ---
def download_databases():
    file_ids = [
        "1Tp7iudab37rOCo38clxZo6fLktwky95_",
        "1uPdPWWXtCxObqqbjwLThU6MaWd-6n7W_",
        "1fnLM68dxLI5vvjFXudoUVfO8DTWVjbuT",
        "1BTYgZt4r9bKwz-40TNW_ZUSXW0itU8GG",
        "1thFi5HoJWIITSxHb-Gl2MnX4pnBVSM0a",
        "13Q1VdW1Uz8JBjBT7WJUdjvMlubmq-_4w",
        "15k3vKPmIoQshsg9WOYaMBFZsgq8rDggc",
        "1lsYEZ5iBpsuop0BtsdwqmXbe8lmN5PR9",
    ]
    for idx, file_id in enumerate(file_ids, start=1):
        try:
            url = f"https://drive.google.com/uc?export=download&id={file_id}"
            r = httpx.get(url, follow_redirects=True)
            r.raise_for_status()
            path = f"data{idx}.db"
            with open(path, "wb") as f:
                f.write(r.content)
            logger.info(f"✅ Загружена {path}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки data{idx}.db: {e}")
    file_id = "1uSMpNJRQJqVziNmVANI7oBG8IyrZguCa"
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    r = httpx.get(url, follow_redirects=True)
    with open("data.db", "wb") as f:
        f.write(r.content)
    logger.info("✅ data.db загружена с Google Drive")

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "FTS5" if USE_FTS else "индексированный SQL"
    await update.message.reply_text(
        f"👋 Привет! Я OSINT-бот. Сейчас использую режим поиска: {mode}\n\n"
        "/phone — информация о номере телефона\n"
        "/ip — информация об IP\n"
        "/domain — информация о домене\n"
        "/email — проверка email через Hunter.io\n"
        "/telegram — проверить Telegram username\n"
        "/searchdb — поиск по SQLite-базе"
    )

async def cmd_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, prompt: str):
    user_states[update.effective_user.id] = state
    await update.message.reply_text(prompt)

async def cmd_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_generic(update, context, "awaiting_phone", "📞 Введите номер телефона:")

async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_generic(update, context, "awaiting_ip", "🌍 Введите IP-адрес:")

async def cmd_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_generic(update, context, "awaiting_domain", "🌐 Введите домен:")

async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_generic(update, context, "awaiting_email", "📧 Введите email:")

async def cmd_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_generic(update, context, "awaiting_telegram", "🔍 Введите Telegram username (@user):")

async def cmd_searchdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_states[update.effective_user.id] = "awaiting_dbsearch"
    await update.message.reply_text("🔎 Введите запрос для поиска в базе:")

# --- Обычный индексированный SQL-поиск ---
async def search_with_index(query: str) -> list[str]:
    if not os.path.exists(DB_PATH):
        return ["❌ База не найдена"]
    results = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT phone, email, name FROM users
            WHERE phone LIKE ? OR email LIKE ? OR name LIKE ?
            LIMIT 10
        """, (f"{query}%", f"{query}%", f"{query}%"))
        rows = cursor.fetchall()
        for phone, email, name in rows:
            results.append(f"📞 {phone} | 📧 {email} | 👤 {name}")
        conn.close()
    except Exception as e:
        results.append(f"❌ Ошибка при поиске: {e}")
    return results or ["❌ Нет результатов"]

# --- Поиск через FTS5 ---
async def search_with_fts(query: str) -> list[str]:
    results = []
    for i in range(1, 9):
        db_path = f"data{i}.db"
        if not os.path.exists(db_path):
            results.append(f"⚠️ {db_path} не найдена")
            continue
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT phone, email, name FROM users_fts
                WHERE users_fts MATCH ?
                LIMIT 10
            """, (query,))
            rows = cursor.fetchall()
            for phone, email, name in rows:
                results.append(f"📁 data{i}.db → 📞 {phone} | 📧 {email} | 👤 {name}")
            conn.close()
        except Exception as e:
            results.append(f"❌ Ошибка в data{i}.db: {e}")
    return results or ["❌ Нет результатов"]

# --- Обработка сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = user_states.pop(update.effective_user.id, "")
    text = update.message.text.strip()
    try:
        if state == "awaiting_phone":
            num = phonenumbers.parse(text, None)
            await update.message.reply_text(f"📞 Страна: {geocoder.description_for_number(num, 'en')}\n📡 Оператор: {carrier.name_for_number(num, 'en')}")
        elif state == "awaiting_ip":
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://ipinfo.io/{text}?token={IPINFO_TOKEN}") as r:
                    data = await r.json()
            await update.message.reply_text("\n".join(f"{k}: {v}" for k, v in data.items()))
        elif state == "awaiting_domain":
            ip = socket.gethostbyname(text)
            ns = dns.resolver.resolve(text, 'NS')
            await update.message.reply_text(f"🌐 {text} → IP: {ip}\nNS: {', '.join(str(r.target) for r in ns)}")
        elif state == "awaiting_email":
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.hunter.io/v2/email-verifier?email={text}&api_key={HUNTER_API_KEY}") as r:
                    data = await r.json()
            result = data.get("data", {})
            await update.message.reply_text("\n".join(f"{k}: {v}" for k, v in result.items()))
        elif state == "awaiting_telegram":
            await update.message.reply_text(f"https://t.me/{text.lstrip('@')}")
        elif state == "awaiting_dbsearch":
            results = await search_with_fts(text) if USE_FTS else await search_with_index(text)
            for r in results:
                await update.message.reply_text(r)
        else:
            await update.message.reply_text("🤖 Используй команду /start")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# --- Запуск ---
async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    await app.bot.delete_webhook(drop_pending_updates=True)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("phone", cmd_phone))
    app.add_handler(CommandHandler("ip", cmd_ip))
    app.add_handler(CommandHandler("domain", cmd_domain))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("telegram", cmd_telegram))
    app.add_handler(CommandHandler("searchdb", cmd_searchdb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ OSINT-бот запущен")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    download_databases()
    asyncio.get_event_loop().run_until_complete(main())

