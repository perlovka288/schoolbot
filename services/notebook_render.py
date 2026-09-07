"""
Генерация изображения "листа в клетку" с ответом, написанным
синей ручкой (или рукописным шрифтом, если он положен в data/fonts).
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

import config

# Украинские названия месяцев для "правильной" даты в шапке листа
# (родительный падеж — "7 вересня 2026 р.").
_UA_MONTHS_GENITIVE = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]


def _today_ua() -> str:
    now = datetime.now()
    return f"{now.day} {_UA_MONTHS_GENITIVE[now.month - 1]} {now.year} р."


# На случай, если модель всё же вставит символы markdown-разметки
# (несмотря на явный запрет в SYSTEM_PROMPT) — вычищаем их перед тем,
# как рисовать текст на "тетрадном листе", чтобы там не остались
# решётки/звёздочки/доллары вместо форматирования.
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_BULLET_RE = re.compile(r"^\s*[\*\-]\s+", re.MULTILINE)
_MD_HRULE_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_MD_INLINE_MATH_RE = re.compile(r"\$\$?(.+?)\$\$?")


def _strip_markdown(text: str) -> str:
    text = _MD_HRULE_RE.sub("", text)
    text = _MD_HEADER_RE.sub("", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _MD_INLINE_MATH_RE.sub(r"\1", text)
    return text

# Параметры листа
PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
MARGIN_LEFT = 110
MARGIN_TOP = 90
MARGIN_RIGHT = 60
MARGIN_BOTTOM = 90

CELL_SIZE = 34
GRID_COLOR = (170, 195, 230)
PAGE_BG_COLOR = (255, 255, 253)
RED_LINE_COLOR = (235, 140, 150)
INK_COLOR = (30, 40, 150)  # синяя ручка

FONT_SIZE = 30
LINE_SPACING = 40


def _create_grid_background() -> Image.Image:
    """Рисует тетрадный лист в клетку с красной полосой полей."""
    img = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), PAGE_BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Вертикальные линии клетки
    x = 0
    while x <= PAGE_WIDTH:
        draw.line([(x, 0), (x, PAGE_HEIGHT)], fill=GRID_COLOR, width=1)
        x += CELL_SIZE

    # Горизонтальные линии клетки
    y = 0
    while y <= PAGE_HEIGHT:
        draw.line([(0, y), (PAGE_WIDTH, y)], fill=GRID_COLOR, width=1)
        y += CELL_SIZE

    # Красная вертикальная линия полей слева
    draw.line(
        [(MARGIN_LEFT - 20, 0), (MARGIN_LEFT - 20, PAGE_HEIGHT)],
        fill=RED_LINE_COLOR,
        width=2,
    )

    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    if config.HANDWRITING_FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(config.HANDWRITING_FONT_PATH), size)
        except Exception:  # noqa: BLE001
            pass
    # Fallback на встроенный шрифт PIL, если рукописного нет
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def render_answer_pages(
    text: str,
    title: str | None = None,
    heading: str = "Домашня робота",
    show_date: bool = True,
) -> list[Path]:
    """
    Рендерит текст ответа на одном или нескольких "тетрадных" листах
    (если текст длинный — разбивается на несколько страниц), как это
    выглядело бы в тетради ученика: сначала правильная сегодняшняя дата,
    затем заголовок ("Домашня робота"), затем (опционально) название
    задания/предмета, затем сам текст решения (Дано / Розв'язання /
    Відповідь).
    Возвращает список путей к PNG-файлам.
    """
    text = _strip_markdown(text)

    font = _load_font(FONT_SIZE)
    title_font = _load_font(FONT_SIZE + 6)

    usable_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    # Примерная ширина символа для переноса строк (эмпирически для кириллицы)
    chars_per_line = max(20, int(usable_width / (FONT_SIZE * 0.52)))

    wrapped_lines: list[str] = []
    header_line_count = 0

    if show_date:
        wrapped_lines.append(_today_ua())
        header_line_count += 1
    if heading:
        wrapped_lines.append(heading)
        header_line_count += 1
    if title:
        wrapped_lines.extend(textwrap.wrap(title, width=chars_per_line) or [title])
        header_line_count += 1
    if wrapped_lines:
        wrapped_lines.append("")  # пустая строка-разделитель перед решением

    for paragraph in text.split("\n"):
        if not paragraph.strip():
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(paragraph, width=chars_per_line) or [""])

    lines_per_page = int((PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM) / LINE_SPACING)

    pages: list[Path] = []
    for page_idx in range(0, len(wrapped_lines), lines_per_page) or [0]:
        page_lines = wrapped_lines[page_idx : page_idx + lines_per_page]
        img = _create_grid_background()
        draw = ImageDraw.Draw(img)

        y = MARGIN_TOP
        for i, line in enumerate(page_lines):
            use_font = title_font if (page_idx == 0 and i < header_line_count) else font
            draw.text((MARGIN_LEFT, y), line, fill=INK_COLOR, font=use_font)
            y += LINE_SPACING

        out_path = config.TMP_DIR / f"answer_{uuid4().hex}.png"
        img.save(out_path)
        pages.append(out_path)

    return pages
