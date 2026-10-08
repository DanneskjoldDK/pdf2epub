"""Download freely available PDFs from archive.org via its public metadata API.

Only items whose files archive.org serves openly are supported. Lending-library
items (``access-restricted-item``) are refused: their page images are only
available on loan and must not be turned into a permanent copy.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

METADATA_URL = "https://archive.org/metadata/{}"
DOWNLOAD_URL = "https://archive.org/download/{}/{}"

# Prefer the OCR'd text PDF; fall back to any other PDF archive.org lists.
PDF_FORMAT_PREFERENCE = ["Text PDF", "Additional Text PDF", "Image Container PDF", "Grayscale PDF"]

# archive.org mostly uses ISO 639-2 codes or English names; EPUB wants BCP 47.
LANGUAGE_CODES = {
    "eng": "en", "english": "en",
    "dan": "da", "danish": "da",
    "ger": "de", "deu": "de", "german": "de",
    "fre": "fr", "fra": "fr", "french": "fr",
    "spa": "es", "spanish": "es",
    "ita": "it", "italian": "it",
    "swe": "sv", "swedish": "sv",
    "nor": "no", "norwegian": "no",
    "dut": "nl", "nld": "nl", "dutch": "nl",
    "lat": "la", "latin": "la",
}


def is_archive_url(value: str) -> bool:
    """Return True if value looks like an archive.org item URL."""
    return bool(re.match(r"^https?://(www\.)?archive\.org/", value.strip(), re.IGNORECASE))


def parse_identifier(value: str) -> str:
    """Extract the item identifier from an archive.org URL (or return it unchanged)."""
    value = value.strip()
    if not is_archive_url(value):
        return value
    parts = [p for p in urllib.parse.urlparse(value).path.split("/") if p]
    # /details/<id>[/...], /download/<id>[/...], /stream/<id>[/...]
    if len(parts) >= 2 and parts[0] in ("details", "download", "stream", "embed"):
        return urllib.parse.unquote(parts[1])
    raise ValueError(f"Could not find an archive.org item identifier in: {value}")


def fetch_metadata(identifier: str) -> dict:
    with urllib.request.urlopen(METADATA_URL.format(urllib.parse.quote(identifier)), timeout=60) as resp:
        data = json.load(resp)
    if not data or "metadata" not in data:
        raise ValueError(f"archive.org item not found: {identifier}")
    return data


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _join(value):
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return value


def choose_pdf(files: list[dict]) -> dict:
    """Pick the best PDF from an item's file list."""
    pdfs = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    if not pdfs:
        raise ValueError("This archive.org item has no freely downloadable PDF.")
    for fmt in PDF_FORMAT_PREFERENCE:
        for f in pdfs:
            if f.get("format") == fmt:
                return f
    return pdfs[0]


def epub_metadata(identifier: str, meta: dict) -> dict:
    """Map archive.org metadata to the dc:* fields used by mark2epub."""
    language = _first(meta.get("language")) or "en"
    language = LANGUAGE_CODES.get(str(language).strip().lower(), str(language))
    date = _first(meta.get("date")) or _first(meta.get("year")) or ""
    result = {
        "dc:title": _first(meta.get("title")) or identifier,
        "dc:creator": _join(meta.get("creator")) or "Unknown Author",
        "dc:identifier": f"archive.org:{identifier}",
        "dc:language": language,
        "dc:rights": _first(meta.get("rights")) or _first(meta.get("licenseurl")) or "",
        "dc:publisher": _first(meta.get("publisher")) or "",
        "dc:date": str(date),
    }
    return {k: v for k, v in result.items() if v}


def download(value: str, dest_dir: Path) -> tuple[Path, dict]:
    """
    Download the PDF for an archive.org URL or identifier into dest_dir.
    Returns (pdf_path, epub_metadata).
    """
    identifier = parse_identifier(value)
    data = fetch_metadata(identifier)
    meta = data["metadata"]

    if str(meta.get("access-restricted-item", "")).lower() == "true" or data.get("is_dark"):
        raise PermissionError(
            f"'{identifier}' is a lending-library (borrow-only) item on archive.org. "
            "Only freely downloadable items are supported."
        )

    pdf = choose_pdf(data.get("files", []))
    if pdf.get("private") in (True, "true"):
        raise PermissionError(f"The PDF for '{identifier}' is not publicly downloadable.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = dest_dir / f"{identifier}.pdf"
    size = int(pdf.get("size") or 0)

    if pdf_path.exists() and size and pdf_path.stat().st_size == size:
        print(f"Already downloaded: {pdf_path}")
    else:
        url = DOWNLOAD_URL.format(urllib.parse.quote(identifier), urllib.parse.quote(pdf["name"]))
        print(f"Downloading {pdf['name']} ({pdf.get('format', 'PDF')}, {size / 1e6:.1f} MB)...")
        tmp_path = pdf_path.with_suffix(".pdf.part")
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp_path, "wb") as out:
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if size:
                    print(f"\r  {done / size:6.1%}", end="", file=sys.stderr)
        print(file=sys.stderr)
        tmp_path.replace(pdf_path)
        print(f"Saved to: {pdf_path}")

    return pdf_path, epub_metadata(identifier, meta)
