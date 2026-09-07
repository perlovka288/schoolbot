"""
Обработчик OAuth-редиректа от Google — часть общего веб-приложения бота.

Раньше пользователь после разрешения доступа получал редирект на
http://localhost, куда никто не слушал — страница просто зависала
(бесконечная загрузка), и код авторизации приходилось копировать
руками из адресной строки и присылать боту отдельным сообщением.

Теперь маршрут /oauth/callback регистрируется в том же aiohttp-приложении,
что и вебхук Telegram (см. main.py): Google сам присылает код прямо сюда,
мы тут же обмениваем его на токен и пишем пользователю в Telegram, что
всё готово. Копировать ничего не нужно.

Локально (USE_WEBHOOK=false) это тоже работает "из коробки", потому что
браузер и сервер бота находятся на одной машине (localhost). После
деплоя на хостинг с публичным адресом поменяйте OAUTH_REDIRECT_BASE_URL
в .env и redirect URI в Google Cloud Console — см. комментарий в config.py.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiohttp import web

import config
from services import google_service
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)

# oauth "state" -> telegram user_id, кому вернуть результат авторизации.
# Хранится в памяти процесса: колбэк-сервер и бот работают в одном процессе,
# так что до перезапуска бота этого достаточно.
_pending: dict[str, int] = {}

_PAGE = """<!doctype html>
<html lang="uk"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 3em;">
<h2>{heading}</h2>
<p>{message}</p>
</body></html>"""


def register_pending(state: str, user_id: int) -> None:
    """Запоминает, какому пользователю Telegram принадлежит этот oauth state."""
    _pending[state] = user_id


def _page(title: str, heading: str, message: str) -> web.Response:
    return web.Response(
        text=_PAGE.format(title=title, heading=heading, message=message),
        content_type="text/html",
    )


def setup_routes(app: web.Application, bot: Bot, dp: Dispatcher) -> None:
    """Регистрирует маршрут /oauth/callback в общем aiohttp-приложении."""

    async def _clear_fsm_state(user_id: int) -> None:
        # Когда пользователь жмёт «Авторизоваться», бот переводит его в
        # состояние AuthStates.waiting_for_code (см. bot/handlers/auth.py) —
        # это нужно на случай, если колбэк-сервер недоступен и код придётся
        # вставлять вручную. Раз колбэк отработал сам, это состояние нужно
        # снять — иначе бот продолжит принимать ЛЮБОЕ следующее сообщение
        # пользователя (даже «Мои ДЗ») за код авторизации и будет пытаться
        # обменять его на токен, получая "Malformed auth code".
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        state = FSMContext(storage=dp.storage, key=key)
        await state.clear()

    async def oauth_callback(request: web.Request) -> web.Response:
        params = request.query
        state = params.get("state")
        code = params.get("code")
        error = params.get("error")

        user_id = _pending.pop(state, None) if state else None

        if error:
            logger.info("OAuth: пользователь отклонил доступ (%s)", error)
            if user_id:
                await _clear_fsm_state(user_id)
            return _page(
                "Скасовано", "Доступ не надано",
                "Ви скасували авторизацію. Поверніться в Telegram і спробуйте "
                "знову, якщо це була помилка.",
            )

        if not user_id or not code:
            return _page(
                "Помилка", "⚠️ Посилання застаріло",
                "Це посилання вже було використане або застаріло. Поверніться "
                "в Telegram і натисніть «🔑 Авторизуватися в Google» ще раз.",
            )

        try:
            google_service.exchange_code_and_save(user_id, code)
        except GoogleAuthError as exc:
            logger.warning("OAuth callback: ошибка обмена кода для %s: %s", user_id, exc)
            await _clear_fsm_state(user_id)
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ Не вдалося авторизуватися: {exc}\n"
                    "Натисніть «🔑 Авторизуватися в Google» ще раз.",
                )
            except Exception:  # noqa: BLE001
                pass
            return _page("Помилка", "⚠️ Не вдалося авторизуватися", str(exc))

        await _clear_fsm_state(user_id)

        try:
            await bot.send_message(
                user_id,
                "✅ Авторизація пройшла успішно! Тепер можна переглядати домашні завдання.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить подтверждение пользователю %s", user_id)

        return _page(
            "Готово", "✅ Авторизація пройшла успішно",
            "Можете закрити цю вкладку і повернутися в Telegram.",
        )

    app.router.add_get("/oauth/callback", oauth_callback)
    logger.info("OAuth callback зарегистрирован (redirect_uri=%s)", config.OAUTH_REDIRECT_URI)
