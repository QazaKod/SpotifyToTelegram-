import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional
import yt_dlp

import config
from services.spotify import SpotifyTrack

logger = logging.getLogger(__name__)


def _search_youtube(track: SpotifyTrack) -> Optional[str]:
    """Поиск трека на YouTube с проверкой длительности."""
    search_queries = [
        f"ytsearch5:{track.artist_str} - {track.title} Audio",
        f"ytsearch5:{track.artist_str} - {track.title}",
    ]
    ydl_search_opts = {
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

    for q in search_queries:
        try:
            with yt_dlp.YoutubeDL(ydl_search_opts) as ydl:
                search_results = ydl.extract_info(q, download=False)
                entries = search_results.get("entries", []) if search_results else []

                # Фильтрация по длительности
                for entry in entries:
                    yt_dur = entry.get("duration")
                    if yt_dur is None:
                        continue
                    diff = abs(track.duration_sec - yt_dur)
                    if diff <= config.DURATION_TOLERANCE:
                        return entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"

                # Запасной вариант (до 20 сек разницы)
                if entries:
                    first = entries[0]
                    first_dur = first.get("duration", 0)
                    if abs(track.duration_sec - first_dur) <= 20:
                        return first.get("url") or f"https://www.youtube.com/watch?v={first.get('id')}"
        except Exception as e:
            logger.debug(f"[YouTube Search] Ошибка поиска '{q}': {e}")

    return None


def _search_soundcloud(track: SpotifyTrack) -> Optional[str]:
    """Резервный поиск трека на SoundCloud (не требует авторизации и не блокирует дата-центры)."""
    search_queries = [
        f"scsearch5:{track.artist_str} {track.title}",
        f"scsearch5:{track.title} {track.artist_str}",
    ]
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "nocheckcertificate": True,
    }

    for q in search_queries:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(q, download=False)
                entries = res.get("entries", []) if res else []

                for entry in entries:
                    dur = entry.get("duration")
                    if dur is None:
                        continue
                    diff = abs(track.duration_sec - dur)
                    if diff <= config.DURATION_TOLERANCE + 8:
                        return entry.get("url")

                if entries:
                    first = entries[0]
                    first_dur = first.get("duration", 0)
                    if abs(track.duration_sec - first_dur) <= 25:
                        return first.get("url")
        except Exception as e:
            logger.debug(f"[SoundCloud Search] Ошибка поиска '{q}': {e}")

    return None


def _download_stream(url: str, file_id: str, temp_dir: Path) -> Optional[Path]:
    """Скачивание аудиопотока по URL и конвертация в MP3 через FFmpeg."""
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
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"]
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_download_opts) as ydl:
            ydl.download([url])

        mp3_file = temp_dir / f"{file_id}.mp3"
        if mp3_file.exists():
            return mp3_file
    except Exception as e:
        logger.warning(f"Ошибка загрузки потока '{url}': {e}")

    return None


def _sync_download(track: SpotifyTrack, temp_dir: Path) -> Optional[Path]:
    """
    Каскадная загрузка трека:
    1. Пробуем скачать через YouTube
    2. Если YouTube выдает блокировку/ошибку -> автоматически переключаемся на SoundCloud
    """
    file_id = f"{uuid.uuid4().hex[:8]}_{track.id}"

    # 1. Попытка через YouTube
    yt_url = _search_youtube(track)
    if yt_url:
        logger.info(f"Пробуем скачать с YouTube: '{track.artist_str} - {track.title}'")
        mp3 = _download_stream(yt_url, file_id, temp_dir)
        if mp3:
            return mp3
        logger.warning(f"YouTube заблокировал скачивание '{track.artist_str} - {track.title}'. Переключаемся на резервный источник (SoundCloud)...")

    # 2. Резервный источник: SoundCloud
    sc_url = _search_soundcloud(track)
    if sc_url:
        logger.info(f"Скачиваем из SoundCloud: '{track.artist_str} - {track.title}'")
        mp3 = _download_stream(sc_url, file_id, temp_dir)
        if mp3:
            logger.info(f"Успешно скачано из SoundCloud: '{track.artist_str} - {track.title}'!")
            return mp3

    logger.error(f"Не удалось скачать трек ни с YouTube, ни со SoundCloud: '{track.artist_str} - {track.title}'")
    return None


async def download_track(track: SpotifyTrack, temp_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Асинхронный вызов загрузки (выполняется в пуле потоков asyncio.to_thread).
    """
    target_dir = temp_dir or config.TEMP_DIR
    return await asyncio.to_thread(_sync_download, track, target_dir)
