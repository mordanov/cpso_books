# screenshot_cpso

Automated tool for capturing full-page screenshots of textbook pages from **russlo-edu.ru**, accessed via authentication on **edpalm-exam.online**. Screenshots are center-cropped, assembled into a single PDF per book, and optionally cleaned up.

---

## Features

- Authenticates on `edpalm-exam.online` and navigates to the embedded reader on `russlo-edu.ru`
- Captures and **center-crops** every page to a fixed `700 × 850 px` viewport
- Assembles all screenshots into a **named PDF** (book title is extracted from the page DOM)
- Deletes intermediate PNG files after PDF assembly (disable with `--keep-png`)
- **Single-book mode** — pass `--book`, `--username`, `--password`
- **Batch mode** — pass a `--books-config` JSON file; all books are processed in a loop, errors per book are collected and reported at the end

---

## Requirements

- Python 3.10+
- Google Chrome / Chromium (installed by Playwright)

### Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

`requirements.txt`:
```
playwright
Pillow
img2pdf
```

---

## Usage

### Single-book mode

```bash
python3 screenshot_tool.py \
  --username YOUR_LOGIN \
  --password YOUR_PASSWORD \
  --book 49
```

### Batch mode (from JSON config)

```bash
python3 screenshot_tool.py \
  --books-config libraries/example.json
```

All common flags (`--headless`, `--delay`, etc.) work in both modes.

---

## CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--username` | — | Login for `edpalm-exam.online` |
| `--password` | — | Password for `edpalm-exam.online` |
| `--book` | — | Single textbook number (e.g. `49`) |
| `--books-config` | — | Path to JSON config for batch mode |
| `--output` | `screenshots` | Root folder for output files |
| `--start-page` | `1` | First page number to capture |
| `--delay` | `1.5` | Delay (seconds) between page screenshots |
| `--headless` | `false` | Run browser without a visible window |
| `--viewport-width` | `1280` | Browser viewport width in pixels |
| `--viewport-height` | `900` | Browser viewport height in pixels |
| `--full-page` | `false` | Capture full scrollable page instead of viewport |
| `--keep-png` | `false` | Keep PNG files after PDF is assembled |

---

## JSON Config Format (batch mode)

```json
{
  "username": "your_login",
  "password": "your_password",
  "books": {
    "49": "Английский язык. 2 класс",
    "50": "Английский язык. 3 класс"
  }
}
```

- `username` and `password` — credentials for `edpalm-exam.online`
- `books` — dictionary where keys are textbook IDs (strings) and values are human-readable titles (informational only; the actual PDF filename is extracted from the page DOM)

See [`libraries/example.json`](libraries/example.json) for a full example with 200+ books.

---

## Output Structure

```
screenshots/
└── book_49/
    ├── cover.png          ← kept only if --keep-png
    ├── page001.png        ← kept only if --keep-png
    ├── page002.png        ← kept only if --keep-png
    └── Английский язык. 2 класс.pdf   ← always produced
```

Each book gets its own subdirectory under `--output`. The PDF filename is taken from the `bookName` element in the reader DOM, sanitized for filesystem safety.

---

## Processing steps

For each book the tool performs these steps:

| # | Description |
|---|---|
| 1 | Open login page |
| 2 | Fill credentials and submit |
| 3 | Navigate to course page |
| 4 | Click *"Открыть пособие. Часть 1"* |
| 5 | Click *"Вернуться в Библиотеку РС"* image |
| 6 | Find and click `div[data-catid="{BOOK}"]` |
| 7 | Extract book title from nested `div.bookName` |
| 8 | Navigate directly to the cover URL |
| 9 | Capture cover + all pages (center-cropped `700×850`) |
| 10 | Assemble PDF; remove PNG files (unless `--keep-png`) |

---

## Example — batch run, headless, keep PNGs

```bash
python3 screenshot_tool.py \
  --books-config libraries/example.json \
  --headless \
  --keep-png \
  --delay 2.0
```

---

## Notes

- The script stops capturing pages for a book when the server returns HTTP 404 (end of book) or another 4xx/5xx error.
- In batch mode, a failure on one book is logged and the run continues with the next book. A non-zero exit code is returned if any book failed.
- PDF filename characters that are invalid on common OS file systems (`< > : " / \ | ? *`), control characters, and non-printable characters are replaced or removed automatically.

