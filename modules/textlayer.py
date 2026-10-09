"""Fast PDF to Markdown conversion for PDFs that already contain text.

PDFs exported from a word processor, a typesetting system or an e-book tool
carry their text, fonts and character positions. Reading those directly is
orders of magnitude faster than OCR and layout models, and it keeps the
italics, bold and underlining that OCR loses. Scanned PDFs have no usable text
layer; use the marker engine for those (see text_coverage()).

The Markdown written here uses HTML for its blocks (paragraphs, headings,
tables), which python-markdown passes through unchanged.

Layouts:

* book: one chapter per top-level heading (or a single file without them).
* letters: a collection of dated letters such as shareholder letters. Each
  letter becomes a chapter titled by its date and grouped by year.
"""
from __future__ import annotations

import html
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
_MONTH_RE = "|".join(MONTHS)
DATE_RE = re.compile(
    rf"^(?P<m>{_MONTH_RE})\s*(?P<d>\d{{1,2}})?(?:st|nd|rd|th)?\s*,?\s*(?P<y>1[6-9]\d\d|20\d\d)\s*$",
    re.IGNORECASE)
NUMERIC_DATE_RE = re.compile(r"(?<![\d/-])(?P<m>\d{1,2})-(?P<d>\d{1,2})-(?P<y>\d{2})(?![\d/-])")
LIST_LABEL_RE = re.compile(r"^(\(?(\d{1,2}|[a-z]|[ivx]{1,4})[).]|\(\d{1,2}\)|\([a-z]\)|[•●▪◦‣–—-])$")
NUMERIC_CELL_RE = re.compile(r"^[\s$£€(+\-–—]*[\d.,½¼¾]+\s*%?\)?\*?$|^N\.?A\.?$|^[-—–]+$")
PAGE_NUMBER_RE = re.compile(r"^(page\s+)?[-–—]?\s*([0-9]+|[ivxlc]+)\s*[-–—]?(\s*(of|/)\s*\d+)?$", re.I)
CLOSING_RE = re.compile(r"^(Cordially|Sincerely|Very truly yours|Yours (truly|sincerely)|"
                        r"Respectfully|Best regards|Kind regards|Regards)\b.*,?$", re.I)
SALUTATION_RE = re.compile(r"^(To (My|the|All|Our) [A-Z]?[\w ]{0,40}:|Dear [\w .,'-]{1,60}[,:])$")
ELISIONS = ("em", "til", "tis", "twas", "n")
NOTE_MARKER_RE = re.compile(r"^(\d{1,3})[.)]?$|^[*†‡§]+$")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Char:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    italic: bool
    ul: bool = False
    sup: bool = False


@dataclass(slots=True)
class Line:
    page: int                 # 1-based page number
    chars: list
    x0: float
    x1: float
    y0: float
    y1: float
    base: float
    size: float               # dominant font size of the line
    figure: str | None = None  # image file name for figure pseudo-lines

    @property
    def text(self) -> str:
        return "".join(c.c for c in self.chars).strip()


@dataclass
class Metrics:
    body_size: float
    left: float
    right: float
    pitch: float
    page_width: float
    page_height: float
    justified: bool
    words: set = field(default_factory=set)
    hyphenated: set = field(default_factory=set)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def cell_gap(self) -> float:
        return 0.8 * self.body_size


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
def text_coverage(pdf_path: str, sample: int = 40) -> float:
    """Fraction of non-blank pages that carry a real text layer (0.0-1.0)."""
    import pypdfium2
    import pypdfium2.raw as pdfraw

    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        n = len(pdf)
        if n == 0:
            return 0.0
        idx = range(n) if n <= sample else sorted({round(i * (n - 1) / (sample - 1)) for i in range(sample)})
        with_text = with_content = 0
        for i in idx:
            page = pdf[i]
            text = page.get_textpage().get_text_bounded()
            has_text = sum(ch.isalnum() for ch in text) >= 40
            has_image = any(o.type == pdfraw.FPDF_PAGEOBJ_IMAGE for o in page.get_objects())
            if has_text or has_image:
                with_content += 1
                with_text += has_text
        return with_text / with_content if with_content else 0.0
    finally:
        pdf.close()


def assess(pdf_path: str, sample: int = 12) -> dict:
    """Whether the text-layer engine suits this PDF.

    Returns {"coverage": float, "multicolumn": bool, "suitable": bool}. Text in
    two or more columns is read line by line across the page here, so such
    PDFs (academic papers, magazines) are left to the layout-aware engine.
    """
    import pypdfium2
    from pdftext.extraction import dictionary_output

    coverage = text_coverage(pdf_path)
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        n = len(pdf)
    finally:
        pdf.close()
    multicolumn = False
    if n and coverage >= 0.5:
        idx = sorted({round(i * (n - 1) / max(1, sample - 1)) for i in range(min(sample, n))})
        boxes = []
        sizes = Counter()
        for page in dictionary_output(pdf_path, page_range=idx):
            for block in page["blocks"]:
                for line in block["lines"]:
                    text = "".join(sp["text"] for sp in line["spans"]).strip()
                    if len(text) > 25:
                        size = round(max(sp["font"]["size"] or 0 for sp in line["spans"]) * 2) / 2
                        sizes[size] += 1
                        boxes.append((line["bbox"][0], line["bbox"][2], size))
        if boxes:
            body = sizes.most_common(1)[0][0]
            lines = [(x0, x1) for x0, x1, sz in boxes if abs(sz - body) < 0.6]
            left = min(x0 for x0, _ in lines)
            right = sorted(x1 for _, x1 in lines)[int(0.95 * (len(lines) - 1))]
            width = max(1.0, right - left)
            narrow = sum(1 for x0, x1 in lines if x1 - x0 < 0.55 * width)
            right_col = sum(1 for x0, _ in lines if x0 > left + 0.45 * width)
            multicolumn = len(lines) >= 20 and narrow > 0.6 * len(lines) and right_col > 0.25 * len(lines)
    return {"coverage": coverage, "multicolumn": multicolumn,
            "suitable": coverage >= 0.9 and not multicolumn}


