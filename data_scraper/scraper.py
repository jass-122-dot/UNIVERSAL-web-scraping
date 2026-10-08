import argparse
import csv
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse, urlunparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_OUTPUT_DIR = "output"
DEFAULT_CSV_PATH = os.path.join(DEFAULT_OUTPUT_DIR, "data.csv")
DEFAULT_JSON_PATH = os.path.join(DEFAULT_OUTPUT_DIR, "data.json")


RESERVED_KEYS = {"link", "section"}


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_input_url(url: str) -> str:
  
    url = url.strip().strip('"')
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        url = "https://" + url
    return url


def normalize_url(raw_url: str, base_url: str = "") -> str:

    if not raw_url:
        return ""
    raw_url = raw_url.strip()
    if raw_url.startswith("#") or raw_url.lower().startswith(("javascript:", "mailto:", "tel:")):
        return ""
    absolute = urljoin(base_url, raw_url) if base_url else raw_url
    parsed = urlparse(absolute)
    return urlunparse(parsed._replace(fragment=""))


def get_next_index(*paths) -> int:
    max_idx = 0
    for path in paths:
        directory = os.path.dirname(path) or "."
        stem, ext = os.path.splitext(os.path.basename(path))
        if not os.path.isdir(directory):
            continue
        pattern = re.compile(rf"^{re.escape(stem)}(\d+)(?:_table\d+)?{re.escape(ext)}$")
        for name in os.listdir(directory):
            m = pattern.match(name)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def numbered_path(path: str, index: int, suffix: str = "") -> str:
    directory = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    return os.path.join(directory, f"{stem}{index}{suffix}{ext}")


# ---------------------------------------------------------------------------
# TABLE EXTRACTION

# ---------------------------------------------------------------------------
TABLE_EXTRACT_JS = r"""
() => {
  const clean = (t) => {
    let s = (t || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    // vertical text like "J A N U A R Y" -> "JANUARY"
    if (/^(?:[A-Za-z] ){2,}[A-Za-z]$/.test(s)) s = s.replace(/ /g, '');
    return s;
  };
  const out = [];

  Array.from(document.querySelectorAll('table')).forEach((table) => {
    const trs = Array.from(table.rows);          // rows of THIS table only (nested tables excluded)
    if (!trs.length) return;

    // 1. Expand the table into a grid
    const grid = [];
    trs.forEach((tr, r) => {
      grid[r] = grid[r] || [];
      let c = 0;
      Array.from(tr.cells).forEach((cell) => {
        while (grid[r][c]) c++;
        const rs = cell.rowSpan === 0 ? trs.length - r : Math.min(Math.max(1, cell.rowSpan || 1), 1000);
        const cs = Math.min(Math.max(1, cell.colSpan || 1), 100);
        const text = clean(cell.innerText);
        const th = cell.tagName === 'TH';
        for (let i = 0; i < rs && r + i < trs.length; i++) {
          grid[r + i] = grid[r + i] || [];
          for (let j = 0; j < cs; j++) {
            grid[r + i][c + j] = { text: text, th: th, origin: (i === 0 && j === 0), cs: cs };
          }
        }
        c += cs;
      });
    });

    const width = Math.max(0, ...grid.map((row) => (row ? row.length : 0)));
    if (!width) return;

    const ownCells = (r) => (grid[r] || []).filter((c) => c && c.origin);
    const isFullWidthSingle = (r) => {
      const own = ownCells(r);
      return width > 1 && own.length === 1 && own[0].cs >= width && own[0].text !== '';
    };
    const inThead = (r) => trs[r].parentElement && trs[r].parentElement.tagName === 'THEAD';
    const allTh = (r) => { const own = ownCells(r); return own.length > 0 && own.every((c) => c.th); };

    // 2. Header zone = leading <thead> / all-<th> rows
    let bodyStart = 0;
    while (bodyStart < trs.length && (inThead(bodyStart) || allTh(bodyStart))) bodyStart++;

    const titleParts = [];
    if (table.caption) titleParts.push(clean(table.caption.innerText));
    const headerRows = [];
    for (let r = 0; r < bodyStart; r++) {
      if (isFullWidthSingle(r)) titleParts.push(ownCells(r)[0].text);
      else headerRows.push(r);
    }

    // 3. Column names (several header rows are joined:  "Opening - Date")
    const headers = [];
    for (let c = 0; c < width; c++) {
      const parts = [];
      headerRows.forEach((r) => {
        const cell = (grid[r] || [])[c];
        const t = cell ? cell.text : '';
        if (t && parts[parts.length - 1] !== t) parts.push(t);
      });
      headers.push(parts.join(' - '));
    }

    // 4. Body rows
    const rows = [];
    let section = '';
    for (let r = bodyStart; r < trs.length; r++) {
      if (isFullWidthSingle(r)) { section = ownCells(r)[0].text; continue; }
      const cells = [];
      for (let c = 0; c < width; c++) {
        const cell = (grid[r] || [])[c];
        cells.push(cell ? cell.text : '');
      }
      if (!cells.some((v) => v)) continue;
      const a = trs[r].querySelector('a[href]');
      rows.push({ cells: cells, link: a ? a.href : '', section: section });
    }

    if (rows.length) out.push({ title: titleParts.filter(Boolean).join(' | '), headers: headers, rows: rows });
  });

  return out;
}
"""


