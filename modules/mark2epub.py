import markdown
import os
from xml.dom import minidom
import zipfile
import sys
import json
from PIL import Image
import regex as re
from pathlib import Path
from datetime import datetime, timezone
import subprocess
from typing import Dict, Optional
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape
import latex2mathml.converter

THEMES_DIR = Path(__file__).parent / "themes"


def available_themes() -> list[str]:
    return sorted(p.stem for p in THEMES_DIR.glob("*.css"))


def theme_css(theme: str) -> bytes:
    """CSS for a theme from modules/themes/<theme>.css."""
    path = THEMES_DIR / f"{theme}.css"
    if not path.exists():
        raise ValueError(f"Unknown theme '{theme}'. Available: {', '.join(available_themes())}")
    return path.read_bytes()


def chapter_href(index: int, md_filename: str) -> str:
    """File name of a chapter inside the EPUB."""
    return quote("s{:05d}-{}.xhtml".format(index, md_filename.split(".")[0]))


def get_user_input(prompt: str, default: str = "") -> str:
    """Get user input with a default value."""
    user_input = input(f"{prompt} [{default}]: ").strip()
    return user_input if user_input else default

def get_metadata_from_user(existing_metadata: Optional[Dict] = None, interactive: bool = True) -> Dict:
    """Collect metadata from user with defaults from existing metadata.

    With interactive=False the defaults are used without prompting.
    """
    if existing_metadata is None:
        existing_metadata = {}
    
    metadata = existing_metadata.get("metadata", {})
    
    if interactive:
        print("\nPlease provide the following metadata for your EPUB (press Enter to use default value):")
    
    fields = {
        "dc:title": ("Title", metadata.get("dc:title", "Untitled Document")),
        "dc:creator": ("Author(s)", metadata.get("dc:creator", "Unknown Author")),
        "dc:identifier": ("Unique Identifier", metadata.get("dc:identifier", f"id-{datetime.now().strftime('%Y%m%d%H%M%S')}")),
        "dc:language": ("Language (e.g., en, de, fr)", metadata.get("dc:language", "en")),
        "dc:rights": ("Rights", metadata.get("dc:rights", "All rights reserved")),
        "dc:publisher": ("Publisher", metadata.get("dc:publisher", "PDF2EPUB")),
        "dc:date": ("Publication Date (YYYY-MM-DD)", metadata.get("dc:date", datetime.now().strftime("%Y-%m-%d")))
    }
    
    updated_metadata = {}
    for key, (prompt, default) in fields.items():
        value = get_user_input(prompt, default) if interactive else default
        updated_metadata[key] = value
        
    result = dict(existing_metadata)   # keep other settings (theme, layout, ...)
    result.update({
        "metadata": updated_metadata,
        "default_css": existing_metadata.get("default_css", ["style.css"]),
        "chapters": existing_metadata.get("chapters", []),
        "cover_image": existing_metadata.get("cover_image", None)
    })
    return result

def _open_path(path: Path) -> None:
    """Open a file or folder in the desktop's default application."""
    if sys.platform == 'darwin':
        subprocess.run(['open', str(path)], check=True)
    elif os.name == 'posix':
        subprocess.run(['xdg-open', str(path)], check=True)
    else:
        os.startfile(str(path))


def review_markdown_files(work_dir: Path, md_filenames: list[str]) -> tuple[bool, dict]:
    """Offer one review of all chapter files; returns (continue, contents)."""
    if len(md_filenames) == 1:
        ok, content = review_markdown(work_dir / md_filenames[0])
        return ok, {md_filenames[0]: content}
    while True:
        response = input(f"\nWould you like to review the {len(md_filenames)} markdown files "
                         "before conversion? (y/n): ").lower()
        if response in ['y', 'yes']:
            try:
                _open_path(work_dir)
            except Exception as e:
                print(f"\nError opening {work_dir}: {e}")
                print("You can edit the files there yourself.")
            while True:
                proceed = input("\nPress Enter when you're done editing (or 'q' to abort): ").lower()
                if proceed == 'q':
                    return False, {}
                if proceed == '':
                    break
            break
        elif response in ['n', 'no']:
            break
        else:
            print("Please enter 'y' or 'n'")
    return True, {name: (work_dir / name).read_text(encoding='utf-8') for name in md_filenames}