# --------------------------------------------------------------------------
# Reading pages
# --------------------------------------------------------------------------
def _rules(page) -> list[tuple[float, float, float, float]]:
    """Thin horizontal path objects (underlines), in top-left coordinates."""
    import pypdfium2.raw as pdfraw

    h = page.get_height()
    out = []
    for o in page.get_objects():
        if o.type == pdfraw.FPDF_PAGEOBJ_PATH:
            left, bottom, right, top = o.get_pos()
            if top - bottom < 2.5 and right - left > 2:
                out.append((left, h - top, right, h - bottom))
    return out


def _mark_underlines(chars: list[Char], rules) -> None:
    vis = [c for c in chars if c.c.strip()]
    if not vis or not rules:
        return
    for (ux0, uy0, ux1, _uy1) in rules:
        hit = [c for c in vis
               if ux0 - 0.5 <= (c.x0 + c.x1) / 2 <= ux1 + 0.5
               and c.y0 + 0.6 * (c.y1 - c.y0) <= uy0 <= c.y1 + 3]
        if not hit:
            continue
        # an underline spans its text; a table border or rule spans more
        if abs(hit[0].x0 - ux0) > 3.5 or abs(hit[-1].x1 - ux1) > 3.5:
            continue
        for c in chars:
            if hit[0].x0 - 0.1 <= c.x0 and c.x1 <= hit[-1].x1 + 0.1:
                c.ul = True


def _page_lines(page_dict, page_no: int, rules) -> list[Line]:
    raw = []
    for block in page_dict["blocks"]:
        for line in block["lines"]:
            chars = []
            for span in line["spans"]:
                font = span["font"]
                name = font.get("name") or ""
                weight = font.get("weight") or 400
                for ch in span.get("chars", []):
                    if ch["char"] in "\n\r":
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    cfont = ch.get("font") or font
                    cname = cfont.get("name") or name
                    cweight = cfont.get("weight") or weight
                    chars.append(Char(
                        ch["char"], x0, y0, x1, y1, float(cfont.get("size") or 0),
                        "Bold" in cname or "Black" in cname or "Heavy" in cname or cweight >= 600,
                        "Italic" in cname or "Oblique" in cname))
            if chars and "".join(c.c for c in chars).strip():
                raw.append(chars)

    # merge fragments sharing a baseline (a table row may arrive in pieces)
    def baseline(cs):
        big = [c for c in cs if c.c.strip()]
        top = max(c.size for c in big)
        return max(c.y1 for c in big if c.size >= 0.8 * top)

    raw.sort(key=lambda cs: (round(baseline(cs)), min(c.x0 for c in cs)))
    merged: list[tuple[float, list[Char]]] = []
    for cs in raw:
        b = baseline(cs)
        if merged and abs(merged[-1][0] - b) < 2.5:
            merged[-1][1].extend(cs)
        else:
            merged.append((b, list(cs)))

    lines = []
    for base, cs in merged:
        cs.sort(key=lambda c: c.x0)
        vis = [c for c in cs if c.c.strip()]
        sizes = Counter(round(c.size * 2) / 2 for c in vis)
        size = sizes.most_common(1)[0][0]
        for c in cs:
            c.sup = c.size < 0.8 * size and c.y1 < base - 0.2 * size
        _mark_underlines(cs, rules)
        lines.append(Line(page_no, cs, vis[0].x0, vis[-1].x1,
                          min(c.y0 for c in vis), max(c.y1 for c in vis), base, size))
    lines.sort(key=lambda ln: (ln.base, ln.x0))
    return lines


def _figures(page, page_no: int, image_dir: Path | None) -> list[Line]:
    """Embedded images worth keeping, as figure pseudo-lines."""
    if image_dir is None:
        return []
    import pypdfium2.raw as pdfraw

    w, h = page.get_width(), page.get_height()
    out = []
    for k, o in enumerate(page.get_objects()):
        if o.type != pdfraw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = o.get_pos()
        bw, bh = right - left, top - bottom
        if bw < 48 or bh < 48 or (bw * bh) > 0.9 * w * h:
            continue  # icons, rules and full-page backgrounds/scans
        image_dir.mkdir(parents=True, exist_ok=True)
        stem = image_dir / f"page{page_no:04d}-{k + 1}"
        try:
            o.extract(stem, fb_format="png")
        except Exception as exc:  # unsupported filter or corrupt data
            print(f"Warning: could not extract image on page {page_no}: {exc}")
            continue
        files = sorted(image_dir.glob(stem.name + ".*"))
        if not files:
            continue
        name = files[0].name
        if files[0].suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif"):
            from PIL import Image
            png = files[0].with_suffix(".png")
            Image.open(files[0]).convert("RGB").save(png)
            files[0].unlink()
            name = png.name
        out.append(Line(page_no, [], left, right, h - top, h - bottom, h - bottom, 0.0, figure=name))
    return out


def read_pdf(pdf_path: str, pages: list[int], image_dir: Path | None = None,
             batch: int = 16) -> list[list[Line]]:
    """Lines (and figures) for the given 0-based page indices."""
    import pypdfium2
    from pdftext.extraction import dictionary_output

    pdf = pypdfium2.PdfDocument(pdf_path)
    out = []
    try:
        for start in range(0, len(pages), batch):
            chunk = pages[start:start + batch]
            data = dictionary_output(pdf_path, page_range=chunk, keep_chars=True)
            for index, page_dict in zip(chunk, data):
                page = pdf[index]
                lines = _page_lines(page_dict, index + 1, _rules(page))
                lines += _figures(page, index + 1, image_dir)
                lines.sort(key=lambda ln: (ln.y0 if ln.figure else ln.base, ln.x0))
                out.append(lines)
    finally:
        pdf.close()
    return out


# --------------------------------------------------------------------------
# Page furniture and metrics
# --------------------------------------------------------------------------
def strip_furniture(pages: list[list[Line]], page_height: float) -> list[list[Line]]:
    """Remove page numbers and running heads/feet repeated across pages."""
    def zone(ln):
        return ln.y1 < 0.10 * page_height or ln.y0 > 0.90 * page_height

    def key(ln):
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", ln.text.lower()))

    def opens_letter(lines):
        top = [ln for ln in lines if not ln.figure and ln.y0 < 0.35 * page_height][:10]
        return any(DATE_RE.match(ln.text) for ln in top)

    counts = Counter()
    for lines in pages:
        counts.update({key(ln) for ln in lines if not ln.figure and zone(ln)})
    limit = max(2, 0.4 * len(pages))
    out = []
    for lines in pages:
        protect = opens_letter(lines)
        keep = []
        for ln in lines:
            if ln.figure or not zone(ln):
                keep.append(ln)
                continue
            if PAGE_NUMBER_RE.match(ln.text) and ln.y0 > 0.5 * page_height:
                continue
            if counts[key(ln)] >= limit and not protect:
                continue
            keep.append(ln)
        out.append(keep)
    return out


