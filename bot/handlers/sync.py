import logging
from aiogram import Bot, Router, types
from aiogram.filters import Command

from bot.handlers.download import extract_spotify_url
from db.repository import Repository
from services.spotify import fetch_spotify_data, parse_spotify_url

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("sync"))
async def handle_sync_command(message: types.Message, bot: Bot):
    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) < 2:
        await message.answer(
            "ℹ️ Для автосинхронизации укажите ссылку на плейлист Spotify:\n"
            "`/sync https://open.spotify.com/playlist/...`\n\n"
            "Бот привяжет этот чат или канал к плейлисту и будет периодически проверять его на наличие новых треков."
        )
        return

    url = extract_spotify_url(args[1])
    parsed = parse_spotify_url(url)
    if not parsed or parsed[0] != "playlist":
        await message.answer("❌ Команда `/sync` работает только с **плейлистами** Spotify.")
        return

    entity_type, playlist_id = parsed
    status_msg = await message.answer("🔄 Проверяю плейлист...")

    collection = await fetch_spotify_data(url)
    if not collection:
        await status_msg.edit_text("❌ Не удалось получить доступ к плейлисту. Проверьте, что он публичный.")
        return

    is_channel = message.chat.type in ("channel", "supergroup")
    chat = await Repository.get_or_create_chat(message.chat.id, is_channel=is_channel)

    sync_record = await Repository.get_or_create_sync_playlist(chat.id, playlist_id)

    await status_msg.edit_text(
        f"✅ **Плейлист успешно привязан!**\n\n"
        f"🎵 **Название:** {collection.title}\n"
        f"📊 **Всего треков:** {len(collection.tracks)}\n"
        f"🤖 Бот будет автоматически проверять плейлист и досылать только новые треки!"
    )
