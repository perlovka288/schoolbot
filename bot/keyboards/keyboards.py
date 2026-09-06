"""Клавиатуры бота: главное меню (reply) и inline-клавиатуры для навигации."""

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

BTN_AUTH = "🔑 Авторизоваться в Google"
BTN_HOMEWORK = "📚 Мои ДЗ из Classroom"
BTN_ASK_BOOK = "📖 Задать вопрос по учебнику"
BTN_LOGOUT = "🚪 Выйти из Google"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_AUTH)],
            [KeyboardButton(text=BTN_HOMEWORK)],
            [KeyboardButton(text=BTN_ASK_BOOK)],
            [KeyboardButton(text=BTN_LOGOUT)],
        ],
        resize_keyboard=True,
    )


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


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
    )
