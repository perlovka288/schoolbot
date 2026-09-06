"""Точка входа: запуск Telegram-бота."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
from bot.handlers import start, auth, classroom, solver
from services import gemini_service, sync_service, oauth_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


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

    # Веб-сервер, который сам ловит редирект от Google после авторизации —
    # без него страница после разрешения доступа зависала на http://localhost.
    await oauth_server.start_oauth_server(bot)

    # Фоновая синхронизация: следит за Classroom и сама шлёт уведомление,
    # как только у пользователя появляется новый курс — без ручного "Мои ДЗ".
    asyncio.create_task(sync_service.run_sync_loop(bot))

    logger.info("Бот запускается…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
