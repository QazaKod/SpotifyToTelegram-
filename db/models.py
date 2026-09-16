from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
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
