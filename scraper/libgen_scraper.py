"""
Scraper LibGen (libgen.li) + Anna's Archive — Domaine public uniquement
=======================================================================
Télécharge les livres de philosophes (domaine public) et les convertit en texte.

Prérequis:
    pip install requests beautifulsoup4 lxml ebooklib

Usage:
    python scraper/libgen_scraper.py
"""

import os
import re
import json
import time
import requests
import urllib3
from pathlib import Path
from bs4 import BeautifulSoup

urllib3.disable_warnings()

# --- CONFIG ---
BASE_DIR = Path(__file__).resolve().parent.parent
BOOKS_DIR = BASE_DIR / "books"
EPUB_DIR = BOOKS_DIR / "epub"
TXT_DIR = BOOKS_DIR / "txt_libgen"
META_DIR = BOOKS_DIR / "metadata"

VERIFY_SSL = False
REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# --- MIRRORS ---
LIBGEN_LI = "https://libgen.li"
ANNAS_ARCHIVE = "https://annas-archive.gl"

# --- AUTEURS CIBLES (POC) ---
AUTHORS = [
    {
        "name": "Platon",
        "queries": ["Platon", "Plato"],
        "death_year": -348,
    },
    {
        "name": "Antisthène",
        "queries": ["Antisthène", "Antisthenes"],
        "death_year": -365,
    },
]

EXT_PRIORITY = {"epub": 0, "pdf": 1, "txt": 2, "djvu": 3, "mobi": 4}


def safe_get(url, params=None, timeout=30):
    """GET with SSL bypass and error handling."""
    try:
        r = requests.get(url, params=params, headers=HEADERS,
                         timeout=timeout, verify=VERIFY_SSL)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"    ❌ {url[:80]}: {e}", flush=True)
        return None


# ═══════════════════════════════════════════════════════════
#  LIBGEN.LI SEARCH
# ═══════════════════════════════════════════════════════════

def search_libgen_li(query, max_results=25):
    """Search libgen.li via index.php."""
    url = f"{LIBGEN_LI}/index.php"
    params = {
        "req": query,
        "columns[]": "a",
        "topics[]": "l",
        "res": str(max_results),
    }

    print(f"  🔍 libgen.li: req={query}, column=author", flush=True)
    resp = safe_get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    result_table = None
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) > 3:
            result_table = t
            break

    if not result_table:
        print(f"    ⚠️  No results table", flush=True)
        return []

    rows = result_table.find_all("tr")[1:]

    results = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 9:
            continue
        try:
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

            md5 = ""
            for a in cols[8].find_all("a", href=True):
                href = a["href"]
                m = re.search(r"md5=([a-fA-F0-9]{32})", href, re.IGNORECASE)
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

    print(f"    📚 {len(results)} results", flush=True)
    return results


# ═══════════════════════════════════════════════════════════
#  ANNA'S ARCHIVE SEARCH
# ═══════════════════════════════════════════════════════════

def search_annas_archive(query, lang="fr"):
    """Search Anna's Archive."""
    url = f"{ANNAS_ARCHIVE}/search"
    params = {
        "q": query,
        "lang": lang,
        "content": "book_nonfiction",
        "ext": "epub",
    }

    print(f"  🔍 Anna's Archive: q={query}, lang={lang}", flush=True)
    resp = safe_get(url, params=params)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    seen = set()
    results = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        m = re.search(r"/md5/([a-fA-F0-9]{32})", href, re.IGNORECASE)
        if not m:
            continue
        md5 = m.group(1).lower()
        if md5 in seen:
            continue
        seen.add(md5)

        text = a.get_text(" ", strip=True)[:200]
        if not text or len(text) < 5:
            continue

        results.append({
            "title": text,
            "md5": md5,
            "source": "annas_archive",
            "extension": "epub",
            "language": lang,
        })

    print(f"    📚 {len(results)} unique results", flush=True)
    return results


# ═══════════════════════════════════════════════════════════
#  DOWNLOAD
# ═══════════════════════════════════════════════════════════

