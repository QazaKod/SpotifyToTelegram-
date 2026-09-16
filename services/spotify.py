import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional
import aiohttp

import config

logger = logging.getLogger(__name__)


@dataclass
class SpotifyTrack:
    id: str
    title: str
    artist_str: str
    album: str
    release_year: str
    duration_sec: int
    cover_url: str
    preview_url: str = ""


@dataclass
class SpotifyCollection:
    type: str  # 'playlist', 'album', 'track'
    id: str
    title: str
    cover_url: str
    tracks: List[SpotifyTrack]


def parse_spotify_url(url: str) -> Optional[tuple[str, str]]:
    """
    Извлекает тип сущности (playlist, album, track) и ID из ссылки Spotify.
    Поддерживает ссылки вида:
    - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...
    - https://open.spotify.com/track/...
    - https://open.spotify.com/album/...
    - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
    """
    match = re.search(r"(playlist|album|track)[/:]([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


def is_personalized_mix(entity_type: str, entity_id: str) -> bool:
    """
    Проверяет, является ли ссылка алгоритмическим персональным миксом Spotify
    (Daily Mix, Hip-Hop Mix и т.д., начинающимся с 37i9dQZF1E...).
    Такие подборки динамически формируются Spotify под каждого слушателя отдельно.
    """
    return entity_type == "playlist" and entity_id.startswith("37i9dQZF1E")


def should_use_sp_dc(user_id: Optional[int] = None) -> bool:
    """
    Проверяет, разрешено ли использовать приватный куки SPOTIFY_SP_DC владельца бота.
    Куки используется ТОЛЬКО если запрос исходит лично от владельца бота (OWNER_TELEGRAM_ID).
    Для всех остальных пользователей запросы строго анонимные!
    """
    if not config.SPOTIFY_SP_DC:
        return False
    if config.OWNER_TELEGRAM_ID and user_id is not None:
        return str(user_id) == str(config.OWNER_TELEGRAM_ID)
    return False


async def fetch_spotify_data(url: str, user_id: Optional[int] = None) -> Optional[SpotifyCollection]:
    """
    Получает метаданные о плейлисте, альбоме или треке без необходимости иметь
    платный Spotify Premium аккаунт разработчика (через Embed Data).
    """
    parsed = parse_spotify_url(url)
    if not parsed:
        logger.warning(f"Не удалось распознать ссылку Spotify: {url}")
        return None

    entity_type, entity_id = parsed
    embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if should_use_sp_dc(user_id):
        headers["Cookie"] = f"sp_dc={config.SPOTIFY_SP_DC}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(embed_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка запроса к Spotify Embed ({resp.status}): {embed_url}")
                    return None
                html = await resp.text()

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
        if not match:
            logger.error("Не найден тег __NEXT_DATA__ на странице Spotify Embed")
            return None

        data = json.loads(match.group(1))
        entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        if not entity:
            logger.error("Пустой объект entity в ответе Spotify Embed")
            return None

        # Определение лучшей обложки (наибольшего разрешения)
        cover_url = ""
        visual_images = entity.get("visualIdentity", {}).get("image", [])
        if visual_images:
            cover_url = visual_images[-1].get("url", "")
        if not cover_url:
            sources = entity.get("coverArt", {}).get("sources", [])
            if sources:
                cover_url = sources[-1].get("url", "")

        collection_title = entity.get("name") or entity.get("title") or "Unknown"
        release_date = str(entity.get("releaseDate", ""))
        release_year = release_date.split("-")[0] if release_date else ""

        tracks: List[SpotifyTrack] = []

        if entity_type in ("playlist", "album"):
            track_list = entity.get("trackList", [])
            for item in track_list:
                t_uri = item.get("uri", "")
                t_id = t_uri.split(":")[-1] if t_uri else item.get("uid", "")
                t_title = item.get("title", "Unknown Title")
                # subtitle обычно содержит список артистов через запятую
                t_artist = item.get("subtitle", "Unknown Artist").replace("\xa0", " ")
                t_duration = int(item.get("duration", 0) / 1000)
                t_preview = ""
                audio_preview = item.get("audioPreview")
                if audio_preview and isinstance(audio_preview, dict):
                    t_preview = audio_preview.get("url", "")

                tracks.append(
                    SpotifyTrack(
                        id=t_id,
                        title=t_title,
                        artist_str=t_artist,
                        album=collection_title,
                        release_year=release_year,
                        duration_sec=t_duration,
                        cover_url=cover_url,
                        preview_url=t_preview,
                    )
                )
        elif entity_type == "track":
            t_id = entity_id
            t_title = collection_title
            artists_list = entity.get("artists", [])
            if artists_list:
                t_artist = ", ".join(a.get("name", "") for a in artists_list if a.get("name"))
            else:
                t_artist = entity.get("subtitle", "Unknown Artist")
            t_duration = int(entity.get("duration", 0) / 1000)

            tracks.append(
                SpotifyTrack(
                    id=t_id,
                    title=t_title,
                    artist_str=t_artist,
                    album=t_title,
                    release_year=release_year,
                    duration_sec=t_duration,
                    cover_url=cover_url,
                )
            )

        return SpotifyCollection(
            type=entity_type,
            id=entity_id,
            title=collection_title,
            cover_url=cover_url,
            tracks=tracks,
        )

    except Exception as e:
        logger.exception(f"Исключение при получении данных Spotify: {e}")
        return None
