"""Просмотр курсов и актуальных домашних заданий из Google Classroom."""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.keyboards.keyboards import (
    BTN_HOMEWORK,
    main_menu_keyboard,
    courses_keyboard,
    coursework_keyboard,
)
from services import google_service
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)
router = Router(name="classroom")


@router.message(F.text == BTN_HOMEWORK)
async def show_courses(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if not google_service.is_authorized(user_id):
        await message.answer(
            "Сначала авторизуйтесь: нажмите «🔑 Авторизоваться в Google».",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer("Загружаю список курсов…")

    try:
        courses = google_service.get_active_courses(user_id)
    except GoogleAuthError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    if not courses:
        await message.answer("Активных курсов в Classroom не найдено.")
        return

    await state.update_data(courses={c["id"]: c["name"] for c in courses})
    await message.answer(
        "Выберите курс, чтобы посмотреть актуальные домашние задания:",
        reply_markup=courses_keyboard(courses),
    )


@router.callback_query(F.data.startswith("course:"))
async def show_coursework(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    course_id = callback.data.split(":", 1)[1]

    await callback.message.edit_text("Загружаю домашние задания…")

    try:
        courseworks = google_service.get_coursework(user_id, course_id)
    except GoogleAuthError as exc:
        await callback.message.edit_text(f"⚠️ {exc}")
        await callback.answer()
        return

    if not courseworks:
        await callback.message.edit_text(
            "🎉 По этому курсу нет невыполненных заданий — всё сдано!"
        )
        await callback.answer()
        return

    # Сохраняем данные о заданиях в состояние, чтобы solver.py мог их достать
    data = await state.get_data()
    cw_store = data.get("courseworks", {})
    for cw in courseworks:
        cw_store[cw.coursework_id] = {
            "course_id": cw.course_id,
            "course_name": cw.course_name,
            "title": cw.title,
            "description": cw.description,
            "due_date": cw.due_date,
            "materials": cw.materials,
        }
    await state.update_data(courseworks=cw_store)

    lines = ["Актуальные задания:\n"]
    for cw in courseworks:
        due = f" (до {cw.due_date})" if cw.due_date else ""
        lines.append(f"• {cw.title}{due}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=coursework_keyboard(course_id, courseworks),
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_courses")
async def back_to_courses(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    try:
        courses = google_service.get_active_courses(user_id)
    except GoogleAuthError as exc:
        await callback.message.edit_text(f"⚠️ {exc}")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выберите курс:",
    )
    await callback.message.edit_reply_markup(reply_markup=courses_keyboard(courses))
    await callback.answer()
