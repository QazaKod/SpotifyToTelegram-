import asyncio
import logging
from pathlib import Path
from typing import Optional
import aiohttp
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1
from mutagen.mp3 import MP3

from services.spotify import SpotifyTrack

logger = logging.getLogger(__name__)


async def download_cover_image(cover_url: str, target_path: Path) -> bool:
    """
    Скачивает обложку трека со Spotify CDN и сохраняет в target_path.
    """
    if not cover_url:
        return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(cover_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    target_path.write_bytes(data)
                    return True
                else:
                    logger.warning(f"Не удалось скачать обложку: статус {resp.status}")
    except Exception as e:
        logger.error(f"Ошибка загрузки обложки: {e}")

    return False


def _sync_embed_tags(mp3_path: Path, track: SpotifyTrack, cover_path: Optional[Path]) -> None:
    """
    Синхронно вшивает ID3v2-теги и обложку внутрь MP3-файла.
    """
    try:
        audio = MP3(str(mp3_path))
        if audio.tags is None:
            audio.add_tags()

        # Название трека
        audio.tags["TIT2"] = TIT2(encoding=3, text=track.title)
        # Исполнители
        audio.tags["TPE1"] = TPE1(encoding=3, text=track.artist_str)
        # Альбом
        if track.album:
            audio.tags["TALB"] = TALB(encoding=3, text=track.album)
        # Год релиза
        if track.release_year:
            audio.tags["TDRC"] = TDRC(encoding=3, text=track.release_year)

        # Вшивание обложки в APIC
        if cover_path and cover_path.exists():
            image_data = cover_path.read_bytes()
            audio.tags["APIC"] = APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,  # Cover Front
                desc="Cover",
                data=image_data,
            )

        audio.save(v2_version=3)
        logger.info(f"Теги успешно вшиты в {mp3_path.name}")
    except Exception as e:
        logger.error(f"Ошибка при вшивании тегов в {mp3_path.name}: {e}")


async def tag_mp3(mp3_path: Path, track: SpotifyTrack) -> Optional[Path]:
    """
    Скачивает обложку, вшивает теги в MP3 и возвращает путь к скачанной обложке (для thumb в Telegram).
    """
    cover_path = None
    if track.cover_url:
        cover_path = mp3_path.with_suffix(".jpg")
        success = await download_cover_image(track.cover_url, cover_path)
        if not success:
            cover_path = None

    await asyncio.to_thread(_sync_embed_tags, mp3_path, track, cover_path)
    return cover_path
