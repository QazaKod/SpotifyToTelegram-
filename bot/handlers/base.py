import config
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

router = Router()


@router.message(CommandStart())
async def handle_start(message: types.Message):
    text = (
        "🎧 **Добро пожаловать в SpotifyToTelegram Bot!**\n\n"
        "Я умею скачивать музыку из Spotify в максимальном качестве, "
        "с красивыми обложками и правильными тегами исполнителей.\n\n"
        "**Как пользоваться:**\n"
        "• Просто отправьте мне ссылку на **плейлист**, **альбом** или **трек** Spotify.\n"
        "• Или используйте команду: `/download <ссылка>`\n"
        "• Во время выгрузки плейлиста под статусом доступны кнопки **⏸ Пауза** и **🛑 Стоп**.\n"
        "• Для автосинхронизации канала с плейлистом: `/sync <ссылка>`\n\n"
        "📌 **Особенности загрузки плейлистов:**\n"
        "• Из-за ограничений Spotify за один раз загружаются **первые 100 добавленных в плейлист треков**.\n"
        "• Если в плейлисте больше 100 песен, рекомендуем разделить его в Spotify на части по <100 треков и отправить ссылки по очереди.\n"
    )

    if config.WEBAPP_URL:
        text += "\n🎵 Или откройте **Mini App** для визуального выбора треков:"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎵 Открыть плеер", web_app=WebAppInfo(url=config.WEBAPP_URL))]
            ]
        )
        await message.answer(text, reply_markup=kb)
    else:
        text += "\nПопробуйте отправить ссылку прямо сейчас!"
        await message.answer(text)


@router.message(Command("help"))
async def handle_help(message: types.Message):
    text = (
        "📖 **Справка по командам:**\n\n"
        "• `/download <spotify_url>` — скачивание трека, альбома или плейлиста.\n"
        "• `/pause` — приостановить текущую выгрузку плейлиста.\n"
        "• `/resume` — возобновить выгрузку с того места, где остановились.\n"
        "• `/stop` — полностью остановить текущую выгрузку.\n"
        "• `/sync <spotify_url>` — привязать плейлист к чату или каналу для периодической выгрузки новинок.\n\n"
        "⚠️ **Обратите внимание:**\n"
        "• Spotify отдаёт боту **первые 100 добавленных в плейлист треков**.\n"
        "• Если ваш плейлист больше (например, 150–200 песен), разделите его на два списка по <100 треков."
    )

    if config.WEBAPP_URL:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎵 Открыть плеер", web_app=WebAppInfo(url=config.WEBAPP_URL))]
            ]
        )
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)
