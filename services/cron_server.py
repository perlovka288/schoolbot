"""
Заглушка для внешнего крона: GET /cron/sync

Раньше синхронизация с Classroom была бесконечным asyncio-циклом внутри
процесса бота (services/sync_service.run_sync_loop) — он крутился 24/7,
не давая бесплатному сервису "уснуть" между запросами, и в режиме
вебхука сводил на нет всю экономию часов.

Теперь вместо цикла — один лёгкий HTTP-эндпоинт. Его нужно раз в
несколько минут дёргать СНАРУЖИ любым бесплатным внешним кроном
(cron-job.org, UptimeRobot, GitHub Actions по расписанию, Render Cron
Job и т.п.) — рекомендуемый интервал такой же, как раньше стоял для
фонового цикла (config.SYNC_INTERVAL_SECONDS, по умолчанию 60 сек).

Сервис при этом всё остальное время может простаивать/спать и
просыпаться только по входящему запросу — от Telegram (вебхук) или от
крона (этот эндпоинт).

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
    """Регистрирует маршрут /cron/sync в общем aiohttp-приложении."""

    async def cron_sync(request: web.Request) -> web.Response:
        key = request.query.get("key") or request.headers.get("X-Cron-Key", "")
        if config.CRON_SECRET and key != config.CRON_SECRET:
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)

        checked = await sync_service.run_cron_sync(bot)
        logger.info("Cron /sync: проверено пользователей — %d", checked)
        return web.json_response({"ok": True, "checked_users": checked})

    app.router.add_get("/cron/sync", cron_sync)
    logger.info(
        "Cron-эндпоинт зарегистрирован: GET /cron/sync "
        "(дёргайте его снаружи раз в %d сек.)",
        config.SYNC_INTERVAL_SECONDS,
    )
