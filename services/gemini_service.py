"""
Работа с Google Gemini API: решение заданий (текст + картинки),
и простой RAG-поиск релевантного контекста по PDF-учебникам из data/books.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from pypdf import PdfReader
from PIL import Image

import config

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)

# Простой in-memory кэш текста учебников, чтобы не парсить PDF при каждом запросе
_books_cache: dict[str, str] | None = None


@dataclass
class SolveResult:
    full_text: str
    short_answer: str


def _load_books_text() -> dict[str, str]:
    """Извлекает текст из всех PDF в data/books (один раз, кэшируется)."""
    global _books_cache
    if _books_cache is not None:
        return _books_cache

    books: dict[str, str] = {}
    for pdf_path in config.BOOKS_DIR.glob("*.pdf"):
        try:
            reader = PdfReader(str(pdf_path))
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            books[pdf_path.name] = "\n".join(text_parts)
            logger.info("Загружен учебник: %s (%d страниц)", pdf_path.name, len(reader.pages))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось прочитать %s: %s", pdf_path.name, exc)

    _books_cache = books
    return books


def refresh_books_cache() -> int:
    """Принудительно пересканировать папку с книгами. Возвращает число книг."""
    global _books_cache
    _books_cache = None
    return len(_load_books_text())


def find_relevant_book_context(query: str, max_chars: int = config.MAX_BOOK_CONTEXT_CHARS) -> str:
    """
    Очень простой лексический поиск релевантных фрагментов учебников:
    ищем абзацы, где встречаются ключевые слова запроса, и берём
    ближайший контекст. Это не векторный поиск, но не требует внешней
    базы данных и работает "из коробки".
    """
    books = _load_books_text()
    if not books:
        return ""

    keywords = [w.lower() for w in re.findall(r"\w{4,}", query) if w.isalpha()]
    if not keywords:
        return ""

    scored_chunks: list[tuple[int, str]] = []
    for book_name, text in books.items():
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        for para in paragraphs:
            para_lower = para.lower()
            score = sum(para_lower.count(kw) for kw in keywords)
            if score > 0:
                scored_chunks.append((score, f"[{book_name}]\n{para.strip()}"))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    context_parts: list[str] = []
    total_len = 0
    for _, chunk in scored_chunks:
        if total_len + len(chunk) > max_chars:
            break
        context_parts.append(chunk)
        total_len += len(chunk)

    return "\n\n---\n\n".join(context_parts)


def _extract_short_answer(full_text: str) -> str:
    match = re.search(r"Ответ:\s*(.+)", full_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # fallback — последняя непустая строка
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    return lines[-1] if lines else full_text[:200]


def solve_task(
    task_text: str,
    image_paths: list[Path] | None = None,
) -> SolveResult:
    """
    Отправляет задание (текст + опционально картинки) в Gemini вместе
    с релевантным контекстом из учебников, возвращает полное объяснение
    и краткий ответ.
    """
    context = find_relevant_book_context(task_text)

    prompt_parts: list = []

    text_prompt = task_text.strip()
    if context:
        text_prompt = (
            f"Вот релевантный фрагмент учебника, используй его как справочный материал:\n\n"
            f"{context}\n\n---\n\nЗадание ученика:\n{text_prompt}"
        )
    prompt_parts.append(text_prompt)

    for img_path in image_paths or []:
        try:
            prompt_parts.append(Image.open(img_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось открыть изображение %s: %s", img_path, exc)

    try:
        response = _client.models.generate_content(
            model=config.GEMINI_MODEL_NAME,
            contents=prompt_parts,
            config=types.GenerateContentConfig(
                system_instruction=config.SYSTEM_PROMPT,
            ),
        )
        full_text = response.text
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка Gemini API")
        raise RuntimeError(f"Ошибка при обращении к Gemini API: {exc}") from exc

    short_answer = _extract_short_answer(full_text)
    return SolveResult(full_text=full_text, short_answer=short_answer)


def answer_book_question(question: str) -> str:
    """Отвечает на прямой вопрос ученика по материалам учебников."""
    context = find_relevant_book_context(question)
    if not context:
        prompt = (
            f"В папке с учебниками не нашлось релевантного материала. "
            f"Ответь на вопрос ученика на основе общих знаний:\n\n{question}"
        )
    else:
        prompt = (
            f"Используй следующий фрагмент учебника, чтобы ответить на вопрос ученика.\n\n"
            f"Фрагмент учебника:\n{context}\n\n---\n\nВопрос ученика:\n{question}"
        )

    try:
        response = _client.models.generate_content(
            model=config.GEMINI_MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=config.SYSTEM_PROMPT,
            ),
        )
        return response.text
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка Gemini API")
        raise RuntimeError(f"Ошибка при обращении к Gemini API: {exc}") from exc