@dataclass
class Note:
    page: int
    marker: str
    lines: list
    nid: str = ""


def _note_marker(ln: Line):
    """(marker, rest-of-line) if a line starts a footnote ("1 Text", "¹Text", "* Text")."""
    chars = ln.chars
    i = 0
    while i < len(chars) and not chars[i].c.strip():
        i += 1
    j = i
    if i < len(chars) and chars[i].sup:
        while j < len(chars) and chars[j].sup and chars[j].c.strip():
            j += 1
    else:
        while j < len(chars) and chars[j].c.strip():
            j += 1
    token = "".join(c.c for c in chars[i:j])
    if not NOTE_MARKER_RE.match(token):
        glued = re.match(r"^(\d{1,3}|[*†‡§]+)(?=[^\d\s.,)])", token)   # "1The note"
        if not glued:
            return None, None
        token = glued.group(1)
        j = i + len(token)
    k = j
    while k < len(chars) and not chars[k].c.strip():
        k += 1
    if k >= len(chars):
        return None, None
    rest = Line(ln.page, chars[k:], chars[k].x0, ln.x1, ln.y0, ln.y1, ln.base, ln.size)
    return token.rstrip(".)"), rest


def extract_footnotes(pages: list[list[Line]], m: Metrics) -> list[Note]:
    """Move small-print notes below a page's last body line out of the text flow."""
    notes = []
    for k, lines in enumerate(pages):
        body = [i for i, ln in enumerate(lines) if not ln.figure and ln.size >= 0.92 * m.body_size]
        if not body:
            continue
        tail = [ln for ln in lines[body[-1] + 1:] if not ln.figure]
        if not tail or tail[0].y0 < 0.5 * m.page_height or any(ln.size > 0.9 * m.body_size for ln in tail):
            continue
        found: list[Note] = []
        for ln in tail:
            marker, rest = _note_marker(ln)
            if marker is not None:
                found.append(Note(ln.page, marker, [rest]))
            elif found:
                found[-1].lines.append(ln)
            else:
                found = []
                break               # small print that is not a note: leave it alone
        if not found:
            continue
        notes.extend(found)
        drop = {id(ln) for ln in tail}
        pages[k] = [ln for ln in lines if id(ln) not in drop]
    return notes


class NoteLinker:
    """Links superscript markers to extracted notes, in reading order."""

    def __init__(self, notes: list[Note], m: Metrics, typography=True):
        self.notes = notes
        self.m = m
        self.typo = typography
        self.cursor = 0
        self.count = 0

    def link(self, html_text: str) -> tuple[str, list[Note]]:
        used: list[Note] = []

        def repl(mt):
            marker = mt.group(1)
            for k in range(self.cursor, min(len(self.notes), self.cursor + 12)):
                note = self.notes[k]
                if note.marker == marker and not note.nid:
                    self.count += 1
                    note.nid = f"fn{self.count}"
                    used.append(note)
                    self.cursor = k + 1
                    return (f'<sup><a class="noteref" epub:type="noteref" id="ref-{note.nid}" '
                            f'href="#{note.nid}">{marker}</a></sup>')
            return mt.group(0)

        html_text = re.sub(r"<sup>(\d{1,3}|[*†‡§]+)</sup>", repl, html_text)
        return html_text, used

    def section(self, notes: list[Note]) -> str:
        if not notes:
            return ""
        parts = ['<section class="footnotes" epub:type="footnotes">']
        for note in notes:
            text = inline_html(_join_lines(note.lines, self.m), typography=self.typo)
            if not note.nid:
                self.count += 1
                note.nid = f"fn{self.count}"
                parts.append(f'<aside epub:type="footnote" id="{note.nid}"><p>{html.escape(note.marker)} {text}</p></aside>')
            else:
                parts.append(f'<aside epub:type="footnote" id="{note.nid}"><p>'
                             f'<a href="#ref-{note.nid}">{html.escape(note.marker)}</a> {text}</p></aside>')
        parts.append("</section>")
        return "\n".join(parts)

    def leftovers(self) -> list[Note]:
        return [n for n in self.notes if not n.nid]


def measure(pages: list[list[Line]], page_width: float, page_height: float) -> Metrics:
    sizes = Counter()
    for lines in pages:
        for ln in lines:
            for c in ln.chars:
                if c.c.strip():
                    sizes[round(c.size * 2) / 2] += 1
    body = sizes.most_common(1)[0][0] if sizes else 11.0
    body_lines = [ln for lines in pages for ln in lines if not ln.figure and abs(ln.size - body) < 0.6]
    lefts = Counter(round(ln.x0) for ln in body_lines)
    left = float(lefts.most_common(1)[0][0]) if lefts else 72.0
    rights = sorted(ln.x1 for ln in body_lines) or [page_width - 72.0]
    right = rights[int(0.95 * (len(rights) - 1))]
    pitches = []
    for lines in pages:
        prev = None
        for ln in lines:
            if ln.figure or abs(ln.size - body) >= 0.6:
                prev = None
                continue
            if prev is not None and 0.9 * body < ln.base - prev.base < 1.8 * body:
                pitches.append(ln.base - prev.base)
            prev = ln
    pitch = statistics.median(pitches) if pitches else 1.2 * body
    full = [ln for ln in body_lines if abs(ln.x0 - left) < 2 and len(ln.text) > 30]
    justified = bool(full) and sum(ln.x1 > right - 1.5 for ln in full) > 0.6 * len(full)
    m = Metrics(body, left, right, pitch, page_width, page_height, justified)
    for lines in pages:
        for ln in lines:
            if ln.figure:
                continue
            for w in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", ln.text):
                lw = w.lower()
                (m.hyphenated if "-" in lw else m.words).add(lw)
    return m