def build_columns(headers: list) -> list:
    names, count = [], {}
    for i, h in enumerate(headers):
        name = h if h else f"Column_{i + 1}"
        if name.lower() in RESERVED_KEYS:
            name += "_col"
        count[name] = count.get(name, 0) + 1
        if count[name] > 1:
            name = f"{name}_{count[name]}"
        names.append(name)
    return names


def extract_html_tables(page) -> list:
    raw_tables = []
    for frame in page.frames:
        try:
            raw_tables.extend(frame.evaluate(TABLE_EXTRACT_JS))
        except Exception:
            continue  # frame detached / not accessible

    tables = []
    for raw in raw_tables:
        columns = build_columns(raw["headers"])
        records = []
        for row in raw["rows"]:
            rec = {col: row["cells"][i] for i, col in enumerate(columns)}
            if row["section"]:
                rec["section"] = row["section"]
            if row["link"]:
                rec["link"] = normalize_url(row["link"], page.url)
            records.append(rec)
        tables.append({"title": raw["title"], "columns": columns, "records": records})
    return tables


def scrape_generic_listings(page) -> list:
    items = []
    # Search for repeating card / list elements
    card_elements = page.query_selector_all("article, li, div.card, div.item, div.listing, div.row")

    for card in card_elements:
        try:
            raw_txt = card.inner_text() or ""
            txt = clean_text(raw_txt)
            if not txt or len(txt) < 5:
                continue

            link_el = card.query_selector("a[href]")
            link = normalize_url(link_el.get_attribute("href"), page.url) if link_el else ""

            heading_el = card.query_selector("h1, h2, h3, h4, h5, a, strong")
            heading = clean_text(heading_el.inner_text()) if heading_el else ""

            # split the ORIGINAL text by lines (clean_text would have removed the line breaks)
            lines = [clean_text(l) for l in raw_txt.split("\n") if clean_text(l)]
            title = heading or (lines[0] if lines else "")
            subtitle = lines[1] if len(lines) > 1 else ""

            items.append({
                "title": title,
                "detail": subtitle,
                "full_text": txt[:150],
                "link": link
            })
        except Exception:
            pass

    return items


def remove_duplicates(data_list: list) -> list:

    seen = set()
    unique = []

    for item in data_list:
        # Hashable key from keys + values (so rows from different tables never clash)
        key = tuple((k, str(v).lower().strip()) for k, v in item.items())

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


