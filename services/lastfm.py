import logging
from typing import Optional
import aiohttp
import config
from db.repository import Repository

logger = logging.getLogger(__name__)

async def fetch_artist_genre(artist_name: str) -> Optional[str]:
    """
    Определяет жанр артиста через Last.fm API.
    Кэширует результат в базе данных.
    """
    if not config.LASTFM_API_KEY:
        return None
    
    # Check cache first
    cached = await Repository.get_cached_genre(artist_name)
    if cached is not None:
        return cached
    
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getTopTags",
            "artist": artist_name,
            "api_key": config.LASTFM_API_KEY,
            "format": "json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=5) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        
        toptags = data.get("toptags", {}).get("tag", [])
        if not toptags:
            return None
        
        # Take top 3 tags
        genres = ", ".join(tag["name"].lower() for tag in toptags[:3] if tag.get("name"))
        
        if genres:
            await Repository.cache_artist_genre(artist_name, genres)
        
        return genres
    except Exception as e:
        logger.debug(f"Ошибка при запросе жанра '{artist_name}' из Last.fm: {e}")
        return None