def download_via_libgen_ads(md5, filepath):
    """Download via libgen.li/ads.php?md5=xxx."""
    url = f"{LIBGEN_LI}/ads.php?md5={md5}"
    print(f"    ⬇️  {url}", flush=True)

    resp = safe_get(url)
    if not resp:
        return False

    soup = BeautifulSoup(resp.text, "lxml")

    dl_link = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()
        if "get" == text or href.startswith("get.php"):
            # Relative URL — must add leading /
            if not href.startswith("http"):
                href = f"{LIBGEN_LI}/{href}"
            dl_link = href
            break
        if any(href.lower().endswith(f".{ext}") for ext in EXT_PRIORITY):
            if not href.startswith("http"):
                href = f"{LIBGEN_LI}/{href}"
            dl_link = href
            break

    if not dl_link:
        for a in soup.find_all("a", href=True):
            if "library.lol" in a["href"]:
                dl_link = a["href"]
                break

    if not dl_link:
        print(f"    ❌ No download link on ads page", flush=True)
        return False

    return _download_file(dl_link, filepath)


def download_via_annas(md5, filepath):
    """Download via Anna's Archive md5 page."""
    url = f"{ANNAS_ARCHIVE}/md5/{md5}"
    print(f"    ⬇️  {url}", flush=True)

    resp = safe_get(url)
    if not resp:
        return False

    soup = BeautifulSoup(resp.text, "lxml")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/slow_download/" in href or "/fast_download/" in href:
            full_url = href if href.startswith("http") else f"{ANNAS_ARCHIVE}{href}"
            print(f"    ⬇️  DL: {full_url[:80]}...", flush=True)
            if _download_file(full_url, filepath):
                return True

    print(f"    ❌ No download link on Anna's page", flush=True)
    return False


