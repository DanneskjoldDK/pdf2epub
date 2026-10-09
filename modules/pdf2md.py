from pathlib import Path
import sys
import json
from PIL import Image
import io

def get_default_output_dir(input_path: Path) -> Path:
    """
    Generate default output directory path based on input PDF path.
    Creates a directory with same name as PDF (without extension) next to the PDF.
    """
    return input_path.parent / input_path.stem

def get_default_input_dir() -> Path:
    """
    Get default input directory (./input) relative to current working directory.
    Creates it if it doesn't exist.
    """
    input_dir = Path.cwd() / 'input'
    input_dir.mkdir(exist_ok=True)
    return input_dir


def save_images(images: dict, image_dir: Path) -> None:
    """
    Save images with proper error handling and format detection.
    Preserves original image filenames from the input.
    
    Args:
        images: Dictionary of images from marker-pdf conversion where keys are filenames
        image_dir: Directory to save images to
    """
    if not images:
        print("No images found in document")
        return
        
    image_dir.mkdir(exist_ok=True)
    saved_count = 0
    
    for filename, image_data in images.items():
        try:
            # Skip if image data is None or empty
            if not image_data:
                continue
                
            image_path = image_dir / filename
            
            # Handle different image data formats
            if isinstance(image_data, Image.Image):
                image_data.save(image_path)
                saved_count += 1
                    
            elif isinstance(image_data, bytes):
                img = Image.open(io.BytesIO(image_data))
                img.save(image_path)
                saved_count += 1
                    
            elif isinstance(image_data, str):
                if Path(image_data).exists():
                    img = Image.open(image_data)
                    img.save(image_path)
                    saved_count += 1
                else:
                    print(f"Image path does not exist: {image_data}")
            else:
                print(f"Unsupported image data type for {filename}: {type(image_data)}")
                
        except Exception as e:
            print(f"Error saving image {filename}: {str(e)}")
            continue
            
    if saved_count > 0:
        print(f"Successfully saved {saved_count} images to: {image_dir}")
    else:
        print("No valid images were found to save")

# Pages converted per marker run. Marker keeps every page image of a run in
# memory, so converting a large PDF in one go can exhaust RAM (the process is
# then killed by the OS). Converting fixed-size chunks with the models loaded
# once keeps peak memory independent of the document length.
DEFAULT_CHUNK_SIZE = 10


def get_page_count(input_path: str) -> int:
    import pypdfium2
    pdf = pypdfium2.PdfDocument(input_path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def page_chunks(first: int, last: int, chunk_size: int) -> list[list[int]]:
    """Split the page indices first..last-1 into lists of at most chunk_size."""
    if chunk_size <= 0:
        chunk_size = max(last - first, 1)
    return [list(range(s, min(s + chunk_size, last))) for s in range(first, last, chunk_size)]


def merge_metadata(parts: list[dict]) -> dict:
    """Combine the metadata dicts of several marker runs into one."""
    merged: dict = {}
    for part in parts:
        for key, value in (part or {}).items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged.setdefault(key, value)
    return merged


def convert_pdf(
    input_path: str,
    output_dir: Path,
    max_pages: int = None,
    start_page: int = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    """
    Convert a single PDF file to markdown format with enhanced image handling.

    The pages are converted in chunks of chunk_size pages (0 = all at once) to
    bound memory use; the results are concatenated in page order.
    """
    import gc

    try:
        from marker.models import create_model_dict
        from marker.converters.pdf import PdfConverter

        page_count = get_page_count(input_path)
        first = start_page or 0
        if first >= page_count:
            raise ValueError(
                f"--start-page {first} is beyond the last page ({page_count - 1})"
            )
        last = page_count if max_pages is None else min(first + max_pages, page_count)
        chunks = page_chunks(first, last, chunk_size)

        # Load the models once and reuse them for every chunk
        models = create_model_dict()

        markdown_parts = []
        metadata_parts = []
        images = {}
        for n, pages in enumerate(chunks, 1):
            if len(chunks) > 1:
                print(f"Pages {pages[0] + 1}-{pages[-1] + 1} of {page_count} (chunk {n}/{len(chunks)})")
            converter = PdfConverter(config={"page_range": pages}, artifact_dict=models)
            rendered = converter(input_path)
            markdown_parts.append(rendered.markdown.strip())
            metadata_parts.append(rendered.metadata)
            # Image names include the absolute page number, so they are unique across chunks
            images.update(rendered.images)
            del converter, rendered
            gc.collect()

        full_text = "\n\n".join(part for part in markdown_parts if part) + "\n"
        metadata = merge_metadata(metadata_parts)

        # All output will go to the output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # A previous text-layer run in this directory listed its own chapter
        # files in description.json; drop them so marker's output is used
        description = output_dir / "description.json"
        if description.exists():
            data = json.loads(description.read_text(encoding="utf-8"))
            if data.get("engine") == "textlayer":
                for chapter in data.get("chapters", []):
                    (output_dir / chapter["markdown"]).unlink(missing_ok=True)
                for key in ("chapters", "layout", "engine", "theme"):
                    data.pop(key, None)
                description.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Save markdown content
        md_output = output_dir / f"{Path(input_path).stem}.md"
        md_output.write_text(full_text, encoding='utf-8')
        print(f"Markdown saved to: {md_output}")

        # Save metadata as JSON
        meta_output = output_dir / f"{Path(input_path).stem}_metadata.json"
        with open(meta_output, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        print(f"Metadata saved to: {meta_output}")

        # Enhanced image handling
        try:
            if images:
                image_dir = output_dir / "images"
                save_images(images, image_dir)

                # Cleanup PIL Images
                for img in images.values():
                    if isinstance(img, Image.Image):
                        try:
                            img.close()
                        except Exception as e:
                            print(f"Warning: Failed to close image: {e}")
                images.clear()
        except Exception as e:
            print(f"Warning: Error during image cleanup: {e}")

    except Exception as e:
        print(f"Error converting {input_path}: {str(e)}", file=sys.stderr)
        raise

    
def add_pdfs_to_queue(input_path: Path) -> list[Path]:
    """
    Add PDF files to the processing queue.
    If input_path is a directory, add all PDFs in it.
    If input_path is a file, add just that file.
    """
    queue = []
    
    if input_path.is_dir():
        pdfs = list(input_path.glob('*.pdf'))
        if not pdfs:
            print(f"No PDF files found in directory: {input_path}", file=sys.stderr)
            sys.exit(1)
        queue.extend(pdfs)
    else:
        if not input_path.is_file():
            print(f"Error: Input file does not exist: {input_path}", file=sys.stderr)
            sys.exit(1)
        if input_path.suffix.lower() != '.pdf':
            print(f"Error: Input file must be a PDF: {input_path}", file=sys.stderr)
            sys.exit(1)
        queue.append(input_path)
        
    return queue
