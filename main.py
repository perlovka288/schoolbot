"""
Точка входа: запуск Telegram-бота.

Два режима работы (переключаются config.USE_WEBHOOK / .env USE_WEBHOOK):

- USE_WEBHOOK=false (по умолчанию, локальная разработка) — классический
  long polling: бот сам постоянно спрашивает Telegram "есть что новое?".
  Плюс к нему поднимается фоновый asyncio-цикл синхронизации Classroom
  (sync_service.run_sync_loop).

- USE_WEBHOOK=true (продакшн, например Render) — Telegram сам присылает
  апдейты HTTP-запросом на /webhook/<секрет>. Это не требует держать
  постоянное соединение, поэтому сервис может простаивать/спать между
  запросами и не жжёт часы бесплатного тарифа впустую, как это было бы
  при вечно работающем polling + фоновом цикле. Синхронизацию Classroom
  в этом режиме запускает внешний крон через GET /cron/sync (см.
  services/cron_server.py) — маленькая заглушка вместо цикла 24/7.

В обоих режимах на одном и том же порту (config.OAUTH_CALLBACK_PORT,
на Render — переменная PORT) поднят общий aiohttp-сервер с маршрутами:
  /                 — health-check
  /oauth/callback   — приём OAuth-редиректа от Google
  /webhook/<секрет> — апдейты Telegram (только когда USE_WEBHOOK=true)
  /cron/sync        — разовая синхронизация Classroom по внешнему триггеру
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
from bot.handlers import start, auth, classroom, solver
from services import cron_server, gemini_service, oauth_server, sync_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _start_web_app(bot: Bot, dp: Dispatcher) -> web.AppRunner:
    """Поднимает общий веб-сервер: OAuth-колбэк, cron-заглушку и, если
    включён вебхук, эндпоинт для апдейтов Telegram."""

    async def health(request: web.Request) -> web.Response:
        # Лёгкий health-check: сюда стучится Render при деплое, и сюда же
        # можно направить внешний пингер, чтобы не давать сервису спать
        # слишком долго (если это вообще нужно) — см. README.
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)

    oauth_server.setup_routes(app, bot, dp)
    cron_server.setup_routes(app, bot)

    if config.USE_WEBHOOK:
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(
            app, path=config.WEBHOOK_PATH
        )
        setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.OAUTH_CALLBACK_HOST, config.OAUTH_CALLBACK_PORT)
    await site.start()
    logger.info(
        "Веб-сервер запущен на %s:%d",
        config.OAUTH_CALLBACK_HOST,
        config.OAUTH_CALLBACK_PORT,
    )
    return runner


async def main() -> None:
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: более специфичные роутеры (с FSM-состояниями) регистрируем
    # раньше, чтобы они успевали перехватить сообщение в нужном состоянии.
    dp.include_router(auth.router)
    dp.include_router(solver.router)
    dp.include_router(classroom.router)
    dp.include_router(start.router)

    # Сканируем папку с учебниками при старте
    books_count = gemini_service.refresh_books_cache()
    logger.info("Найдено учебников в data/books: %d", books_count)

    await _start_web_app(bot, dp)

    if config.USE_WEBHOOK:
        await bot.set_webhook(
            config.WEBHOOK_URL,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        logger.info("Бот работает через webhook: %s", config.WEBHOOK_URL)
        logger.info(
            "Синхронизация Classroom — через /cron/sync. Настройте внешний "
            "крон (cron-job.org и т.п.), см. README."
        )
        # Бот больше не занимает поток на polling — просто ждём вечно,
        # пока не придёт сигнал остановки. Всю работу делают обработчики
        # aiohttp-приложения (вебхук, oauth callback, cron).
        await asyncio.Event().wait()
    else:
        # Локальная разработка: обычный polling + фоновый цикл синхронизации.
        asyncio.create_task(sync_service.run_sync_loop(bot))
        logger.info("Бот запускается в режиме polling…")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
