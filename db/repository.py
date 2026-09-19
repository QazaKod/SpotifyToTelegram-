from collections import Counter
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import selectinload

from db.database import async_session
from db.models import Chat, SentTrack, SyncPlaylist, User, DownloadLog, ArtistGenre, TrackInfo, UserPlaylist, UserPlaylistTrack


class Repository:
    # --- TrackInfo Management ---

    @staticmethod
    async def get_or_create_track_info(
        source_id: str, title: str, artist: str,
        album: str = "", duration_sec: int = 0,
        cover_url: str = "", preview_url: str = "", genre: str = ""
    ) -> TrackInfo:
        async with async_session() as session:
            stmt = select(TrackInfo).where(TrackInfo.source_id == source_id)
            result = await session.execute(stmt)
            track = result.scalars().first()
            if not track:
                track = TrackInfo(
                    source_id=source_id, title=title, artist=artist,
                    album=album, duration_sec=duration_sec,
                    cover_url=cover_url, preview_url=preview_url, genre=genre
                )
                session.add(track)
                await session.commit()
                await session.refresh(track)
            return track

    # --- UserPlaylist Management ---

    @staticmethod
    async def get_or_create_favorites(user_db_id: int) -> UserPlaylist:
        async with async_session() as session:
            stmt = select(UserPlaylist).where(UserPlaylist.user_id == user_db_id, UserPlaylist.is_favorites == True)
            result = await session.execute(stmt)
            playlist = result.scalars().first()
            if not playlist:
                playlist = UserPlaylist(user_id=user_db_id, name="Любимые треки", emoji="❤️", is_favorites=True)
                session.add(playlist)
                await session.commit()
                await session.refresh(playlist)
            return playlist

    @staticmethod
    async def create_playlist(user_db_id: int, name: str, emoji: str = "🎵") -> UserPlaylist:
        async with async_session() as session:
            playlist = UserPlaylist(user_id=user_db_id, name=name, emoji=emoji, is_favorites=False)
            session.add(playlist)
            await session.commit()
            await session.refresh(playlist)
            return playlist

    @staticmethod
    async def get_user_playlists(user_db_id: int) -> list[dict]:
        async with async_session() as session:
            stmt = select(UserPlaylist).where(UserPlaylist.user_id == user_db_id).order_by(UserPlaylist.is_favorites.desc(), UserPlaylist.created_at.desc())
            result = await session.execute(stmt)
            playlists = result.scalars().all()
            
            out = []
            for p in playlists:
                # Get track count
                count_stmt = select(func.count(UserPlaylistTrack.id)).where(UserPlaylistTrack.playlist_id == p.id)
                count_res = await session.execute(count_stmt)
                track_count = count_res.scalar() or 0
                
                # Get cover url from first track if any
                cover_url = ""
                if track_count > 0:
                    cover_stmt = select(TrackInfo.cover_url).join(UserPlaylistTrack).where(
                        UserPlaylistTrack.playlist_id == p.id
                    ).order_by(UserPlaylistTrack.position.asc()).limit(1)
                    cover_res = await session.execute(cover_stmt)
                    cover_url = cover_res.scalar() or ""
                
                out.append({
                    "id": p.id,
                    "name": p.name,
                    "emoji": p.emoji,
                    "is_favorites": p.is_favorites,
                    "track_count": track_count,
                    "cover_url": cover_url
                })
            return out

    @staticmethod
    async def delete_playlist(playlist_id: int, user_db_id: int) -> bool:
        async with async_session() as session:
            stmt = select(UserPlaylist).where(UserPlaylist.id == playlist_id, UserPlaylist.user_id == user_db_id)
            result = await session.execute(stmt)
            playlist = result.scalars().first()
            if playlist and not playlist.is_favorites:
                await session.delete(playlist)
                await session.commit()
                return True
            return False

    @staticmethod
    async def rename_playlist(playlist_id: int, user_db_id: int, new_name: str, new_emoji: str = None) -> bool:
        async with async_session() as session:
            stmt = select(UserPlaylist).where(UserPlaylist.id == playlist_id, UserPlaylist.user_id == user_db_id)
            result = await session.execute(stmt)
            playlist = result.scalars().first()
            if playlist and not playlist.is_favorites:
                playlist.name = new_name
                if new_emoji:
                    playlist.emoji = new_emoji
                await session.commit()
                return True
            return False

    # --- Playlist Tracks Management ---

    @staticmethod
    async def add_track_to_playlist(playlist_id: int, track_info_id: int) -> bool:
        async with async_session() as session:
            # Check duplicate
            stmt = select(UserPlaylistTrack).where(
                UserPlaylistTrack.playlist_id == playlist_id,
                UserPlaylistTrack.track_info_id == track_info_id
            )
            res = await session.execute(stmt)
            if res.scalars().first():
                return False
                
            # Get max position
            pos_stmt = select(func.max(UserPlaylistTrack.position)).where(UserPlaylistTrack.playlist_id == playlist_id)
            pos_res = await session.execute(pos_stmt)
            max_pos = pos_res.scalar() or 0
            
            link = UserPlaylistTrack(playlist_id=playlist_id, track_info_id=track_info_id, position=max_pos + 1)
            session.add(link)
            await session.commit()
            return True

    @staticmethod
    async def remove_track_from_playlist(playlist_id: int, track_info_id: int) -> bool:
        async with async_session() as session:
            stmt = select(UserPlaylistTrack).where(
                UserPlaylistTrack.playlist_id == playlist_id,
                UserPlaylistTrack.track_info_id == track_info_id
            )
            res = await session.execute(stmt)
            link = res.scalars().first()
            if link:
                await session.delete(link)
                await session.commit()
                return True
            return False

    @staticmethod
    async def get_playlist_tracks(playlist_id: int, user_db_id: int) -> list[dict]:
        # Verifying ownership
        async with async_session() as session:
            owner_stmt = select(UserPlaylist).where(UserPlaylist.id == playlist_id, UserPlaylist.user_id == user_db_id)
            owner_res = await session.execute(owner_stmt)
            if not owner_res.scalars().first():
                return []
                
            stmt = select(TrackInfo, UserPlaylistTrack.added_at).join(
                UserPlaylistTrack, TrackInfo.id == UserPlaylistTrack.track_info_id
            ).where(
                UserPlaylistTrack.playlist_id == playlist_id
            ).order_by(UserPlaylistTrack.position.asc())
            
            result = await session.execute(stmt)
            out = []
            for track, added_at in result.all():
                out.append({
                    "db_id": track.id,
                    "id": track.source_id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration_sec": track.duration_sec,
                    "cover_url": track.cover_url,
                    "preview_url": track.preview_url,
                    "genre": track.genre,
                    "added_at": added_at.isoformat() if added_at else None
                })
            return out

    @staticmethod
    async def is_track_in_favorites(user_db_id: int, source_id: str) -> bool:
        async with async_session() as session:
            stmt = select(UserPlaylistTrack).join(UserPlaylist).join(TrackInfo, UserPlaylistTrack.track_info_id == TrackInfo.id).where(
                UserPlaylist.user_id == user_db_id,
                UserPlaylist.is_favorites == True,
                TrackInfo.source_id == source_id
            )
            res = await session.execute(stmt)
            return res.scalars().first() is not None

    @staticmethod
    async def get_or_create_chat(telegram_chat_id: int, is_channel: bool = False) -> Chat:
        async with async_session() as session:
            result = await session.execute(select(Chat).where(Chat.telegram_chat_id == telegram_chat_id))
            chat = result.scalar_one_or_none()
            if not chat:
                chat = Chat(telegram_chat_id=telegram_chat_id, is_channel=is_channel)
                session.add(chat)
                await session.commit()
                await session.refresh(chat)
            return chat

    @staticmethod
    async def get_cached_file_id(spotify_track_id: str) -> Optional[str]:
        """
        Возвращает telegram file_id, если трек уже однажды загружался в Telegram.
        Это позволяет мгновенно переслать трек без повторного скачивания с YouTube!
        """
        async with async_session() as session:
            result = await session.execute(
                select(SentTrack.file_id).where(SentTrack.spotify_track_id == spotify_track_id).limit(1)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def is_track_sent_to_playlist(playlist_db_id: int, spotify_track_id: str) -> bool:
        async with async_session() as session:
            result = await session.execute(
                select(SentTrack.id)
                .where(SentTrack.playlist_id == playlist_db_id, SentTrack.spotify_track_id == spotify_track_id)
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    @staticmethod
    async def save_sent_track(spotify_track_id: str, file_id: str, playlist_id: Optional[int] = None) -> SentTrack:
        async with async_session() as session:
            sent_track = SentTrack(
                playlist_id=playlist_id,
                spotify_track_id=spotify_track_id,
                file_id=file_id,
                sent_at=datetime.utcnow(),
            )
            session.add(sent_track)
            await session.commit()
            return sent_track

    @staticmethod
    async def get_or_create_sync_playlist(chat_db_id: int, spotify_playlist_id: str) -> SyncPlaylist:
        async with async_session() as session:
            result = await session.execute(
                select(SyncPlaylist).where(
                    SyncPlaylist.chat_id == chat_db_id,
                    SyncPlaylist.spotify_playlist_id == spotify_playlist_id,
                )
            )
            pl = result.scalar_one_or_none()
            if not pl:
                pl = SyncPlaylist(
                    chat_id=chat_db_id,
                    spotify_playlist_id=spotify_playlist_id,
                    is_active=True,
                )
                session.add(pl)
                await session.commit()
                await session.refresh(pl)
            return pl

    @staticmethod
    async def get_active_sync_playlists() -> List[SyncPlaylist]:
        from sqlalchemy.orm import selectinload
        async with async_session() as session:
            result = await session.execute(
                select(SyncPlaylist).options(selectinload(SyncPlaylist.chat)).where(SyncPlaylist.is_active == True)
            )
            return list(result.scalars().all())

    @staticmethod
    async def update_sync_time(playlist_id: int) -> None:
        async with async_session() as session:
            result = await session.execute(select(SyncPlaylist).where(SyncPlaylist.id == playlist_id))
            pl = result.scalar_one_or_none()
            if pl:
                pl.last_synced_at = datetime.utcnow()
                await session.commit()

    @staticmethod
    async def get_or_create_user(telegram_user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, language_code: Optional[str] = None) -> User:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(
                    telegram_user_id=telegram_user_id,
                    username=username,
                    first_name=first_name,
                    language_code=language_code
                )
                session.add(user)
            else:
                user.last_active_at = datetime.utcnow()
                if username is not None:
                    user.username = username
                if first_name is not None:
                    user.first_name = first_name
                if language_code is not None:
                    user.language_code = language_code
            await session.commit()
            await session.refresh(user)
            return user

    @staticmethod
    async def log_download(user_db_id: Optional[int], spotify_track_id: str, track_title: str, track_artist: str, track_duration_sec: int, source: str, success: bool) -> DownloadLog:
        async with async_session() as session:
            log = DownloadLog(
                user_id=user_db_id,
                spotify_track_id=spotify_track_id,
                track_title=track_title,
                track_artist=track_artist,
                track_duration_sec=track_duration_sec,
                source=source,
                success=success,
                downloaded_at=datetime.utcnow()
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    @staticmethod
    async def cache_artist_genre(artist_name: str, genres: str) -> ArtistGenre:
        async with async_session() as session:
            result = await session.execute(select(ArtistGenre).where(ArtistGenre.artist_name == artist_name))
            ag = result.scalar_one_or_none()
            if not ag:
                ag = ArtistGenre(artist_name=artist_name, genres=genres)
                session.add(ag)
            else:
                ag.genres = genres
                ag.fetched_at = datetime.utcnow()
            await session.commit()
            await session.refresh(ag)
            return ag

    @staticmethod
    async def get_cached_genre(artist_name: str) -> Optional[str]:
        async with async_session() as session:
            result = await session.execute(select(ArtistGenre.genres).where(ArtistGenre.artist_name == artist_name))
            return result.scalar_one_or_none()

    @classmethod
    async def get_stats_summary(cls) -> dict:
        async with async_session() as session:
            total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
            total_downloads = (await session.execute(select(func.count(DownloadLog.id)))).scalar() or 0
            successful = (await session.execute(select(func.count(DownloadLog.id)).where(DownloadLog.success == True))).scalar() or 0
            unique_tracks = (await session.execute(select(func.count(distinct(DownloadLog.spotify_track_id))))).scalar() or 0
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_downloads = (await session.execute(select(func.count(DownloadLog.id)).where(DownloadLog.downloaded_at >= today))).scalar() or 0
            return {
                "total_users": total_users,
                "total_downloads": total_downloads,
                "successful_downloads": successful,
                "unique_tracks": unique_tracks,
                "today_downloads": today_downloads
            }

    @classmethod
    async def get_downloads_by_day(cls, days=30) -> list[dict]:
        start_date = datetime.utcnow() - timedelta(days=days)
        async with async_session() as session:
            result = await session.execute(
                select(func.date(DownloadLog.downloaded_at).label('date'), func.count(DownloadLog.id).label('count'))
                .where(DownloadLog.downloaded_at >= start_date)
                .group_by('date')
                .order_by('date')
            )
            return [{"date": row.date, "count": row.count} for row in result.all()]

    @classmethod
    async def get_downloads_by_hour(cls) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(func.strftime('%H', DownloadLog.downloaded_at).label('hour'), func.count(DownloadLog.id).label('count'))
                .group_by('hour')
                .order_by('hour')
            )
            # Fill all 24 hours
            counts = {int(row.hour): row.count for row in result.all() if row.hour is not None}
            return [{"hour": i, "count": counts.get(i, 0)} for i in range(24)]

    @classmethod
    async def get_top_tracks(cls, limit=20) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(DownloadLog.track_title.label('title'), DownloadLog.track_artist.label('artist'), func.count(DownloadLog.id).label('count'))
                .group_by(DownloadLog.spotify_track_id, DownloadLog.track_title, DownloadLog.track_artist)
                .order_by(func.count(DownloadLog.id).desc())
                .limit(limit)
            )
            return [{"title": row.title, "artist": row.artist, "count": row.count} for row in result.all()]

    @classmethod
    async def get_top_artists(cls, limit=20) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(DownloadLog.track_artist.label('artist'), func.count(DownloadLog.id).label('count'))
                .group_by(DownloadLog.track_artist)
                .order_by(func.count(DownloadLog.id).desc())
                .limit(limit)
            )
            return [{"artist": row.artist, "count": row.count} for row in result.all()]

    @classmethod
    async def get_top_genres(cls, limit=15) -> list[dict]:
        async with async_session() as session:
            # Query all download_logs joined with artist_genres
            result = await session.execute(
                select(ArtistGenre.genres)
                .select_from(DownloadLog)
                .join(ArtistGenre, DownloadLog.track_artist == ArtistGenre.artist_name)
            )
            genre_counter = Counter()
            for row in result.all():
                if row.genres:
                    genres = [g.strip() for g in row.genres.split(",")]
                    genre_counter.update(genres)
            return [{"genre": g, "count": c} for g, c in genre_counter.most_common(limit)]

    @classmethod
    async def get_source_distribution(cls) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(DownloadLog.source, func.count(DownloadLog.id).label('count'))
                .group_by(DownloadLog.source)
            )
            return [{"source": row.source, "count": row.count} for row in result.all()]

    @classmethod
    async def get_recent_users(cls, limit=20) -> list[dict]:
        async with async_session() as session:
            result = await session.execute(
                select(User.telegram_user_id, User.username, User.first_name, User.language_code, User.last_active_at)
                .order_by(User.last_active_at.desc())
                .limit(limit)
            )
            return [
                {
                    "telegram_user_id": row.telegram_user_id,
                    "username": row.username,
                    "first_name": row.first_name,
                    "language_code": row.language_code,
                    "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None
                }
                for row in result.all()
            ]
