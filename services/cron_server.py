"""
Заглушка для внешнего крона: GET /cron/sync

Вместо бесконечного фонового цикла синхронизации (который бы не давал
бесплатному сервису "уснуть" между запросами) — один лёгкий HTTP-
эндпоинт. Дёргайте его снаружи раз в несколько минут любым бесплатным
кроном (cron-job.org, UptimeRobot, GitHub Actions по расписанию и т.п.).

Пример вызова:
    GET https://ваш-адрес.onrender.com/cron/sync?key=ВАШ_CRON_SECRET
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiohttp import web

import config
from services import sync_service

logger = logging.getLogger(__name__)


def setup_routes(app: web.Application, bot: Bot) -> None:
    async def cron_sync(request: web.Request) -> web.Response:
        key = request.query.get("key") or request.headers.get("X-Cron-Key", "")
        if config.CRON_SECRET and key != config.CRON_SECRET:
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)

        checked = await sync_service.run_cron_sync(bot)
        logger.info("Cron /sync: проверено пользователей — %d", checked)
        return web.json_response({"ok": True, "checked_users": checked})

    app.router.add_get("/cron/sync", cron_sync)
    logger.info("Cron-эндпоинт зарегистрирован: GET /cron/sync")