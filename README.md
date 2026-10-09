# PDF2EPUB 📚

Convert PDF files to nicely structured Markdown and EPUB format with intelligent layout detection.

## ✨ Features

- 📖 Smart layout detection for books and academic papers
- ⚡ Fast text-layer engine for born-digital PDFs: no OCR, keeps italics, bold and underlining, rebuilds tables and links footnotes
- ✉️ Letters layout for letter collections: one chapter per dated letter, grouped by year
- 🎨 Themes with a generated cover, title page and contents page
- 🔍 Advanced text extraction and OCR capabilities
- 📊 Table detection and formatting
- 🖼️ Image extraction and optimization
- 📝 Clean markdown output with preserved structure
- 📱 EPUB generation with customizable styling
- 🌍 Multi-language support
- 🚀 GPU acceleration support (NVIDIA & AMD)
- 🍎 Apple Silicon support

## 🛠️ Dependencies

- Python 3.10–3.14 (3.13 recommended, see below)
- PyTorch (with CUDA/ROCm support for GPU acceleration)
- marker-pdf==1.10.2
- transformers==4.57.6
- markdown==3.10.2
- latex2mathml==3.81.0

### ⚠️ Python version

Python 3.13 is recommended.

`marker-pdf` constrains `Pillow<11.0.0`, and Pillow only ships Python 3.14
wheels from 11.3.0 onward. On Python 3.10–3.13 every dependency installs as a
prebuilt wheel. On 3.14, pip has to build Pillow from source instead: this
works, but it is slower and requires a working C toolchain plus the image
library headers Pillow links against. On Debian/Ubuntu install them first:

```bash
sudo apt install libjpeg-dev zlib1g-dev libtiff-dev libfreetype6-dev libwebp-dev
```

Without these headers the install fails with
`RequiredDependencyException: The headers or library files could not be found for jpeg`.

## 💻 Installation

1. Create and activate a virtual environment.

On Linux/Mac:
```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

On Windows:
```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
```

2. Install Python dependencies (this installs PyTorch as well):
```bash
pip install -r requirements.txt
```

3. GPU acceleration (optional):

PyTorch is installed as a dependency in step 2. On Apple Silicon that wheel
already supports MPS, so no further action is needed. For a specific CUDA or
ROCm build, reinstall PyTorch using the selector at
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/).
For example, for AMD GPUs with ROCm:
```bash
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.2
```

4. Verify GPU support:
```python
import torch
print(torch.__version__)  # PyTorch version
print(torch.cuda.is_available())  # Should return True for NVIDIA
print(torch.backends.mps.is_available())  # Should return True for Apple Silicon
print(torch.version.hip)  # Should print ROCm version for AMD
```

### 🪟 Windows quick start (Windows Terminal / PowerShell)

1. Install Git and Python 3.13, then close and reopen the terminal so the new
   commands are found:
   ```powershell
   winget install Git.Git
   winget install Python.Python.3.13
   ```

2. Clone the repository:
   ```powershell
   cd $HOME\Documents
   git clone https://github.com/DanneskjoldDK/pdf2epub.git
   cd pdf2epub
   ```

3. Create a virtual environment and install the packages:
   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   If PowerShell refuses to run `Activate.ps1`, run
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.
   The install is large (several GB, mostly PyTorch) and takes a while.

4. Convert a PDF. In every new terminal, activate the environment first:
   ```powershell
   cd $HOME\Documents\pdf2epub
   .\.venv\Scripts\Activate.ps1
   python main.py "$HOME\Downloads\letters.pdf" --layout letters --yes
   ```
   The EPUB is written to a folder named after the PDF, next to the PDF. Leave
   out `--yes` to be asked for the title, author and other metadata.

To update later, run `git pull` and `pip install -r requirements.txt` in the
activated environment.

Born-digital PDFs use the text-layer engine and convert in seconds without a
GPU. Scanned PDFs go to marker, which is slow on a CPU.

### 🐳 Docker

A CPU-only image can be built from the included `Dockerfile`:

```bash
docker build -t pdf2epub .
```

Run it with your PDFs mounted at `/data` and a model cache volume (marker-pdf
downloads its models on first run):

```bash
docker run -it --rm \
  -v "$(pwd)":/data \
  -v pdf2epub-models:/models \
  pdf2epub input.pdf
```

`-it` is required for EPUB generation because metadata is prompted
interactively; with `--skip-epub` it can run non-interactively.

Tagged releases are also published to
`ghcr.io/overcuriousity/pdf2epub` by the Docker workflow.

## 🚀 Usage

### Basic Usage

Convert a single PDF file:
```bash
python main.py input.pdf
```

Convert all PDFs in a directory:
```bash
python main.py input_directory/
```

EPUB generation prompts interactively for metadata (title, author, language,
and so on; press Enter to accept each default). It therefore needs a terminal —
run it non-interactively and it will fail with `EOFError`. Use `--skip-epub` to
produce only markdown without any prompts.

### From archive.org

Pass an archive.org item URL instead of a file. The PDF is downloaded to
`./input/` and the item's title, author, language, publisher and date are used
as the EPUB metadata:

```bash
python main.py https://archive.org/details/alicesadventur00carr --yes
```

Only freely downloadable items (public domain / openly licensed) are
supported. Lending-library ("Borrow") items are refused.

### Advanced Options

```bash
python main.py [input_path] [output_path] [options]

