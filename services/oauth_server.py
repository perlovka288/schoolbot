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

from aiogram import Bot
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
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>
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


def setup_routes(app: web.Application, bot: Bot) -> None:
    """Регистрирует маршрут /oauth/callback в общем aiohttp-приложении."""

    async def oauth_callback(request: web.Request) -> web.Response:
        params = request.query
        state = params.get("state")
        code = params.get("code")
        error = params.get("error")

        user_id = _pending.pop(state, None) if state else None

        if error:
            logger.info("OAuth: пользователь отклонил доступ (%s)", error)
            return _page(
                "Отменено", "Доступ не предоставлен",
                "Вы отменили авторизацию. Вернитесь в Telegram и попробуйте снова, "
                "если это была ошибка.",
            )

        if not user_id or not code:
            return _page(
                "Ошибка", "⚠️ Ссылка устарела",
                "Эта ссылка уже была использована или устарела. Вернитесь в "
                "Telegram и нажмите «🔑 Авторизоваться в Google» ещё раз.",
            )

        try:
            google_service.exchange_code_and_save(user_id, code)
        except GoogleAuthError as exc:
            logger.warning("OAuth callback: ошибка обмена кода для %s: %s", user_id, exc)
            try:
                await bot.send_message(user_id, f"⚠️ Не получилось авторизоваться: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return _page("Ошибка", "⚠️ Не получилось авторизоваться", str(exc))

        try:
            await bot.send_message(
                user_id,
                "✅ Авторизация прошла успешно! Теперь можно смотреть домашние задания.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить подтверждение пользователю %s", user_id)

        return _page(
            "Готово", "✅ Авторизация прошла успешно",
            "Можете закрыть эту вкладку и вернуться в Telegram.",
        )

    app.router.add_get("/oauth/callback", oauth_callback)
    logger.info("OAuth callback зарегистрирован (redirect_uri=%s)", config.OAUTH_REDIRECT_URI)
