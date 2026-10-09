"""Generate a typographic cover image when a book has no cover of its own."""
from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 2560

PALETTES = {
    # background, text, accent (rules and frame)
    "letters": ((14, 17, 24), (214, 178, 94), (178, 145, 70)),
    "book": ((244, 239, 228), (28, 28, 30), (139, 43, 34)),
    "novel": ((23, 48, 40), (236, 226, 200), (196, 164, 98)),
}

BOLD_SERIF = ["georgiab.ttf", "Georgia Bold.ttf", "Caladea-Bold.ttf", "LiberationSerif-Bold.ttf",
              "timesbd.ttf", "Times New Roman Bold.ttf", "DejaVuSerif-Bold.ttf", "NotoSerif-Bold.ttf",
              "FreeSerifBold.ttf"]
REGULAR_SERIF = ["georgia.ttf", "Georgia.ttf", "Caladea-Regular.ttf", "LiberationSerif-Regular.ttf",
                 "times.ttf", "Times New Roman.ttf", "DejaVuSerif.ttf", "NotoSerif-Regular.ttf",
                 "FreeSerif.ttf"]


def _font_dirs() -> list[Path]:
    dirs = [Path.home() / ".fonts", Path.home() / ".local/share/fonts", Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts"),
            Path.home() / "Library/Fonts"]
    if sys.platform == "win32":
        dirs.insert(0, Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=None)
def _find_font(names: tuple[str, ...]) -> str | None:
    wanted = {n.lower(): n for n in names}
    found: dict[str, str] = {}
    for d in _font_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower() in wanted and f.lower() not in found:
                    found[f.lower()] = os.path.join(root, f)
    for n in names:
        if n.lower() in found:
            return found[n.lower()]
    return None


def _font(bold: bool, size: int) -> ImageFont.ImageFont:
    path = _find_font(tuple(BOLD_SERIF if bold else REGULAR_SERIF))
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def split_title(title: str) -> tuple[str, str | None]:
    """'Some Letters, 1957–1970' -> ('Some Letters', '1957–1970')."""
    m = re.match(r"^(.*?)[,\s]+(\d{4}\s*[–-]\s*\d{4}|\d{4})$", title.strip())
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).replace("-", "–").replace(" ", "")
    return title.strip(), None


def _wrap(draw, text, font, max_width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _spaced(text: str) -> str:
    """Letter-spaced capitals for small display lines."""
    return " ".join(text.upper())


def _center(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)


def make_cover(path: Path, title: str, author: str | None = None, subtitle: str | None = None,
               theme: str = "letters") -> Path:
    """Draw a cover and save it as JPEG at path. Returns path."""
    bg, ink, accent = PALETTES.get(theme, PALETTES["letters"])
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    d = ImageDraw.Draw(img)

    # double frame
    for inset, width in ((70, 6), (92, 2)):
        d.rectangle([inset, inset, WIDTH - inset, HEIGHT - inset], outline=accent, width=width)

    main, years = split_title(title)
    subtitle = subtitle or years

    y = 420
    if author:
        f = _font(False, 54)
        _center(d, y, _spaced(author), f, ink)
        y += 120
        d.line([(WIDTH / 2 - 160, y), (WIDTH / 2 + 160, y)], fill=accent, width=3)

    # title: largest size that fits in four lines
    size = 170
    while True:
        f = _font(True, size)
        lines = _wrap(d, main, f, WIDTH - 420)
        if len(lines) <= 4 or size <= 70:
            break
        size -= 10
    line_h = int(size * 1.22)
    block_h = line_h * len(lines)
    y = max(y + 150, int(HEIGHT * 0.42 - block_h / 2))
    for ln in lines:
        _center(d, y, ln, f, ink)
        y += line_h

    if subtitle:
        y += 70
        d.line([(WIDTH / 2 - 160, y), (WIDTH / 2 + 160, y)], fill=accent, width=3)
        y += 80
        f = _font(False, 64)
        for ln in _wrap(d, subtitle, f, WIDTH - 420)[:3]:
            _center(d, y, _spaced(ln) if len(ln) <= 12 else ln, f, ink)
            y += 90

    # small ornament near the foot
    cy = HEIGHT - 330
    for dx in (-70, 0, 70):
        r = 16 if dx == 0 else 10
        cx = WIDTH / 2 + dx
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=accent)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, optimize=True)
    return path
