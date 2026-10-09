# Test fixtures

All the documents were written for this test suite (no rights
restrictions). Each `.fodt` (flat OpenDocument) file is the source; the PDF
next to it is generated from it with LibreOffice.

| Fixture | What it exercises |
| --- | --- |
| `letters` | Four short fictional partnership letters: centered letterhead, datelines (one with an ordinal superscript), underlined headings and underlined emphasis, tables with wrapped bold headers, a centered sub-heading row and bold total rows, tab-aligned figures, a block quote, hanging-indent numbered items, a hard line break, a paragraph running across a page break, page numbers. |
| `book` | A short essay set as a book: centered chapter headings, a sub-heading, first-line indents without paragraph spacing, footnotes with a separator rule, a running header, page numbers, an embedded image and a caption. |
| `novel` | A short story set like a Project Gutenberg text in Word: a title page, "Chapter I" with an all-capitals title, hand-wrapped lines with blank lines between paragraphs, and junk PDF metadata ("Microsoft Word - …", "Compaq_Owner"). |
| `twocolumn` | Filler text in two columns, which the text-layer engine must decline. |

Regenerate a PDF whenever its `.fodt` source changes, and commit both:

```bash
soffice --headless --convert-to pdf --outdir tests/fixtures tests/fixtures/letters.fodt
soffice --headless --convert-to pdf --outdir tests/fixtures tests/fixtures/book.fodt
soffice --headless --convert-to pdf --outdir tests/fixtures tests/fixtures/novel.fodt
soffice --headless --convert-to pdf --outdir tests/fixtures tests/fixtures/twocolumn.fodt
```

When editing the sources: automatic styles cannot inherit from other
automatic styles (give each one all its properties), and XML comments
cannot contain double hyphens.
