import asyncio
import logging
import re
from aiogram import Bot, F, Router, types
from aiogram.filters import Command

from bot.keyboards.download import get_personalized_mix_keyboard, get_running_keyboard
from db.repository import Repository
from services.job_manager import JobManager
from services.processor import process_and_send_track
from services.spotify import fetch_spotify_data, is_personalized_mix, parse_spotify_url

logger = logging.getLogger(__name__)
router = Router()


def extract_all_spotify_urls(text: str) -> list[str]:
    """
    Находит все ссылки на Spotify в переданном сообщении.
    """
    return re.findall(r"(https?://open\.spotify\.com/[^\s]+|spotify:[a-zA-Z0-9:]+)", text)


@router.message(Command("download"))
async def handle_download_command(message: types.Message, bot: Bot):
    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) < 2:
        await message.answer(
            "ℹ️ Укажите ссылку на трек или плейлист Spotify:\n`/download https://open.spotify.com/playlist/...`"
        )
        return

    urls = extract_all_spotify_urls(args[1])
    if not urls:
        await message.answer("❌ Ссылка на Spotify не найдена в сообщении. Пожалуйста, проверьте формат.")
        return

    await process_multiple_spotify_urls(message, bot, urls)


@router.message(Command("stop"))
@router.message(Command("pause"))
async def handle_stop_command(message: types.Message, bot: Bot):
    job = JobManager.get_job(message.chat.id)
    if not job or job.status not in ("running", "paused"):
        await message.answer("ℹ️ Сейчас нет активных процессов скачивания.")
        return

    if message.text and "/pause" in message.text:
        await JobManager.pause_job(message.chat.id, bot)
        await message.answer("⏸ Загрузка поставлена на паузу.")
    else:
        await JobManager.stop_job(message.chat.id, bot, notify=True)
        await message.answer("🛑 Загрузка остановлена.")


@router.message(Command("resume"))
async def handle_resume_command(message: types.Message, bot: Bot):
    job = JobManager.get_job(message.chat.id)
    if not job or job.status != "paused":
        await message.answer("ℹ️ Нет приостановленных загрузок для возобновления.")
        return

    await JobManager.resume_job(message.chat.id, bot)
    await message.answer("▶️ Возобновляю загрузку.")


