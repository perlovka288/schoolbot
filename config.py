"""
Конфигурация проекта.

Секреты (токен бота, ключ Gemini) НЕ хранятся в коде — они читаются
из переменных окружения / файла .env. Локально создайте файл .env рядом
с этим файлом (по образцу .env.example). На хостинге (Render и т.п.)
эти же переменные задаются в панели управления — .env там не нужен.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Где хранятся ИЗМЕНЯЕМЫЕ данные (токены пользователей, состояние синхрони-
# зации, скачанные учебники и т.п.)
# ---------------------------------------------------------------------------
# Локально — просто рядом с проектом. На хостинге с постоянным диском
# (например, Render Persistent Disk) задайте переменную DATA_ROOT равной
# пути монтирования диска (например, /var/data) — тогда все эти файлы
# переживут редеплой и перезапуск, а не потеряются вместе с контейнером.
DATA_ROOT: Path = Path(os.getenv("DATA_ROOT", str(BASE_DIR)))
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Секреты
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN не задан. Создайте файл .env "
        "(см. .env.example) и укажите там токен бота."
    )
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY не задан. Создайте файл .env "
        "(см. .env.example) и укажите там ключ Gemini API."
    )

# ---------------------------------------------------------------------------
# Google OAuth / Classroom
# ---------------------------------------------------------------------------
# GOOGLE_CREDENTIALS_PATH может быть как относительным именем файла рядом
# с проектом (локально), так и абсолютным путём — например, путём к
# Render Secret File (/etc/secrets/credentials.json). Абсолютный путь
# всегда побеждает relative join ниже.
GOOGLE_CREDENTIALS_FILE: Path = BASE_DIR / os.getenv(
    "GOOGLE_CREDENTIALS_PATH", "credentials.json"
)

# ---------------------------------------------------------------------------
# OAuth callback — веб-сервер, который автоматически ловит редирект от Google
# ---------------------------------------------------------------------------
# Раньше redirect_uri был жёстко "http://localhost" — там никто не слушал,
# и браузер зависал в бесконечной загрузке, а код приходилось копировать
# руками. Теперь на этом порту реально поднят веб-сервер
# (services/oauth_server.py), который сам принимает код и сохраняет токен.
#
# Локально — ничего менять не нужно, всё работает "из коробки" (браузер
# и бот на одной машине) или через туннель (ngrok и т.п.).
#
# На Render (Web Service) порт задаёт сама платформа через переменную
# PORT — мы её подхватываем автоматически. Останется только задать
# OAUTH_REDIRECT_BASE_URL = публичный адрес сервиса и прописать тот же
# адрес + "/oauth/callback" как Authorized redirect URI в Google Cloud
# Console (тип OAuth-клиента — "Web application", не "Desktop app").
OAUTH_CALLBACK_HOST: str = os.getenv("OAUTH_CALLBACK_HOST", "0.0.0.0")
OAUTH_CALLBACK_PORT: int = int(
    os.getenv("PORT", os.getenv("OAUTH_CALLBACK_PORT", "8765"))
)
OAUTH_REDIRECT_BASE_URL: str = os.getenv(
    "OAUTH_REDIRECT_BASE_URL", f"http://localhost:{OAUTH_CALLBACK_PORT}"
)
OAUTH_REDIRECT_URI: str = f"{OAUTH_REDIRECT_BASE_URL}/oauth/callback"

GOOGLE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKENS_DIR: Path = DATA_ROOT / "tokens"
TOKENS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Фоновая синхронизация курсов
# ---------------------------------------------------------------------------
# Папка, где хранится "последнее известное" множество курсов каждого
# пользователя — чтобы понимать, какие курсы новые.
SYNC_STATE_DIR: Path = DATA_ROOT / "data" / "sync_state"
SYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)

# Как часто (в секундах) опрашивать Classroom API в фоне на предмет новых
# курсов. Можно переопределить через .env (SYNC_INTERVAL_SECONDS=30 и т.д.).
SYNC_INTERVAL_SECONDS: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
GEMINI_MODEL_NAME: str = "gemini-1.5-flash"

SYSTEM_PROMPT: str = (
    "Ты опытный школьный репетитор. Реши задание по шагам, объясни логику "
    "и дай итоговый краткий ответ. Отвечай на русском языке. Структурируй "
    "ответ так:\n"
    "1) Кратко перескажи, что дано в задаче.\n"
    "2) Пошаговое решение с объяснением каждого шага.\n"
    "3) Итоговый краткий ответ, выделенный отдельной строкой "
    "('Ответ: ...').\n"
    "Если условие дано на картинке — сначала распознай текст задачи, "
    "затем реши её."
)

# ---------------------------------------------------------------------------
# Книги (RAG-контекст)
# ---------------------------------------------------------------------------
BOOKS_DIR: Path = DATA_ROOT / "data" / "books"
BOOKS_DIR.mkdir(parents=True, exist_ok=True)

# Максимум символов текста учебников, которые отправляем в модель как контекст
MAX_BOOK_CONTEXT_CHARS: int = 15000

# ---------------------------------------------------------------------------
# Генератор тетрадного листа
# ---------------------------------------------------------------------------
FONTS_DIR: Path = DATA_ROOT / "data" / "fonts"
FONTS_DIR.mkdir(parents=True, exist_ok=True)

# Путь к рукописному TTF-шрифту, если пользователь его положит в data/fonts
HANDWRITING_FONT_PATH: Path = FONTS_DIR / "handwriting.ttf"

# Временная папка для скачанных вложений / сгенерированных изображений
TMP_DIR: Path = DATA_ROOT / "tmp"
TMP_DIR.mkdir(exist_ok=True)
USE_WEBHOOK: bool = os.getenv("USE_WEBHOOK", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", TELEGRAM_BOT_TOKEN)
WEBHOOK_PATH: str = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL: str = f"{OAUTH_REDIRECT_BASE_URL}{WEBHOOK_PATH}"
CRON_SECRET: str = os.getenv("CRON_SECRET", "")