def save_to_csv(data: list, csv_path: str):
   
    if not data:
        print(f"[WARN] No records to save to CSV: {csv_path}")
        return

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    # Collect all unique fieldnames in first-seen order (= table header order)
    all_fieldnames = []
    for row in data:
        for key in row.keys():
            if key not in all_fieldnames:
                all_fieldnames.append(key)

  
    with open(csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=all_fieldnames, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in data:
            writer.writerow(row)

    print(f"[SUCCESS] Saved {len(data)} records to CSV: {csv_path}")


def save_to_json(data: list, json_path: str):
    """Save extracted records cleanly to formatted JSON output."""
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, mode="w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[SUCCESS] Saved {len(data)} records to JSON: {json_path}")


def main():
    parser = argparse.ArgumentParser(description="Universal Web Table & Data Scraper using Playwright")
    parser.add_argument("--url", type=str, help="Any webpage link to scrape data from.")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode (headless=False)")
    parser.add_argument("--split", action="store_true", help="Save every HTML table in its own CSV/JSON file")
    parser.add_argument("--csv", type=str, default=DEFAULT_CSV_PATH, help="Base path for CSV output (saved as data1.csv, data2.csv, ...)")
    parser.add_argument("--json", type=str, default=DEFAULT_JSON_PATH, help="Base path for JSON output (saved as data1.json, data2.json, ...)")

    args = parser.parse_args()

    url = args.url or input("Website link kudunga: ").strip()
    if not url:
        print("[ERROR] No link provided.")
        sys.exit(1)
    target_url = normalize_input_url(url)

    headless = not args.headed

    print("=" * 60)
    print("UNIVERSAL WEB TABLE & DATA SCRAPER")
    print("=" * 60)
    print(f"Target URL: {target_url}")
    print(f"Headless Mode: {headless}")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception as err:
            print(f"[ERROR] Could not start browser: {err}")
            print("Run this once and try again:  playwright install chromium")
            sys.exit(1)

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )

        page = context.new_page()
        print("[NAVIGATE] Loading target webpage...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print("[ERROR] Page took too long to load.")
            browser.close()
            sys.exit(1)
        except Exception as err:
            print(f"[ERROR] Could not open the link: {err}")
            browser.close()
            sys.exit(1)

        print(f"[PAGE TITLE] {page.title()}")

    
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        for _ in range(6):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(400)

        scraped_data = []

        # 1. HTML <table> elements (each table keeps its own header keys)
        print("[ENGINE] Checking for HTML <table> elements...")
        tables = extract_html_tables(page)
        for n, t in enumerate(tables, 1):
            label = f" '{t['title']}'" if t["title"] else ""
            print(f"[TABLE {n}]{label} {len(t['records'])} rows | keys: {t['columns']}")
        scraped_data = [rec for t in tables for rec in t["records"]]

        # 2. Repeating Cards/Listings (only when the page has no table data)
        if not scraped_data:
            print("[ENGINE] No tables found. Checking for repeating list/card items...")
            scraped_data = scrape_generic_listings(page)

        browser.close()

    if not scraped_data:
        print("[WARN] Nothing extracted from this page. No files saved.")
        print("       (The page may need login, or the data may be loaded in some special way.)")
        sys.exit(0)

    run_idx = get_next_index(args.csv, args.json)

    if args.split and tables:
        # every table in its own files: data1_table1.csv, data1_table2.csv ...
        for n, t in enumerate(tables, 1):
            records = remove_duplicates(t["records"])
            save_to_csv(records, numbered_path(args.csv, run_idx, f"_table{n}"))
            save_to_json(records, numbered_path(args.json, run_idx, f"_table{n}"))
    else:
        clean_data = remove_duplicates(scraped_data)
        print(f"[SUMMARY] Total raw extracted: {len(scraped_data)} | Clean unique records: {len(clean_data)}")
        save_to_csv(clean_data, numbered_path(args.csv, run_idx))
        save_to_json(clean_data, numbered_path(args.json, run_idx))

    print("=" * 60)
    print("UNIVERSAL SCRAPING COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()