def _download_file(url, filepath):
    """Download from direct URL."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=120,
                         verify=VERIFY_SSL, stream=True)
        r.raise_for_status()

        total = 0
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                total += len(chunk)

        if total < 1000:
            filepath.unlink(missing_ok=True)
            print(f"    ❌ Too small ({total} bytes)", flush=True)
            return False

        size_mb = total / (1024 * 1024)
        print(f"    ✅ {filepath.name} ({size_mb:.1f} MB)", flush=True)
        return True

    except requests.RequestException as e:
        print(f"    ❌ Download failed: {e}", flush=True)
        if filepath.exists():
            filepath.unlink(missing_ok=True)
        return False


def download_book(book, author_dir):
    """Try all download methods."""
    ext = book.get("extension", "epub")
    safe_title = re.sub(r'[^\w\sàâéèêëïîôùûüçÀÂÉÈÊËÏÎÔÙÛÜÇ\-]', '', book.get("title", "unknown"))
    safe_title = re.sub(r'\s+', '_', safe_title.strip())[:80]
    filepath = author_dir / f"{safe_title}.{ext}"

    if filepath.exists() and filepath.stat().st_size > 1000:
        print(f"    ✅ Already exists: {filepath.name}", flush=True)
        return filepath

    md5 = book["md5"]

    if download_via_libgen_ads(md5, filepath):
        return filepath
    time.sleep(REQUEST_DELAY)

    if download_via_annas(md5, filepath):
        return filepath

    return None


# ═══════════════════════════════════════════════════════════
#  EPUB → TEXT
# ═══════════════════════════════════════════════════════════

def epub_to_text(epub_path):
    """Convert EPUB to plain text."""
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError:
        print("    ⚠️  pip install ebooklib", flush=True)
        return ""
    try:
        book = epub.read_epub(str(epub_path))
    except Exception as e:
        print(f"    ❌ EPUB read error: {e}", flush=True)
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

def filter_and_rank(results):
    """FR first, epub first, deduplicate."""
    def lang_score(r):
        lang = r.get("language", "").lower()
        if any(x in lang for x in ["french", "français", "fr"]):
            return 0
        if any(x in lang for x in ["english", "en"]):
            return 1
        return 2

    def ext_score(r):
        return EXT_PRIORITY.get(r.get("extension", ""), 99)

    results.sort(key=lambda r: (lang_score(r), ext_score(r)))

    seen = set()
    unique = []
    for r in results:
        key = re.sub(r'[^a-zàâéèêëïîôùûüç0-9]', '', r.get("title", "").lower())[:50]
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def process_author(author, max_books=5):
    """Search + download + convert for one author."""
    name = author["name"]
    print(f"\n{'='*60}", flush=True)
    print(f"📖 {name}", flush=True)
    print(f"{'='*60}", flush=True)

    all_results = []

    for query in author["queries"]:
        results = search_libgen_li(query)
        all_results.extend(results)
        time.sleep(REQUEST_DELAY)

        for lang in ["fr", "en"]:
            results = search_annas_archive(query, lang=lang)
            all_results.extend(results)
            time.sleep(REQUEST_DELAY)

    if not all_results:
        print(f"  ⚠️  No results for {name}", flush=True)
        return []

    ranked = filter_and_rank(all_results)
    print(f"\n  📋 {len(ranked)} unique (from {len(all_results)} raw)", flush=True)

    for i, book in enumerate(ranked[:max_books * 2]):
        lang = book.get("language", "?")[:6]
        src = book.get("source", "?")[:12]
        ext = book.get("extension", "?")
        print(f"  {i+1:2d}. [{ext:4s}] [{lang:6s}] [{src:12s}] {book.get('title', '?')[:55]}", flush=True)

    # Download
    author_epub = EPUB_DIR / name.replace(" ", "_")
    author_epub.mkdir(parents=True, exist_ok=True)
    author_txt = TXT_DIR / name.replace(" ", "_")
    author_txt.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for book in ranked[:max_books]:
        print(f"\n  --- {book.get('title','?')[:60]} ---", flush=True)
        filepath = download_book(book, author_epub)
        if filepath:
            book["local_path"] = str(filepath)

            if filepath.suffix.lower() == ".epub":
                text = epub_to_text(filepath)
                if text:
                    txt_path = author_txt / f"{filepath.stem}.txt"
                    txt_path.write_text(
                        f"# {book.get('title', filepath.stem)}\n"
                        f"# Source: {book.get('source', 'libgen')}\n"
                        f"# Chars: {len(text)}\n\n{text}",
                        encoding="utf-8"
                    )
                    book["txt_path"] = str(txt_path)
                    book["chars"] = len(text)
                    book["words"] = len(text.split())
                    print(f"    📝 Text: {len(text):,} chars, ~{len(text.split()):,} words", flush=True)

            downloaded.append(book)
        time.sleep(REQUEST_DELAY)

    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{name}_libgen.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(downloaded, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 Metadata: {meta_path}", flush=True)

    return downloaded


def main():
    print("=" * 60, flush=True)
    print("📚 LibGen.li + Anna's Archive Scraper", flush=True)
    print("   Public domain philosophers only", flush=True)
    print(f"   Sources: {LIBGEN_LI} + {ANNAS_ARCHIVE}", flush=True)
    print("=" * 60, flush=True)

    for d in [EPUB_DIR, TXT_DIR, META_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    all_downloaded = []
    for author in AUTHORS:
        downloaded = process_author(author, max_books=5)
        all_downloaded.extend(downloaded)

    print(f"\n{'='*60}", flush=True)
    print(f"📊 SUMMARY", flush=True)
    print(f"   Downloaded: {len(all_downloaded)} books", flush=True)
    total_chars = sum(b.get("chars", 0) for b in all_downloaded)
    total_words = sum(b.get("words", 0) for b in all_downloaded)
    if total_chars:
        print(f"   Text: {total_chars:,} chars (~{total_words:,} words)", flush=True)
    for b in all_downloaded:
        print(f"   - [{b.get('extension','?')}] {b.get('title', '?')[:55]} ({b.get('chars', '?')} chars)", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
