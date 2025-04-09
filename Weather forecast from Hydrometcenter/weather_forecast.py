from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import requests
import feedparser
import re
from datetime import datetime
import pytz
import asyncio
import os
import logging
import json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

os.environ['TZ'] = 'Europe/Moscow'

# Функция для загрузки данных из файла
def load_cities():
    try:
        with open("cities.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}

# Функция для сохранения данных в файл
def save_cities():
    with open("cities.json", "w", encoding="utf-8") as file:
        json.dump(CITIES, file, ensure_ascii=False, indent=4)

# Загрузка городов при старте
CITIES = load_cities()

# Словарь для хранения последнего состояния прогноза для каждого города
last_weather_data = {}

# Функция для получения прогноза погоды из RSS
def get_weather_from_rss(city_name, city_code):
    try:
        rss_url = f"https://meteoinfo.ru/rss/forecasts/index.php?s={city_code}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        }
        response = requests.get(rss_url, headers=headers)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo:
            logger.error(f"Ошибка при парсинге RSS для города {city_name}: {feed.bozo_exception}")
            return f"Ошибка при парсинге RSS для города {city_name}: {feed.bozo_exception}", None, []
        # Остальная логика функции...
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе для города {city_name}: {e}")
        return f"Ошибка при запросе для города {city_name}: {e}", None, []
    except Exception as e:
        logger.error(f"Неизвестная ошибка для города {city_name}: {e}")
        return f"Неизвестная ошибка для города {city_name}: {e}", None, []

# Функция для отправки прогноза погоды в Telegram
async def check_and_send_weather(context):
    try:
        TELEGRAM_CHAT_ID = context.bot_data.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID")
        for city_name, city_code in CITIES.items():
            weather, update_time, parsed_data = get_weather_from_rss(city_name, city_code)
            last_update_time = last_weather_data.get(city_code, {}).get("update_time")
            if update_time != last_update_time:
                significant_change = False
                last_parsed_data = last_weather_data.get(city_code, {}).get("parsed_data", [])
                for i, current_entry in enumerate(parsed_data):
                    if i < len(last_parsed_data):
                        last_entry = last_parsed_data[i]
                        if (
                            current_entry["description"] != last_entry["description"] or
                            abs(current_entry["night_temp"] - last_entry["night_temp"]) > 5 or
                            abs(current_entry["day_temp"] - last_entry["day_temp"]) > 5 or
                            current_entry["wind_direction"] != last_entry["wind_direction"] or
                            abs(current_entry["wind_speed"] - last_entry["wind_speed"]) > 3 or
                            abs(current_entry["night_pressure"] - last_entry["night_pressure"]) > 5 or
                            abs(current_entry["day_pressure"] - last_entry["day_pressure"]) > 5 or
                            abs(current_entry["precipitation"] - last_entry["precipitation"]) > 20
                        ):
                            significant_change = True
                            break
                if significant_change or not last_update_time:
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=weather, parse_mode="Markdown")
                    logger.info(f"Прогноз для города {city_name} отправлен.")
                last_weather_data[city_code] = {"update_time": update_time, "parsed_data": parsed_data}
            else:
                logger.info(f"Прогноз для города {city_name} не изменился.")
    except Exception as e:
        logger.error(f"Ошибка при отправке прогноза: {e}")

# Функция для запуска планировщика
def start_scheduler(application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send_weather, 'interval', minutes=60, args=[application])
    scheduler.start()
    logger.info("Планировщик запущен. Прогноз будет проверяться каждые 60 минут.")

# Команда /add_city
async def add_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /add_city <Название города> <Код города>")
        return

    city_name, city_code = args
    CITIES[city_name] = city_code  # Добавляем город в словарь
    save_cities()  # Сохраняем изменения в файл
    await update.message.reply_text(f"✅ Город {city_name} с кодом {city_code} добавлен.")

# Команда /remove_city
async def remove_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /remove_city <Название города>")
        return

    city_name = args[0]
    if city_name in CITIES:
        del CITIES[city_name]  # Удаляем город из словаря
        save_cities()  # Сохраняем изменения в файл
        await update.message.reply_text(f"❌ Город {city_name} удален.")
    else:
        await update.message.reply_text(f"⚠️ Город {city_name} не найден в списке.")

# Команда /list_cities
async def list_cities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CITIES:
        await update.message.reply_text("Список городов пуст.")
        return

    cities_list = "\n".join([f"🏙️ {city}: {code}" for city, code in CITIES.items()])
    await update.message.reply_text(f"Список отслеживаемых городов:\n{cities_list}")

# Основная функция
async def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", lambda update, context: update.message.reply_text("Бот запущен.")))
    application.add_handler(CommandHandler("add_city", add_city))
    application.add_handler(CommandHandler("remove_city", remove_city))
    application.add_handler(CommandHandler("list_cities", list_cities))

    # Запуск планировщика
    start_scheduler(application)

    # Запуск бота
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
