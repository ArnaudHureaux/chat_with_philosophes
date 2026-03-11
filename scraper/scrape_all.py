#!/usr/bin/env python3
"""
Scraper autonome LibGen — Tous les philosophes
===============================================
Télécharge les livres de philosophes depuis libgen.li
et convertit les EPUBs en texte brut.

Usage:
    python scraper/scrape_all.py --start 525 --end 630
    python scraper/scrape_all.py --start 1 --end 100
    python scraper/scrape_all.py                       # tout (1-630)

Reprise:
    Si interrompu, relancer la même commande. Les auteurs déjà scrapés
    (metadata existante avec ≥1 livre) sont automatiquement ignorés.

Dépendances:
    pip install requests beautifulsoup4 lxml ebooklib
"""

import os
import re
import sys
import json
import time
import argparse
import logging
import warnings
from difflib import SequenceMatcher
import requests
import urllib3
from pathlib import Path
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from langdetect import detect, LangDetectException

urllib3.disable_warnings()
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent
BOOKS_DIR = BASE_DIR / "books"
EPUB_PDF_DIR = BOOKS_DIR / "epub_pdf"
TXT_DIR = BOOKS_DIR / "txt"
META_DIR = BOOKS_DIR / "metadata"
DATA_FILE = Path(__file__).resolve().parent / "philosophers_data.json"

LIBGEN_LI = "https://libgen.li"
VERIFY_SSL = False
REQUEST_DELAY = 2.0       # seconds between requests
MAX_BOOKS = 100           # books to download per author
MAX_RESULTS = 100         # results per libgen search

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36"
}

EXT_PRIORITY = {"epub": 0, "pdf": 1, "txt": 2, "djvu": 3, "mobi": 4}

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "scrape_all.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scrape_all")


# ═══════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════

def load_philosophers() -> dict:
    """Load the hardcoded philosopher mapping from JSON."""
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_folder_name(name: str) -> str:
    """Convert a philosopher name to a safe folder name."""
    # Remove parenthetical suffixes like "(2)", "(acc.)", "(esthétique)"
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name)
    # Remove special chars but keep accented letters and hyphens
    name = re.sub(r'[^\w\sàâäéèêëïîôùûüçÀÂÄÉÈÊËÏÎÔÙÛÜÇæœ\-]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name


# ═══════════════════════════════════════════════════════════
#  DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════

# Some philosophers appear multiple times in PHILOSOPHERS.md.
# We track which canonical names have been processed to avoid re-scraping.
_CANONICAL_MAP = {}  # populated in main()


def get_canonical_name(philosopher: dict) -> str:
    """Get a canonical name for deduplication."""
    name = philosopher["name"]
    # Remove trailing annotations like "(2)", "(acc.)", "(esthétique)", "(Pensées)", etc.
    clean = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
    return clean


# ═══════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════

def safe_get(url, params=None, timeout=30):
    """GET with SSL bypass and error handling."""
    try:
        r = requests.get(
            url, params=params, headers=HEADERS,
            timeout=timeout, verify=VERIFY_SSL
        )
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        log.warning("GET failed %s: %s", url[:80], e)
        return None


# ═══════════════════════════════════════════════════════════
#  LIBGEN.LI SEARCH
# ═══════════════════════════════════════════════════════════

def search_libgen_li(query: str, column: str = "a") -> list[dict]:
    """Search libgen.li via index.php.

    Args:
        query: Search query string.
        column: Column to search in — "a" (author), "t" (title),
                or "all" (default/all columns).
    """
    url = f"{LIBGEN_LI}/index.php"
    params = {
        "req": query,
        "topics[]": "l",
        "res": str(MAX_RESULTS),
    }
    if column != "all":
        params["columns[]"] = column

    col_label = {"a": "author", "t": "title", "all": "all"}
    log.info("  🔍 libgen.li [%s]: req=%s", col_label.get(column, column), query)
    resp = safe_get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Find the results table (first table with >3 rows)
    result_table = None
    for t in soup.find_all("table"):
        if len(t.find_all("tr")) > 3:
            result_table = t
            break

    if not result_table:
        log.info("    ⚠️  Pas de table de résultats")
        return []

    rows = result_table.find_all("tr")[1:]  # skip header
    results = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 9:
            continue
        try:
            # Title: look for edition.php link in col 0
            title = ""
            for a in cols[0].find_all("a", href=True):
                if "edition.php" in a["href"]:
                    title = a.get_text(strip=True)
                    break
            if not title:
                title = cols[0].get_text(strip=True)[:150]

            authors = cols[1].get_text(strip=True)
            year = cols[3].get_text(strip=True)
            language = cols[4].get_text(strip=True)
            size = cols[6].get_text(strip=True)
            extension = cols[7].get_text(strip=True).lower()

            # MD5 from mirrors column (col 8)
            md5 = ""
            for a in cols[8].find_all("a", href=True):
                m = re.search(r"md5=([a-fA-F0-9]{32})", a["href"], re.IGNORECASE)
                if m:
                    md5 = m.group(1).lower()
                    break

            if not md5:
                continue

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "language": language,
                "size": size,
                "extension": extension,
                "md5": md5,
                "source": "libgen.li",
            })
        except (IndexError, AttributeError):
            continue

    log.info("    📚 %d résultats", len(results))
    return results


