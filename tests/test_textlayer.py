"""Tests for the text-layer engine on the fixtures in tests/fixtures."""
import json
import re
from pathlib import Path

import pytest

from modules import textlayer

FIXTURES = Path(__file__).parent / "fixtures"


def chapter(d: Path, name: str) -> str:
    return (d / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def letters(tmp_path_factory):
    out = tmp_path_factory.mktemp("letters")
    textlayer.convert_pdf(str(FIXTURES / "letters.pdf"), out)
    return out


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    out = tmp_path_factory.mktemp("book")
    textlayer.convert_pdf(str(FIXTURES / "book.pdf"), out)
    return out


# ---------------------------------------------------------------- assessment
def test_assess_accepts_single_column_text():
    for name in ("letters.pdf", "book.pdf"):
        result = textlayer.assess(str(FIXTURES / name))
        assert result["coverage"] == 1.0
        assert result["suitable"]


def test_assess_declines_two_columns():
    result = textlayer.assess(str(FIXTURES / "twocolumn.pdf"))
    assert result["multicolumn"]
    assert not result["suitable"]


# ------------------------------------------------------------------- letters
def test_letters_are_split_dated_and_grouped(letters):
    desc = json.loads(chapter(letters, "description.json"))
    assert desc["layout"] == "letters"
    assert desc["theme"] == "letters"
    assert [(c["title"], c["group"]) for c in desc["chapters"]] == [
        ("January 21, 1972", "1971"),   # an annual letter covers the year before
        ("July 9, 1972", "1972"),       # "July 9th" with a superscript ordinal
        ("October 2, 1972", "1972"),
        ("January 19, 1973", "1972"),
    ]
    assert desc["metadata"]["dc:creator"] == "Edward T. Lindqvist"
    assert desc["metadata"]["dc:title"] == "Harbor Lane Partners, Ltd. Letters, 1971–1972"


def test_letterhead_and_page_numbers_are_dropped(letters):
    for name in ("001.md", "002.md", "003.md", "004.md"):
        text = chapter(letters, name)
        assert "HARBOR LANE" not in text
        assert "PORTLAND" not in text
        assert "<p>1</p>" not in text and "<p>2</p>" not in text


def test_headings_and_underlined_emphasis(letters):
    text = chapter(letters, "001.md")
    assert '<h1 class="letter-date">January 21, 1972</h1>' in text
    assert "<h2>How We Fared in 1971</h2>" in text
    assert "measured <em>before</em> any allocation" in text
    assert '<h2 class="center">APPENDIX</h2>' in chapter(letters, "004.md")


def test_paragraphs(letters):
    text = chapter(letters, "001.md")
    # a paragraph that continues on the next page is joined
    assert "That is the only claim I care to make for it.</p>" in text
    # a forced line break after a sentence starts a new paragraph
    assert "was $4.15 per share.</p>" in text
    assert "<p>Mr. Abel attended" in text
    assert "<blockquote><p>“A single year" in text and "longer stretch.”</p></blockquote>" in text


def test_list_items_keep_their_labels(letters):
    text = chapter(letters, "001.md")
    for label in ("(1)", "(2)", "(3)"):
        assert f'<p class="item"><span class="label">{label}</span> ' in text
    assert "general market in the short run" in text   # continuation line joined
    assert text.count('<p class="item">') == 3
    assert '<span class="label">(a)</span>' in chapter(letters, "003.md")


def test_tables(letters):
    text = chapter(letters, "001.md")
    assert '<th class="num">Partnerships Operating<br/>Entire Year</th>' in text
    # shading restarts after the sub-heading: its first row is shaded again
    assert '<tr class="sub gap"><th colspan="3">COMPOUNDED</th></tr><tr class="gap shade"><td>1968</td>' in text
    assert '<tr><td>1968–69</td><td class="num">20.6%</td><td class="num">−2.8%</td>' in text
    assert '<p class="table-note">* Including dividends' in text
    # tab-aligned figures become a two-column table
    assert '<td>Harbor Lane Partners</td><td class="num">4.6%</td>' in chapter(letters, "002.md")


def test_balance_sheet(letters):
    text = chapter(letters, "004.md")
    assert '<td>Marketable securities at market (which exceeds cost)</td>' in text
    assert len(re.findall(r'<tr class="[^"]*\btotal\b', text)) == 3
    assert '<th class="num">11/30/72 (unaudited)</th>' in text


def test_typography(letters):
    text = chapter(letters, "001.md") + chapter(letters, "002.md")
    for expected in ("“yardstick”", "It’s a habit", "keep—good years", "6½% notes", "called at 104¼"):
        assert expected in text


def test_salutation_closing_and_signature(letters):
    text = chapter(letters, "003.md")
    assert '<p class="salutation">To My Partners:</p>' in text
    assert '<p class="closing">Sincerely,</p>' in text
    assert '<p class="signature">Edward T. Lindqvist</p>' in text


# ---------------------------------------------------------------------- book
def test_book_chapters_and_running_head(book):
    desc = json.loads(chapter(book, "description.json"))
    assert desc["layout"] == "book"
    assert [c["title"] for c in desc["chapters"]] == ["The Slow Craft", "Waiting Well"]
    text = chapter(book, "001.md") + chapter(book, "002.md")
    assert "ON PATIENCE" not in text
    assert "<h2>Measuring the Unmeasurable</h2>" in text


def test_book_first_line_indents_make_paragraphs(book):
    text = chapter(book, "001.md")
    assert "a list of appointments.</p>\n\n<p>Patience in a craft" in text


def test_book_footnotes_are_linked(book):
    first, second = chapter(book, "001.md"), chapter(book, "002.md")
    assert 'met.<sup><a class="noteref" epub:type="noteref" id="ref-fn1" href="#fn1">1</a></sup>' in first
    assert '<aside epub:type="footnote" id="fn1"><p><a href="#ref-fn1">1</a> The phrase is borrowed' in first
    assert '<aside epub:type="footnote" id="fn2">' in first
    assert '<aside epub:type="footnote" id="fn3">' in second   # notes stay with their chapter
    assert "1The phrase" not in first


def test_book_image_and_caption(book):
    images = list((book / "images").glob("*.png"))
    assert len(images) == 1
    text = chapter(book, "001.md")
    assert f"![](images/{images[0].name})" in text
    assert '<p class="center"><em>Figure 1. Hours of practice recorded each month.</em></p>' in text


# -------------------------------------------------------------- typography
@pytest.mark.parametrize("raw, expected", [
    ("1968-69", "1968–69"),
    ("in 1946-60 we", "in 1946–60 we"),
    ("signed 1-30-61", "signed 1-30-61"),
    ("call 342-4110", "call 342-4110"),
    ("wait -- then", "wait—then"),
    ("a 6 1/2% note at 104 1/4", "a 6½% note at 104¼"),
    ("and so...", "and so…"),
])
def test_tidy(raw, expected):
    assert textlayer._tidy(raw) == expected


# --------------------------------------------------------------------- novel
@pytest.fixture(scope="module")
def novel(tmp_path_factory):
    out = tmp_path_factory.mktemp("novel")
    textlayer.convert_pdf(str(FIXTURES / "novel.pdf"), out)
    return out


def test_novel_chapters_and_title_page(novel):
    desc = json.loads(chapter(novel, "description.json"))
    assert desc["layout"] == "novel" and desc["theme"] == "novel"
    # the title page wins over junk PDF metadata ("Microsoft Word - ...", "Compaq_Owner")
    assert desc["metadata"]["dc:title"] == "The Keeper of Gull Point"
    assert desc["metadata"]["dc:creator"] == "Margaret Ellis"
    assert [c["title"] for c in desc["chapters"]] == [
        "I. In Which the Lighthouse Keeper Receives a Letter",
        "II. Which Concerns Oranges, and a Visitor",
        "III. In Which the Letter Is Opened at Last",
    ]


def test_novel_chapter_opening(novel):
    text = chapter(novel, "001.md")
    assert '<p class="chapter-number">Chapter I</p>' in text
    assert '<h1 class="chapter-title">In Which the Lighthouse Keeper Receives a Letter</h1>' in text
    assert '<p class="first">Mr. Tobias Wren had kept the light' in text
    assert "THE KEEPER OF GULL POINT" not in text


def test_novel_hand_wrapped_paragraphs(novel):
    text = chapter(novel, "001.md")
    # sentences ending at a line end inside a paragraph do not split it
    assert text.count("<p>") + text.count('<p class="first">') == 4
    assert "He made tea. He did not open it.</p>" in text
    assert "<p>“It has,” said Tobias.</p>" in chapter(novel, "002.md")


def test_titlecase_keeps_roman_numerals():
    assert textlayer._titlecase("THE REIGN OF CHARLES II") == "The Reign of Charles II"
    assert textlayer._titlecase("A TALE OF TWO CITIES") == "A Tale of Two Cities"
