import asyncio
import logging
import os
import re
import shutil
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo

import config
from bot.handlers import base, download, sync
from db.database import init_db
from webapp.api import create_webapp
from worker.scheduler import start_scheduler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SpotifyToTelegram")

WEBAPP_PORT = 8080

async def start_cloudflared(port: int):
    cf_path = shutil.which("cloudflared")
    if not cf_path:
        cf_path = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
        if not os.path.exists(cf_path):
            logger.warning("cloudflared не найден! Установите: winget install Cloudflare.cloudflared")
            return None, None

    logger.info("Запуск cloudflared туннеля...")
    process = await asyncio.create_subprocess_exec(
        cf_path, "tunnel", "--url", f"http://localhost:{port}",
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE
    )

    url = None
    while True:
        line = await process.stderr.readline()
        if not line:
            break
        line_str = line.decode('utf-8', errors='ignore')
        if "trycloudflare.com" in line_str:
            match = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line_str)
            if match:
                url = match.group(1)
                break

    async def consume(stream):
        while await stream.readline():
            pass
    asyncio.create_task(consume(process.stderr))
    asyncio.create_task(consume(process.stdout))

    return process, url

async def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("BOT_TOKEN не установлен в файле .env! Заполните токен перед запуском.")
        return

    # 1. Инициализируем базу данных
    logger.info("Инициализация базы данных SQLite...")
    await init_db()
    logger.info("База данных готова к работе.")

    # 2. Инициализируем бота и диспетчер
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()

    # 3. Подключаем обработчики (роутеры)
    dp.include_router(base.router)
    dp.include_router(sync.router)
    dp.include_router(download.router)

    # 4. Запускаем фоновый планировщик синхронизации
    scheduler = start_scheduler(bot, interval_minutes=15)

    # 5. Создаем и запускаем веб-сервер для Mini App
    import os
    port = int(os.getenv("PORT", WEBAPP_PORT))
    webapp = create_webapp(bot=bot)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Mini App web server запущен на порту {port}")
    
    cf_process = None
    # Если уже указан постоянный домен (например, на бесплатном хостинге Koyeb/Render):
    if config.WEBAPP_URL and ("trycloudflare" not in config.WEBAPP_URL and "localhost" not in config.WEBAPP_URL):
        tunnel_url = config.WEBAPP_URL
        logger.info(f"Используем постоянный серверный URL: {tunnel_url}")
    else:
        # Локальный запуск на ПК: поднимаем автоматический туннель Cloudflare
        cf_process, tunnel_url = await start_cloudflared(port)
        if tunnel_url:
            logger.info(f"Туннель поднят! URL: {tunnel_url}")
            config.WEBAPP_URL = tunnel_url
            
            # Обновляем .env для локального запуска
            env_path = config.BASE_DIR / ".env"
            if env_path.exists():
                content = env_path.read_text('utf-8')
                if 'WEBAPP_URL=' in content:
                    content = re.sub(r'WEBAPP_URL=.*', f'WEBAPP_URL={tunnel_url}', content)
                else:
                    content += f"\nWEBAPP_URL={tunnel_url}\n"
                env_path.write_text(content, 'utf-8')

    if tunnel_url:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    type="web_app", 
                    text="Плеер", 
                    web_app=WebAppInfo(url=tunnel_url)
                )
            )
            logger.info("Кнопка меню (слева от скрепки) успешно обновлена!")
        except Exception as e:
            logger.error(f"Не удалось обновить кнопку меню: {e}")
    else:
        logger.warning("URL веб-приложения не определён.")

    logger.info("Бот успешно запущен! Нажмите Ctrl+C для остановки.")

    try:
        # 6. Запуск Long Polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if cf_process:
            cf_process.terminate()
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Принудительное завершение работы.")
