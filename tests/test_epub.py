"""End-to-end: text-layer output -> EPUB with themes, grouped contents, cover."""
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from modules import mark2epub, textlayer

FIXTURES = Path(__file__).parent / "fixtures"
EPUBCHECK = os.environ.get("EPUBCHECK_JAR")


def build(tmp_path: Path, fixture: str, theme=None) -> Path:
    work = tmp_path / fixture
    textlayer.convert_pdf(str(FIXTURES / f"{fixture}.pdf"), work)
    mark2epub.convert_to_epub(work, tmp_path, interactive=False, theme=theme)
    return work / f"{fixture}.epub"


def test_letters_epub(tmp_path):
    epub = build(tmp_path, "letters")
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        assert "OPS/images/cover.jpg" in names
        assert "OPS/s00000-_title.xhtml" in names and "OPS/s00001-_contents.xhtml" in names
        opf = z.read("OPS/package.opf").decode()
        assert 'properties="cover-image"' in opf
        nav = z.read("OPS/TOC.xhtml").decode()
        # letters nested under their year
        assert re.search(r'<li><a href="s00002-001.xhtml">1971</a><ol><li><a href="s00002-001.xhtml">'
                         r'January 21, 1972</a></li></ol></li>', nav)
        assert 'epub:type="landmarks"' in nav
        ncx = z.read("OPS/toc.ncx").decode()
        assert '<meta name="dtb:depth" content="2"/>' in ncx
        contents = z.read("OPS/s00001-_contents.xhtml").decode()
        assert '<table class="letter-index">' in contents
        css = z.read("OPS/css/style.css").decode()
        assert "header.letter-head" in css


def test_default_theme_is_unchanged(tmp_path):
    epub = build(tmp_path, "book", theme="default")
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        assert not any("_title" in n or "_contents" in n for n in names)
        assert "OPS/images/cover.jpg" not in names


@pytest.mark.skipif(not (EPUBCHECK and shutil.which("java")), reason="set EPUBCHECK_JAR to run epubcheck")
@pytest.mark.parametrize("fixture, theme", [("letters", None), ("book", None), ("book", "default")])
def test_epubcheck(tmp_path, fixture, theme):
    epub = build(tmp_path, fixture, theme)
    result = subprocess.run(["java", "-jar", EPUBCHECK, str(epub)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
