"""
Генерация изображения "листа в клетку" с ответом, написанным
синей ручкой (или рукописным шрифтом, если он положен в data/fonts).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

import config

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


def render_answer_pages(text: str, title: str | None = None) -> list[Path]:
    """
    Рендерит текст ответа на одном или нескольких "тетрадных" листах
    (если текст длинный — разбивается на несколько страниц).
    Возвращает список путей к PNG-файлам.
    """
    font = _load_font(FONT_SIZE)
    title_font = _load_font(FONT_SIZE + 6)

    usable_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    # Примерная ширина символа для переноса строк (эмпирически для кириллицы)
    chars_per_line = max(20, int(usable_width / (FONT_SIZE * 0.52)))

    wrapped_lines: list[str] = []
    if title:
        wrapped_lines.extend(textwrap.wrap(title, width=chars_per_line))
        wrapped_lines.append("")  # пустая строка-разделитель

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
            use_font = title_font if (title and page_idx == 0 and i == 0) else font
            draw.text((MARGIN_LEFT, y), line, fill=INK_COLOR, font=use_font)
            y += LINE_SPACING

        out_path = config.TMP_DIR / f"answer_{uuid4().hex}.png"
        img.save(out_path)
        pages.append(out_path)

    return pages
