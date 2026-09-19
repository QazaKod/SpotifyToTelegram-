from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    is_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sync_playlists: Mapped[list["SyncPlaylist"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class SyncPlaylist(Base):
    __tablename__ = "sync_playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    spotify_playlist_id: Mapped[str] = mapped_column(String(64), index=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    chat: Mapped["Chat"] = relationship(back_populates="sync_playlists")
    sent_tracks: Mapped[list["SentTrack"]] = relationship(back_populates="playlist", cascade="all, delete-orphan")


class SentTrack(Base):
    __tablename__ = "sent_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sync_playlists.id", ondelete="CASCADE"), nullable=True, index=True
    )
    spotify_track_id: Mapped[str] = mapped_column(String(64), index=True)
    file_id: Mapped[str] = mapped_column(String(256))  # Telegram file_id для мгновенной пересылки
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    playlist: Mapped[Optional["SyncPlaylist"]] = relationship(back_populates="sent_tracks")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(128), nullable=True)
    first_name = Column(String(128), nullable=True)
    language_code = Column(String(10), nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_active_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DownloadLog(Base):
    __tablename__ = "download_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    spotify_track_id = Column(String(64), index=True, nullable=False)
    track_title = Column(String(256), nullable=False)
    track_artist = Column(String(256), nullable=False)
    track_duration_sec = Column(Integer, nullable=True)
    source = Column(String(32), nullable=False)  # youtube, soundcloud, cache
    success = Column(Boolean, default=True, nullable=False)
    downloaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ArtistGenre(Base):
    __tablename__ = "artist_genres"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_name = Column(String(256), unique=True, index=True, nullable=False)
    genres = Column(String(512), nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TrackInfo(Base):
    """Универсальное хранилище метаданных треков (Spotify, iTunes, ручной ввод)."""
    __tablename__ = "track_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(128), unique=True, index=True, nullable=False)
    title = Column(String(256), nullable=False)
    artist = Column(String(256), nullable=False)
    album = Column(String(256), nullable=True)
    duration_sec = Column(Integer, nullable=True)
    cover_url = Column(String(512), nullable=True)
    preview_url = Column(String(512), nullable=True)
    genre = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserPlaylist(Base):
    """Пользовательский плейлист (папка). Один специальный: is_favorites=True."""
    __tablename__ = "user_playlists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(128), nullable=False)
    emoji = Column(String(8), nullable=True)
    is_favorites = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserPlaylistTrack(Base):
    """Связующая таблица: какой трек лежит в каком плейлисте."""
    __tablename__ = "user_playlist_tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    playlist_id = Column(Integer, ForeignKey("user_playlists.id", ondelete="CASCADE"), index=True, nullable=False)
    track_info_id = Column(Integer, ForeignKey("track_info.id", ondelete="CASCADE"), index=True, nullable=False)
    position = Column(Integer, default=0)
    added_at = Column(DateTime, default=datetime.utcnow)