def review_markdown(markdown_path: Path) -> tuple[bool, str]:
    """Ask user if they want to review the markdown file."""
    content = markdown_path.read_text(encoding='utf-8')
    
    while True:
        response = input("\nWould you like to review the markdown file before conversion? (y/n): ").lower()
        if response in ['y', 'yes']:
            try:
                _open_path(markdown_path)
                
                while True:
                    proceed = input("\nPress Enter when you're done editing (or 'q' to abort): ").lower()
                    if proceed == 'q':
                        return False, content
                    elif proceed == '':
                        updated_content = markdown_path.read_text(encoding='utf-8')
                        return True, updated_content
            except Exception as e:
                print(f"\nError opening markdown file: {e}")
                print("Proceeding with conversion...")
                return True, content
        elif response in ['n', 'no']:
            return True, content
        else:
            print("Please enter 'y' or 'n'")

def build_image_lookup(images_dir: Path) -> dict:
    """Build a {lowercase_name: actual_path} map for O(1) case-insensitive lookups."""
    lookup = {}
    try:
        for entry in images_dir.iterdir():
            lookup[entry.name.lower()] = entry
    except FileNotFoundError:
        pass
    return lookup

def process_markdown_for_images(markdown_text: str, work_dir: Path) -> tuple[str, list[str]]:
    """Process markdown content to find image references."""
    image_pattern = r'!\[(.*?)\]\((.*?)\)'
    images_found = []
    modified_text = markdown_text
    images_dir = work_dir / 'images'
    lookup = build_image_lookup(images_dir)

    for match in re.finditer(image_pattern, markdown_text):
        alt_text, image_path = match.groups()
        img_path = Path(image_path.strip())

        actual = lookup.get(img_path.name.lower())
        if actual is not None:
            images_found.append(actual.name)
            new_ref = f'![{alt_text}](images/{actual.name})'
            modified_text = modified_text.replace(match.group(0), new_ref)
        else:
            print(f"Warning: Image not found: {images_dir / img_path.name}")

    return modified_text, images_found

def copy_and_optimize_image(src_path: Path, dest_path: Path, max_dimension: int = 1800) -> None:
    """Copy image to destination path with optimization for EPUB."""
    try:
        with Image.open(src_path) as img:
            if img.mode == 'RGBA':
                img = img.convert('RGB')
                
            ratio = min(max_dimension / max(img.size[0], img.size[1]), 1.0)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            
            if ratio < 1.0:
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            if src_path.suffix.lower() in ['.jpg', '.jpeg']:
                img.save(dest_path, 'JPEG', quality=85, optimize=True)
            elif src_path.suffix.lower() == '.png':
                img.save(dest_path, 'PNG', optimize=True)
            else:
                dest_path = dest_path.with_suffix('.jpg')
                img.save(dest_path, 'JPEG', quality=85, optimize=True)
                
    except Exception as e:
        print(f"Error processing image {src_path}: {e}")
        raise

def update_package_manifest(doc: minidom.Document, image_filenames: list[str], 
                          manifest: minidom.Element) -> None:
    """
    Update package manifest with image items, ensuring proper media types.
    """
    for i, image_filename in enumerate(image_filenames):
        item = doc.createElement('item')
        item.setAttribute('id', f"image-{i:05d}")
        item.setAttribute('href', f"images/{image_filename}")
        
        # Set appropriate media type based on file extension
        ext = Path(image_filename).suffix.lower()
        if ext in ['.jpg', '.jpeg']:
            media_type = 'image/jpeg'
        elif ext == '.png':
            media_type = 'image/png'
        elif ext == '.gif':
            media_type = 'image/gif'
        else:
            print(f"Warning: Unsupported image type {ext} for {image_filename}")
            continue
            
        item.setAttribute('media-type', media_type)
        manifest.appendChild(item)
        
def get_all_filenames(the_dir, extensions=[]):
    if not os.path.exists(the_dir):
        return []
    all_files = [x for x in os.listdir(the_dir)]
    all_files = [x for x in all_files if x.split(".")[-1] in extensions]
    return all_files

