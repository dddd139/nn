import os
import sqlite3
import telegram
from telegram.ext import Application, CommandHandler
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import asyncio
import tempfile
import nest_asyncio
import base64

# Применяем nest_asyncio для поддержки вложенных циклов событий
nest_asyncio.apply()

# Конфигурация
TELEGRAM_TOKEN = '7272612416:AAHgZU0SgaQwpn08mJeqk0lHgviCUOcxE5c'
GOOGLE_CREDENTIALS_FILE = 'credentials.json'  # Файл учетных данных Google API
FILE_IDS = [
    '1fnLM68dxLI5vvjFXudoUVfO8DTWVjbuT',
    '1uPdPWWXtCxObqqbjwLThU6MaWd-6n7W_',
    '15k3vKPmIoQshsg9WOYaMBFZsgq8rDggc',
    '1thFi5HoJWIITSxHb-Gl2MnX4pnBVSM0a',
    '1lsYEZ5iBpsuop0BtsdwqmXbe8lmN5PR9',
    '1BTYgZt4r9bKwz-40TNW_ZUSXW0itU8GG',
    '13Q1VdW1Uz8JBjBT7WJUdjvMlubmq-_4w',
    '1Tp7iudab37rOCo38clxZo6fLktwky95_'
]
FILE_NAMES = [
    'yandex_eda.db',
    'Telegram5.db',
    'Telegram4.db',
    'Telegram3.db',
    'Telegram2.db',
    'Telegram1.db',
    'telegram_bd.db',
    'burgerkingrus.ru_08.2024_(5.627.676)_orders.csv.db'
]
TEMP_DIR = tempfile.gettempdir()  # Временная папка для хранения баз

# Инициализация Google Drive API
def init_drive_service():
    try:
        if os.getenv('GOOGLE_CREDENTIALS'):
            creds_data = base64.b64decode(os.getenv('GOOGLE_CREDENTIALS')).decode('utf-8')
            with open(os.path.join(TEMP_DIR, 'credentials.json'), 'w') as f:
                f.write(creds_data)
        creds = Credentials.from_authorized_user_file(os.path.join(TEMP_DIR, 'credentials.json'), [
            'https://www.googleapis.com/auth/drive.readonly'
        ])
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"Ошибка инициализации Google Drive API: {e}")
        raise

# Функция загрузки файла с Google Drive
async def download_file(drive_service, file_id, file_name, context):
    try:
        file_path = os.path.join(TEMP_DIR, file_name)
        request = drive_service.files().get_media(fileId=file_id)
        with open(file_path, 'wb') as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                if status:
                    await context.bot.send_message(
                        chat_id=context.job.context,
                        text=f"Загрузка {file_name}: {int(status.progress() * 100)}%"
                    )
        await context.bot.send_message(
            chat_id=context.job.context,
            text=f"Файл {file_name} успешно загружен на сервер"
        )
        return file_path
    except Exception as e:
        await context.bot.send_message(
            chat_id=context.job.context,
            text=f"Ошибка при загрузке {file_name}: {str(e)}"
        )
        return None

# Функция поиска в базе данных
async def search_in_db(file_path, query, chat_id, bot):
    try:
        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_fts WHERE user_fts MATCH ?", (query,))
        results = cursor.fetchall()
        conn.close()
        
        if results:
            response = f"Результаты поиска в {os.path.basename(file_path)}:\n"
            for row in results[:5]:
                response += f"{row}\n"
            if len(results) > 5:
                response += f"...и еще {len(results) - 5} результатов\n"
            await bot.send_message(chat_id=chat_id, text=response)
        else:
            await bot.send_message(chat_id=chat_id, text=f"В {os.path.basename(file_path)} ничего не найдено")
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"Ошибка при поиске в {os.path.basename(file_path)}: {str(e)}")

# Фproofункция очистки временных файлов
async def cleanup(update, context):
    chat_id = update.message.chat_id
    try:
        for file_name in FILE_NAMES:
            file_path = os.path.join(TEMP_DIR, file_name)
            if os.path.exists(file_path):
                os.remove(file_path)
                await context.bot.send_message(chat_id=chat_id, text=f"Файл {file_name} удалён")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"Файл {file_name} не найден")
        await context.bot.send_message(chat_id=chat_id, text="Очистка завершена")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Ошибка при очистке: {str(e)}")

# Команда /start
async def start(update, context):
    await update.message.reply_text(
        'Привет! Используйте /download для загрузки баз данных (около 20 ГБ), /search <запрос> для поиска по таблице user_fts, или /cleanup для удаления загруженных баз.'
    )

# Команда /download
async def download(update, context):
    chat_id = update.message.chat_id
    await update.message.reply_text('Начинаю загрузку баз данных (это может занять время из-за размера 20 ГБ)...')
    
    drive_service = init_drive_service()
    
    for file_id, file_name in zip(FILE_IDS, FILE_NAMES):
        context.job_queue.run_once(
            lambda ctx: download_file(drive_service, file_id, file_name, ctx),
            when=0,
            context=chat_id
        )
    
    await update.message.reply_text('Все файлы поставлены в очередь на загрузку.')

# Команда /search
async def search(update, context):
    chat_id = update.message.chat_id
    query = ' '.join(context.args)
    
    if not query:
        await update.message.reply_text('Пожалуйста, укажите запрос для поиска. Пример: /search текст')
        return
    
    await update.message.reply_text(f'Выполняю поиск по запросу: {query}')
    
    found = False
    for file_name in FILE_NAMES:
        file_path = os.path.join(TEMP_DIR, file_name)
        if os.path.exists(file_path):
            found = True
            await search_in_db(file_path, query, chat_id, context.bot)
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"База {file_name} не найдена. Сначала выполните /download"
            )
    
    if not found:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ни одна база не загружена. Выполните /download для загрузки баз."
        )

# Основная функция
async def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('download', download))
    application.add_handler(CommandHandler('search', search))
    application.add_handler(CommandHandler('cleanup', cleanup))
    
    # Запускаем polling в текущем цикле событий
    await application.run_polling()

if __name__ == '__main__':
    # Используем текущий цикл событий вместо asyncio.run()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
