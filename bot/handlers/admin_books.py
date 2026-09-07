"""
Загрузка учебников в базу знаний ИИ — ТОЛЬКО для админов (config.ADMIN_IDS).

Два способа добавить учебник:
1. Прислать сам PDF-файл документом.
2. Прислать ссылку на страницу учебника (например, pidruchnyk.com.ua) —
   бот попробует сам найти на странице прямую ссылку на PDF и скачать его.
   Многие такие сайты — это просто HTML-страница с кнопкой "Завантажити",
   ведущей на настоящий .pdf; именно такую ссылку бот и ищет. Если найти
   не получилось (например, книга открывается только во встроенной
   читалке без прямого файла) — бот честно об этом сообщает, тогда нужно
   скачать PDF вручную и прислать его файлом (способ 1).

После добавления учебник сразу попадает в data/books и участвует в
поиске контекста (services/gemini_service.find_relevant_book_context)
при решении заданий и ответах на вопросы — без перезапуска бота.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import aiohttp
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import config
from bot.keyboards.keyboards import BTN_ADMIN_UPLOAD_BOOK, main_menu_keyboard, cancel_keyboard
from services import gemini_service

logger = logging.getLogger(__name__)
router = Router(name="admin_books")

_HTTP_HEADERS = {
    # Некоторые сайты отдают 403 роботам с дефолтным User-Agent aiohttp.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

_PDF_LINK_RE = re.compile(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class AdminBookStates(StatesGroup):
    waiting_for_book = State()


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w .-]", "_", name, flags=re.UNICODE).strip(" ._") or uuid4().hex
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:150]


@router.message(F.text == BTN_ADMIN_UPLOAD_BOOK)
async def start_upload(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if not _is_admin(user_id):
        # Кнопка и так скрыта для не-админов — это на случай, если кто-то
        # угадает текст кнопки вручную.
        return

    await state.set_state(AdminBookStates.waiting_for_book)
    await message.answer(
        "Надішліть підручник одним із способів:\n\n"
        "📎 <b>PDF-файлом</b> — просто прикріпіть файл сюди.\n\n"
        "🔗 <b>Посиланням</b> на сторінку підручника (наприклад, "
        "pidruchnyk.com.ua) — я спробую сам знайти на сторінці пряме "
        "посилання на PDF і завантажити його. Якщо не вийде — надішліть "
        "PDF файлом вручну.",
        reply_markup=cancel_keyboard(),
    )


@router.callback_query(F.data == "cancel", AdminBookStates.waiting_for_book)
async def cancel_upload(callback, state: FSMContext) -> None:  # noqa: ANN001
    await state.clear()
    await callback.message.edit_text("Скасовано.")
    await callback.message.answer(
        "Головне меню:", reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()


async def _save_book_bytes(data: bytes, suggested_name: str) -> Path:
    target = config.BOOKS_DIR / _safe_filename(suggested_name)
    # Не перезаписываем случайно другой учебник с таким же именем.
    if target.exists():
        target = target.with_stem(target.stem + "_" + uuid4().hex[:6])
    target.write_bytes(data)
    return target


@router.message(AdminBookStates.waiting_for_book, F.document)
async def upload_book_file(message: Message, state: FSMContext, bot) -> None:  # noqa: ANN001
    await state.clear()
    doc = message.document

    if not (doc.file_name or "").lower().endswith(".pdf"):
        await message.answer(
            "⚠️ Поки приймаю тільки PDF. Сконвертуйте файл у PDF і надішліть знову.",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        return

    file = await bot.get_file(doc.file_id)
    buf = await bot.download_file(file.file_path)
    target = await _save_book_bytes(buf.read(), doc.file_name)

    count = gemini_service.refresh_books_cache()
    await message.answer(
        f"✅ Підручник збережено: {target.name}\n"
        f"Усього підручників у базі тепер: {count}.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


async def _find_pdf_url(session: aiohttp.ClientSession, page_url: str) -> tuple[str | None, str]:
    """Возвращает (прямая_ссылка_на_pdf_или_None, заголовок_страницы_для_имени_файла)."""
    async with session.get(page_url, headers=_HTTP_HEADERS, timeout=_HTTP_TIMEOUT) as resp:
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" in content_type.lower():
            # Ссылка сама по себе уже ведёт на PDF.
            return page_url, urlparse(page_url).path.rsplit("/", 1)[-1] or "book"

        html = await resp.text(errors="ignore")

    title_match = _TITLE_RE.search(html)
    page_title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "book"

    candidates = _PDF_LINK_RE.findall(html)
    if not candidates:
        return None, page_title

    pdf_url = urljoin(page_url, candidates[0])
    return pdf_url, page_title


@router.message(AdminBookStates.waiting_for_book, F.text)
async def upload_book_link(message: Message, state: FSMContext) -> None:
    await state.clear()
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.answer(
            "⚠️ Це не схоже на посилання. Надішліть посилання (починається з http/https) "
            "або сам PDF-файл.",
            reply_markup=main_menu_keyboard(message.from_user.id),
        )
        return

    status_msg = await message.answer("🔎 Шукаю PDF на сторінці…")

    try:
        async with aiohttp.ClientSession() as session:
            pdf_url, page_title = await _find_pdf_url(session, url)

            if not pdf_url:
                await status_msg.edit_text(
                    "⚠️ На цій сторінці не знайшлося прямого посилання на PDF "
                    "(книга відкривається лише у вбудованій читалці без "
                    "файлу для завантаження). Завантажте PDF вручну (якщо є "
                    "кнопка «Завантажити»/«Скачать») і надішліть його сюди "
                    "файлом.",
                )
                return

            async with session.get(
                pdf_url, headers=_HTTP_HEADERS, timeout=_HTTP_TIMEOUT
            ) as pdf_resp:
                if pdf_resp.status != 200:
                    await status_msg.edit_text(
                        f"⚠️ Не вдалося завантажити PDF за знайденим посиланням "
                        f"(HTTP {pdf_resp.status}): {pdf_url}"
                    )
                    return
                pdf_bytes = await pdf_resp.read()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось скачать учебник по ссылке %s", url)
        await status_msg.edit_text(f"⚠️ Помилка при завантаженні сторінки: {exc}")
        return

    if not pdf_bytes or len(pdf_bytes) < 1024:
        await status_msg.edit_text("⚠️ Завантажився порожній або надто маленький файл — схоже, це не книга.")
        return

    target = await _save_book_bytes(pdf_bytes, page_title)
    count = gemini_service.refresh_books_cache()

    await status_msg.edit_text(
        f"✅ Підручник завантажено та збережено: {target.name}\n"
        f"Усього підручників у базі тепер: {count}."
    )
    await message.answer("Головне меню:", reply_markup=main_menu_keyboard(message.from_user.id))