def search_with_fallback(queries: list[str], search_mode: str = "author") -> list[dict]:
    """Multi-strategy search with fallback.

    For search_mode="author" (default):
        1. Author column search (columns[]=a)
        2. If 0 results → all-columns search (no columns[] param)

    For search_mode="title":
        1. Title column search (columns[]=t)
        2. If 0 results → all-columns search

    Returns aggregated results from all queries.
    """
    primary_col = "a" if search_mode == "author" else "t"
    all_results = []

    for query in queries:
        # Primary search
        results = search_libgen_li(query, column=primary_col)
        if results:
            all_results.extend(results)
        else:
            # Fallback: all-columns search
            log.info("  🔄 Fallback → recherche tous champs: %s", query)
            time.sleep(REQUEST_DELAY)
            results = search_libgen_li(query, column="all")
            all_results.extend(results)
        time.sleep(REQUEST_DELAY)

    return all_results


# ═══════════════════════════════════════════════════════════
#  DOWNLOAD
# ═══════════════════════════════════════════════════════════

def download_via_libgen_ads(md5: str, filepath: Path) -> bool:
    """Download via libgen.li/ads.php?md5=xxx → get.php link."""
    url = f"{LIBGEN_LI}/ads.php?md5={md5}"
    log.info("    ⬇️  %s", url)

    resp = safe_get(url)
    if not resp:
        return False

    soup = BeautifulSoup(resp.text, "lxml")

    dl_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()
        if text == "get" or href.startswith("get.php"):
            if not href.startswith("http"):
                href = f"{LIBGEN_LI}/{href}"
            dl_link = href
            break
        if any(href.lower().endswith(f".{ext}") for ext in EXT_PRIORITY):
            if not href.startswith("http"):
                href = f"{LIBGEN_LI}/{href}"
            dl_link = href
            break

    # Fallback: library.lol
    if not dl_link:
        for a in soup.find_all("a", href=True):
            if "library.lol" in a["href"]:
                dl_link = a["href"]
                break

    if not dl_link:
        log.warning("    ❌ Pas de lien de téléchargement sur la page ads")
        return False

    return _download_file(dl_link, filepath)


def _download_file(url: str, filepath: Path) -> bool:
    """Download from direct URL to filepath."""
    try:
        r = requests.get(
            url, headers=HEADERS, timeout=120,
            verify=VERIFY_SSL, stream=True
        )
        r.raise_for_status()

        total = 0
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                total += len(chunk)

        if total < 1000:
            filepath.unlink(missing_ok=True)
            log.warning("    ❌ Trop petit (%d bytes) — supprimé", total)
            return False

        size_mb = total / (1024 * 1024)
        log.info("    ✅ %s (%.1f MB)", filepath.name, size_mb)
        return True

    except requests.RequestException as e:
        log.warning("    ❌ Téléchargement échoué: %s", e)
        if filepath.exists():
            filepath.unlink(missing_ok=True)
        return False


def download_book(book: dict, author_dir: Path) -> Path | None:
    """Download a book. Returns filepath on success, None on failure."""
    ext = book.get("extension", "epub")
    # Sanitize title for filename
    safe_title = re.sub(
        r'[^\w\sàâéèêëïîôùûüçÀÂÉÈÊËÏÎÔÙÛÜÇ\-]', '',
        book.get("title", "unknown")
    )
    safe_title = re.sub(r'\s+', '_', safe_title.strip())[:80]
    filepath = author_dir / f"{safe_title}.{ext}"

    if filepath.exists() and filepath.stat().st_size > 1000:
        log.info("    ✅ Déjà téléchargé: %s", filepath.name)
        return filepath

    md5 = book["md5"]
    if download_via_libgen_ads(md5, filepath):
        return filepath

    return None


# ═══════════════════════════════════════════════════════════
#  EPUB → TEXT
# ═══════════════════════════════════════════════════════════

def epub_to_text(epub_path: Path) -> str:
    """Convert EPUB to plain text."""
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError:
        log.error("    ⚠️  pip install ebooklib")
        return ""
    try:
        book = epub.read_epub(str(epub_path))
    except Exception as e:
        log.warning("    ❌ Lecture EPUB échouée: %s", e)
        return ""

    parts = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), "lxml")
            text = soup.get_text(separator="\n")
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            if len(text) > 50:
                parts.append(text)
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════
#  FILTER & RANK
# ═══════════════════════════════════════════════════════════