# --------------------------------------------------------------------------
# Line classification
# --------------------------------------------------------------------------
def cells(ln: Line, m: Metrics) -> list[list[Char]]:
    """Split a line into cells at large horizontal gaps."""
    out, cur, last = [], [], None
    for c in ln.chars:
        if not c.c.strip():
            if cur:
                cur.append(c)
            continue
        if last is not None and c.x0 - last.x1 > m.cell_gap:
            out.append(cur)
            cur = []
        cur.append(c)
        last = c
    if cur:
        out.append(cur)
    res = []
    for cs in out:
        while cs and not cs[-1].c.strip():
            cs = cs[:-1]
        if cs:
            res.append(cs)
    return res


def _cell_text(cs) -> str:
    return "".join(c.c for c in cs).strip()


def _extent(cs) -> tuple[float, float]:
    vis = [c for c in cs if c.c.strip()]
    return vis[0].x0, vis[-1].x1


def _label(ln: Line):
    """(label, index of first text char, x where the text starts, gap) for a
    line that starts with a list label such as "(1)", "a." or a bullet."""
    if ln.figure:
        return None
    chars = ln.chars
    i = 0
    while i < len(chars) and not chars[i].c.strip():
        i += 1
    j = i
    while j < len(chars) and chars[j].c.strip():
        j += 1
    token = "".join(c.c for c in chars[i:j])
    if not token or not LIST_LABEL_RE.match(token):
        return None
    k = j
    while k < len(chars) and not chars[k].c.strip():
        k += 1
    if k >= len(chars):
        return None
    return token, k, chars[k].x0, chars[k].x0 - chars[j - 1].x1


def is_list_start(ln: Line, m: Metrics, nxt: Line | None = None) -> bool:
    """A label followed by a tab-sized gap, or by text that later lines hang under."""
    lab = _label(ln)
    if lab is None:
        return False
    if nxt is not None and abs(nxt.x0 - lab[2]) < 2.5:
        return True
    return lab[3] >= 0.4 * m.body_size


def is_tabular(ln: Line, m: Metrics) -> bool:
    if ln.figure:
        return False
    cs = cells(ln, m)
    if len(cs) < 2 or is_list_start(ln, m):
        return False
    widths = [_extent(c)[1] - _extent(c)[0] for c in cs]
    # running text split by one wide gap (e.g. loosely justified) is not a row
    if len(cs) == 2 and max(widths) > 0.6 * m.width:
        return False
    if len(ln.text.split()) >= 10 and ln.x1 - ln.x0 > 0.9 * m.width and len(cs) < 4:
        return False
    return True


def is_centered(ln: Line, m: Metrics) -> bool:
    """Short line with equal space on both sides (letterheads, titles)."""
    left_gap, right_gap = ln.x0 - m.left, m.right - ln.x1
    return (left_gap > 2 * m.body_size and right_gap > 2 * m.body_size
            and ln.x1 - ln.x0 < 0.7 * m.width
            and abs(left_gap - right_gap) < max(1.2 * m.body_size, 0.03 * m.width))


def _ratio(ln: Line, attr: str) -> float:
    vis = [c for c in ln.chars if c.c.strip()]
    return sum(getattr(c, attr) for c in vis) / max(1, len(vis))


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith((".", "?", "!", ":", '"', "”", "’", ")"))


def _is_full(ln: Line, m: Metrics) -> bool:
    return ln.x1 > m.right - max(3 * m.body_size, 0.1 * m.width)


def _is_heading_line(ln: Line, m: Metrics) -> bool:
    t = ln.text
    if not t or len(t) > 120:
        return False
    return (ln.size >= 1.18 * m.body_size or _ratio(ln, "ul") > 0.85
            or (_ratio(ln, "bold") > 0.9 and len(t) < 100 and not t.endswith(".")))


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------
def group_blocks(lines: list[Line], m: Metrics) -> list[list[Line]]:
    """Split a flow of lines into raw blocks (paragraph or table candidates)."""
    blocks, cur = [], []
    for ln in lines:
        if ln.figure:
            if cur:
                blocks.append(cur)
            blocks.append([ln])
            cur = []
            continue
        if cur:
            prev = cur[-1]
            tab, ptab = is_tabular(ln, m), is_tabular(prev, m)
            new = False
            if ln.page != prev.page:
                cont = (not tab and not ptab and _is_full(prev, m)
                        and abs(ln.x0 - min(x.x0 for x in cur[-2:])) < 3
                        and not _is_heading_line(ln, m))
                cont = cont or (not tab and not ptab and ln.text[:1].islower())
                new = not cont
            elif ln.y0 - prev.y1 > 0.45 * m.body_size:
                new = True                                   # blank line / spacing
            elif tab != ptab:
                new = True
            elif not tab:
                lab = _label(cur[0])
                hanging = lab is not None and abs(ln.x0 - lab[2]) < 2.5
                same_left = all(abs(x.x0 - cur[0].x0) < 1.5 for x in cur)
                indent = ln.x0 - prev.x0
                if hanging:
                    pass                                     # list item continuation
                elif same_left and 0.8 * m.body_size < indent < 5 * m.body_size:
                    new = True                               # first-line indent
                elif (prev.x1 < m.right - max(4 * m.body_size, 0.15 * m.width)
                      and abs(ln.x0 - prev.x0) < 2 and _ends_sentence(prev.text)
                      and (ln.text[:1].isupper() or ln.text[:1] in "\"“‘'(0123456789")
                      and not is_centered(prev, m)):
                    new = True                               # hard line break
                elif is_centered(ln, m) != is_centered(prev, m):
                    new = True
                elif _is_heading_line(prev, m) != _is_heading_line(ln, m):
                    new = True
            if new:
                blocks.append(cur)
                cur = []
        cur.append(ln)
    if cur:
        blocks.append(cur)
    return blocks


def _kind(block: list[Line], m: Metrics) -> str:
    if block[0].figure:
        return "F"
    if all(is_tabular(ln, m) for ln in block):
        return "T"
    if len(block) == 1 and (block[0].x1 - block[0].x0 < 0.55 * m.width) and not is_list_start(block[0], m):
        return "s"   # short single line: label, sub-heading or caption
    return "P"


def _aligned(region: list[Line], m: Metrics) -> bool:
    """A real table has at least two rows whose cells line up."""
    rows = [[_extent(c) for c in cells(ln, m)] for ln in region if is_tabular(ln, m)]
    if len(rows) < 2:
        return False
    def shares(a, b):
        hits = sum(1 for (x0, x1) in a for (y0, y1) in b if min(x1, y1) - max(x0, y0) > -2)
        return hits >= 2
    return any(shares(rows[i], rows[j]) for i in range(len(rows)) for j in range(i + 1, len(rows)))


