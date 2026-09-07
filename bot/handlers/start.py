"""Обработка /start и главного меню."""

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.keyboards.keyboards import main_menu_keyboard, BTN_LOGOUT
from services import google_service, sync_service

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привіт! Я бот-помічник у навчанні 🎓\n\n"
        "Що я вмію:\n"
        "🔑 Авторизувати тебе в Google Classroom\n"
        "📚 Показувати актуальні домашні завдання та розв'язувати їх за допомогою ШІ\n"
        "📖 Відповідати на запитання по підручниках\n\n"
        "Обери дію на клавіатурі нижче 👇",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


@router.message(F.text == BTN_LOGOUT)
async def logout_handler(message: Message) -> None:
    google_service.logout(message.from_user.id)
    sync_service.clear_state(message.from_user.id)
    await message.answer(
        "Ви вийшли з облікового запису Google. Токен видалено.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )
