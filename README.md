# Universal Web Table & Data Scraper

A Python tool that takes **any website link** and extracts the data on that page into **CSV** and **JSON** files. It is built with [Playwright](https://playwright.dev/python/), so it also works on pages that load their content with JavaScript.

If the page has HTML tables, every table is extracted with its **header names as keys**, so each row becomes a clean object (`{"Title": "...", "Director": "..."}`). If the page has no tables, it falls back to extracting repeating list / card items.

---

## Features

- **Works with any link** - just give a URL (or run it and paste the link when asked).
- **Smart table extraction**
  - Handles `rowspan` and `colspan` cells (common on Wikipedia) by expanding each table into a full grid, so values are never shifted into the wrong column.
  - Detects header rows (`<thead>` or leading `<th>` rows), including multi-row headers.
  - Keeps `<th>` cells inside body rows (for example a "Rank" column).
  - Treats a full-width title row as the table title and a full-width divider row as a `section` label.
  - Reads nested tables and tables inside iframes separately, so rows never mix.
- **Card / list fallback** for pages without tables.
- **Dynamic pages supported** - waits for network activity to settle and scrolls down to trigger lazy-loaded content.
- **Duplicate removal** and text cleaning (extra spaces, line breaks, non-breaking spaces).
- **Numbered output files** - `data1.csv`, `data2.csv`, ... Old results are never overwritten.
- **`--split` mode** - save every table in its own CSV / JSON file.
- **Excel-friendly CSV** - saved as `utf-8-sig`, so characters like the rupee symbol and Tamil text display correctly.

---

## Tech Stack

- Python 3.9+
- Playwright (Chromium) for browser automation
- Built-in libraries: `argparse`, `csv`, `json`, `re`, `os`, `urllib`

---

## Installation

```bash
# 1. Clone or download the project, then go into the folder
cd data_scraper

# 2. (Optional) create a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt      # or: pip install playwright

# 4. Download the browser Playwright uses (one-time)
playwright install chromium
```

---

## Usage

```bash
python scraper.py --url https://en.wikipedia.org/wiki/List_of_Tamil_films_of_2026
```

Run without `--url` and the program will ask for the link:

```bash
python scraper.py
```

### Options

| Option | Description |
|---|---|
| `--url` | Link of the page to scrape. If omitted, you will be asked for it. `https://` is added automatically if missing. |
| `--split` | Save each table in its own CSV and JSON file. |
| `--headed` | Show the browser window while scraping (useful for debugging). |
| `--csv` | Base path for the CSV output (default `output/data.csv`). |
| `--json` | Base path for the JSON output (default `output/data.json`). |

### Examples

```bash
# All tables combined in one CSV and one JSON
python scraper.py --url https://example.com/page

# One file per table
python scraper.py --url https://example.com/page --split

# Watch the browser while it works
python scraper.py --url https://example.com/page --headed
```

---

## Output

Files are saved in the `output/` folder and numbered on every run:

```
output/
├── data1.csv
├── data1.json
├── data2.csv
├── data2.json
└── ...
```

With `--split`:

```
output/
├── data3_table1.csv
├── data3_table1.json
├── data3_table2.csv
├── data3_table2.json
└── ...
```

### Example JSON (illustrative)

For a table with the headers `Title`, `Director` and `Production company`, each row becomes one object:

```json
[
  {
    "Title": "Salliyargal",
    "Director": "T. Kittu",
    "Production company": "Indian Cineway",
    "link": "https://en.wikipedia.org/wiki/Salliyargal"
  }
]
```

Extra keys the scraper may add:

- `link` - the first link found in that row.
- `section` - the label of a divider row the record belongs to.

If a table has no header, the keys are `Column_1`, `Column_2`, and so on. Duplicate header names get a suffix (`Opening`, `Opening_2`).

---

## How It Works

1. Read the link (from `--url` or from a prompt).
2. Launch Chromium with Playwright and open the page.
3. Wait for the page to settle and scroll down so lazy-loaded content appears.
4. Run the table extraction logic inside the page:
   - expand every table into a full grid (handling `rowspan` / `colspan`),
   - detect the header rows and build the column names,
   - turn each body row into a `{header: value}` record.
5. If no table is found, extract repeating cards / list items instead.
6. Remove duplicates and save to numbered CSV and JSON files.

---

## Project Structure

```
data_scraper/
├── scraper.py          # the scraper (all logic)
├── requirements.txt    # Python dependencies
├── README.md
└── output/             # generated CSV / JSON files
```


## Limitations

- Works best on pages that use HTML `<table>` tags.
- On card / list pages without tables, the generic extraction can include some extra items such as menu or footer entries.
- Pages that require login are not supported.
- Data drawn on a canvas (for example the Google Sheets editor) is not readable as HTML tables.

---

## Responsible Use

Scrape only data you are allowed to use. Check the website's Terms of Service and `robots.txt`, avoid collecting personal data, and do not send requests at a rate that could overload a site.

---

## Future Improvements

- Pagination support (follow "next page" links).
- Retry logic and per-URL error handling.
- Scrape multiple URLs from a file.
- Wrap it in a Flask API (send a URL, receive JSON).
- Unit tests with local HTML fixtures.

---

## Author

Samitha Jasmine H