def assemble(blocks: list[list[Line]], m: Metrics) -> list[tuple[str, list[Line]]]:
    """Merge table fragments into table regions; label everything else."""
    out = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        k = _kind(b, m)
        if k != "T":
            out.append((k, b))
            i += 1
            continue
        region = list(b)
        j = i + 1
        while j < len(blocks):
            nb = blocks[j]
            nk = _kind(nb, m)
            same_page = nb[0].page == region[-1].page
            gap = nb[0].y0 - region[-1].y1 if same_page else 0.0
            if gap > 3.5 * m.pitch:
                break
            if nk == "T":
                region.extend(nb)
                j += 1
                continue
            if nk == "s" and j + 1 < len(blocks) and _kind(blocks[j + 1], m) == "T":
                region.extend(nb)
                j += 1
                continue
            # a wrapped label line directly below a row ("exceeds cost)")
            if (nk == "s" and nb[0].text[:1].islower() and gap < 0.45 * m.body_size
                    and abs(nb[0].x0 - region[-1].x0) < 3):
                region.extend(nb)
                j += 1
                continue
            break
        # header lines right above the region (wrapped header, bold title row)
        while out and out[-1][0] in ("s", "T") and region[0].y0 - out[-1][1][-1].y1 < 0.45 * m.body_size \
                and out[-1][1][-1].page == region[0].page and _ratio(out[-1][1][-1], "bold") > 0.9:
            region = out.pop()[1] + region
        if _aligned(region, m):
            out.append(("T", region))
        else:
            out.extend(("P", [ln]) for ln in region)
        i = j
    # a bold single line followed by a wrapped bold header line belongs to the table
    return out


# --------------------------------------------------------------------------
# Inline formatting
# --------------------------------------------------------------------------
def _smart_quotes(s: str) -> str:
    out = list(s)
    for i, ch in enumerate(s):
        prev = s[i - 1] if i else " "
        nxt = s[i + 1] if i + 1 < len(s) else " "
        if ch == '"':
            out[i] = "“" if (prev.isspace() or prev in "([{—–-/‘“") else "”"
        elif ch == "'":
            word = re.match(r"[A-Za-z]+", s[i + 1:])
            opening = (prev.isspace() or prev in "([{—–“") and (nxt.isalpha())
            if opening and not (word and word.group(0).lower() in ELISIONS):
                out[i] = "‘"
            else:
                out[i] = "’"
    return "".join(out)