def get_packageOPF_XML(md_filenames=[], image_filenames=[], css_filenames=[], description_data=None, lang="en",
                       mathml_filenames=()):
    doc = minidom.Document()

    package = doc.createElement('package')
    package.setAttribute('xmlns',"http://www.idpf.org/2007/opf")
    package.setAttribute('version',"3.0")
    package.setAttribute('xml:lang', lang)
    package.setAttribute("unique-identifier","pub-id")

    ## Now building the metadata

    metadata = doc.createElement('metadata')
    metadata.setAttribute('xmlns:dc', 'http://purl.org/dc/elements/1.1/')

    for k,v in description_data["metadata"].items():
        if len(v):
            x = doc.createElement(k)
            for metadata_type,id_label in [("dc:title","title"),("dc:creator","creator"),("dc:identifier","pub-id")]:
                if k==metadata_type:
                    x.setAttribute('id',id_label)
            x.appendChild(doc.createTextNode(v))
            metadata.appendChild(x)

    # Required by EPUB 3: dcterms:modified timestamp
    modified_meta = doc.createElement('meta')
    modified_meta.setAttribute('property', 'dcterms:modified')
    modified_meta.appendChild(doc.createTextNode(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    metadata.appendChild(modified_meta)


    ## Now building the manifest

    manifest = doc.createElement('manifest')

    ## TOC.xhtml file for EPUB 3
    x = doc.createElement('item')
    x.setAttribute('id',"toc")
    x.setAttribute('properties',"nav")
    x.setAttribute('href',"TOC.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    ## Ensure retrocompatibility by also providing a TOC.ncx file
    x = doc.createElement('item')
    x.setAttribute('id',"ncx")
    x.setAttribute('href',"toc.ncx")
    x.setAttribute('media-type',"application/x-dtbncx+xml")
    manifest.appendChild(x)

    x = doc.createElement('item')
    x.setAttribute('id',"titlepage")
    x.setAttribute('href',"titlepage.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    for i,md_filename in enumerate(md_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"s{:05d}".format(i))
        if md_filename in mathml_filenames:
            x.setAttribute('properties', "mathml")
        x.setAttribute('href', chapter_href(i, md_filename))
        x.setAttribute('media-type',"application/xhtml+xml")
        manifest.appendChild(x)

    for i,image_filename in enumerate(image_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"image-{:05d}".format(i))
        x.setAttribute('href', "images/{}".format(quote(image_filename)))
        ext = Path(image_filename).suffix.lower()
        if ext == '.gif':
            x.setAttribute('media-type',"image/gif")
        elif ext in ['.jpg', '.jpeg']:
            x.setAttribute('media-type',"image/jpeg")
        elif ext == '.png':
            x.setAttribute('media-type',"image/png")
        if image_filename==description_data["cover_image"]:
            x.setAttribute('properties',"cover-image")

            ## Ensure compatibility by also providing a meta tag in the metadata
            y = doc.createElement('meta')
            y.setAttribute('name',"cover")
            y.setAttribute('content',"image-{:05d}".format(i))
            metadata.appendChild(y)
        manifest.appendChild(x)

    for i,css_filename in enumerate(css_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"css-{:05d}".format(i))
        x.setAttribute('href',"css/{}".format(css_filename))
        x.setAttribute('media-type',"text/css")
        manifest.appendChild(x)

    ## Now building the spine

    spine = doc.createElement('spine')
    spine.setAttribute('toc', "ncx")

    x = doc.createElement('itemref')
    x.setAttribute('idref',"titlepage")
    x.setAttribute('linear',"yes")
    spine.appendChild(x)
    for i,_ in enumerate(md_filenames):
        x = doc.createElement('itemref')
        x.setAttribute('idref',"s{:05d}".format(i))
        x.setAttribute('linear',"yes")
        spine.appendChild(x)

    guide = doc.createElement('guide')
    x = doc.createElement('reference')
    x.setAttribute('type',"cover")
    x.setAttribute('title',"Cover image")
    x.setAttribute('href',"titlepage.xhtml")
    guide.appendChild(x)


    package.appendChild(metadata)
    package.appendChild(manifest)
    package.appendChild(spine)
    package.appendChild(guide)
    doc.appendChild(package)

    return doc.toprettyxml()


def get_container_XML():
    container_data = """<?xml version="1.0" encoding="UTF-8" ?>\n"""
    container_data += """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n"""
    container_data += """<rootfiles>\n"""
    container_data += """<rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>\n"""
    container_data += """</rootfiles>\n</container>"""
    return container_data

def get_coverpage_XML(title, authors, lang="en", cover_image=None):
    """Generate the cover page: the cover image if given, else title and author."""
    if cover_image:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{xml_escape(lang)}" lang="{xml_escape(lang)}">
<head>
<title>{xml_escape(title)}</title>
<style type="text/css">
html, body {{ margin: 0; padding: 0; height: 100%; text-align: center; }}
img {{ max-width: 100%; max-height: 100%; height: auto; }}
</style>
</head>
<body epub:type="cover">
<img src="images/{quote(cover_image)}" alt="{xml_escape(title)}"/>
</body>
</html>"""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{xml_escape(lang)}" lang="{xml_escape(lang)}">
<head>
<title>{xml_escape(title)}</title>
<style type="text/css">
body {{ 
    margin: 0;
    padding: 0;
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    font-family: serif;
}}
.cover {{
    padding: 3em;
    text-align: center;
    border: 1px solid #ccc;
    max-width: 80%;
}}
h1 {{
    font-size: 2em;
    margin-bottom: 1em;
    line-height: 1.2;
    color: #333;
}}
p {{
    font-size: 1.2em;
    font-style: italic;
    color: #666;
    line-height: 1.4;
}}
</style>
</head>
<body>
    <div class="cover">
        <h1>{xml_escape(title)}</h1>
        <p>{xml_escape(authors) if authors else ''}</p>
    </div>
</body>
</html>"""

def _toc_entries(markdown_filenames, titles=None, groups=None, hidden=()):
    """[(label, href, children)]; consecutive chapters sharing a group are nested."""
    entries = []
    last_group = None
    for i, md_filename in enumerate(markdown_filenames):
        if md_filename in hidden:
            continue
        label = (titles or {}).get(md_filename) or md_filename.split(".")[0]
        href = chapter_href(i, md_filename)
        group = (groups or {}).get(md_filename)
        if group:
            if entries and last_group == group:
                entries[-1][2].append((label, href, []))
            else:
                entries.append((group, href, [(label, href, [])]))
        else:
            entries.append((label, href, []))
        last_group = group
    return entries


def get_TOC_XML(default_css_filenames, markdown_filenames, lang="en", titles=None, groups=None,
                hidden=(), landmarks=None):
    ## Returns the XML data for the TOC.xhtml file

    toc_xhtml = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_xhtml += """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{0}" lang="{0}">\n""".format(xml_escape(lang))
    toc_xhtml += """<head>\n<meta http-equiv="default-style" content="text/html; charset=utf-8"/>\n"""
    toc_xhtml += """<title>Contents</title>\n"""

    for css_filename in default_css_filenames:
        toc_xhtml += """<link rel="stylesheet" href="css/{}" type="text/css"/>\n""".format(css_filename)

    def items(entries):
        out = ""
        for label, href, children in entries:
            out += """<li><a href="{}">{}</a>""".format(href, xml_escape(label))
            if children:
                out += "<ol>" + items(children) + "</ol>"
            out += "</li>"
        return out

    toc_xhtml += """</head>\n<body>\n"""
    toc_xhtml += """<nav epub:type="toc" role="doc-toc" id="toc">\n<h2>Contents</h2>\n<ol epub:type="list">"""
    toc_xhtml += items(_toc_entries(markdown_filenames, titles, groups, hidden))
    toc_xhtml += """</ol>\n</nav>\n"""
    if landmarks:
        toc_xhtml += """<nav epub:type="landmarks" hidden="hidden">\n<h2>Guide</h2>\n<ol>"""
        for kind, href, label in landmarks:
            toc_xhtml += """<li><a epub:type="{}" href="{}">{}</a></li>""".format(kind, href, xml_escape(label))
        toc_xhtml += """</ol>\n</nav>\n"""
    toc_xhtml += """</body>\n</html>"""

    return toc_xhtml

def get_TOCNCX_XML(markdown_filenames, uid="", title="", lang="en", titles=None, groups=None, hidden=()):
    ## Returns the XML data for the TOC.ncx file

    entries = _toc_entries(markdown_filenames, titles, groups, hidden)
    depth = 2 if any(children for _, _, children in entries) else 1
    toc_ncx = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_ncx += """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" xml:lang="{}" version="2005-1">\n""".format(xml_escape(lang))
    toc_ncx += """<head>\n"""
    toc_ncx += """<meta name="dtb:uid" content="{}"/>\n""".format(xml_escape(uid))
    toc_ncx += """<meta name="dtb:depth" content="{}"/>\n""".format(depth)
    toc_ncx += """<meta name="dtb:totalPageCount" content="0"/>\n"""
    toc_ncx += """<meta name="dtb:maxPageNumber" content="0"/>\n"""
    toc_ncx += """</head>\n"""
    toc_ncx += """<docTitle><text>{}</text></docTitle>\n""".format(xml_escape(title))
    toc_ncx += """<navMap>\n"""

    # navPoints that point at the same file must share a playOrder
    order = {}
    counter = [0]

    def points(entries):
        out = ""
        for label, src, children in entries:
            if src not in order:
                order[src] = len(order) + 1
            counter[0] += 1
            out += """<navPoint id="navpoint-{}" playOrder="{}">\n""".format(counter[0], order[src])
            out += """<navLabel>\n<text>{}</text>\n</navLabel>""".format(xml_escape(label))
            out += """<content src="{}"/>""".format(src)
            out += points(children)
            out += """ </navPoint>"""
        return out

    toc_ncx += points(entries)
    toc_ncx += """</navMap>\n</ncx>"""

    return toc_ncx

def convert_math_to_mathml(html_text: str) -> str:
    """Replace LaTeX math expressions in HTML with MathML, skipping code blocks."""
    # Mask <pre>/<code> blocks so their $ delimiters are never treated as math
    placeholders = {}
    counter = [0]

    def mask(m):
        key = f"\x00MASK{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = m.group(0)
        return key

    masked = re.sub(r'<pre[\s\S]*?</pre>|<code[\s\S]*?</code>', mask, html_text, flags=re.DOTALL)

    def try_convert(latex):
        try:
            return latex2mathml.converter.convert(latex)
        except Exception:
            return None

    # Standalone display math that Markdown wrapped in <p>: replace the whole
    # paragraph to avoid invalid XHTML like <p><div>...</div></p>
    def replace_display_paragraph(m):
        mathml = try_convert(m.group(1))
        return f'<div class="math-display">{mathml}</div>' if mathml else m.group(0)

    # Remaining $$...$$ (inside phrasing content): use <span> to stay valid
    def replace_display_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-display">{mathml}</span>' if mathml else m.group(0)

    # Inline $...$, using pandoc's rule so currency is left alone: the opening $
    # must be followed by a non-space, the closing $ preceded by a non-space
    # and not followed by a digit ("$10 and $20" is not math)
    def replace_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-inline">{mathml}</span>' if mathml else m.group(0)

    masked = re.sub(r'<p>\s*\$\$(.*?)\$\$\s*</p>', replace_display_paragraph, masked, flags=re.DOTALL)
    masked = re.sub(r'\$\$(.*?)\$\$', replace_display_inline, masked, flags=re.DOTALL)
    masked = re.sub(r'(?<![\$\\])\$(?![\s$])([^$]+?)(?<![\s\\])\$(?![\d$])', replace_inline, masked)

    for key, original in placeholders.items():
        masked = masked.replace(key, original)

    return masked

def get_chapter_XML(work_dir: str, md_filename: str, css_filenames: list[str], content: Optional[str] = None, lang: str = "en", title: Optional[str] = None) -> tuple[str, list[str]]:
    """
    Convert markdown chapter to XHTML and process images.
    Returns tuple of (XHTML content, list of images referenced in chapter)
    
    Args:
        work_dir: Working directory containing markdown files
        md_filename: Name of markdown file
        css_filenames: List of CSS files to include
        content: Optional pre-loaded markdown content. If None, content is read from file
    """
    work_dir_path = Path(work_dir)
    
    if content is None:
        with open(work_dir_path / md_filename, "r", encoding="utf-8") as f:
            markdown_data = f.read()
    else:
        markdown_data = content
    
    # Process markdown for images and get list of referenced images
    markdown_data, chapter_images = process_markdown_for_images(markdown_data, work_dir_path)
    
    # Convert to HTML
    html_text = markdown.markdown(
        markdown_data,
        extensions=["codehilite", "tables", "fenced_code", "footnotes"],
        extension_configs={"codehilite": {"guess_lang": False}}
    )

    # Convert LaTeX math to MathML for EPUB readers
    html_text = convert_math_to_mathml(html_text)

    # Generate XHTML wrapper
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:m="http://www.w3.org/1998/Math/MathML" xml:lang="{xml_escape(lang)}" lang="{xml_escape(lang)}">
<head>
    <meta http-equiv="default-style" content="text/html; charset=utf-8"/>
    <title>{xml_escape(title or Path(md_filename).stem)}</title>
    {''.join(f'<link rel="stylesheet" href="css/{css}" type="text/css" media="all"/>' for css in css_filenames)}
</head>
<body>
{html_text}
</body>
</html>"""

    return xhtml, chapter_images



def _cover_module():
    try:
        from . import cover
    except ImportError:          # run as a script from the modules directory
        import cover
    return cover


def title_page_html(title: str, author: Optional[str], publisher: Optional[str]) -> str:
    """Generated title page (themes other than 'default')."""
    main_title, years = _cover_module().split_title(title)
    parts = ['<section class="titlepage" epub:type="titlepage">']
    if author:
        parts.append(f'<p class="tp-author">{xml_escape(author)}</p>')
    parts.append(f'<h1 class="tp-title">{xml_escape(main_title)}</h1>')
    if years:
        parts.append(f'<p class="tp-subtitle">{xml_escape(years)}</p>')
    if publisher and publisher != "PDF2EPUB":
        parts.append(f'<p class="tp-publisher">{xml_escape(publisher)}</p>')
    parts.append("</section>")
    return "\n".join(parts)


def contents_page_html(entries) -> str:
    """Generated contents page: a year-by-year index for grouped chapters."""
    parts = ['<section class="contents" epub:type="toc">', "<h1>Contents</h1>"]
    if any(children for _, _, children in entries):
        parts.append('<table class="letter-index"><tbody>')
        for label, href, children in entries:
            if children:
                links = "<br/>".join(f'<a href="{h}">{xml_escape(t)}</a>' for t, h, _ in children)
                parts.append(f"<tr><th>{xml_escape(label)}</th><td>{links}</td></tr>")
            else:
                parts.append(f'<tr><th></th><td><a href="{href}">{xml_escape(label)}</a></td></tr>')
        parts.append("</tbody></table>")
    else:
        parts.append('<ol class="contents-list">')
        parts.extend(f'<li><a href="{h}">{xml_escape(t)}</a></li>' for t, h, _ in entries)
        parts.append("</ol>")
    parts.append("</section>")
    return "\n".join(parts)


def convert_to_epub(markdown_dir: Path, output_path: Path, metadata: Optional[Dict] = None,
                    interactive: bool = True, theme: Optional[str] = None) -> None:
    """
    Convert markdown files and images to EPUB format.

    metadata: optional dc:* fields used as defaults (e.g. from archive.org).
    interactive: prompt for metadata and markdown review; if False, use defaults.
    theme: CSS theme from modules/themes (default, book, letters); stored in
        description.json, so later runs reuse it.
    """
    if not markdown_dir.exists():
        raise FileNotFoundError(f"Markdown directory not found: {markdown_dir}")
        
    if not list(markdown_dir.glob('*.md')):
        raise ValueError(f"No markdown files found in: {markdown_dir}")
    
    # Generate EPUB file
    epub_path = markdown_dir / f"{markdown_dir.name}.epub"
    main([str(markdown_dir), str(epub_path)], metadata=metadata, interactive=interactive, theme=theme)

def main(args, metadata: Optional[Dict] = None, interactive: bool = True, theme: Optional[str] = None):
    if len(args) < 2:
        print("\nUsage:\n    python md2epub.py <markdown_directory> <output_file.epub>")
        exit(1)

    work_dir = args[0]
    output_path = args[1]

    images_dir = os.path.join(work_dir, 'images/')
    css_dir = os.path.join(work_dir, 'css/')

    try:
        # Reading/Creating the JSON file containing the description of the eBook
        description_path = os.path.join(work_dir, "description.json")
        existing_metadata = {}
        
        if os.path.exists(description_path):
            with open(description_path, 'r', encoding='utf-8') as f:
                existing_metadata = json.load(f)
        
        # Values supplied by the caller override stored defaults
        if metadata:
            existing_metadata.setdefault("metadata", {}).update(metadata)

        # Get metadata from user
        json_data = get_metadata_from_user(existing_metadata, interactive=interactive)
        if theme:
            json_data["theme"] = theme
        theme = json_data.get("theme") or "default"
        css_content = theme_css(theme)
        
        # Find all markdown files if not already in metadata
        if not json_data["chapters"]:
            markdown_files = [f for f in os.listdir(work_dir) if f.endswith('.md')]
            for md_file in sorted(markdown_files):
                json_data["chapters"].append({
                    "markdown": md_file,
                    "css": ""
                })
        
        # Get title and author
        title = json_data["metadata"].get("dc:title", "Untitled Document")
        authors = json_data["metadata"].get("dc:creator", None)
        lang = json_data["metadata"].get("dc:language", "en") or "en"

        # Themes draw a cover when the book has none (redrawn on every run so
        # it follows metadata changes; set cover_image to use your own)
        if theme != "default" and (not json_data.get("cover_image") or json_data.get("cover_generated")):
            _cover_module().make_cover(Path(work_dir) / "images" / "cover.jpg", title, authors, theme=theme)
            json_data["cover_image"] = "cover.jpg"
            json_data["cover_generated"] = True

        # Save the updated description.json
        with open(description_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        # Themes add a title page and a contents page in front of the text
        chapters = list(json_data["chapters"])
        generated = {}
        if theme != "default":
            chapters = [{"markdown": "_title.md", "css": "", "title": "Title Page"},
                        {"markdown": "_contents.md", "css": "", "title": "Contents"}] + chapters
            generated["_title.md"] = title_page_html(title, authors, json_data["metadata"].get("dc:publisher"))
        
        # Review markdown files and store updated content
        md_files = [c["markdown"] for c in json_data["chapters"]]
        if interactive:
            should_continue, chapter_contents = review_markdown_files(Path(work_dir), md_files)
            if not should_continue:
                print("\nConversion aborted by user.")
                return
        else:
            chapter_contents = {name: (Path(work_dir) / name).read_text(encoding='utf-8') for name in md_files}

        # Compile list of files
        all_md_filenames = []
        # Optional per-chapter "title" used as the table-of-contents label,
        # and "group" (e.g. a year) under which chapters are nested
        chapter_titles = {c["markdown"]: c["title"] for c in chapters if c.get("title")}
        chapter_groups = {c["markdown"]: c["group"] for c in chapters if c.get("group")}
        hidden = {"_title.md"}
        all_css_filenames = json_data["default_css"][:]
        for chapter in chapters:
            if chapter["markdown"] not in all_md_filenames:
                all_md_filenames.append(chapter["markdown"])
            if len(chapter["css"]) and (chapter["css"] not in all_css_filenames):
                all_css_filenames.append(chapter["css"])
        
        all_image_filenames = get_all_filenames(images_dir, extensions=["gif", "jpg", "jpeg", "png"])
        if "_contents.md" in all_md_filenames:
            entries = _toc_entries(all_md_filenames, chapter_titles, chapter_groups, hidden | {"_contents.md"})
            generated["_contents.md"] = contents_page_html(entries)
        chapter_contents.update(generated)
        body_start = next((chapter_href(i, md) for i, md in enumerate(all_md_filenames)
                           if md not in generated), chapter_href(0, all_md_filenames[0]))
        # landmarks may only point at documents in the spine (TOC.xhtml is not)
        landmarks = [("cover", "titlepage.xhtml", "Cover")]
        if "_contents.md" in all_md_filenames:
            landmarks.append(("toc", chapter_href(all_md_filenames.index("_contents.md"), "_contents.md"),
                              "Contents"))
        landmarks.append(("bodymatter", body_start, "Start"))

        # First process all chapters and images
        images_dir = Path(work_dir) / 'images'
        epub_images_dir = Path(work_dir) / 'epub_images'
        processed_images = {}  # Store processed image data
        all_referenced_images = set()
        chapter_data = {}  # Store processed chapter data

        # First pass: Process chapters and collect image references
        print("\nProcessing chapters and collecting image references...")
        for i, chapter in enumerate(chapters):
            css_files = json_data["default_css"][:]
            if chapter["css"]:
                css_files.append(chapter["css"])
                
            # Process chapter content
            chapter_xhtml, chapter_images = get_chapter_XML(
                work_dir, 
                chapter["markdown"], 
                css_files,
                content=chapter_contents[chapter["markdown"]],
                lang=lang,
                title=chapter.get("title")
            )
            chapter_data[chapter["markdown"]] = chapter_xhtml
            all_referenced_images.update(chapter_images)

        # Process and optimize images
        print("\nProcessing and optimizing images...")
        if images_dir.exists() and all_referenced_images:
            epub_images_dir.mkdir(exist_ok=True)
            
            for image in all_referenced_images:
                src_path = images_dir / image
                if src_path.exists():
                    try:
                        dest_path = epub_images_dir / image
                        copy_and_optimize_image(src_path, dest_path)
                        
                        # Store processed image data
                        with open(dest_path, "rb") as f:
                            processed_images[image] = f.read()
                    except Exception as e:
                        print(f"Warning: Failed to process image {image}: {e}")
                else:
                    print(f"Warning: Referenced image not found: {src_path}")
            
            # Cleanup temporary directory
            import shutil
            shutil.rmtree(epub_images_dir, ignore_errors=True)

        # Now create the EPUB file with all prepared content
        print("\nCreating EPUB file...")
        with zipfile.ZipFile(output_path, "w") as epub:
            # Write mimetype (must be first and uncompressed)
            epub.writestr("mimetype", "application/epub+zip")

            # Write container.xml
            epub.writestr("META-INF/container.xml", get_container_XML(), zipfile.ZIP_DEFLATED)

            # Write package.opf
            epub.writestr("OPS/package.opf", 
                get_packageOPF_XML(
                    md_filenames=all_md_filenames,
                    mathml_filenames={md for md, xhtml in chapter_data.items() if "<math" in xhtml},
                    image_filenames=all_image_filenames,
                    css_filenames=all_css_filenames,
                    description_data=json_data,
                    lang=lang
                ), 
                zipfile.ZIP_DEFLATED
            )

            # Write cover page
            coverpage_data = get_coverpage_XML(title, authors, lang, json_data.get("cover_image"))
            epub.writestr("OPS/titlepage.xhtml", coverpage_data.encode('utf-8'), zipfile.ZIP_DEFLATED)

            # Write processed chapters
            print("Writing chapters...")
            for i, chapter in enumerate(chapters):
                print(f"  Writing chapter {i+1}/{len(chapters)}: {chapter['markdown']}")
                epub.writestr(
                    f"OPS/{chapter_href(i, chapter['markdown'])}",
                    chapter_data[chapter["markdown"]].encode('utf-8'),
                    zipfile.ZIP_DEFLATED
                )

            # Write processed images
            if processed_images:
                print(f"Writing {len(processed_images)} processed images...")
                for image_name, image_data in processed_images.items():
                    epub.writestr(f"OPS/images/{image_name}", image_data, zipfile.ZIP_DEFLATED)

            # Write TOC files
            print("Writing table of contents...")
            epub.writestr("OPS/TOC.xhtml", 
                get_TOC_XML(json_data["default_css"], all_md_filenames, lang, chapter_titles,
                            chapter_groups, hidden, landmarks),
                zipfile.ZIP_DEFLATED
            )
            
            epub.writestr("OPS/toc.ncx",
                get_TOCNCX_XML(
                    all_md_filenames,
                    uid=json_data["metadata"].get("dc:identifier", ""),
                    title=json_data["metadata"].get("dc:title", ""),
                    lang=lang,
                    titles=chapter_titles,
                    groups=chapter_groups,
                    hidden=hidden
                ),
                zipfile.ZIP_DEFLATED
            )

            # Copy remaining images that weren't referenced in markdown
            remaining_images = set(all_image_filenames) - set(processed_images.keys())
            if remaining_images and os.path.exists(images_dir):
                print(f"Writing {len(remaining_images)} additional images...")
                for image in remaining_images:
                    with open(os.path.join(images_dir, image), "rb") as f:
                        epub.writestr(f"OPS/images/{image}", f.read(), zipfile.ZIP_DEFLATED)

            # Copy CSS files; missing ones get the theme's stylesheet
            print(f"Writing {len(all_css_filenames)} CSS files...")
            for css in all_css_filenames:
                css_path = os.path.join(css_dir, css)
                if os.path.exists(css_path):
                    with open(css_path, "rb") as f:
                        epub.writestr(f"OPS/css/{css}", f.read(), zipfile.ZIP_DEFLATED)
                else:
                    epub.writestr(f"OPS/css/{css}", css_content, zipfile.ZIP_DEFLATED)

        print(f"\nEPUB creation complete: {output_path}")
        
    except Exception:
        import traceback
        print(f"Error processing {work_dir}:")
        print(traceback.format_exc())
        raise

if __name__ == "__main__":
    main(sys.argv[1:])
