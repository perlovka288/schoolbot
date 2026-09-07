"""Клавиатуры бота: главное меню (reply) и inline-клавиатуры для навигации."""

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import config

BTN_AUTH = "🔑 Авторизоваться в Google"
BTN_HOMEWORK = "📚 Мои ДЗ из Classroom"
BTN_ASK_BOOK = "📖 Задать вопрос по учебнику"
BTN_LOGOUT = "🚪 Выйти из Google"
BTN_ADMIN_UPLOAD_BOOK = "📥 Загрузить учебник (админ)"


def main_menu_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_AUTH)],
        [KeyboardButton(text=BTN_HOMEWORK)],
        [KeyboardButton(text=BTN_ASK_BOOK)],
        [KeyboardButton(text=BTN_LOGOUT)],
    ]
    # Кнопка загрузки учебников видна ТОЛЬКО админам (config.ADMIN_IDS) —
    # для всех остальных пользователей её вообще нет в клавиатуре.
    if user_id is not None and user_id in config.ADMIN_IDS:
        rows.append([KeyboardButton(text=BTN_ADMIN_UPLOAD_BOOK)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def courses_keyboard(courses: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=course.get("name", "Без названия"),
                callback_data=f"course:{course['id']}",
            )
        ]
        for course in courses
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def coursework_keyboard(course_id: str, courseworks: list) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=cw.title[:60],
                callback_data=f"cw:{course_id}:{cw.coursework_id}",
            )
        ]
        for cw in courseworks
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад к курсам", callback_data="back_to_courses")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def coursework_detail_keyboard(course_id: str, coursework_id: str) -> InlineKeyboardMarkup:
    """Клавиатура на экране деталей задания: решить / назад к списку."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Решить (по учебникам)",
                callback_data=f"solve:{course_id}:{coursework_id}",
            )],
            [InlineKeyboardButton(text="⬅️ Назад к заданиям", callback_data=f"course:{course_id}")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
    )
