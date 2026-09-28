# Google Slides Dump Tool

A Windows desktop toolkit for capturing Google Slides presentations and turning the images into PDF, DOCX, or searchable PDF files. It supports Presenter View captures with visible speaker notes and sequential capture from the regular Google Slides browser editor. The processing pipeline runs locally and uses Tesseract OCR for searchable PDFs.

## Table of Contents


- [Features](#features)
- [Intended Use](#intended-use)
- [Tech Stack](#tech-stack)
- [System Architecture](#system-architecture)
- [Project Structure](#project-structure)
- [Database](#database)
- [Main Workflow](#main-workflow)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Running the Tool](#running-the-tool)
- [API Documentation](#api-documentation)
- [Authentication and Authorization](#authentication-and-authorization)
- [Screenshots](#screenshots)
- [Testing](#testing)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Authors](#authors)
- [Project Status](#project-status)

## Intended Use

Use this project only for lawful personal archiving or content you own or have permission to reproduce. The project does not bypass access controls and is not affiliated with Google LLC. “Google Slides” is a trademark of Google LLC. This notice is not legal advice.

## Features

### Presentation operator

- Capture a fixed number of slides from the primary display in sequence.
- Capture slide and speaker notes from Presenter View by selecting the two screen regions with the mouse. The regions are saved locally and can be reused.
- Capture Google Slides in the regular browser view or edit interface, without entering full-screen Present mode. Choose a full browser window, one rectangle containing slide and visible notes, or separate slide and notes rectangles.
- Optionally start browser capture at slide 1. The tool focuses the Google Slides window, moves through slides, checks for a visible transition, and retries navigation once if the frame does not change.
- Reuse existing `Page_N` images or process an existing PDF without taking new screenshots.
- Select PDF, DOCX, or searchable PDF output. PDF and DOCX can also be generated together.

### PDF and document processing

- Convert numbered capture images into a PDF, with an optional page-number label.
- Convert capture images into a landscape DOCX, fitting each image to the page while preserving its aspect ratio.
- Build a searchable PDF with a hidden OCR text layer and automatically generated bookmarks based on detected slide titles.
- Process an existing PDF. Existing searchable text is reused where available; pages without a usable text layer receive OCR.
- Supply exact bookmark titles through an optional `titles.json` file, inspect title candidates with debug output, and optionally apply smart dark mode to pages detected as light backgrounds with dark neutral text.

For bookmark detection, the title module considers page layout and repeated document templates, with conservative fallbacks when a slide has no reliable heading. Manual titles take priority. When an existing PDF already has searchable text, the tool can preserve that layer and limit OCR to title analysis. The source PDF is kept separate from the output; the input and output paths cannot be the same.

## Tech Stack

### Languages and platform

- Windows PowerShell for screen capture, region selection, window activation, and workflow orchestration.
- Python for PDF, DOCX, and OCR processing.
- Windows Forms, System.Drawing, and Win32 APIs for screen selection, display geometry, DPI handling, and keyboard/window control.

### Python packages and external tools

- Pillow for image handling and PDF image assembly.
- `python-docx` for DOCX generation.
- PyMuPDF (`fitz`) for PDF reading and writing.
- `pytesseract` as the Python interface to the separately installed Tesseract OCR engine.
- Tesseract OCR for local text recognition.

## System Architecture

The PowerShell recorder chooses and gathers input. The Python scripts then create the requested document from the numbered images or process a supplied PDF.

```text
Google Slides window / existing PDF / saved captures
                         |
                         v
          screenshot-recorder.ps1
        capture and region selection
                         |
                         v
             captures/Page_N.png
                  /     |      \
                 v      v       v
           img-2-pdf  img-2-docx  img-2-searchable-pdf
                 \      |       /
                  PDF / DOCX / searchable PDF
```

For an existing PDF, `img-2-searchable-pdf.py` can read that file directly without the capture step. `slide_title_detection.py` ranks OCR and native PDF text candidates to produce bookmark titles.

## Project Structure

```text
.
├── .github/
│   └── workflows/
│       └── tests.yml
├── tests/
│   ├── test_browser_capture.ps1
│   ├── test_capture_functions.ps1
│   ├── test_docx_fit.py
│   ├── test_searchable_pdf_pipeline.py
│   └── test_slide_title_detection.py
├── .gitignore
├── img-2-docx.py
├── img-2-pdf.py
├── img-2-searchable-pdf.py
├── LICENSE
├── README.md
├── requirements.txt
├── screenshot-recorder.ps1
└── slide_title_detection.py
```

At runtime, the recorder creates `config.json` for local display and capture-region settings and stores numbered images in `captures/`. Both are excluded by `.gitignore`. The checkout also contains `new.pdf` and `ouput.pdf`; their purpose is not described by the source or documentation, so they are not treated as required inputs or outputs here.

- `screenshot-recorder.ps1` provides the interactive source and output menus and starts the Python exporters.
- `img-2-pdf.py`, `img-2-docx.py`, and `img-2-searchable-pdf.py` create the supported output formats.
- `slide_title_detection.py` contains the local layout-aware title detection logic.
- `tests/` contains Python unit tests and PowerShell geometry/configuration/navigation tests.
- `.github/workflows/tests.yml` runs the project checks on Windows in GitHub Actions.

## Database

This is a local file-processing utility. The repository contains no database, schema, migration, entity, or ERD files, and the tool does not require a database server.

## Main Workflow

```text
Choose a source
  ├─ Capture a presentation or browser slides
  ├─ Reuse captures/Page_N images
  └─ Process an existing PDF (searchable PDF workflow)
           |
           v
Create or select numbered slide images
           |
           v
Choose PDF, DOCX, both, or searchable PDF
           |
           v
Write the result next to the project scripts
```

## Getting Started

### Prerequisites

- Windows with Windows PowerShell available.
- Python 3.9 or newer. The CI workflow tests Python 3.9, 3.11, and 3.13.
- The Python dependencies listed in [`requirements.txt`](requirements.txt).
- Tesseract OCR installed and available on `PATH` when creating searchable PDFs. OCR language data must also be installed for the selected language.
- Google Slides open in a browser for browser capture, or a presentation in Presenter View for slide-plus-notes capture.

### Clone and install

Use the repository URL supplied by its owner; no GitHub URL is defined in this checkout.

```bash
git clone <repository-url>
cd <repository-folder>
python -m pip install -r requirements.txt
```

Install Tesseract separately if searchable PDF output is needed. On Windows systems with WinGet, the existing project documentation uses:

```powershell
winget install UB-Mannheim.TesseractOCR
```

For French OCR, install Tesseract's French language data (`fra.traineddata`) as well. The script selects an available language from the installed Tesseract languages; `OCR_LANG` can override its automatic choice.

## Configuration

No `.env` file, database connection, account, or API key is required. `config.json` is generated beside the scripts when screen regions are saved. It contains machine/display-specific geometry, is ignored by Git, and should be reselected when monitor layout, browser position, or DPI changes.

For browser capture, choose one of these areas:

1. **Full browser window** — includes the thumbnail sidebar, slide, and visible notes.
2. **One selected area** — drag a single rectangle around the slide and notes.
3. **Separate slide and notes areas** — select both rectangles; the recorder composes them into one image.

The capture selector uses physical screen pixels and supports mixed/secondary-monitor coordinates. Only notes visible in the selected area are captured; long notes are not automatically scrolled. When saved coordinates no longer match the current display/window configuration, select the area again.

## Running the Tool

From Windows Command Prompt, change to the project directory and start the recorder:

```cmd
cd /d "C:\path\to\Google-Slides-Dump-Tool"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\screenshot-recorder.ps1"
```

The source menu offers:

1. Capture slide only.
2. Capture slide and speaker notes from Presenter View.
3. Reuse captures.
4. Process an existing PDF.
5. Capture a chosen number of slides sequentially in Google Slides browser view/edit mode.

For browser capture, keep the Google Slides window visible, select the capture area, choose whether to start at slide 1, and enter the delay between navigation and capture. The script uses the Google Slides filmstrip shortcut, `Home` for the first slide, and `Page Down` for the next slide. It retries once when its frame comparison does not detect a slide transition. The number entered by the user controls when the loop ends; the tool does not detect the final slide automatically.

Captures are written as `captures/Page_1.png`, `captures/Page_2.png`, and so on. The exporter scripts can also be run directly:

```powershell
python .\img-2-pdf.py
python .\img-2-docx.py
python .\img-2-searchable-pdf.py
python .\img-2-searchable-pdf.py --input-pdf .\slides.pdf --output .\slides-searchable.pdf
```

The default outputs are `result.pdf`, `result.docx`, and `result-searchable.pdf`. The searchable PDF command also accepts `--captures-dir`, `--titles-file`, `--debug-titles`, `--dark-mode`, and `--render-dpi` (96–600). Use `python .\img-2-searchable-pdf.py --help` for the current options.

## API Documentation

The project does not expose an HTTP API and has no Swagger/OpenAPI documentation.

## Authentication and Authorization

The project has no application-managed login or role system. Browser capture operates on an already open Google Slides window in the user's browser session.

## Screenshots

Screenshots will be added here.

## Testing

Run the Python unit tests from the repository root:

```powershell
python -m unittest discover -s tests -v
```

Run the Windows PowerShell capture/configuration and browser-navigation checks:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test_capture_functions.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test_browser_capture.ps1
```

The tests cover title selection, searchable PDF generation and failure handling, DOCX image fitting, capture-region geometry and config validation, multi-monitor coordinate mapping, and browser navigation retry behavior. The GitHub Actions workflow also compiles Python files and runs these checks on Windows with Python 3.9, 3.11, and 3.13. Interactive capture quality still depends on the user's current browser and display layout.

## Known Limitations

- Browser capture needs a visible Google Slides window and a slide count entered by the user; it does not automatically identify the end of the deck.
- Only the speaker notes visible in the selected screen region are captured. Notes are not auto-scrolled.
- Saved capture coordinates depend on the current display geometry, DPI, and browser window placement.
- Searchable PDF generation requires the separate Tesseract executable and suitable language data.

## Roadmap

The repository does not document a separate roadmap or mark features as in development. The implemented capabilities are listed under [Features](#features); no additional planned work is stated here.

## Contributing

Contributions can be made through a branch and pull request. Run the tests above before submitting changes.

```bash
git checkout -b feature/short-description
git add <files>
git commit -m "Describe the change"
git push origin feature/short-description
```

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE) for the full text.

## Authors

The repository's MIT license identifies `SpiRaL` as the copyright holder. No contributor roster or team description is included in the project files.

## Project Status

The repository contains implemented capture and export workflows plus a Windows CI workflow. It does not state a release version or an explicit maintenance status.
