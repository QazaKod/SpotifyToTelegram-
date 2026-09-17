import logging
import os
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile

from db.repository import Repository
from services.downloader import download_track
from services.queue_manager import download_semaphore
from services.spotify import SpotifyTrack
from services.tagger import tag_mp3

logger = logging.getLogger(__name__)


async def process_and_send_track(
    bot: Bot,
    chat_id: int,
    track: SpotifyTrack,
    playlist_db_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> bool:
    """
    Полный сквозной пайплайн обработки одного трека:
    1. Проверка кэша (если трек уже есть в Telegram - шлем мгновенно по file_id)
    2. Скачивание с YouTube через yt-dlp под семафором
    3. Вшивание обложки и ID3-тегов через mutagen
    4. Отправка в Telegram через bot.send_audio
    5. Сохранение telegram_file_id в базу данных
    6. Гарантированное удаление временных файлов (try...finally)
    """
    user_db_id = None
    if user_id:
        try:
            user = await Repository.get_or_create_user(telegram_user_id=user_id)
            user_db_id = user.id
        except Exception as e:
            logger.warning(f"Ошибка при регистрации пользователя: {e}")

    if track.title == "LazyTrack" and track.artist_str == "LazyArtist" and track.id.startswith("http"):
        from services.spotify import fetch_spotify_data
        real_collection = await fetch_spotify_data(track.id)
        if real_collection and real_collection.tracks:
            track = real_collection.tracks[0]
        else:
            logger.error(f"Не удалось получить данные о треке по ссылке: {track.id}")
            return False

    # 1. Проверяем кэш базы данных
    cached_file_id = await Repository.get_cached_file_id(track.id)
    if cached_file_id:
        try:
            logger.info(f"Трек '{track.artist_str} - {track.title}' найден в кэше! Отправка по file_id...")
            msg = await bot.send_audio(
                chat_id=chat_id,
                audio=cached_file_id,
                title=track.title,
                performer=track.artist_str,
                duration=track.duration_sec,
            )
            if playlist_db_id:
                await Repository.save_sent_track(track.id, cached_file_id, playlist_id=playlist_db_id)
            try:
                await Repository.log_download(user_db_id, track.id, track.title, track.artist_str, track.duration_sec, "cache", True)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.warning(f"Не удалось отправить трек из кэша (возможно устарел file_id): {e}. Скачиваем заново.")

    # 2. Скачивание под семафором (не более N параллельных загрузок)
    mp3_path: Optional[Path] = None
    cover_path: Optional[Path] = None
    source: str = "unknown"

    try:
        async with download_semaphore:
            logger.info(f"Начало загрузки: {track.artist_str} - {track.title}")
            result = await download_track(track)
            if not result:
                logger.error(f"Не удалось скачать трек: {track.artist_str} - {track.title}")
                try:
                    await Repository.log_download(user_db_id, track.id, track.title, track.artist_str, track.duration_sec, "unknown", False)
                except Exception:
                    pass
                return False
            mp3_path, source = result

            # 3. Вшивание тегов и скачивание обложки
            cover_path = await tag_mp3(mp3_path, track)

        # 4. Отправка в Telegram
        audio_file = FSInputFile(path=mp3_path, filename=f"{track.artist_str} - {track.title}.mp3")
        thumb_file = FSInputFile(path=cover_path) if cover_path and cover_path.exists() else None

        msg = await bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=track.title,
            performer=track.artist_str,
            duration=track.duration_sec,
            thumbnail=thumb_file,
        )

        # 5. Сохраняем file_id в БД для кэширования
        if msg.audio:
            await Repository.save_sent_track(
                spotify_track_id=track.id,
                file_id=msg.audio.file_id,
                playlist_id=playlist_db_id,
            )

        logger.info(f"Трек успешно отправлен: {track.artist_str} - {track.title}")
        try:
            await Repository.log_download(user_db_id, track.id, track.title, track.artist_str, track.duration_sec, source, True)
        except Exception:
            pass

        try:
            from services.lastfm import fetch_artist_genre
            import asyncio
            asyncio.create_task(fetch_artist_genre(track.artist_str.split(',')[0].strip()))
        except Exception:
            pass

        return True

    except Exception as e:
        logger.exception(f"Ошибка при обработке и отправке трека '{track.artist_str} - {track.title}': {e}")
        try:
            await Repository.log_download(user_db_id, track.id, track.title, track.artist_str, track.duration_sec, "unknown", False)
        except Exception:
            pass
        return False
    finally:
        # 6. Гарантированная очистка временных файлов
        if mp3_path and os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass
        if cover_path and os.path.exists(cover_path):
            try:
                os.remove(cover_path)
            except OSError:
                pass
