import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Dict, Optional

from aiohttp import web

import hashlib
import secrets
import config
from db.repository import Repository
from services.processor import process_and_send_track
from services.spotify import fetch_spotify_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# In-memory job storage for download progress tracking
download_jobs: Dict[str, dict] = {}


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_playlist(request: web.Request) -> web.Response:
    url = request.query.get("url", "")
    user_id = request.query.get("user_id")
    if not url:
        return web.json_response({"error": "Missing 'url' parameter"}, status=400)

    collection = await fetch_spotify_data(url, user_id=user_id)
    if not collection or not collection.tracks:
        return web.json_response({"error": "Failed to fetch playlist or no tracks found"}, status=404)

    tracks_data = []
    for t in collection.tracks:
        tracks_data.append({
            "id": t.id,
            "title": t.title,
            "artist": t.artist_str,
            "album": t.album,
            "duration_sec": t.duration_sec,
            "cover_url": t.cover_url,
            "preview_url": t.preview_url,
        })

    return web.json_response({
        "type": collection.type,
        "id": collection.id,
        "title": collection.title,
        "cover_url": collection.cover_url,
        "total": len(tracks_data),
        "tracks": tracks_data,
    })


async def handle_search(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"error": "Missing 'q' parameter"}, status=400)
    
    import aiohttp
    
    try:
        url = "https://itunes.apple.com/search"
        params = {
            "term": query,
            "entity": "song",
            "limit": 15
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return web.json_response({"error": "Failed to search iTunes API"}, status=500)
                data = await resp.json()
                
        results = data.get("results", [])
        tracks_data = []
        for item in results:
            track_id = f"itunes_{item.get('trackId')}"
            cover_url = item.get("artworkUrl100", "")
            if cover_url:
                cover_url = cover_url.replace("100x100bb", "600x600bb")
                
            tracks_data.append({
                "id": track_id,
                "title": item.get("trackName", "Unknown"),
                "artist": item.get("artistName", "Unknown"),
                "album": item.get("collectionName", ""),
                "duration_sec": item.get("trackTimeMillis", 0) // 1000,
                "cover_url": cover_url,
                "preview_url": item.get("previewUrl", "")
            })
            
        return web.json_response({
            "type": "search",
            "id": f"search_{uuid.uuid4().hex[:8]}",
            "title": f"Поиск: {query}",
            "cover_url": "",
            "total": len(tracks_data),
            "tracks": tracks_data,
        })
        
    except Exception as e:
        logger.error(f"Search API error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_download(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.json_response({"error": "Bot not available"}, status=500)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = body.get("chat_id")
    tracks_data = body.get("tracks", [])
    playlist_url = body.get("playlist_url", "")

    if not chat_id or not tracks_data:
        return web.json_response({"error": "Missing chat_id or tracks"}, status=400)

    # Create a job
    job_id = uuid.uuid4().hex[:12]
    download_jobs[job_id] = {
        "status": "running",
        "total": len(tracks_data),
        "current": 0,
        "success": 0,
        "current_track": "",
        "done": False,
    }

    # Launch background task
    asyncio.create_task(_run_download_job(job_id, bot, chat_id, tracks_data, playlist_url))

    return web.json_response({"job_id": job_id})


async def _run_download_job(job_id: str, bot, chat_id: int, tracks_data: list, playlist_url: str):
    from services.spotify import SpotifyTrack

    job = download_jobs[job_id]
    queue: asyncio.Queue = asyncio.Queue()
    for td in tracks_data:
        queue.put_nowait(td)

    lock = asyncio.Lock()

    async def worker():
        while not queue.empty():
            if job["status"] == "cancelled":
                break

            try:
                td = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            track = SpotifyTrack(
                id=td["id"],
                title=td["title"],
                artist_str=td["artist"],
                album=td.get("album", ""),
                release_year="",
                duration_sec=td["duration_sec"],
                cover_url=td.get("cover_url", ""),
                preview_url=td.get("preview_url", ""),
            )

            async with lock:
                job["current_track"] = f"{track.artist_str} — {track.title}"

            try:
                ok = await process_and_send_track(bot, chat_id, track)
                if ok:
                    async with lock:
                        job["success"] += 1
            except Exception as e:
                logger.error(f"Error downloading track {track.title}: {e}")
            finally:
                async with lock:
                    job["current"] += 1
                queue.task_done()

            await asyncio.sleep(0.5)

    num_workers = min(config.MAX_CONCURRENT_DOWNLOADS, len(tracks_data)) if tracks_data else 1
    workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
    await asyncio.gather(*workers)

    job["status"] = "completed"
    job["done"] = True


async def handle_status(request: web.Request) -> web.StreamResponse:
    """Server-Sent Events endpoint for real-time download progress."""
    job_id = request.match_info.get("job_id", "")
    job = download_jobs.get(job_id)

    if not job:
        return web.json_response({"error": "Job not found"}, status=404)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    while not job.get("done", False):
        data = json.dumps({
            "status": job["status"],
            "current": job["current"],
            "total": job["total"],
            "success": job["success"],
            "current_track": job["current_track"],
        })
        await response.write(f"data: {data}\n\n".encode())
        await asyncio.sleep(1)

    # Final event
    data = json.dumps({
        "status": "completed",
        "current": job["total"],
        "total": job["total"],
        "success": job["success"],
        "current_track": "",
    })
    await response.write(f"data: {data}\n\n".encode())

    # Cleanup
    download_jobs.pop(job_id, None)
    return response


async def handle_cancel(request: web.Request) -> web.Response:
    job_id = request.match_info.get("job_id", "")
    job = download_jobs.get(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)

    job["status"] = "cancelled"
    job["done"] = True
    return web.json_response({"status": "cancelled"})

admin_sessions = {}

def verify_admin(request):
    token = request.cookies.get('admin_token')
    if not token or token not in admin_sessions:
        return False
    import time
    if admin_sessions[token] < time.time():
        del admin_sessions[token]
        return False
    return True

async def handle_admin_login_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "admin" / "login.html")

async def handle_admin_login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    password = body.get("password")
    if password == config.ADMIN_PASSWORD:
        import time
        token = secrets.token_urlsafe(32)
        admin_sessions[token] = time.time() + 86400  # 24h
        resp = web.json_response({"success": True})
        resp.set_cookie('admin_token', token, max_age=86400, httponly=True)
        return resp
    else:
        return web.json_response({"error": "Invalid password"}, status=401)

async def handle_admin_dashboard(request: web.Request) -> web.Response:
    if not verify_admin(request):
        raise web.HTTPFound('/admin/')
    return web.FileResponse(STATIC_DIR / "admin" / "dashboard.html")

async def handle_admin_api_summary(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_stats_summary())

async def handle_admin_api_downloads_by_day(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_downloads_by_day())

async def handle_admin_api_downloads_by_hour(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_downloads_by_hour())

async def handle_admin_api_top_tracks(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_top_tracks())

async def handle_admin_api_top_artists(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_top_artists())

async def handle_admin_api_top_genres(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_top_genres())

async def handle_admin_api_sources(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_source_distribution())

async def handle_admin_api_recent_users(request: web.Request) -> web.Response:
    if not verify_admin(request): return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(await Repository.get_recent_users())


# ==========================================
# LIBRARY & EXPLORE ENDPOINTS (Component 2)
# ==========================================
from services.delivery import DeliveryManager
from services.job_manager import JobManager
from services.spotify import SpotifyCollection, SpotifyTrack

async def _get_user_db_id(user_id: str) -> Optional[int]:
    if not user_id:
        return None
    try:
        user = await Repository.get_or_create_user(telegram_user_id=int(user_id))
        return user.id
    except Exception as e:
        logger.error(f"Error getting user_db_id: {e}")
        return None

async def handle_library(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    
    playlists = await Repository.get_user_playlists(user_db_id)
    return web.json_response(playlists)

async def handle_create_playlist(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    
    data = await request.json()
    name = data.get("name")
    emoji = data.get("emoji", "🎵")
    if not name:
        return web.json_response({"error": "Missing name"}, status=400)
    
    playlist = await Repository.create_playlist(user_db_id, name, emoji)
    return web.json_response({"id": playlist.id, "name": playlist.name, "emoji": playlist.emoji})

async def handle_delete_playlist(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    playlist_id = int(request.match_info["id"])
    success = await Repository.delete_playlist(playlist_id, user_db_id)
    return web.json_response({"success": success})

async def handle_rename_playlist(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    playlist_id = int(request.match_info["id"])
    data = await request.json()
    name = data.get("name")
    emoji = data.get("emoji")
    if not name:
        return web.json_response({"error": "Missing name"}, status=400)
        
    success = await Repository.rename_playlist(playlist_id, user_db_id, name, emoji)
    return web.json_response({"success": success})

async def handle_playlist_tracks(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    playlist_id = int(request.match_info["id"])
    tracks = await Repository.get_playlist_tracks(playlist_id, user_db_id)
    return web.json_response(tracks)

async def handle_add_track(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    data = await request.json()
    playlist_id = data.get("playlist_id")
    track_data = data.get("track")
    if not playlist_id or not track_data:
        return web.json_response({"error": "Missing playlist_id or track data"}, status=400)
        
    # Get or create TrackInfo
    track_info = await Repository.get_or_create_track_info(
        source_id=track_data["id"],
        title=track_data["title"],
        artist=track_data.get("artist", ""),
        album=track_data.get("album", ""),
        duration_sec=track_data.get("duration_sec", 0),
        cover_url=track_data.get("cover_url", ""),
        preview_url=track_data.get("preview_url", ""),
        genre=track_data.get("genre", "")
    )
    
    # Actually if they add to "Favorites", they might not pass playlist_id? 
    # Let's assume frontend gets the favorites playlist ID or uses a specific endpoint.
    # The plan says: `POST /api/library/track` -> `{playlist_id, track}`
    success = await Repository.add_track_to_playlist(playlist_id, track_info.id)
    return web.json_response({"success": success})

async def handle_remove_track(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    data = await request.json()
    playlist_id = data.get("playlist_id")
    track_info_id = data.get("track_info_id")
    if not playlist_id or not track_info_id:
        return web.json_response({"error": "Missing playlist_id or track_info_id"}, status=400)
        
    success = await Repository.remove_track_from_playlist(playlist_id, track_info_id)
    return web.json_response({"success": success})

async def handle_send_playlist(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.json_response({"error": "Bot not available"}, status=500)
        
    user_id = request.query.get("user_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
        
    data = await request.json()
    playlist_id = data.get("playlist_id")
    chat_id = data.get("chat_id")
    if not playlist_id or not chat_id:
        return web.json_response({"error": "Missing playlist_id or chat_id"}, status=400)
        
    # Fetch tracks from DB
    db_tracks = await Repository.get_playlist_tracks(playlist_id, user_db_id)
    if not db_tracks:
        return web.json_response({"error": "Playlist empty or not found"}, status=404)
        
    # Get playlist metadata (need name, emoji)
    playlists = await Repository.get_user_playlists(user_db_id)
    pl = next((p for p in playlists if p['id'] == playlist_id), None)
    if not pl:
        return web.json_response({"error": "Playlist not found"}, status=404)
        
    # Create SpotifyCollection
    tracks = []
    for t in db_tracks:
        tracks.append(SpotifyTrack(
            id=t["source_id"],
            title=t["title"],
            artist_str=t["artist"],
            album=t["album"],
            release_year="",
            duration_sec=t["duration_sec"],
            cover_url=t["cover_url"]
        ))
        
    collection = SpotifyCollection(
        type="playlist",
        id=f"user_pl_{playlist_id}",
        title=f"{pl.get('emoji', '🎵')} {pl.get('name', 'Плейлист')}",
        cover_url=pl.get("cover_url", ""),
        tracks=tracks
    )
    
    # 1. Send Header
    header_msg_id = await DeliveryManager.send_header(bot, chat_id, collection)
    
    # 2. Start JobManager task
    await JobManager.start_job(
        chat_id=chat_id,
        collection=collection,
        status_msg_id=header_msg_id, # Can reuse header as status optionally, but we usually use a separate msg
        bot=bot,
        user_id=user_id,
        header_msg_id=header_msg_id,
        use_media_groups=True
    )
    
    return web.json_response({"success": True})

async def handle_check_favorite(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id")
    source_id = request.query.get("source_id")
    user_db_id = await _get_user_db_id(user_id)
    if not user_db_id or not source_id:
        return web.json_response({"error": "Missing params"}, status=400)
        
    is_fav = await Repository.is_track_in_favorites(user_db_id, source_id)
    return web.json_response({"is_favorite": is_fav})

async def handle_explore_genres(request: web.Request) -> web.Response:
    genres = [
      {"slug": "pop",       "name": "Поп",       "color": "#E13300"},
      {"slug": "hip-hop",   "name": "Хип-хоп",   "color": "#BA5D07"},
      {"slug": "rock",      "name": "Рок",       "color": "#608108"},
      {"slug": "rnb",       "name": "R&B",       "color": "#477D95"},
      {"slug": "electronic","name": "Электроника","color": "#0D73EC"},
      {"slug": "jazz",      "name": "Джаз",      "color": "#777777"},
      {"slug": "classical", "name": "Классика",  "color": "#7358FF"},
      {"slug": "phonk",     "name": "Phonk",     "color": "#1E3264"},
      {"slug": "metal",     "name": "Метал",     "color": "#503750"},
      {"slug": "indie",     "name": "Инди",      "color": "#148A08"},
      {"slug": "latin",     "name": "Латино",    "color": "#E8115B"},
      {"slug": "reggaeton", "name": "Реггетон",  "color": "#D84000"}
    ]
    return web.json_response(genres)

async def handle_explore_genre(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    genre_map = {
        "pop": 14, "hip-hop": 18, "rock": 21, "rnb": 15, "electronic": 7, 
        "jazz": 11, "classical": 5, "phonk": 18, "metal": 21, "indie": 20, 
        "latin": 12, "reggaeton": 12
    }
    genre_id = genre_map.get(slug)
    if not genre_id:
        return web.json_response({"error": "Genre not found"}, status=404)
        
    import aiohttp
    url = f"https://itunes.apple.com/search?term={slug}&genreId={genre_id}&entity=song&limit=30"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return web.json_response({"error": "Failed to search iTunes API"}, status=500)
                data = await resp.json()
                
        results = data.get("results", [])
        tracks_data = []
        for item in results:
            track_id = f"itunes_{item.get('trackId')}"
            cover_url = item.get("artworkUrl100", "")
            if cover_url:
                cover_url = cover_url.replace("100x100bb", "600x600bb")
                
            tracks_data.append({
                "id": track_id,
                "title": item.get("trackName", "Unknown"),
                "artist": item.get("artistName", "Unknown"),
                "album": item.get("collectionName", ""),
                "duration_sec": item.get("trackTimeMillis", 0) // 1000,
                "cover_url": cover_url,
                "preview_url": item.get("previewUrl", "")
            })
            
        return web.json_response({
            "type": "genre",
            "id": f"genre_{slug}",
            "title": f"Жанр: {slug}",
            "cover_url": "",
            "total": len(tracks_data),
            "tracks": tracks_data,
        })
    except Exception as e:
        logger.error(f"Explore API error: {e}")
        return web.json_response({"error": str(e)}, status=500)

def create_webapp(bot=None) -> web.Application:

    app = web.Application()
    if bot:
        app["bot"] = bot

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/playlist", handle_playlist)
    app.router.add_get("/api/search", handle_search)
    app.router.add_post("/api/download", handle_download)
    app.router.add_get("/api/status/{job_id}", handle_status)
    app.router.add_post("/api/cancel/{job_id}", handle_cancel)
    app.router.add_get("/admin/", handle_admin_login_page)
    app.router.add_post("/admin/login", handle_admin_login)
    app.router.add_get("/admin/dashboard", handle_admin_dashboard)
    app.router.add_get("/admin/api/summary", handle_admin_api_summary)
    app.router.add_get("/admin/api/downloads-by-day", handle_admin_api_downloads_by_day)
    app.router.add_get("/admin/api/downloads-by-hour", handle_admin_api_downloads_by_hour)
    app.router.add_get("/admin/api/top-tracks", handle_admin_api_top_tracks)
    app.router.add_get("/admin/api/top-artists", handle_admin_api_top_artists)
    app.router.add_get("/admin/api/top-genres", handle_admin_api_top_genres)
    app.router.add_get("/admin/api/sources", handle_admin_api_sources)
    app.router.add_get("/admin/api/recent-users", handle_admin_api_recent_users)
    app.router.add_static("/static/", STATIC_DIR, name="static")

    # Library
    app.router.add_get("/api/library", handle_library)
    app.router.add_post("/api/library/playlist", handle_create_playlist)
    app.router.add_delete("/api/library/playlist/{id}", handle_delete_playlist)
    app.router.add_patch("/api/library/playlist/{id}", handle_rename_playlist)
    app.router.add_get("/api/library/playlist/{id}/tracks", handle_playlist_tracks)
    app.router.add_post("/api/library/track", handle_add_track)
    app.router.add_delete("/api/library/track", handle_remove_track)
    app.router.add_post("/api/library/send", handle_send_playlist)
    app.router.add_get("/api/library/favorites/check", handle_check_favorite)
    # Explore
    app.router.add_get("/api/explore/genres", handle_explore_genres)
    app.router.add_get("/api/explore/genre/{slug}", handle_explore_genre)

    return app

