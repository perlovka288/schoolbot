"""
Фоновая синхронизация курсов Google Classroom.

Раньше список курсов подтягивался только когда пользователь сам нажимал
«Мои ДЗ из Classroom» — то есть по сути был разовым «переносом» данных по
запросу. Этот модуль вместо этого раз в SYNC_INTERVAL_SECONDS сам опрашивает
Classroom API по каждому авторизованному пользователю и, как только видит
курс, которого не было при предыдущей проверке, сразу присылает уведомление
в Telegram — без участия пользователя.

Это не настоящий push от Google (Classroom API не поддерживает вебхуки без
отдельной настройки Pub/Sub и Domain-Wide Delegation в Google Workspace,
что недоступно для обычного личного аккаунта), но при небольшом интервале
опроса (по умолчанию 60 секунд) выглядит для пользователя как обновление
в реальном времени.
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot

import config
from services import google_service
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)


def _state_path(user_id: int):
    return config.SYNC_STATE_DIR / f"{user_id}.json"


def _load_seen_course_ids(user_id: int) -> set[str]:
    path = _state_path(user_id)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen_course_ids(user_id: int, course_ids: set[str]) -> None:
    _state_path(user_id).write_text(
        json.dumps(sorted(course_ids), ensure_ascii=False),
        encoding="utf-8",
    )


def _authorized_user_ids() -> list[int]:
    """Все пользователи, у которых есть сохранённый Google-токен."""
    ids: list[int] = []
    for path in config.TOKENS_DIR.glob("*.json"):
        try:
            ids.append(int(path.stem))
        except ValueError:
            continue
    return ids


def clear_state(user_id: int) -> None:
    """Сбрасывает запомненный список курсов пользователя (например, при выходе
    из Google), чтобы после повторной авторизации все текущие курсы не были
    ошибочно разосланы как "новые"."""
    path = _state_path(user_id)
    if path.exists():
        path.unlink()


async def _check_user(bot: Bot, user_id: int) -> None:
    try:
        courses = google_service.get_active_courses(user_id)
    except GoogleAuthError as exc:
        logger.info("Синхронизация: пропуск пользователя %s (%s)", user_id, exc)
        return
    except Exception:  # noqa: BLE001 — сеть/квоты не должны ронять фоновый цикл
        logger.exception("Синхронизация: ошибка Classroom API для %s", user_id)
        return

    current_ids = {c["id"] for c in courses}

    # Первая проверка для этого пользователя: просто запоминаем текущий
    # набор курсов, чтобы не засыпать его уведомлениями обо всём, что у него
    # уже было в Classroom до включения синхронизации.
    if not _state_path(user_id).exists():
        _save_seen_course_ids(user_id, current_ids)
        return

    seen_ids = _load_seen_course_ids(user_id)
    new_ids = current_ids - seen_ids

    if new_ids:
        by_id = {c["id"]: c.get("name", "Без названия") for c in courses}
        names = "\n".join(f"• {by_id[cid]}" for cid in new_ids)
        try:
            await bot.send_message(
                user_id,
                "🆕 В Google Classroom появился новый курс:\n"
                f"{names}\n\n"
                "Нажмите «📚 Мои ДЗ из Classroom», чтобы посмотреть задания.",
            )
        except Exception:  # noqa: BLE001 — например, пользователь заблокировал бота
            logger.exception("Не удалось уведомить пользователя %s", user_id)

    _save_seen_course_ids(user_id, current_ids)


async def run_cron_sync(bot: Bot) -> int:
    """Один проход синхронизации — вызывается извне через /cron/sync."""
    user_ids = _authorized_user_ids()
    for user_id in user_ids:
        await _check_user(bot, user_id)
    return len(user_ids)


async def run_sync_loop(bot: Bot) -> None:
    """Бесконечный фоновый цикл. Запускается один раз через create_task."""
    logger.info(
        "Фоновая синхронизация курсов запущена (опрос каждые %d сек.)",
        config.SYNC_INTERVAL_SECONDS,
    )
    while True:
        for user_id in _authorized_user_ids():
            await _check_user(bot, user_id)
        await asyncio.sleep(config.SYNC_INTERVAL_SECONDS)