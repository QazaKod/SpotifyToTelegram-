import asyncio
import logging
import time
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiogram import Bot
from aiogram.types import InputMediaAudio, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import config
from bot.keyboards.download import get_paused_keyboard, get_running_keyboard
from services.processor import process_and_send_track, download_and_tag_track
from services.delivery import DeliveryManager
from services.spotify import SpotifyCollection
from db.repository import Repository

logger = logging.getLogger(__name__)

@dataclass
class DownloadJob:
    chat_id: int
    collection: SpotifyCollection
    status_msg_id: int
    user_id: Optional[int] = None
    current_index: int = 0
    success_count: int = 0
    status: str = "running"  # "running", "paused", "stopped", "completed"
    task: Optional[asyncio.Task] = None
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    spam_pause_until: float = 0.0
    header_msg_id: int = 0
    batch_buffer: list = field(default_factory=list)
    batch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    use_media_groups: bool = True

    def __post_init__(self):
        self.pause_event.set()


class JobManager:
    _jobs: Dict[int, DownloadJob] = {}

    @classmethod
    def get_job(cls, chat_id: int) -> Optional[DownloadJob]:
        return cls._jobs.get(chat_id)

    @classmethod
    def has_active_job(cls, chat_id: int) -> bool:
        job = cls.get_job(chat_id)
        return job is not None and job.status in ("running", "paused")

    @classmethod
    async def start_job(
        cls,
        chat_id: int,
        collection: SpotifyCollection,
        status_msg_id: int,
        bot: Bot,
        user_id: Optional[int] = None,
        header_msg_id: int = 0,
        use_media_groups: bool = True
    ) -> DownloadJob:
        if chat_id in cls._jobs:
            await cls.stop_job(chat_id, bot, notify=False)

        job = DownloadJob(
            chat_id=chat_id,
            collection=collection,
            status_msg_id=status_msg_id,
            user_id=user_id,
            header_msg_id=header_msg_id,
            use_media_groups=use_media_groups
        )
        cls._jobs[chat_id] = job
        job.task = asyncio.create_task(cls._run_job(job, bot))
        return job

    @classmethod
    async def flush_batch(cls, job: DownloadJob, bot: Bot):
        async with job.batch_lock:
            if not job.batch_buffer:
                return
            items_to_send = job.batch_buffer[:10]
            job.batch_buffer = job.batch_buffer[10:]

        media_group = []
        temp_files = []
        for item in items_to_send:
            cached = await Repository.get_cached_file_id(item['track'].id)
            if cached:
                media_group.append(InputMediaAudio(
                    media=cached,
                    title=item['track'].title,
                    performer=item['track'].artist_str
                ))
            else:
                audio_file = FSInputFile(path=item['mp3_path'])
                media_group.append(InputMediaAudio(
                    media=audio_file,
                    title=item['track'].title,
                    performer=item['track'].artist_str
                ))
                temp_files.append(item)

        messages = await bot.send_media_group(chat_id=job.chat_id, media=media_group)

        for msg, item in zip(messages, items_to_send):
            if msg.audio:
                await Repository.save_sent_track(item['track'].id, msg.audio.file_id)

        for item in items_to_send:
            for path in [item.get('mp3_path'), item.get('cover_path')]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass

    @classmethod
    async def stop_job(cls, chat_id: int, bot: Bot, notify: bool = True) -> bool:
        job = cls.get_job(chat_id)
        if not job:
            return False

        job.status = "stopped"
        job.pause_event.set()

        if job.task and not job.task.done():
            job.task.cancel()

        total = len(job.collection.tracks)

        if notify:
            text = (
                f"🛑 **Загрузка отменена пользователем.**\n\n"
                f"🎵 Коллекция: **{job.collection.title}**\n"
                f"✅ Успешно отправлено: **{job.success_count} из {total}** треков."
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=job.status_msg_id,
                    text=text,
                    reply_markup=None,
                )
            except:
                pass

        cls._jobs.pop(chat_id, None)
        return True

    @classmethod
    async def pause_job(cls, chat_id: int, bot: Bot) -> bool:
        job = cls.get_job(chat_id)
        if not job or job.status != "running":
            return False

        job.status = "paused"
        job.pause_event.clear()

        total = len(job.collection.tracks)
        curr = min(job.current_index + 1, total)

        text = (
            f"⏸ **Загрузка приостановлена**\n\n"
            f"🎵 Коллекция: **{job.collection.title}**\n"
            f"📊 Остановлено на: **{curr} из {total}**\n"
            f"✅ Успешно отправлено: **{job.success_count}**\n\n"
            f"Нажмите кнопку ниже, чтобы продолжить с этого места в любое время."
        )

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=job.status_msg_id,
                text=text,
                reply_markup=get_paused_keyboard(),
            )
        except:
            pass

        return True

    @classmethod
    async def resume_job(cls, chat_id: int, bot: Bot) -> bool:
        job = cls.get_job(chat_id)
        if not job or job.status != "paused":
            return False

        job.status = "running"
        job.pause_event.set()

        total = len(job.collection.tracks)
        curr = min(job.current_index + 1, total)

        text = (
            f"▶️ **Загрузка продолжается...**\n\n"
            f"🎵 Коллекция: **{job.collection.title}**\n"
            f"📊 Прогресс: **{curr} из {total}**\n"
            f"⏳ Скачиваем следующий трек..."
        )

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=job.status_msg_id,
                text=text,
                reply_markup=get_running_keyboard(),
            )
        except:
            pass

        return True

    @classmethod
    async def _run_job(cls, job: DownloadJob, bot: Bot):
        total = len(job.collection.tracks)
        queue: asyncio.Queue[int] = asyncio.Queue()
        for idx in range(job.current_index, total):
            queue.put_nowait(idx)

        last_edit_time = 0.0
        lock = asyncio.Lock()

        async def update_status(track_name: str):
            nonlocal last_edit_time
            now = time.time()
            if now - last_edit_time < 2.0:
                return
            last_edit_time = now
            progress_text = (
                f"📑 **«{job.collection.title}»**\n\n"
                f"⚡ Скачиваю [{job.success_count}/{total}]:\n"
                f"**{track_name}**\n\n"
                f"🚀 Параллельный режим (по {config.MAX_CONCURRENT_DOWNLOADS} трека)"
            )
            try:
                await bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.status_msg_id,
                    text=progress_text,
                    reply_markup=get_running_keyboard(),
                )
            except:
                pass

        async def worker():
            while not queue.empty():
                if job.status == "stopped":
                    break

                await job.pause_event.wait()
                if job.status == "stopped":
                    break

                now = time.time()
                if now < job.spam_pause_until:
                    await update_status("Сервера под нагрузкой, ожидание 2 мин...")
                    await asyncio.sleep(job.spam_pause_until - now)

                try:
                    idx = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                async with lock:
                    job.current_index = max(job.current_index, idx)

                track = job.collection.tracks[idx]
                await update_status(f"{track.artist_str} — {track.title}")

                try:
                    if job.use_media_groups:
                        res = await download_and_tag_track(track, user_id=job.user_id)
                        if res:
                            async with job.batch_lock:
                                job.batch_buffer.append(res)
                            
                            async with lock:
                                job.success_count += 1
                                if job.success_count > 0 and job.success_count % 30 == 0 and job.collection.id.startswith("custom_"):
                                    job.spam_pause_until = time.time() + 120.0
                            
                            buffer_len = 0
                            async with job.batch_lock:
                                buffer_len = len(job.batch_buffer)
                            if buffer_len >= 10:
                                await cls.flush_batch(job, bot)
                    else:
                        ok = await process_and_send_track(bot, job.chat_id, track, user_id=job.user_id)
                        if ok:
                            async with lock:
                                job.success_count += 1
                                if job.success_count > 0 and job.success_count % 30 == 0 and job.collection.id.startswith("custom_"):
                                    job.spam_pause_until = time.time() + 120.0
                except Exception as e:
                    logger.error(f"Ошибка при скачивании трека {track.title}: {e}")
                finally:
                    queue.task_done()

                await asyncio.sleep(0.5)

        try:
            num_workers = min(config.MAX_CONCURRENT_DOWNLOADS, total)
            workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
            await asyncio.gather(*workers)
            
            # Flush remaining items
            if job.use_media_groups:
                while True:
                    buffer_len = 0
                    async with job.batch_lock:
                        buffer_len = len(job.batch_buffer)
                    if buffer_len == 0:
                        break
                    await cls.flush_batch(job, bot)
                
                if job.header_msg_id:
                    try:
                        caption = f"🎵 *{job.collection.title}*\n✅ Загрузка завершена!"
                        # Here it might be edit_message_caption or edit_message_text based on how send_header sent it.
                        # we'll just try edit_message_caption
                        await bot.edit_message_caption(
                            chat_id=job.chat_id,
                            message_id=job.header_msg_id,
                            caption=caption,
                            parse_mode="Markdown"
                        )
                    except:
                        pass
                
                if job.status not in ("stopped", "paused"):
                    await DeliveryManager.send_footer(bot, job.chat_id, job.success_count, total)

            if job.status not in ("stopped", "paused"):
                job.status = "completed"
                finish_text = (
                    f"🎉 **Загрузка полностью завершена!**\n\n"
                    f"🎵 Коллекция: **{job.collection.title}**\n"
                    f"✅ Отправлено: **{job.success_count}** из **{total}** треков."
                )
                try:
                    await bot.edit_message_text(
                        chat_id=job.chat_id,
                        message_id=job.status_msg_id,
                        text=finish_text,
                        reply_markup=None,
                    )
                except:
                    pass
                cls._jobs.pop(job.chat_id, None)

        except asyncio.CancelledError:
            logger.info(f"Задача для чата {job.chat_id} была отменена.")
        except Exception as e:
            logger.exception(f"Непредвиденная ошибка в воркере загрузки: {e}")
            cls._jobs.pop(job.chat_id, None)
