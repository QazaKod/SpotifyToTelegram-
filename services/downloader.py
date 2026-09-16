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
    # 1. Поиск: сначала с суффиксом "Audio", берем до 5 результатов
    search_query = f"ytsearch5:{track.artist_str} - {track.title} Audio"

    ydl_search_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "nocheckcertificate": True,
        "extractor_retries": 1,
    }

    selected_video_url = None

    try:
        with yt_dlp.YoutubeDL(ydl_search_opts) as ydl:
            search_results = ydl.extract_info(search_query, download=False)
            entries = search_results.get("entries", []) if search_results else []

            # Если ничего не нашли с суффиксом "Audio", ищем просто Artist - Title
            if not entries:
                fallback_query = f"ytsearch5:{track.artist_str} - {track.title}"
                search_results = ydl.extract_info(fallback_query, download=False)
                entries = search_results.get("entries", []) if search_results else []

            # 2. Фильтрация по хронометражу
            for entry in entries:
                yt_dur = entry.get("duration")
                if yt_dur is None:
                    continue

                diff = abs(track.duration_sec - yt_dur)
                if diff <= config.DURATION_TOLERANCE:
                    selected_video_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    logger.info(
                        f"Найден подходящий трек: '{entry.get('title')}' (длительность {yt_dur}с, Spotify: {track.duration_sec}с, разница {diff}с)"
                    )
                    break

            # Если строгое совпадение не найдено, но есть хотя бы первый результат
            if not selected_video_url and entries:
                first = entries[0]
                first_dur = first.get("duration", 0)
                diff = abs(track.duration_sec - first_dur)
                # Разрешаем отклонение до 20 сек как запасной вариант
                if diff <= 20:
                    selected_video_url = first.get("url") or f"https://www.youtube.com/watch?v={first.get('id')}"
                    logger.info(f"Используем запасной вариант с отклонением {diff}с: '{first.get('title')}'")

    except Exception as e:
        logger.error(f"Ошибка при поиске трека '{track.artist_str} - {track.title}': {e}")
        return None

    if not selected_video_url:
        logger.warning(f"Не удалось найти аудио подходящей длительности для '{track.artist_str} - {track.title}'")
        return None

    # 3. Скачивание аудиопотока и конвертация в MP3 через FFmpeg
    file_id = f"{uuid.uuid4().hex[:8]}_{track.id}"
    out_tmpl = str(temp_dir / f"{file_id}.%(ext)s")

    ydl_download_opts = {
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
    }

    try:
        with yt_dlp.YoutubeDL(ydl_download_opts) as ydl:
            ydl.download([selected_video_url])

        mp3_file = temp_dir / f"{file_id}.mp3"
        if mp3_file.exists():
            return mp3_file
        else:
            logger.error(f"Файл {mp3_file} не найден после конвертации yt-dlp")
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания '{selected_video_url}': {e}")
        return None


async def download_track(track: SpotifyTrack, temp_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Асинхронный вызов загрузки (выполняется в пуле потоков asyncio.to_thread).
    """
    target_dir = temp_dir or config.TEMP_DIR
    return await asyncio.to_thread(_sync_download, track, target_dir)
