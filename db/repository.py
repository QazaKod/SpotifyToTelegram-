from datetime import datetime
from typing import List, Optional
from sqlalchemy import select

from db.database import async_session
from db.models import Chat, SentTrack, SyncPlaylist


class Repository:
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