def _tidy(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = s.replace("...", "…")
    s = re.sub(r"\s*(?<!-)--(?!-)\s*", "—", s)                 # typewriter dash
    s = re.sub(r"(?<![\d-])((?:1[5-9]|20)\d\d)-(\d\d(?:\d\d)?)(?![\d-])", r"\1–\2", s)  # 1968-69
    for frac, glyph in (("1/2", "½"), ("1/4", "¼"), ("3/4", "¾")):
        s = re.sub(rf"(?<=\d)[ -]{frac}(?![\d/])", glyph, s)    # 6 1/2 -> 6½
        s = re.sub(rf"(?<![\d/]){frac}(?![\d/])", glyph, s)
    return s


def inline_html(chars: list[Char], *, strip_ul=False, strip_bold=False, typography=True) -> str:
    """Styled characters -> XHTML (em for italic/underline, strong, sup)."""
    text = "".join(c.c for c in chars)
    if typography:
        text = _smart_quotes(text)
    pieces, buf, cur = [], [], None

    def flush():
        if not buf:
            return
        s = "".join(buf)
        em, strong, sup = cur
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        core = html.escape(s.strip(), quote=False)
        if core:
            if sup:
                core = f"<sup>{core}</sup>"
            if em:
                core = f"<em>{core}</em>"
            if strong:
                core = f"<strong>{core}</strong>"
        pieces.append(lead + core + trail)

    for c, ch in zip(chars, text):
        if ch.strip():
            k = (bool(c.italic or (c.ul and not strip_ul)), bool(c.bold and not strip_bold), bool(c.sup))
        else:
            k = cur if cur is not None else (False, False, False)
        if k != cur:
            flush()
            buf, cur = [], k
        buf.append(ch)
    flush()
    out = "".join(pieces)
    out = re.sub(r"</em>(\s*)<em>", r"\1", out)
    out = re.sub(r"</strong>(\s*)<strong>", r"\1", out)
    return _tidy(out) if typography else re.sub(r"\s+", " ", out).strip()


def _join_lines(lines: list[Line], m: Metrics) -> list[Char]:
    """Characters of consecutive lines joined into one run of text."""
    out: list[Char] = []
    for ln in lines:
        cs = list(ln.chars)
        if out:
            tail = "".join(c.c for c in out[-30:])
            head = "".join(c.c for c in cs[:30]).lstrip()
            if tail.endswith("­"):
                out.pop()
            elif re.search(r"[A-Za-z]-$", tail) and head[:1].islower():
                left = re.search(r"([A-Za-z]+)-$", tail).group(1).lower()
                right = re.match(r"[A-Za-z]+", head).group(0).lower()
                compound = f"{left}-{right}" in m.hyphenated
                if m.justified and not compound and (left + right in m.words or len(left) > 1):
                    out.pop()                                # soft hyphenation
            elif not tail.endswith(("—", "–", "/")) and not tail.endswith("--"):
                out.append(Char(" ", 0, 0, 0, 0, 0, False, False))
        out.extend(cs)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
class Renderer:
    def __init__(self, m: Metrics, heading_sizes: list[float], base_level: int = 1, typography=True):
        self.m = m
        self.sizes = heading_sizes        # distinct heading font sizes, largest first
        self.base = base_level
        self.typo = typography
        self.after_closing = False

    def inline(self, chars, **kw):
        return inline_html(chars, typography=self.typo, **kw)

    def heading_level(self, ln: Line) -> int | None:
        for k, s in enumerate(self.sizes):
            if abs(ln.size - s) < 0.6:
                return min(self.base + k, 6)
        if _ratio(ln, "ul") > 0.85 or (_ratio(ln, "bold") > 0.9 and not ln.text.endswith(".")):
            return min(self.base + len(self.sizes), 6)
        return None

    def block(self, kind: str, block: list[Line], prev_kind: str | None) -> str:
        if kind == "F":
            return f"![](images/{block[0].figure})"
        if kind == "T":
            return render_table(block, self.m, self.typo)
        m = self.m
        first = block[0]
        if len(block) == 1 and _is_heading_line(first, m) and not is_list_start(first, m):
            level = self.heading_level(first)
            if level:
                cls = ' class="center"' if is_centered(first, m) else ""
                text = self.inline(first.chars, strip_ul=True, strip_bold=True)
                return f"<h{level}{cls}>{text}</h{level}>"
        if is_list_start(first, m, block[1] if len(block) > 1 else None):
            token, k, _x, _gap = _label(first)
            label = html.escape(token)
            item = Line(first.page, first.chars[k:], first.x0, first.x1, first.y0, first.y1,
                        first.base, first.size)
            chars = _join_lines([item] + block[1:], m)
            return f'<p class="item"><span class="label">{label}</span> {self.inline(chars)}</p>'
        text = self.inline(_join_lines(block, m))
        plain = first.text
        if len(block) == 1 and is_centered(first, m):
            return f'<p class="center">{text}</p>'
        if min(ln.x0 for ln in block) > m.left + 1.5 * m.body_size:
            return f"<blockquote><p>{text}</p></blockquote>"
        cls = ""
        if prev_kind == "T" and re.match(r"^(\*|\(\d\)|\d\))\s", plain):
            cls = "table-note"
        elif CLOSING_RE.match(plain) and len(block) == 1:
            cls = "closing"
            self.after_closing = True
            return f'<p class="{cls}">{text}</p>'
        elif self.after_closing and len(block) <= 2 and len(plain) < 60:
            cls = "signature"
        elif SALUTATION_RE.match(plain):
            cls = "salutation"
        self.after_closing = False
        return f'<p class="{cls}">{text}</p>' if cls else f"<p>{text}</p>"


def render_table(region: list[Line], m: Metrics, typography=True) -> str:
    rows = []
    prev = None
    for ln in region:
        gap = prev is not None and prev.page == ln.page and ln.y0 - prev.y1 > 0.45 * m.body_size
        rows.append({"line": ln, "cells": cells(ln, m), "gap": gap, "bold": _ratio(ln, "bold") > 0.9})
        prev = ln

    # header: leading bold rows without a blank line between them
    n_head = 0
    for r in rows:
        single_centered = len(r["cells"]) == 1 and is_centered(r["line"], m)
        if r["bold"] and (n_head == 0 or not r["gap"]) and not single_centered:
            n_head += 1
        else:
            break
    if n_head == len(rows):
        n_head = 0
    body = rows[n_head:]

    # column bands from the multi-cell body rows
    ivs = sorted(_extent(c) for r in body if len(r["cells"]) >= 2 for c in r["cells"])
    if not ivs:
        ivs = sorted(_extent(c) for r in rows for c in r["cells"])
    bands: list[list[float]] = []
    for a, b in ivs:
        if bands and a <= bands[-1][1] + 2:
            bands[-1][1] = max(bands[-1][1], b)
        else:
            bands.append([a, b])
    ncol = len(bands)

    def col_of(cs):
        x0, x1 = _extent(cs)
        best, score = 0, -1e9
        for k, (a, b) in enumerate(bands):
            ov = min(b, x1) - max(a, x0)
            sc = ov if ov > 0 else -min(abs(x0 - b), abs(x1 - a))
            if sc > score:
                best, score = k, sc
        return best

    def slots(r):
        out = [[] for _ in range(ncol)]
        for cs in r["cells"]:
            out[col_of(cs)].append(cs)
        return out

    # wrapped labels: a lower-case continuation of the first column
    merged = []
    for r in body:
        s = slots(r)
        only_first = s[0] and not any(s[1:])
        if merged and only_first and _cell_text(s[0][0])[:1].islower() and not r["gap"]:
            merged[-1]["slots"][0].extend(s[0])
            continue
        merged.append({**r, "slots": s})

    def cell_html(cell_list, strip_bold=False):
        parts = [inline_html(cs, strip_bold=strip_bold, strip_ul=True, typography=typography) for cs in cell_list]
        txt = " ".join(p for p in parts if p)
        if typography:
            if txt in ("--", "-", "—"):
                return "—"
            if NUMERIC_CELL_RE.match(txt):
                txt = re.sub(r"^([$£€(]*)-(?=[$\d.])", "\\1−", txt)   # -8.4% -> −8.4%
        return txt

    numeric = []
    for k in range(ncol):
        vals = [cell_html(r["slots"][k]) for r in merged if r["slots"][k]]
        num = sum(1 for v in vals if NUMERIC_CELL_RE.match(re.sub(r"<[^>]+>", "", v)))
        numeric.append(k > 0 and bool(vals) and num >= 0.6 * len(vals))

    head = [[] for _ in range(ncol)]
    for r in rows[:n_head]:
        for k, s in enumerate(slots(r)):
            for cs in s:
                head[k].append(cell_html([cs], strip_bold=True))

    trs = []
    for r in merged:
        ln = r["line"]
        cls = ["gap"] if r["gap"] else []
        if len(r["cells"]) == 1 and is_centered(ln, m):
            text = cell_html(r["cells"], strip_bold=True)
            trs.append(f'<tr class="{" ".join(["sub"] + cls)}"><th colspan="{ncol}">{text}</th></tr>')
            continue
        if r["bold"]:
            cls.append("total")
        tds = []
        for k, s in enumerate(r["slots"]):
            al = ' class="num"' if numeric[k] else ""
            tds.append(f"<td{al}>{cell_html(s, strip_bold=r['bold'])}</td>")
        c = f' class="{" ".join(cls)}"' if cls else ""
        trs.append(f"<tr{c}>{''.join(tds)}</tr>")

    thead = ""
    if any(head):
        ths = []
        for k, h in enumerate(head):
            al = ' class="num"' if numeric[k] else ""
            ths.append(f"<th{al}>{'<br/>'.join(h)}</th>")
        thead = f"<thead><tr>{''.join(ths)}</tr></thead>"
    return f'<table class="data">{thead}<tbody>{"".join(trs)}</tbody></table>'


# --------------------------------------------------------------------------
# Letters
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _head(lines: list[Line], m: Metrics) -> list[Line]:
    """Short lines at the top of a page: letterhead, dateline, salutation."""
    out = []
    for ln in lines:
        if ln.figure:
            continue
        if ln.y0 > 0.4 * m.page_height or ln.x1 - ln.x0 > 0.6 * m.width or len(out) >= 10:
            break
        out.append(ln)
    return out


def _is_set_off(ln: Line, m: Metrics) -> bool:
    """Centered or right-set (as letterheads and datelines are)."""
    return is_centered(ln, m) or ln.x0 > m.left + 0.4 * m.width


def _parse_date(text: str) -> tuple[date | None, str | None]:
    mt = DATE_RE.match(text.strip())
    if mt:
        month = MONTHS.index(mt.group("m").capitalize()) + 1
        year = int(mt.group("y"))
        if mt.group("d"):
            d = date(year, month, int(mt.group("d")))
            return d, f"{MONTHS[month - 1]} {d.day}, {year}"
        return date(year, month, 1), f"{MONTHS[month - 1]} {year}"
    mt = NUMERIC_DATE_RE.search(text)
    if mt:
        try:
            year = 1900 + int(mt.group("y"))
            d = date(year, int(mt.group("m")), int(mt.group("d")))
            return d, f"{MONTHS[d.month - 1]} {d.day}, {year}"
        except ValueError:
            pass
    return None, None


def find_letter_starts(pages: list[list[Line]], m: Metrics) -> list[int]:
    """Indices (into pages) where a new letter begins."""
    heads = [_head(lines, m) for lines in pages]
    recurring = Counter()
    for h in heads:
        recurring.update({_norm(ln.text) for ln in h})
    starts = []
    for i, h in enumerate(heads):
        has_date = any(_parse_date(ln.text)[0] and DATE_RE.match(ln.text.strip()) for ln in h)
        set_off = [ln for ln in h if _is_set_off(ln, m)]
        repeated = sum(1 for ln in set_off if recurring[_norm(ln.text)] >= 2)
        if has_date or (len(set_off) >= 2 and repeated >= 1):
            starts.append(i)
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return starts


def _review_year(d: date, text: str) -> int:
    """Year a letter reports on: early-year annual letters cover the year before."""
    if d.month <= 3 and text.count(str(d.year - 1)) > text.count(str(d.year)):
        return d.year - 1
    return d.year


def _titlecase(s: str) -> str:
    small = {"a", "an", "and", "of", "the", "to", "in", "on", "for", "at", "by"}
    words = s.lower().split()
    return " ".join(w if (k and w in small) else w[:1].upper() + w[1:] for k, w in enumerate(words))


def split_letters(pages: list[list[Line]], m: Metrics) -> list[dict]:
    starts = find_letter_starts(pages, m)
    heads = {s: _head(pages[s], m) for s in starts}
    recurring = Counter()
    for h in heads.values():
        recurring.update({_norm(ln.text) for ln in h})
    letters = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(pages)
        head = heads[s]
        when, title, subtitle = None, None, None
        drop: list[Line] = []
        date_at = next((k for k, ln in enumerate(head) if DATE_RE.match(ln.text.strip())), None)
        if date_at is not None:
            drop = head[:date_at + 1]
            when, title = _parse_date(head[date_at].text)
        else:
            for ln in head:                       # letterhead without a dateline
                if _is_set_off(ln, m) or recurring[_norm(ln.text)] >= 2 and len(starts) > 2:
                    drop.append(ln)
                else:
                    break
        letterhead = []
        last_y = None
        for ln in (drop[:-1] if date_at is not None else drop):   # all but the dateline
            if (last_y is not None and ln.y0 - last_y > 0.45 * m.body_size and is_centered(ln, m)
                    and ln.text.isupper() and len(ln.text.split()) >= 3):
                subtitle = ln.text
            else:
                letterhead.append(ln.text)
            last_y = ln.y1
        # a centered title just below the dateline ("SECOND ANNUAL LETTER")
        rest = [ln for ln in head if ln not in drop]
        if rest and is_centered(rest[0], m) and rest[0].text.isupper() and len(rest[0].text.split()) >= 3:
            subtitle = subtitle or rest[0].text
            drop.append(rest[0])
        dropped = {id(ln) for ln in drop}
        body = [ln for p in pages[s:e] for ln in p if id(ln) not in dropped]
        if when is None or title.count(" ") < 2:   # no dateline, or no day in it
            for ln in reversed([x for x in body if not x.figure][-8:]):
                d, t = _parse_date(ln.text)
                if d is None:
                    d, t = _parse_date(ln.text.split()[-1] if ln.text.split() else "")
                if d and (when is None or (d.year, d.month) == (when.year, when.month)):
                    when, title = d, t
                    break
        letters.append({"lines": body, "date": when, "title": title, "subtitle": subtitle,
                        "letterhead": letterhead, "first_page": s + 1, "last_page": e})
    return letters


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def _heading_sizes(lines: list[Line], m: Metrics) -> list[float]:
    sizes = Counter(round(ln.size * 2) / 2 for ln in lines
                    if not ln.figure and ln.size >= 1.18 * m.body_size and len(ln.text) < 120)
    return sorted((s for s, n in sizes.items()), reverse=True)[:3]


def render(lines: list[Line], m: Metrics, sizes: list[float], base_level: int, typography=True) -> list[tuple[int | None, str]]:
    """Render a flow of lines; returns (heading level or None, html) per block."""
    r = Renderer(m, sizes, base_level, typography)
    out = []
    prev_kind = None
    for kind, block in assemble(group_blocks(lines, m), m):
        h = r.block(kind, block, prev_kind)
        lvl = None
        mt = re.match(r"<h(\d)", h)
        if mt:
            lvl = int(mt.group(1))
        out.append((lvl, h))
        prev_kind = kind
    return out


def _signature(letters_html: list[str]) -> str | None:
    names = Counter()
    for h in letters_html:
        for mt in re.finditer(r'<p class="signature">(.*?)</p>', h):
            name = re.sub(r"<[^>]+>", "", mt.group(1))
            name = NUMERIC_DATE_RE.sub("", name).strip(" ,")
            if 1 <= len(name.split()) <= 5:
                names[name] += 1
    return names.most_common(1)[0][0] if names else None


def detect_layout(pages: list[list[Line]], m: Metrics) -> str:
    starts = find_letter_starts(pages, m)
    dated = sum(1 for s in starts if any(DATE_RE.match(ln.text.strip()) for ln in _head(pages[s], m)))
    return "letters" if len(starts) >= 3 and dated >= 2 else "book"


def convert_pdf(input_path: str, output_dir: Path, max_pages: int = None, start_page: int = None,
                layout: str = "auto", typography: bool = True) -> dict:
    """Convert a born-digital PDF; writes Markdown chapters and description.json.

    Returns a summary dict (layout, chapters, seconds).
    """
    import time
    import pypdfium2

    t0 = time.time()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pypdfium2.PdfDocument(input_path)
    try:
        count = len(pdf)
        width, height = pdf[0].get_size() if count else (612.0, 792.0)
        meta = pdf.get_metadata_dict()
    finally:
        pdf.close()
    first = start_page or 0
    last = count if max_pages is None else min(first + max_pages, count)
    page_ids = list(range(first, last))
    if not page_ids:
        raise ValueError(f"No pages to convert: the PDF has {count} page(s), start page is {first}")

    pages = read_pdf(input_path, page_ids, image_dir=output_dir / "images")
    pages = strip_furniture(pages, height)
    m = measure(pages, width, height)
    notes = extract_footnotes(pages, m)
    linker = NoteLinker(notes, m, typography)
    if layout == "auto":
        layout = detect_layout(pages, m)

    stem = Path(input_path).stem
    for old in output_dir.glob("[0-9][0-9][0-9].md"):
        old.unlink()
    chapters = []
    metadata = {}
    if meta.get("Title"):
        metadata["dc:title"] = meta["Title"].strip()
    if meta.get("Author"):
        metadata["dc:creator"] = meta["Author"].strip()

    if layout == "letters":
        letters = split_letters(pages, m)
        sizes = _heading_sizes([ln for p in pages for ln in p], m)
        rendered = []
        for n, letter in enumerate(letters, 1):
            blocks = render(letter["lines"], m, sizes, base_level=2, typography=typography)
            body_html, used = linker.link("\n\n".join(h for _, h in blocks))
            if n == len(letters):
                used += linker.leftovers()
            if used:
                body_html += "\n\n" + linker.section(used)
            rendered.append(body_html)
            plain = re.sub(r"<[^>]+>", " ", body_html)
            group = str(_review_year(letter["date"], plain)) if letter["date"] else (
                chapters[-1]["group"] if chapters else "")
            title = letter["title"] or (letter["subtitle"] and _titlecase(letter["subtitle"])) or f"Letter {n}"
            head = ['<header class="letter-head">']
            org = next((t for t in letter["letterhead"]
                        if re.search(r"[A-Za-z]{3}", t) and not re.match(r"^\d{4}\b", t)), None)
            if org:
                head.append(f'<p class="letter-org">{html.escape(_titlecase(org) if org.isupper() else org)}</p>')
            if group:
                head.append(f'<p class="letter-year">{html.escape(group)}</p>')
            head.append(f'<h1 class="letter-date">{html.escape(title)}</h1>')
            if letter["subtitle"]:
                head.append(f'<p class="letter-subtitle">{html.escape(_titlecase(letter["subtitle"]))}</p>')
            head.append("</header>")
            name = f"{n:03d}.md"
            (output_dir / name).write_text("\n".join(head) + "\n\n" + body_html + "\n", encoding="utf-8")
            chapters.append({"markdown": name, "css": "", "title": title, "group": group})
        author = _signature(rendered)
        if author:
            metadata["dc:creator"] = author
        org = Counter(ln for letter in letters for ln in letter["letterhead"][:1]).most_common(1)
        years = [c["group"] for c in chapters if c["group"]]
        if org:
            span = f", {years[0]}–{years[-1]}" if years and years[0] != years[-1] else ""
            metadata["dc:title"] = f"{_titlecase(org[0][0])} Letters{span}"
        if letters and letters[0]["date"]:
            metadata["dc:date"] = letters[0]["date"].isoformat()[:4]
    else:
        lines = [ln for p in pages for ln in p]
        sizes = _heading_sizes(lines, m)
        blocks = render(lines, m, sizes, base_level=1, typography=typography)
        top = min((lvl for lvl, _ in blocks if lvl), default=None)
        parts: list[tuple[str, list[str]]] = []
        for lvl, h in blocks:
            if lvl is not None and lvl == top and sum(1 for x, _ in blocks if x == top) >= 2:
                parts.append((re.sub(r"<[^>]+>", "", h), [h]))
            elif parts:
                parts[-1][1].append(h)
            else:
                parts.append(("", [h]))
        texts = []
        for k, (_title, hs) in enumerate(parts):
            body_html, used = linker.link("\n\n".join(hs))
            if k == len(parts) - 1:
                used += linker.leftovers()
            texts.append(body_html + ("\n\n" + linker.section(used) if used else ""))
        if len(parts) == 1:
            name = f"{stem}.md"
            (output_dir / name).write_text(texts[0] + "\n", encoding="utf-8")
            chapters.append({"markdown": name, "css": "", "title": metadata.get("dc:title", stem)})
        else:
            for n, ((title, _hs), body_html) in enumerate(zip(parts, texts), 1):
                name = f"{n:03d}.md"
                (output_dir / name).write_text(body_html + "\n", encoding="utf-8")
                chapters.append({"markdown": name, "css": "", "title": html.unescape(title) or "Front Matter"})

    description_path = output_dir / "description.json"
    existing = json.loads(description_path.read_text(encoding="utf-8")) if description_path.exists() else {}
    existing.setdefault("metadata", {})
    for k, v in metadata.items():
        existing["metadata"].setdefault(k, v)
    existing["chapters"] = chapters
    existing["layout"] = layout
    existing["engine"] = "textlayer"
    existing.setdefault("theme", "letters" if layout == "letters" else "book")
    existing.setdefault("default_css", ["style.css"])
    existing.setdefault("cover_image", None)
    description_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    secs = time.time() - t0
    print(f"Text layer: {len(page_ids)} pages, layout '{layout}', {len(chapters)} chapter(s) in {secs:.1f}s")
    return {"layout": layout, "chapters": len(chapters), "seconds": secs}
