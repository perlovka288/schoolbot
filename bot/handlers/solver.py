"""
Розв'язання конкретного домашнього завдання:
завантажуємо вкладення -> надсилаємо в Gemini -> отримуємо пояснення ->
малюємо "аркуш із зошита" з відповіддю -> надсилаємо користувачу.

Тут же — режим "Поставити запитання по підручнику" (текст/фото запитання
без прив'язки до Classroom).
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
from services import attachment_reader, google_service, gemini_service, notebook_render
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)
router = Router(name="solver")


class AskBookStates(StatesGroup):
    waiting_for_question = State()


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


async def _solve_and_reply(
    message: Message,
    task_text: str,
    image_paths: list[Path],
    notebook_title: str | None = None,
) -> None:
    await message.answer("🤔 Розв'язую завдання, це може зайняти трохи часу…")

    try:
        result = gemini_service.solve_task(task_text, image_paths)
    except RuntimeError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await message.answer(
        f"📝 Розв'язання:\n\n{result.full_text}",
    )

    try:
        pages = notebook_render.render_answer_pages(
            result.full_text, title=notebook_title
        )
        for page_path in pages:
            await message.answer_photo(FSInputFile(page_path))
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сгенерировать тетрадный лист")
        await message.answer(
            "(Не вдалося згенерувати зображення аркуша із зошита, "
            "але текстове розв'язання вище вже готове.)"
        )


@router.callback_query(F.data.startswith("solve:"))
async def solve_coursework(callback: CallbackQuery, state: FSMContext) -> None:
    _, course_id, coursework_id = callback.data.split(":", 2)
    user_id = callback.from_user.id

    data = await state.get_data()
    cw_store = data.get("courseworks", {})
    cw_info = cw_store.get(coursework_id)

    if not cw_info:
        await callback.answer("Дані про завдання застаріли, відкрийте список знову.", show_alert=True)
        return

    await callback.answer("Починаю розв'язувати завдання…")
    await callback.message.answer(f"📌 Завдання: {cw_info['title']}")

    task_dir = config.TMP_DIR / f"cw_{coursework_id}_{uuid4().hex[:8]}"
    image_paths: list[Path] = []
    attachments_text = ""

    try:
        downloaded = google_service.download_material_files(
            user_id, cw_info.get("materials", []), task_dir
        )
        image_paths = [p for p in downloaded if p.suffix.lower() in IMAGE_EXTENSIONS]
        document_paths = [
            p for p in downloaded if p.suffix.lower() in attachment_reader.DOCUMENT_EXTENSIONS
        ]
        if document_paths:
            attachments_text = attachment_reader.extract_all(document_paths)

            # Если из PDF не удалось извлечь текст (например, это скан
            # домашки без текстового слоя) — рендерим его страницы как
            # картинки и отдаём их Gemini напрямую, а не оставляем задание
            # без условия. См. attachment_reader.pdf_pages_as_images.
            pdf_paths = [p for p in document_paths if p.suffix.lower() == ".pdf"]
            extracted_pdf_names = set()
            for block in attachments_text.split("--- Файл: ")[1:]:
                extracted_pdf_names.add(block.split(" ---", 1)[0])
            for pdf_path in pdf_paths:
                if pdf_path.name in extracted_pdf_names:
                    continue
                image_paths.extend(
                    attachment_reader.pdf_pages_as_images(pdf_path, task_dir)
                )
    except GoogleAuthError as exc:
        await callback.message.answer(f"⚠️ Не вдалося завантажити вкладення: {exc}")
    finally:
        pass

    task_text = (
        f"Тема/курс: {cw_info['course_name']}\n"
        f"Завдання: {cw_info['title']}\n"
        f"Опис: {cw_info.get('description') or '(опис відсутній)'}"
    )
    if attachments_text:
        task_text += f"\n\nВміст прикріплених файлів:\n{attachments_text}"
    if image_paths:
        task_text += (
            "\n\n(До завдання також додані зображення/скан сторінок — "
            "умову дивись на них.)"
        )

    await _solve_and_reply(callback.message, task_text, image_paths, notebook_title=cw_info["title"])

    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)


@router.message(F.text == BTN_ASK_BOOK)
async def ask_book_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AskBookStates.waiting_for_question)
    await message.answer(
        "Напишіть запитання або надішліть завдання текстом/фотографією — "
        "я пошукаю відповідь у завантажених підручниках і розв'яжу його.",
        reply_markup=cancel_keyboard(),
    )


@router.callback_query(F.data == "cancel", AskBookStates.waiting_for_question)
async def cancel_ask_book(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Скасовано.")
    await callback.message.answer("Головне меню:", reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.message(AskBookStates.waiting_for_question, F.photo)
async def ask_book_with_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    img_path = config.TMP_DIR / f"question_{uuid4().hex}.jpg"
    await bot.download_file(file.file_path, destination=img_path)

    task_text = message.caption or "Розв'яжи завдання, зображене на фотографії."

    await _solve_and_reply(message, task_text, [img_path])

    img_path.unlink(missing_ok=True)


@router.message(AskBookStates.waiting_for_question, F.document)
async def ask_book_with_document(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()

    doc = message.document
    suffix = Path(doc.file_name or "").suffix.lower()
    if suffix not in attachment_reader.DOCUMENT_EXTENSIONS:
        await message.answer(
            "⚠️ Такий тип файлу поки не читаю (підтримую PDF, PPTX, DOCX, TXT). "
            "Надішліть текст або фото завдання.",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        return

    file = await bot.get_file(doc.file_id)
    file_path = config.TMP_DIR / f"question_{uuid4().hex}{suffix}"
    await bot.download_file(file.file_path, destination=file_path)

    extracted = attachment_reader.extract_text(file_path)

    image_paths: list[Path] = []
    task_text: str

    if extracted:
        caption = message.caption or "Розв'яжи завдання з прикріпленого файлу."
        task_text = f"{caption}\n\nВміст файлу «{doc.file_name}»:\n{extracted}"
    elif suffix == ".pdf":
        # Похоже на скан без текстового слоя — пробуем отрендерить
        # страницы как картинки вместо того, чтобы сразу сдаваться.
        image_paths = attachment_reader.pdf_pages_as_images(file_path, config.TMP_DIR)
        if not image_paths:
            file_path.unlink(missing_ok=True)
            await message.answer(
                "⚠️ Не вдалося прочитати файл — можливо, він пошкоджений. "
                "Спробуйте надіслати фото завдання окремим знімком.",
                reply_markup=main_menu_keyboard(message.from_user.id),
            )
            return
        task_text = message.caption or (
            f"Розв'яжи завдання зі сторінок файлу «{doc.file_name}» (додані як зображення)."
        )
    else:
        file_path.unlink(missing_ok=True)
        await message.answer(
            "⚠️ Не вдалося витягти текст із файлу — можливо, він пошкоджений "
            "або це формат без текстового шару.",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        return

    await _solve_and_reply(message, task_text, image_paths)

    file_path.unlink(missing_ok=True)
    for img_path in image_paths:
        img_path.unlink(missing_ok=True)


@router.message(AskBookStates.waiting_for_question, F.text)
async def ask_book_with_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _solve_and_reply(message, message.text, [])
