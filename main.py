import logging
import os
import csv
import socket
import dns.resolver
import aiohttp
import phonenumbers
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

if not TOKEN:
    raise RuntimeError("❌ Укажите переменную окружения TOKEN")

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Состояния пользователей ---
user_states: dict[int, str] = {}

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я OSINT-бот. Вот что я умею:\n\n"
        "/phone — информация о номере телефона\n"
        "/ip — информация об IP\n"
        "/domain — информация о домене\n"
        "/email — проверка email через Hunter.io\n"
        "/telegram — проверить Telegram username\n"
        "/searchdb — быстрый поиск по SQLite базе"
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
    await update.message.reply_text("🔎 Введите ключевое слово для поиска в базе данных:")

# --- FTS SQLite поиск ---
def search_in_fts(keyword: str) -> list[str]:
    if not os.path.exists(DB_PATH):
        return ["❌ База данных не найдена"]

    results = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        safe_keyword = f'"{keyword}"'  # Кавычки для FTS
        query = \"""
        SELECT phone, email, name FROM users_fts
        WHERE users_fts MATCH ?
        LIMIT 10;
        \"""
        cursor.execute(query, (safe_keyword,))
        rows = cursor.fetchall()
        for row in rows:
            results.append(" | ".join(str(x) for x in row))
        conn.close()
    except Exception as e:
        results.append(f"❌ Ошибка SQLite: {e}")

    return results or ["❌ Ничего не найдено"]

# --- Обработка сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = user_states.pop(update.effective_user.id, "")
    text = update.message.text.strip()

    try:
        if state == "awaiting_phone":
            num = phonenumbers.parse(text, None)
            country = geocoder.description_for_number(num, "en")
            operator = carrier.name_for_number(num, "en")
            await update.message.reply_text(f"📞 Страна: {country}\\n📡 Оператор: {operator}")

        elif state == "awaiting_ip":
            url = f"https://ipinfo.io/{text}?token={IPINFO_TOKEN}"
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url) as resp:
                    data = await resp.json()
            await update.message.reply_text("\\n".join(f"{k}: {v}" for k, v in data.items()))

        elif state == "awaiting_domain":
            ip = socket.gethostbyname(text)
            answers = dns.resolver.resolve(text, 'NS')
            ns = ", ".join(str(r.target) for r in answers)
            await update.message.reply_text(f"🌐 {text} → IP: {ip}\\nNS: {ns}")

        elif state == "awaiting_email":
            url = f"https://api.hunter.io/v2/email-verifier?email={text}&api_key={HUNTER_API_KEY}"
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url) as resp:
                    data = await resp.json()
            result = data.get("data", {})
            await update.message.reply_text("\\n".join(f"{k}: {v}" for k, v in result.items()))

        elif state == "awaiting_telegram":
            user = text.lstrip("@")
            await update.message.reply_text(f"https://t.me/{user}")

        elif state == "awaiting_dbsearch":
            results = search_in_fts(text)
            for r in results:
                await update.message.reply_text(r)

        else:
            await update.message.reply_text("🤖 Используй команды: /start")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# --- Запуск ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("phone", cmd_phone))
    app.add_handler(CommandHandler("ip", cmd_ip))
    app.add_handler(CommandHandler("domain", cmd_domain))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("telegram", cmd_telegram))
    app.add_handler(CommandHandler("searchdb", cmd_searchdb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ OSINT-бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()


