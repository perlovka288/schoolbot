"""
Работа с Google OAuth2, Classroom API и Drive API.

OAuth-флоу: генерируем authorization_url с redirect_uri, указывающим на
локальный веб-сервер бота (services/oauth_server.py). Пользователь
переходит по ссылке, разрешает доступ — Google сам отправляет код
на этот сервер, который тут же обменивает его на токен и сохраняет в
tokens/{user_id}.json. Никакого копирования ссылок вручную.

(Ручной ввод кода в чат оставлен как запасной вариант на случай, если
колбэк-сервер недоступен — см. bot/handlers/auth.py.)
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Google иногда возвращает набор выданных scope'ов в другом виде, чем мы
# запросили (например, подменяет "classroom.coursework.me.readonly" на
# эквивалентный "classroom.student-submissions.me.readonly") — это не
# ошибка авторизации, а особенность Classroom API. Библиотека oauthlib
# по умолчанию считает это фатальной ошибкой ("Scope has changed") — эта
# переменная окружения превращает её в безобидное предупреждение в лог.
# Должна быть установлена ДО первого запроса токена.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    """Ошибка авторизации Google."""


# Раньше тип вложения определялся ТОЛЬКО по расширению в имени файла
# (взятому из названия материала в Classroom). Если учитель прикреплял,
# например, PDF или PPTX без расширения в названии ("Домашнє завдання 5"
# вместо "Домашнє завдання 5.pdf"), файл скачивался нормально, но
# solver.py его просто не находил среди document_paths/image_paths —
# и в Gemini уходило пустое задание, хотя вложение по факту было.
# Теперь расширение всегда переопределяется по РЕАЛЬНОМУ mimeType,
# который отдаёт Drive API — это надёжнее, чем верить названию файла.
_MIME_TO_EXTENSION: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "text/plain": ".txt",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def _guess_extension(mime_type: str) -> str:
    ext = _MIME_TO_EXTENSION.get(mime_type)
    if ext:
        return ext
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed or ""


@dataclass
class CourseWork:
    course_id: str
    course_name: str
    coursework_id: str
    title: str
    description: str
    due_date: str | None
    materials: list[dict]


def describe_materials(materials: list[dict]) -> list[str]:
    """Человекочитаемый список вложений задания (без скачивания) —
    для показа на экране деталей задания."""
    lines: list[str] = []
    for m in materials:
        if "driveFile" in m:
            title = m["driveFile"].get("driveFile", {}).get("title", "Файл без назви")
            lines.append(f"📎 {title}")
        elif "link" in m:
            title = m["link"].get("title") or m["link"].get("url", "посилання")
            lines.append(f"🔗 {title}")
        elif "youtubeVideo" in m:
            title = m["youtubeVideo"].get("title", "YouTube-відео")
            lines.append(f"▶️ {title}")
        elif "form" in m:
            title = m["form"].get("title", "Google-форма")
            lines.append(f"📝 {title}")
        else:
            lines.append("📎 Вкладення")
    return lines


def _token_path(user_id: int) -> Path:
    return config.TOKENS_DIR / f"{user_id}.json"


def build_auth_flow(state: str | None = None) -> Flow:
    """Создаёт объект Flow на основе credentials.json (Desktop App)."""
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        raise GoogleAuthError(
            "Файл credentials.json не знайдено. Завантажте його в Google "
            "Cloud Console (Credentials -> Create OAuth client ID -> тип "
            "«Web application», НЕ «Desktop app» — інакше redirect_uri не "
            "збіжиться з адресою бота). У Authorized redirect URIs додайте "
            f"{config.OAUTH_REDIRECT_URI}. На Render завантажте файл як "
            "Secret File (Environment -> Secret Files, шлях "
            "/etc/secrets/credentials.json — вже прописано в render.yaml)."
        )
    flow = Flow.from_client_secrets_file(
        str(config.GOOGLE_CREDENTIALS_FILE),
        scopes=config.GOOGLE_SCOPES,
        redirect_uri=config.OAUTH_REDIRECT_URI,
        state=state,
    )
    return flow


def get_authorization_url() -> tuple[str, str]:
    """Возвращает (auth_url, state) для отправки пользователю."""
    flow = build_auth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url, state


def extract_code(user_text: str) -> str:
    """
    Пользователь может прислать либо голый код, либо полную ссылку вида
    http://localhost/?code=XXXX&scope=... — извлекаем код в обоих случаях.
    """
    user_text = user_text.strip()
    if user_text.startswith("http"):
        parsed = urlparse(user_text)
        qs = parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        if not code:
            raise GoogleAuthError(
                "Не вдалося знайти параметр code= у надісланому посиланні."
            )
        return code
    return user_text


def exchange_code_and_save(user_id: int, code_or_url: str) -> None:
    """Обменивает код на токены и сохраняет их для пользователя."""
    code = extract_code(code_or_url)
    flow = build_auth_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        raise GoogleAuthError(
            f"Не вдалося обміняти код на токен: {exc}"
        ) from exc

    creds = flow.credentials
    _token_path(user_id).write_text(creds.to_json(), encoding="utf-8")
    logger.info("Сохранён Google-токен для пользователя %s", user_id)


def is_authorized(user_id: int) -> bool:
    return _token_path(user_id).exists()


def load_credentials(user_id: int) -> Credentials:
    path = _token_path(user_id)
    if not path.exists():
        raise GoogleAuthError("Користувача не авторизовано в Google.")

    creds = Credentials.from_authorized_user_file(str(path), config.GOOGLE_SCOPES)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise GoogleAuthError(
                "Термін дії токена сплив, і оновити його не вдалося. "
                "Авторизуйтеся заново."
            ) from exc

    return creds


def logout(user_id: int) -> None:
    path = _token_path(user_id)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Classroom API
# ---------------------------------------------------------------------------
def get_active_courses(user_id: int) -> list[dict]:
    """Возвращает список активных курсов пользователя."""
    creds = load_credentials(user_id)
    try:
        service = build("classroom", "v1", credentials=creds)
        response = (
            service.courses()
            .list(courseStates=["ACTIVE"], studentId="me")
            .execute()
        )
        return response.get("courses", [])
    except HttpError as exc:
        raise GoogleAuthError(f"Помилка Classroom API: {exc}") from exc


def get_coursework(user_id: int, course_id: str) -> list[CourseWork]:
    """
    Возвращает список актуальных (не выполненных) заданий по курсу,
    сопоставляя coursework со статусом submission студента.
    """
    creds = load_credentials(user_id)
    try:
        service = build("classroom", "v1", credentials=creds)

        course = service.courses().get(id=course_id).execute()
        course_name = course.get("name", "Без названия")

        coursework_resp = (
            service.courses().courseWork().list(courseId=course_id).execute()
        )
        courseworks = coursework_resp.get("courseWork", [])

        result: list[CourseWork] = []
        for cw in courseworks:
            cw_id = cw["id"]

            # Проверяем статус сдачи именно этим студентом
            submissions_resp = (
                service.courses()
                .courseWork()
                .studentSubmissions()
                .list(courseId=course_id, courseWorkId=cw_id, userId="me")
                .execute()
            )
            submissions = submissions_resp.get("studentSubmissions", [])
            state = submissions[0].get("state") if submissions else None

            # Пропускаем уже сданные/оценённые задания
            if state in ("TURNED_IN", "RETURNED"):
                continue

            due = cw.get("dueDate")
            due_str = None
            if due:
                due_str = f"{due.get('day'):02d}.{due.get('month'):02d}.{due.get('year')}"

            materials = cw.get("materials", [])

            result.append(
                CourseWork(
                    course_id=course_id,
                    course_name=course_name,
                    coursework_id=cw_id,
                    title=cw.get("title", "Без названия"),
                    description=cw.get("description", ""),
                    due_date=due_str,
                    materials=materials,
                )
            )
        return result
    except HttpError as exc:
        raise GoogleAuthError(f"Помилка Classroom API: {exc}") from exc


# ---------------------------------------------------------------------------
# Drive API — скачивание вложений
# ---------------------------------------------------------------------------
def download_material_files(
    user_id: int, materials: list[dict], dest_dir: Path
) -> list[Path]:
    """
    Скачивает файлы, прикреплённые к заданию (Drive-файлы), в dest_dir.
    Ссылки (link/youtube) и формы игнорируются — скачиваются только
    настоящие файлы Drive.
    """
    creds = load_credentials(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    try:
        drive = build("drive", "v3", credentials=creds)
    except HttpError as exc:
        raise GoogleAuthError(f"Помилка Drive API: {exc}") from exc

    for material in materials:
        drive_file = material.get("driveFile", {}).get("driveFile")
        if not drive_file:
            continue

        file_id = drive_file.get("id")
        title = drive_file.get("title", file_id)

        try:
            meta = drive.files().get(fileId=file_id, fields="mimeType,name").execute()
            mime_type = meta.get("mimeType", "")

            safe_name = "".join(c for c in title if c.isalnum() or c in " ._-") or file_id
            target_path = dest_dir / safe_name

            if mime_type.startswith("application/vnd.google-apps"):
                # Google Docs/Slides и т.п. — экспортируем в PDF
                export_mime = "application/pdf"
                request = drive.files().export_media(fileId=file_id, mimeType=export_mime)
                target_path = target_path.with_suffix(".pdf")
            else:
                request = drive.files().get_media(fileId=file_id)
                # Всегда приводим расширение к реальному mimeType файла —
                # см. комментарий у _MIME_TO_EXTENSION выше.
                real_ext = _guess_extension(mime_type)
                if real_ext and target_path.suffix.lower() != real_ext.lower():
                    target_path = target_path.with_suffix(real_ext)

            with open(target_path, "wb") as f:
                f.write(request.execute())

            downloaded.append(target_path)
        except HttpError as exc:
            logger.warning("Не вдалося завантажити файл %s: %s", title, exc)
            continue

    return downloaded