Options:
  --max-pages INT          Maximum number of pages to process
  --start-page INT         Page number to start from
  --skip-epub              Skip EPUB generation, only create markdown
  --skip-md                Skip markdown generation, use existing markdown files
  --chunk-size INT         Pages converted per batch (default 10, 0 = all at once)
  --engine ENGINE          auto (default), textlayer or marker; see below
  --layout LAYOUT          Text-layer engine: auto (default), book, novel or letters
  --theme THEME            EPUB styling: default, book, novel or letters
  -y, --yes                Non-interactive: accept default metadata, skip review
```

If `input_path` is omitted, all PDFs in `./input/` are processed.

PDFs are converted in batches of `--chunk-size` pages with the models loaded
once, so memory use stays flat regardless of document length. If the process
is still killed for running out of memory (exit code 137), lower it, e.g.
`--chunk-size 4`.

### Engines

PDF2EPUB has two ways of turning a PDF into Markdown:

- **textlayer** reads the text a born-digital PDF already contains (PDFs
  exported from Word, LibreOffice, InDesign, LaTeX or an e-book tool). It takes
  seconds rather than hours on a CPU, and it keeps italics, bold and underlining
  (underlined text becomes italics), rebuilds tables from the column positions,
  drops running heads and page numbers, joins paragraphs across page breaks and
  turns footnotes into linked notes. It does not handle scanned pages or text
  set in several columns.
- **marker** runs OCR and layout models. It handles scans, multi-column pages
  and equations, but is slow without a GPU.

With `--engine auto` (the default) each PDF is checked first: the text-layer
engine is used when the PDF has a usable text layer in a single column, marker
otherwise. The choice is printed for every file.

### Layouts and themes

The text-layer engine recognises two layouts (`--layout`, detected by default):

- **book**: one chapter per top-level heading.
- **novel**: fiction and other books divided into "Chapter I", "CHAPTER 12."
  and the like. Each chapter keeps its number and title (all-capital titles
  are set in title case), the book title and author come from the title page,
  and text wrapped by hand at a fixed width (as in Project Gutenberg texts)
  is rejoined into paragraphs. Detected automatically when the PDF has at
  least three chapter headings.
- **letters**: a collection of dated letters, such as shareholder letters. Each
  letter becomes a chapter titled by its date, and the contents group the
  letters by year (an annual letter written in January or February is filed
  under the year it reports on). Letterheads are dropped, and the book title
  and author are suggested from the letterhead and the signature.

Themes (`--theme`) control the look of the EPUB:

- **default**: the plain style, as before.
- **book**: indented paragraphs, generated cover, title page and contents.
- **novel**: classic fiction typography: chapters open on a new page with the
  number in spaced small capitals, the title in italics and a large initial;
  indented paragraphs; a dark green and cream cover.
- **letters**: letters open under their year, financial tables are set with
  rules above and below and right-aligned figures, and a black-and-gold cover,
  a title page and a year-by-year contents page are generated.

Text-layer output uses the matching novel, letters or book theme automatically; marker output
keeps the default theme unless you pass `--theme`. The theme is remembered in
`description.json`, so `--skip-md` runs reuse it. The generated cover is
redrawn on every run; to use your own, put the image in `images/` and set
`"cover_image"` to its file name and `"cover_generated"` to `false` in
`description.json`.

### Examples

Convert a novel from archive.org (public domain) in one go:
```bash
python main.py https://archive.org/details/AroundTheWorldInEightyDays-JulesVerne --layout novel --yes
```

Convert a collection of letters, without prompts:
```bash
python main.py letters.pdf --layout letters --yes
```

Process a specific range of pages:
```bash
python main.py book.pdf --start-page 10 --max-pages 50
```

Convert to markdown only:
```bash
python main.py thesis.pdf --skip-epub
```

### Output Structure

```
output_directory/
├── document_name/
│   ├── document_name.md
│   ├── document_name.epub
│   ├── document_name_metadata.json
│   └── images/
│       ├── image1.png
│       ├── image2.jpg
│       └── ...
```

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. Fork the repository
2. Create a new branch for your feature
3. Commit your changes
4. Push to your branch
5. Create a Pull Request

Please ensure your code follows the existing style and includes appropriate tests.

### Development Setup

1. Clone the repository:
```bash
git clone https://github.com/overcuriousity/pdf2epub.git
cd pdf2epub
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. Install development dependencies:
```bash
pip install -r requirements.txt
pip install pytest
```

4. Run the tests:
```bash
python -m pytest -q
```

The tests use the fixtures in `tests/fixtures/` (see the README there) and
need neither torch nor marker. Set `EPUBCHECK_JAR` to the path of
`epubcheck.jar` to also validate the generated EPUBs with
[EPUBCheck](https://github.com/w3c/epubcheck).

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🐛 Known Issues

- Some image embedding might need manual adjustment
- Some complex mathematical equations might not be perfectly converted
- Certain PDF layouts with multiple columns may require manual adjustment
  (`--engine auto` sends them to marker; `--engine textlayer` would read them
  across the columns)
- Font detection might be imperfect in some cases

## 🙏 Acknowledgments

This project builds upon several excellent open-source libraries:
- [marker-pdf](https://github.com/VikParuchuri/marker) for PDF processing
- [mark2epub](https://github.com/AlexPof/mark2epub) for markdown conversion
- [PyTorch](https://pytorch.org/) for GPU acceleration
- [Transformers](https://huggingface.co/transformers) for advanced text processing