# Callback-кнопки
@router.callback_query(F.data == "pause_download")
async def cb_pause(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("Приостанавливаем...")
    await JobManager.pause_job(callback.message.chat.id, bot)


@router.callback_query(F.data == "resume_download")
async def cb_resume(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("Возобновляем...")
    await JobManager.resume_job(callback.message.chat.id, bot)


@router.callback_query(F.data == "cancel_download")
async def cb_cancel(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("Останавливаем...")
    await JobManager.stop_job(callback.message.chat.id, bot, notify=True)


@router.callback_query(F.data.startswith("force_mix:"))
async def cb_force_mix(callback: types.CallbackQuery, bot: Bot):
    await callback.answer()
    playlist_id = callback.data.split(":", 1)[1]
    url = f"https://open.spotify.com/playlist/{playlist_id}"
    try:
        await callback.message.delete()
    except Exception:
        pass
    await process_spotify_url(callback.message, bot, url, force=True)


@router.callback_query(F.data == "dismiss_mix")
async def cb_dismiss_mix(callback: types.CallbackQuery):
    await callback.answer("Отменено")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(lambda msg: msg.text and ("open.spotify.com" in msg.text or "spotify:" in msg.text))
async def handle_spotify_link(message: types.Message, bot: Bot):
    if message.text and message.text.startswith(("/sync", "/download")):
        return

    urls = extract_all_spotify_urls(message.text)
    if not urls:
        return

    await process_multiple_spotify_urls(message, bot, urls)


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    if not message.document.file_name.endswith(".txt"):
        return
    
    import io
    file_in_memory = io.BytesIO()
    await bot.download(message.document, destination=file_in_memory)
    text = file_in_memory.getvalue().decode('utf-8', errors='ignore')
    
    urls = extract_all_spotify_urls(text)
    if not urls:
        await message.answer("❌ Ссылки на Spotify не найдены в файле.")
        return

    await process_multiple_spotify_urls(message, bot, urls)


async def process_multiple_spotify_urls(message: types.Message, bot: Bot, urls: list[str], force: bool = False):
    if len(urls) == 1:
        await process_spotify_url(message, bot, urls[0], force)
        return

    # Проверяем, нет ли уже активной загрузки
    if JobManager.has_active_job(message.chat.id):
        await message.answer(
            "⚠️ В этом чате уже идет или приостановлена загрузка.\n"
            "Вы можете поставить её на паузу или остановить кнопкой под статусом, либо командой /stop."
        )
        return

    status_msg = await message.answer(f"🔍 Найдено {len(urls)} ссылок. Создаю виртуальный плейлист...")

    # Регистрируем чат в БД
    is_channel = message.chat.type in ("channel", "supergroup")
    await Repository.get_or_create_chat(message.chat.id, is_channel=is_channel)

    # Создаем виртуальный SpotifyCollection
    from services.spotify import SpotifyCollection, SpotifyTrack
    
    dummy_tracks = []
    for i, u in enumerate(urls):
        dummy_tracks.append(
            SpotifyTrack(
                id=u,  # URL храним в id
                title="LazyTrack",
                artist_str="LazyArtist",
                album="Unknown",
                release_year="",
                duration_sec=0,
                cover_url=""
            )
        )

    collection = SpotifyCollection(
        type="playlist",
        id=f"custom_{message.message_id}",
        title=f"Пользовательский список ({len(urls)} треков)",
        cover_url="",
        tracks=dummy_tracks
    )

    await status_msg.edit_text(
        f"📑 **«{collection.title}»**\n\n⏳ Начинаем выгрузку порциями (защита от спама Spotify включена)...",
        reply_markup=get_running_keyboard(),
    )

    await JobManager.start_job(
        chat_id=message.chat.id,
        collection=collection,
        status_msg_id=status_msg.message_id,
        bot=bot,
    )


async def process_spotify_url(message: types.Message, bot: Bot, url: str, force: bool = False):
    parsed = parse_spotify_url(url)
    if not parsed:
        await message.answer("❌ Неподдерживаемый формат ссылки Spotify.")
        return

    # Проверяем на персональный алгоритмический микс
    if not force and is_personalized_mix(parsed[0], parsed[1]):
        text = (
            "💡 **Это персональный микс Spotify («Made for You»)**\n\n"
            "Spotify подбирает треки в таких миксах **индивидуально под каждого слушателя**.\n\n"
            "👉 **Чтобы скачать именно ваш личный список треков:**\n"
            "1. Откройте этот микс в приложении Spotify\n"
            "2. Нажмите **`...` ➔ «Добавить в другой плейлист» ➔ «Создать плейлист»**\n"
            "3. Отправьте боту ссылку на созданный плейлист!\n\n"
            "Или нажмите кнопку ниже, чтобы скачать текущую версию:"
        )
        from bot.keyboards.download import get_personalized_mix_keyboard
        await message.answer(text, reply_markup=get_personalized_mix_keyboard(parsed[1]))
        return

    # Проверяем, нет ли уже активной загрузки
    if JobManager.has_active_job(message.chat.id):
        await message.answer(
            "⚠️ В этом чате уже идет или приостановлена загрузка.\n"
            "Вы можете поставить её на паузу или остановить кнопкой под статусом, либо командой /stop."
        )
        return

    status_msg = await message.answer("🔍 Загружаю информацию из Spotify...")

    # Регистрируем чат в БД
    is_channel = message.chat.type in ("channel", "supergroup")
    await Repository.get_or_create_chat(message.chat.id, is_channel=is_channel)

    user_id = message.from_user.id if message.from_user else None
    collection = await fetch_spotify_data(url, user_id=user_id)
    if not collection or not collection.tracks:
        await status_msg.edit_text("❌ Не удалось извлечь треки. Убедитесь, что плейлист или трек является публичным.")
        return

    total = len(collection.tracks)

    # Одиночный трек
    if collection.type == "track":
        track = collection.tracks[0]
        await status_msg.edit_text(f"⏳ Скачиваю: **{track.artist_str} — {track.title}**...")
        success = await process_and_send_track(bot, message.chat.id, track)
        if success:
            await status_msg.delete()
        else:
            await status_msg.edit_text(f"❌ Не удалось найти или скачать трек: **{track.artist_str} — {track.title}**")
        return

    # Подсказка про лимит 100 треков
    limit_note = ""
    if collection.type == "playlist" and total >= 100:
        limit_note = "\n\n⚠️ _Внимание: загружены первые 100 добавленных в плейлист треков (лимит Spotify на плейлист). Если в плейлисте больше 100 треков, рекомендуем разделить его на части._"

    # Плейлисты и альбомы -> передаем в JobManager с интерактивными кнопками!
    await status_msg.edit_text(
        f"📑 **«{collection.title}»** (всего {total} треков){limit_note}\n\n⏳ Начинаем выгрузку...",
        reply_markup=get_running_keyboard(),
    )

    await JobManager.start_job(
        chat_id=message.chat.id,
        collection=collection,
        status_msg_id=status_msg.message_id,
        bot=bot,
    )
