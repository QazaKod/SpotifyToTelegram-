from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_running_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏸ Пауза", callback_data="pause_download"),
                InlineKeyboardButton(text="🛑 Стоп", callback_data="cancel_download"),
            ]
        ]
    )


def get_paused_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Продолжить скачивание", callback_data="resume_download"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_download"),
            ]
        ]
    )


def get_personalized_mix_keyboard(playlist_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Все равно скачать", callback_data=f"force_mix:{playlist_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="dismiss_mix"),
            ]
        ]
    )
