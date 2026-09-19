import os
from aiogram import Bot
from aiogram.types import Message, InputMediaAudio, FSInputFile

MEDIA_GROUP_SIZE = 10

class DeliveryManager:
    """Управляет пакетной отправкой треков в формате MediaGroup."""

    @staticmethod
    async def send_header(bot: Bot, chat_id: int, collection) -> int:
        """
        Отправляет обложку плейлиста как фото + подпись.
        Возвращает message_id отправленного сообщения.
        """
        caption = f"🎵 *{collection.title}*\n{len(collection.tracks)} треков • Загрузка..."
        if hasattr(collection, 'cover_url') and collection.cover_url:
            msg = await bot.send_photo(chat_id, photo=collection.cover_url, caption=caption, parse_mode="Markdown")
        else:
            msg = await bot.send_message(chat_id, caption, parse_mode="Markdown")
        return msg.message_id

    @staticmethod
    async def send_batch(bot: Bot, chat_id: int, audio_items: list[InputMediaAudio]) -> list[Message]:
        """
        Отправляет группу аудио (до 10) одним MediaGroup.
        audio_items — список InputMediaAudio с file_id или FSInputFile.
        """
        return await bot.send_media_group(chat_id=chat_id, media=audio_items)

    @staticmethod
    async def send_footer(bot: Bot, chat_id: int, success: int, total: int):
        """Отправляет финальное сообщение."""
        await bot.send_message(
            chat_id,
            f"✅ *Загрузка завершена!*\nОтправлено {success} из {total} треков.",
            parse_mode="Markdown"
        )
