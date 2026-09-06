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
        "Привет! Я бот-помощник в учёбе 🎓\n\n"
        "Что я умею:\n"
        "🔑 Авторизовать тебя в Google Classroom\n"
        "📚 Показывать актуальные домашние задания и решать их с помощью ИИ\n"
        "📖 Отвечать на вопросы по учебникам\n\n"
        "Выбери действие на клавиатуре ниже 👇",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == BTN_LOGOUT)
async def logout_handler(message: Message) -> None:
    google_service.logout(message.from_user.id)
    sync_service.clear_state(message.from_user.id)
    await message.answer(
        "Вы вышли из аккаунта Google. Токен удалён.",
        reply_markup=main_menu_keyboard(),
    )
