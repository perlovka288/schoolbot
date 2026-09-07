"""
Работа с Google Gemini API: решение заданий (текст + картинки),
и простой RAG-поиск релевантного контекста по PDF-учебникам из data/books.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pypdf import PdfReader
from PIL import Image

import config

logger = logging.getLogger(__name__)

# По клиенту на каждый ключ из config.GEMINI_API_KEYS (обычно один, но
# можно указать несколько через запятую в .env — см. config.py).
_clients: list[genai.Client] = [genai.Client(api_key=key) for key in config.GEMINI_API_KEYS]

# Простой in-memory кэш текста учебников, чтобы не парсить PDF при каждом запросе
_books_cache: dict[str, str] | None = None

# Сколько раз повторять запрос на ОДНОМ ключе, если модель временно
# перегружена (503 UNAVAILABLE — "This model is currently experiencing
# high demand"). Это не ошибка конфигурации, а обычная временная
# перегрузка на стороне Google, которая обычно проходит за секунды.
_MAX_RETRIES_PER_KEY = 3
_RETRY_DELAY_SECONDS = 4


def _generate_with_retry(**kwargs):
    """Пробует все доступные ключи Gemini по очереди (в случайном порядке),
    на каждом — несколько попыток при временной перегрузке (503/500).
    Если у ключа кончилась квота или он невалиден (4xx) — сразу переходит
    к следующему ключу, не тратя время на повтор того же ключа.

    Если ВСЕ ключи упёрлись в перегрузку (503) на основной модели —
    дополнительно пробует резервные модели из config.GEMINI_FALLBACK_MODELS
    (тоже перебирая все ключи для каждой) прежде чем сдаться. Это отдельный
    механизм от смены ключей: перегружена обычно конкретная модель, а не
    аккаунт/ключ, так что соседняя модель часто доступна сразу."""
    models_order = [kwargs.get("model")] + [
        m for m in config.GEMINI_FALLBACK_MODELS if m != kwargs.get("model")
    ]

    last_exc: Exception | None = None
    for model_idx, model_name in enumerate(models_order, start=1):
        call_kwargs = {**kwargs, "model": model_name}

        clients_order = _clients[:]
        random.shuffle(clients_order)

        all_keys_overloaded = True  # остаётся True, только если КАЖДЫЙ ключ вернул 503/500
        for client_idx, client in enumerate(clients_order, start=1):
            for attempt in range(1, _MAX_RETRIES_PER_KEY + 1):
                try:
                    response = client.models.generate_content(**call_kwargs)
                    if model_idx > 1:
                        logger.info(
                            "Gemini API: основная модель была перегружена, "
                            "успешно ответила резервная модель %s", model_name,
                        )
                    return response
                except ServerError as exc:
                    # 503/500 — временная перегрузка модели, есть смысл повторить
                    # тем же ключом ещё раз, прежде чем переходить к следующему.
                    last_exc = exc
                    logger.warning(
                        "Gemini API временно недоступен (модель %s, ключ %d/%d, "
                        "попытка %d/%d): %s",
                        model_name, client_idx, len(clients_order), attempt,
                        _MAX_RETRIES_PER_KEY, exc,
                    )
                    if attempt < _MAX_RETRIES_PER_KEY:
                        # Небольшой разброс задержки (jitter), чтобы при
                        # нескольких одновременных запросах они не долбили
                        # API синхронными залпами.
                        time.sleep(_RETRY_DELAY_SECONDS + random.uniform(0, 2))
                except ClientError as exc:
                    # 4xx — например, у этого конкретного ключа кончилась квота
                    # (429) или он невалиден. Повторять тем же ключом бессмысленно,
                    # сразу пробуем следующий, если он есть. Это НЕ перегрузка
                    # модели, так что смена модели тут не поможет — не считаем
                    # ключ "перегруженным" для целей перехода на fallback-модель.
                    all_keys_overloaded = False
                    last_exc = exc
                    logger.warning(
                        "Gemini API отклонил запрос по ключу %d/%d (модель %s): %s",
                        client_idx, len(clients_order), model_name, exc,
                    )
                    break

        if not all_keys_overloaded:
            # Хотя бы один ключ дал не-503 ошибку (например, у всех кончилась
            # квота, 429) — переход на другую модель тут не поможет, не тратим
            # время на её перебор.
            break

    raise last_exc


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
    # Основной шаблон — украинский ("Відповідь:"), но на всякий случай
    # (если модель вдруг ответит по-старому) оставляем и русский вариант.
    match = re.search(r"Відповідь:\s*(.+)", full_text, re.IGNORECASE)
    if not match:
        match = re.search(r"Ответ:\s*(.+)", full_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # fallback — последняя непустая строка
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    return lines[-1] if lines else full_text[:200]


def _friendly_gemini_error(exc: Exception) -> str:
    """Превращает сырую ошибку Gemini API (часто — простыня JSON) в
    короткое понятное сообщение на украинском для пользователя."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return (
            "Вичерпано денний ліміт запитів до Gemini API (безкоштовна квота). "
            "Спробуйте, будь ласка, трохи пізніше — квота оновлюється щодня. "
            "Якщо це повторюється часто: квота Gemini рахується на Google Cloud "
            "ПРОЄКТ, а не на окремий ключ — якщо всі ваші ключі в GEMINI_API_KEY "
            "створені в одному й тому ж проєкті, вони діляться одним і тим самим "
            "лімітом і не допомагають. Потрібен ключ зі СПРАВДІ іншого проєкту "
            "(або іншого Google-акаунта)."
        )
    if "UNAVAILABLE" in text or "503" in text or "500" in text:
        return (
            "Gemini API зараз тимчасово перевантажений. Спробуйте, будь "
            "ласка, ще раз за хвилину."
        )
    return f"Помилка при зверненні до Gemini API: {exc}"


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
            f"Ось релевантний фрагмент підручника, використай його як довідковий матеріал:\n\n"
            f"{context}\n\n---\n\nЗавдання учня:\n{text_prompt}"
        )
    prompt_parts.append(text_prompt)

    for img_path in image_paths or []:
        try:
            prompt_parts.append(Image.open(img_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось открыть изображение %s: %s", img_path, exc)

    try:
        response = _generate_with_retry(
            model=config.GEMINI_MODEL_NAME,
            contents=prompt_parts,
            config=types.GenerateContentConfig(
                system_instruction=config.SYSTEM_PROMPT,
            ),
        )
        full_text = response.text
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка Gemini API")
        raise RuntimeError(_friendly_gemini_error(exc)) from exc

    short_answer = _extract_short_answer(full_text)
    return SolveResult(full_text=full_text, short_answer=short_answer)


def answer_book_question(question: str) -> str:
    """Отвечает на прямой вопрос ученика по материалам учебников."""
    context = find_relevant_book_context(question)
    if not context:
        prompt = (
            f"У папці з підручниками не знайшлося релевантного матеріалу. "
            f"Дай відповідь на запитання учня на основі загальних знань:\n\n{question}"
        )
    else:
        prompt = (
            f"Використай наступний фрагмент підручника, щоб відповісти на запитання учня.\n\n"
            f"Фрагмент підручника:\n{context}\n\n---\n\nЗапитання учня:\n{question}"
        )

    try:
        response = _generate_with_retry(
            model=config.GEMINI_MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=config.SYSTEM_PROMPT,
            ),
        )
        return response.text
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка Gemini API")
        raise RuntimeError(_friendly_gemini_error(exc)) from exc
