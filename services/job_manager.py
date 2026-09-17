import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import config
from bot.keyboards.download import get_paused_keyboard, get_running_keyboard
from services.processor import process_and_send_track
from services.spotify import SpotifyCollection

logger = logging.getLogger(__name__)


@dataclass
class DownloadJob:
    chat_id: int
    collection: SpotifyCollection
    status_msg_id: int
    current_index: int = 0
    success_count: int = 0
    status: str = "running"  # "running", "paused", "stopped", "completed"
    task: Optional[asyncio.Task] = None
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    spam_pause_until: float = 0.0

    def __post_init__(self):
        self.pause_event.set()  # изначально не на паузе


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
    ) -> DownloadJob:
        # Если была старая задача, останавливаем
        if chat_id in cls._jobs:
            await cls.stop_job(chat_id, bot, notify=False)

        job = DownloadJob(
            chat_id=chat_id,
            collection=collection,
            status_msg_id=status_msg_id,
        )
        cls._jobs[chat_id] = job

        # Запускаем фоновую обработку
        job.task = asyncio.create_task(cls._run_job(job, bot))
        return job

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
        except Exception as e:
            logger.warning(f"Не удалось обновить статус паузы: {e}")

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
        except Exception as e:
            logger.warning(f"Не удалось обновить статус продолжения: {e}")

        return True

    @classmethod
    async def stop_job(cls, chat_id: int, bot: Bot, notify: bool = True) -> bool:
        job = cls.get_job(chat_id)
        if not job:
            return False

        job.status = "stopped"
        job.pause_event.set()  # разблокируем, если был на паузе

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
            except Exception:
                pass

        cls._jobs.pop(chat_id, None)
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
            except (TelegramBadRequest, TelegramRetryAfter):
                pass
            except Exception as e:
                logger.debug(f"Игнорируем ошибку обновления статуса: {e}")

        async def worker():
            while not queue.empty():
                if job.status == "stopped":
                    break

                # Ждем, если на паузе
                await job.pause_event.wait()
                if job.status == "stopped":
                    break

                # Спам пауза
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
                    ok = await process_and_send_track(bot, job.chat_id, track)
                    if ok:
                        async with lock:
                            job.success_count += 1
                            # Ставим всех воркеров на паузу 120 сек каждые 30 треков (для custom)
                            if job.success_count > 0 and job.success_count % 30 == 0 and job.collection.id.startswith("custom_"):
                                job.spam_pause_until = time.time() + 120.0
                except Exception as e:
                    logger.error(f"Ошибка при скачивании трека {track.title}: {e}")
                finally:
                    queue.task_done()

                # Небольшая пауза между отправками, чтобы не превышать лимиты Telegram
                await asyncio.sleep(0.5)

        try:
            num_workers = min(config.MAX_CONCURRENT_DOWNLOADS, total)
            workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
            await asyncio.gather(*workers)

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
                except Exception:
                    pass
                cls._jobs.pop(job.chat_id, None)

        except asyncio.CancelledError:
            logger.info(f"Задача для чата {job.chat_id} была отменена.")
        except Exception as e:
            logger.exception(f"Непредвиденная ошибка в воркере загрузки: {e}")
            cls._jobs.pop(job.chat_id, None)
