import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional
import yt_dlp

import config
from services.spotify import SpotifyTrack

logger = logging.getLogger(__name__)


def _sync_download(track: SpotifyTrack, temp_dir: Path) -> Optional[Path]:
    """
    Синхронная функция поиска и скачивания трека через yt-dlp.
    Вызывается в отдельном потоке (через asyncio.to_thread).
    """
    ydl_base_search_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "nocheckcertificate": True,
        "extractor_retries": 1,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"]
            }
        },
    }

    file_id = f"{uuid.uuid4().hex[:8]}_{track.id}"
    out_tmpl = str(temp_dir / f"{file_id}.%(ext)s")

    ydl_base_download_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": getattr(config, "AUDIO_BITRATE", "256"),
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "concurrent_fragment_downloads": 4,
        "buffersize": 1024 * 64,
        "http_chunk_size": 10485760,
        "retries": 3,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"]
            }
        },
    }

    sources = [
        {"name": "soundcloud", "prefix": "scsearch5:"},
        {"name": "youtube", "prefix": "ytsearch5:"}
    ]

    for source in sources:
        logger.info(f"[{source['name']}] Поиск трека '{track.artist_str} - {track.title}'")
        search_query = f"{source['prefix']}{track.artist_str} - {track.title}"
        if source["name"] == "youtube":
            search_query += " Audio"

        selected_video_url = None

        try:
            with yt_dlp.YoutubeDL(ydl_base_search_opts) as ydl:
                search_results = ydl.extract_info(search_query, download=False)
                entries = search_results.get("entries", []) if search_results else []

                # Если для ютуба с "Audio" не нашли, ищем без него
                if not entries and source["name"] == "youtube":
                    fallback_query = f"{source['prefix']}{track.artist_str} - {track.title}"
                    search_results = ydl.extract_info(fallback_query, download=False)
                    entries = search_results.get("entries", []) if search_results else []

                # Фильтрация по хронометражу
                for entry in entries:
                    yt_dur = entry.get("duration")
                    if yt_dur is None:
                        continue

                    diff = abs(track.duration_sec - yt_dur)
                    if diff <= config.DURATION_TOLERANCE:
                        url_val = entry.get("url") or entry.get("webpage_url")
                        if not url_val and source["name"] == "youtube" and entry.get("id"):
                            url_val = f"https://www.youtube.com/watch?v={entry.get('id')}"
                        selected_video_url = url_val
                        logger.info(
                            f"[{source['name']}] Найден подходящий трек: '{entry.get('title')}' (длительность {yt_dur}с, Spotify: {track.duration_sec}с, разница {diff}с)"
                        )
                        break

                # Если строгое совпадение не найдено, берем первый результат с запасом до 20с
                if not selected_video_url and entries:
                    first = entries[0]
                    first_dur = first.get("duration", 0)
                    diff = abs(track.duration_sec - first_dur)
                    if diff <= 20:
                        url_val = first.get("url") or first.get("webpage_url")
                        if not url_val and source["name"] == "youtube" and first.get("id"):
                            url_val = f"https://www.youtube.com/watch?v={first.get('id')}"
                        selected_video_url = url_val
                        logger.info(f"[{source['name']}] Используем запасной вариант с отклонением {diff}с: '{first.get('title')}'")

        except Exception as e:
            logger.warning(f"[{source['name']}] Ошибка при поиске трека '{track.artist_str} - {track.title}': {e}")
            continue

        if not selected_video_url:
            logger.warning(f"[{source['name']}] Не удалось найти аудио подходящей длительности.")
            continue

        # Скачивание
        try:
            with yt_dlp.YoutubeDL(ydl_base_download_opts) as ydl:
                ydl.download([selected_video_url])

            mp3_file = temp_dir / f"{file_id}.mp3"
            if mp3_file.exists():
                logger.info(f"[{source['name']}] Трек успешно скачан: {mp3_file.name}")
                return mp3_file
            else:
                logger.error(f"[{source['name']}] Файл {mp3_file} не найден после конвертации")
        except Exception as e:
            logger.warning(f"[{source['name']}] Ошибка скачивания '{selected_video_url}': {e}")
            continue

    logger.error(f"Не удалось скачать трек '{track.artist_str} - {track.title}' ни с одного из источников.")
    return None


async def download_track(track: SpotifyTrack, temp_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Асинхронный вызов загрузки (выполняется в пуле потоков asyncio.to_thread).
    """
    target_dir = temp_dir or config.TEMP_DIR
    return await asyncio.to_thread(_sync_download, track, target_dir)
