import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from db.repository import Repository
from services.processor import process_and_send_track
from services.spotify import fetch_spotify_data

logger = logging.getLogger(__name__)


async def check_playlist_updates(bot: Bot):
    """
    Периодическая задача: опрашивает привязанные плейлисты
    и отправляет только новые появившиеся треки.
    """
    logger.info("Запуск плановой проверки обновлений плейлистов...")
    try:
        active_playlists = await Repository.get_active_sync_playlists()
        if not active_playlists:
            return

        for sync_item in active_playlists:
            url = f"https://open.spotify.com/playlist/{sync_item.spotify_playlist_id}"
            collection = await fetch_spotify_data(url)
            if not collection or not collection.tracks:
                continue

            # Получаем telegram_chat_id для этого плейлиста
            chat = sync_item.chat
            if not chat:
                continue

            for track in collection.tracks:
                already_sent = await Repository.is_track_sent_to_playlist(sync_item.id, track.id)
                if not already_sent:
                    logger.info(f"Обнаружен новый трек для чата {chat.telegram_chat_id}: {track.artist_str} - {track.title}")
                    ok = await process_and_send_track(
                        bot=bot,
                        chat_id=chat.telegram_chat_id,
                        track=track,
                        playlist_db_id=sync_item.id,
                    )
                    if ok:
                        await asyncio.sleep(1)

            await Repository.update_sync_time(sync_item.id)

    except Exception as e:
        logger.exception(f"Ошибка при выполнении фоновой синхронизации: {e}")


def start_scheduler(bot: Bot, interval_minutes: int = 15) -> AsyncIOScheduler:
    """
    Запускает фоновый планировщик задач APScheduler.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_playlist_updates,
        trigger="interval",
        minutes=interval_minutes,
        args=[bot],
        id="sync_spotify_playlists",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"APScheduler запущен (интервал проверки: {interval_minutes} минут).")
    return scheduler