ALLOWED_LANGS = {"french", "français", "fr", "english", "en", "anglais"}
FUZZY_THRESHOLD = 0.55  # titles more similar than this are considered duplicates


def _is_lang_allowed(r: dict) -> bool:
    """Return True if book language is FR, EN, or unknown/empty."""
    lang = r.get("language", "").strip().lower()
    if not lang:
        return True  # unknown → keep, langdetect will verify later
    return any(x in lang for x in ALLOWED_LANGS)


def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy comparison."""
    t = title.lower()
    t = re.sub(r'[^a-zàâéèêëïîôùûüç0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _is_fuzzy_dup(title: str, existing_titles: list[str]) -> bool:
    """Check if title is a fuzzy duplicate of any existing title."""
    norm = _normalize_title(title)
    if len(norm) < 5:
        return False
    for existing in existing_titles:
        ratio = SequenceMatcher(None, norm, existing).ratio()
        if ratio >= FUZZY_THRESHOLD:
            return True
    return False


def filter_and_rank(results: list[dict]) -> list[dict]:
    """FR/EN only, epub first, fuzzy-deduplicate by title."""

    # 1. Strict language filter — drop non-FR/EN
    filtered = [r for r in results if _is_lang_allowed(r)]
    dropped = len(results) - len(filtered)
    if dropped:
        log.info("    🌐 %d résultats non-FR/EN supprimés", dropped)

    # 2. Sort: FR first, then EN, then epub > pdf > ...
    def lang_score(r):
        lang = r.get("language", "").lower()
        if any(x in lang for x in ["french", "français", "fr"]):
            return 0
        if any(x in lang for x in ["english", "en"]):
            return 1
        return 2

    def ext_score(r):
        return EXT_PRIORITY.get(r.get("extension", ""), 99)

    filtered.sort(key=lambda r: (lang_score(r), ext_score(r)))

    # 3. Fuzzy dedup — catches translations & near-identical titles
    unique = []
    seen_titles = []  # normalized titles for fuzzy matching
    seen_md5 = set()
    for r in filtered:
        md5 = r.get("md5", "")
        if md5 in seen_md5:
            continue
        seen_md5.add(md5)

        title = r.get("title", "")
        norm = _normalize_title(title)
        if norm and _is_fuzzy_dup(title, seen_titles):
            continue
        seen_titles.append(norm)
        unique.append(r)

    if len(filtered) - len(unique) > 0:
        log.info("    🔄 %d doublons fuzzy supprimés", len(filtered) - len(unique))

    return unique


# ═══════════════════════════════════════════════════════════
#  RESUMABILITY CHECK
# ═══════════════════════════════════════════════════════════

def is_already_scraped(folder_name: str) -> bool:
    """Check if an author has already been scraped (metadata file with data)."""
    meta_path = META_DIR / f"{folder_name}_libgen.json"
    if not meta_path.exists():
        return False
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Consider scraped if metadata exists (even empty list = searched but nothing found)
        return True
    except (json.JSONDecodeError, IOError):
        return False


# ═══════════════════════════════════════════════════════════
#  PROCESS ONE AUTHOR
# ═══════════════════════════════════════════════════════════

def process_author(num: int, philosopher: dict) -> list[dict]:
    """Search + download + convert for one author. Returns list of downloaded books."""
    name = philosopher["name"]
    folder = safe_folder_name(name)

    log.info("=" * 60)
    log.info("📖 #%d — %s", num, name)
    log.info("=" * 60)

    # Resumability
    if is_already_scraped(folder):
        log.info("  ⏭️  Déjà scrapé (metadata existante) → skip")
        return []

    # Search all queries — use search_mode from JSON if defined
    search_mode = philosopher.get("search_mode", "author")
    all_results = search_with_fallback(philosopher["queries"], search_mode)

    if not all_results:
        log.info("  ⚠️  Aucun résultat pour %s", name)
        # Save empty metadata to mark as "searched"
        META_DIR.mkdir(parents=True, exist_ok=True)
        meta_path = META_DIR / f"{folder}_libgen.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []

    ranked = filter_and_rank(all_results)
    log.info("  📋 %d uniques (sur %d bruts)", len(ranked), len(all_results))

    # Show top results
    for i, book in enumerate(ranked[:MAX_BOOKS * 2]):
        lang = book.get("language", "?")[:6]
        ext = book.get("extension", "?")
        log.info(
            "  %2d. [%4s] [%6s] %s",
            i + 1, ext, lang, book.get("title", "?")[:55]
        )

    # Download
    author_epub = EPUB_PDF_DIR / folder
    author_epub.mkdir(parents=True, exist_ok=True)
    author_txt = TXT_DIR / folder
    author_txt.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for book in ranked[:MAX_BOOKS]:
        log.info("  --- %s ---", book.get("title", "?")[:60])
        filepath = download_book(book, author_epub)
        if filepath:
            book["local_path"] = str(filepath)

            # Convert EPUB to text
            if filepath.suffix.lower() == ".epub":
                text = epub_to_text(filepath)
                if text:
                    # Post-download language detection
                    try:
                        sample = text[:5000]
                        detected_lang = detect(sample)
                    except LangDetectException:
                        detected_lang = "unknown"

                    if detected_lang not in ("fr", "en"):
                        log.info(
                            "    🚫 Langue détectée: %s → supprimé",
                            detected_lang
                        )
                        filepath.unlink(missing_ok=True)
                        continue

                    txt_path = author_txt / f"{filepath.stem}.txt"
                    txt_path.write_text(
                        f"# {book.get('title', filepath.stem)}\n"
                        f"# Source: libgen.li\n"
                        f"# Lang: {detected_lang}\n"
                        f"# Chars: {len(text)}\n\n{text}",
                        encoding="utf-8"
                    )
                    book["txt_path"] = str(txt_path)
                    book["chars"] = len(text)
                    book["words"] = len(text.split())
                    book["detected_lang"] = detected_lang
                    log.info(
                        "    📝 Texte [%s]: %s chars, ~%s mots",
                        detected_lang,
                        f"{len(text):,}", f"{len(text.split()):,}"
                    )

            downloaded.append(book)
        time.sleep(REQUEST_DELAY)

    # Save metadata
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{folder}_libgen.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(downloaded, f, ensure_ascii=False, indent=2)
    log.info("  💾 Metadata: %s", meta_path.name)

    return downloaded


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scraper autonome LibGen — Philosophes"
    )
    parser.add_argument(
        "--start", type=int, default=1,
        help="Numéro du premier philosophe (défaut: 1)"
    )
    parser.add_argument(
        "--end", type=int, default=630,
        help="Numéro du dernier philosophe (défaut: 630)"
    )
    parser.add_argument(
        "--max-books", type=int, default=100,
        help="Nombre max de livres par auteur (défaut: 100)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-scraper même si déjà fait"
    )
    args = parser.parse_args()

    global MAX_BOOKS
    MAX_BOOKS = args.max_books

    # Load data
    philosophers = load_philosophers()

    # Create directories
    for d in [EPUB_PDF_DIR, TXT_DIR, META_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("📚 Scraper LibGen.li — Philosophes #%d → #%d", args.start, args.end)
    log.info("   Max livres/auteur: %d", MAX_BOOKS)
    log.info("   Force rescrape: %s", args.force)
    log.info("   Source: %s", LIBGEN_LI)
    log.info("   Dossiers: epub_pdf/ + txt/ + metadata/")
    log.info("=" * 60)

    # Track duplicates — some entries in the MD refer to the same person
    processed_canonical = set()

    total_books = 0
    total_chars = 0
    total_skipped = 0
    total_errors = 0

    for num in range(args.start, args.end + 1):
        key = str(num)
        if key not in philosophers:
            log.warning("⚠️  #%d non trouvé dans le mapping — skip", num)
            continue

        philosopher = philosophers[key]
        canonical = get_canonical_name(philosopher)

        # Deduplicate: skip if same canonical name already processed in this run
        if canonical in processed_canonical:
            log.info("⏭️  #%d %s — doublon de %s → skip", num, philosopher["name"], canonical)
            total_skipped += 1
            continue

        folder = safe_folder_name(philosopher["name"])

        # Resumability (unless --force)
        if not args.force and is_already_scraped(folder):
            log.info("⏭️  #%d %s — déjà scrapé → skip", num, philosopher["name"])
            processed_canonical.add(canonical)
            total_skipped += 1
            continue

        try:
            downloaded = process_author(num, philosopher)
            total_books += len(downloaded)
            total_chars += sum(b.get("chars", 0) for b in downloaded)
            processed_canonical.add(canonical)
        except KeyboardInterrupt:
            log.info("\n⏸️  Interrompu par l'utilisateur à #%d", num)
            log.info("   Relancer avec --start %d pour reprendre", num)
            break
        except Exception as e:
            log.error("❌ Erreur sur #%d %s: %s", num, philosopher["name"], e)
            total_errors += 1
            processed_canonical.add(canonical)
            continue

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info("📊 RÉSUMÉ")
    log.info("   Plage: #%d → #%d", args.start, args.end)
    log.info("   Livres téléchargés: %d", total_books)
    if total_chars:
        log.info("   Texte extrait: %s chars", f"{total_chars:,}")
    log.info("   Auteurs ignorés (déjà faits/doublons): %d", total_skipped)
    if total_errors:
        log.info("   Erreurs: %d", total_errors)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
