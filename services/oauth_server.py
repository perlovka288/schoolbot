"""
Обработчик OAuth-редиректа от Google — часть общего веб-приложения бота.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiohttp import web

import config
from services import google_service
from services.google_service import GoogleAuthError

logger = logging.getLogger(__name__)

_pending: dict[str, int] = {}

_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 3em;">
<h2>{heading}</h2>
<p>{message}</p>
</body></html>"""


def register_pending(state: str, user_id: int) -> None:
    _pending[state] = user_id


def _page(title: str, heading: str, message: str) -> web.Response:
    return web.Response(
        text=_PAGE.format(title=title, heading=heading, message=message),
        content_type="text/html",
    )


def setup_routes(app: web.Application, bot: Bot) -> None:
    async def oauth_callback(request: web.Request) -> web.Response:
        params = request.query
        state = params.get("state")
        code = params.get("code")
        error = params.get("error")

        user_id = _pending.pop(state, None) if state else None

        if error:
            return _page(
                "Отменено", "Доступ не предоставлен",
                "Вы отменили авторизацию. Вернитесь в Telegram и попробуйте снова.",
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
            try:
                await bot.send_message(user_id, f"⚠️ Не получилось авторизоваться: {exc}")
            except Exception:
                pass
            return _page("Ошибка", "⚠️ Не получилось авторизоваться", str(exc))

        try:
            await bot.send_message(
                user_id,
                "✅ Авторизация прошла успешно! Теперь можно смотреть домашние задания.",
            )
        except Exception:
            logger.exception("Не удалось отправить подтверждение пользователю %s", user_id)

        return _page(
            "Готово", "✅ Авторизация прошла успешно",
            "Можете закрыть эту вкладку и вернуться в Telegram.",
        )

    app.router.add_get("/oauth/callback", oauth_callback)