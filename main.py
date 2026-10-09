#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path
import modules.pdf2md as pdf2md
import modules.mark2epub as mark2epub
import modules.archive_org as archive_org


def report_device():
    """Say which device marker's models will run on (imports torch lazily)."""
    import torch
    if torch.cuda.is_available():
        print("CUDA is available. Using GPU for processing.")
    elif torch.backends.mps.is_available():
        print("MPS is available. Using Apple Silicon for processing.")
    else:
        print("CUDA is not available. Using CPU for processing.")


def choose_engine(pdf_path: Path, requested: str) -> str:
    """Resolve --engine auto to 'textlayer' or 'marker' for one PDF."""
    if requested != "auto":
        return requested
    try:
        import modules.textlayer as textlayer
        result = textlayer.assess(str(pdf_path))
    except Exception as e:
        print(f"Could not inspect the text layer ({e}); using marker.")
        return "marker"
    if result["suitable"]:
        print("The PDF has its own text layer; using the fast text-layer engine.")
        return "textlayer"
    reason = ("its text is set in columns" if result["multicolumn"]
              else "it has no usable text layer (scanned?)")
    print(f"Using marker (OCR and layout models) because {reason}.")
    return "marker"


def main():
    parser = argparse.ArgumentParser(
        description='Convert PDF files to EPUB format via Markdown'
    )
    parser.add_argument(
        'input_path',
        nargs='?',
        type=str,
        help='Path to input PDF file or directory, or an archive.org item URL '
             '(default: ./input/*.pdf)'
    )
    parser.add_argument(
        'output_path',
        nargs='?',
        type=str,
        help='Path to output directory (default: directory named after PDF)'
    )
    parser.add_argument(
        '--max-pages',
        type=int,
        default=None,
        help='Maximum number of pages to process'
    )
    parser.add_argument(
        '--start-page',
        type=int,
        default=None,
        help='Page number to start from'
    )
    parser.add_argument(
        '--skip-epub',
        action='store_true',
        help='Skip EPUB generation, only create markdown'
    )
    parser.add_argument(
        '--skip-md',
        action='store_true',
        help='Skip markdown generation, use existing markdown files'
    )
    
    parser.add_argument(
        '--chunk-size',
        type=int,
        default=pdf2md.DEFAULT_CHUNK_SIZE,
        help='Pages converted per batch to limit memory use; lower it if the '
             'process runs out of memory, 0 converts all pages at once '
             f'(default: {pdf2md.DEFAULT_CHUNK_SIZE})'
    )
    parser.add_argument(
        '--engine',
        choices=['auto', 'marker', 'textlayer'],
        default='auto',
        help="PDF to Markdown engine. 'textlayer' reads the text the PDF already "
             "contains: much faster, keeps italics and underlining, but needs a "
             "born-digital, single-column PDF. 'marker' runs OCR and layout models "
             "and handles scans and multi-column pages. 'auto' (default) picks per PDF"
    )
    parser.add_argument(
        '--layout',
        choices=['auto', 'book', 'letters', 'novel'],
        default='auto',
        help="Text-layer engine only: 'novel' makes one chapter per 'Chapter N' heading; 'letters' makes one chapter per dated letter, "
             "grouped by year; 'book' splits at the top-level headings "
             "(default: detect)"
    )
    parser.add_argument(
        '--theme',
        choices=mark2epub.available_themes(),
        default=None,
        help="EPUB styling. 'letters' and 'book' add a generated cover, title page "
             "and contents page (default: the theme stored for this book, else "
             "letters/book for text-layer output and default for marker output)"
    )
    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Non-interactive: accept default EPUB metadata and skip markdown review'
    )

    args = parser.parse_args()

    # Metadata to prefill per PDF (e.g. from archive.org)
    known_metadata = {}

    # Download from archive.org if a URL was given
    if args.input_path and archive_org.is_archive_url(args.input_path):
        try:
            pdf_file, metadata = archive_org.download(
                args.input_path, pdf2md.get_default_input_dir()
            )
        except Exception as e:
            print(f"Error downloading from archive.org: {e}", file=sys.stderr)
            sys.exit(1)
        known_metadata[pdf_file] = metadata
        input_path = pdf_file
    else:
        input_path = Path(args.input_path) if args.input_path else pdf2md.get_default_input_dir()

    # Get queue of PDFs to process
    queue = pdf2md.add_pdfs_to_queue(input_path)
    print(f"Found {len(queue)} PDF files to process")
    
    # Process each PDF
    failed = []
    device_reported = False
    for pdf_path in queue:
        print(f"\nProcessing: {pdf_path.name}")
        
        # Get output directory for this PDF
        if args.output_path:
            output_path = Path(args.output_path)
            markdown_dir = output_path / pdf_path.stem
        else:
            markdown_dir = pdf2md.get_default_output_dir(pdf_path)
            output_path = markdown_dir.parent
            
        try:
            # Check if markdown directory exists when skipping MD generation
            if args.skip_md:
                if not markdown_dir.exists():
                    print(f"Error: Markdown directory not found: {markdown_dir}", file=sys.stderr)
                    failed.append(pdf_path.name)
                    continue
                print(f"Using existing markdown files from: {markdown_dir}")
                
            # Convert PDF to Markdown unless skipped
            if not args.skip_md:
                engine = choose_engine(pdf_path, args.engine)
                print("Converting PDF to Markdown...")
                if engine == "textlayer":
                    import modules.textlayer as textlayer
                    textlayer.convert_pdf(
                        str(pdf_path),
                        markdown_dir,
                        args.max_pages,
                        args.start_page,
                        layout=args.layout,
                    )
                else:
                    if not device_reported:
                        report_device()
                        device_reported = True
                    pdf2md.convert_pdf(
                        str(pdf_path),
                        markdown_dir,
                        args.max_pages,
                        args.start_page,
                        args.chunk_size,
                    )
            
            # Convert Markdown to EPUB unless skipped
            if not args.skip_epub:
                print("Converting Markdown to EPUB...")
                mark2epub.convert_to_epub(
                    markdown_dir,
                    output_path,
                    metadata=known_metadata.get(pdf_path),
                    interactive=not args.yes,
                    theme=args.theme,
                )
                
        except Exception as e:
            print(f"Error processing {pdf_path.name}: {str(e)}", file=sys.stderr)
            failed.append(pdf_path.name)
            continue

    if failed:
        print(
            f"\nFailed to process {len(failed)} of {len(queue)} PDF file(s):",
            file=sys.stderr,
        )
        for name in failed:
            print(f"  - {name}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()