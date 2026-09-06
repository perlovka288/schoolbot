"""
Решение конкретного домашнего задания:
скачиваем вложения -> отправляем в Gemini -> получаем объяснение ->
рендерим "тетрадный лист" с ответом -> отправляем пользователю.

Также здесь же — режим "Задать вопрос по учебнику" (текст/фото вопроса
без привязки к Classroom).
"""

import logging
import shutil
from pathlib import Path
from uuid import uuid4

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile

import config
from bot.keyboards.keyboards import BTN_ASK_BOOK, main_menu_keyboard, cancel_keyboard
from services import google_service, gemini_service, notebook_render
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)
router = Router(name="solver")


class AskBookStates(StatesGroup):
    waiting_for_question = State()


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


async def _solve_and_reply(message: Message, task_text: str, image_paths: list[Path]) -> None:
    await message.answer("🤔 Решаю задание, это может занять немного времени…")

    try:
        result = gemini_service.solve_task(task_text, image_paths)
    except RuntimeError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await message.answer(
        f"📝 Решение:\n\n{result.full_text}",
    )

    try:
        pages = notebook_render.render_answer_pages(
            result.full_text, title="Решение"
        )
        for page_path in pages:
            await message.answer_photo(FSInputFile(page_path))
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сгенерировать тетрадный лист")
        await message.answer(
            "(Не удалось сгенерировать изображение тетрадного листа, "
            "но текстовое решение выше уже готово.)"
        )


@router.callback_query(F.data.startswith("cw:"))
async def solve_coursework(callback: CallbackQuery, state: FSMContext) -> None:
    _, course_id, coursework_id = callback.data.split(":", 2)
    user_id = callback.from_user.id

    data = await state.get_data()
    cw_store = data.get("courseworks", {})
    cw_info = cw_store.get(coursework_id)

    if not cw_info:
        await callback.answer("Данные о задании устарели, откройте список заново.", show_alert=True)
        return

    await callback.answer("Начинаю решать задание…")
    await callback.message.answer(f"📌 Задание: {cw_info['title']}")

    task_dir = config.TMP_DIR / f"cw_{coursework_id}_{uuid4().hex[:8]}"
    image_paths: list[Path] = []

    try:
        downloaded = google_service.download_material_files(
            user_id, cw_info.get("materials", []), task_dir
        )
        image_paths = [p for p in downloaded if p.suffix.lower() in IMAGE_EXTENSIONS]
        # PDF-вложения пока не конвертируются в изображения постранично —
        # Gemini 1.5 Flash также неплохо работает по тексту описания задания.
    except GoogleAuthError as exc:
        await callback.message.answer(f"⚠️ Не удалось скачать вложения: {exc}")
    finally:
        pass

    task_text = (
        f"Тема/курс: {cw_info['course_name']}\n"
        f"Задание: {cw_info['title']}\n"
        f"Описание: {cw_info.get('description') or '(описание отсутствует)'}"
    )

    await _solve_and_reply(callback.message, task_text, image_paths)

    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)


@router.message(F.text == BTN_ASK_BOOK)
async def ask_book_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AskBookStates.waiting_for_question)
    await message.answer(
        "Напишите вопрос или пришлите задание текстом/фотографией — "
        "я поищу ответ в загруженных учебниках и решу его.",
        reply_markup=cancel_keyboard(),
    )


@router.callback_query(F.data == "cancel", AskBookStates.waiting_for_question)
async def cancel_ask_book(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.message(AskBookStates.waiting_for_question, F.photo)
async def ask_book_with_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    img_path = config.TMP_DIR / f"question_{uuid4().hex}.jpg"
    await bot.download_file(file.file_path, destination=img_path)

    task_text = message.caption or "Реши задание, изображённое на фотографии."

    await _solve_and_reply(message, task_text, [img_path])

    img_path.unlink(missing_ok=True)


@router.message(AskBookStates.waiting_for_question, F.text)
async def ask_book_with_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _solve_and_reply(message, message.text, [])
