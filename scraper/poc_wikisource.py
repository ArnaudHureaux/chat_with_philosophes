"""
POC Scraper Wikisource FR — Platon + Antisthène
================================================
Récupère les textes intégraux des dialogues de Platon
(traductions domaine public) depuis fr.wikisource.org.

Usage:
    python scraper/poc_wikisource.py
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
TXT_DIR = BOOKS_DIR / "txt"
META_DIR = BOOKS_DIR / "metadata"

API = "https://fr.wikisource.org/w/api.php"
HEADERS = {"User-Agent": "PhiloScraper/1.0 (research project; contact: github)"}
VERIFY_SSL = False  # Désactivé car problème certificats sur cette machine

# Délai entre requêtes (en secondes) — respecter Wikimedia
REQUEST_DELAY = 0.5


# --- AUTEURS CIBLES (POC) ---
AUTHORS = {
    "Platon": {
        "category": "Œuvres de Platon",
        "death_year": -348,
        "search_prefixes": ["Platon"],
    },
    "Antisthène": {
        "category": None,  # Pas de catégorie dédiée
        "death_year": -365,
        "search_prefixes": ["Antisthène"],
    },
}


def api_get(params: dict) -> dict:
    """Requête GET sur l'API Wikisource."""
    params["format"] = "json"
    r = requests.get(API, params=params, timeout=30, verify=VERIFY_SSL, headers=HEADERS)
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r.json()


def get_category_pages(category: str, limit: int = 500) -> list[dict]:
    """Liste toutes les pages d'une catégorie (avec continuation)."""
    all_pages = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Catégorie:{category}",
        "cmlimit": str(min(limit, 500)),
        "cmtype": "page|subcat",
    }
    while True:
        data = api_get(params)
        pages = data.get("query", {}).get("categorymembers", [])
        all_pages.extend(pages)
        cont = data.get("continue")
        if not cont or len(all_pages) >= limit:
            break
        params["cmcontinue"] = cont["cmcontinue"]
    return all_pages


def get_all_subpages(prefix: str, limit: int = 500) -> list[dict]:
    """Liste toutes les sous-pages d'un prefix."""
    all_pages = []
    params = {
        "action": "query",
        "list": "allpages",
        "apprefix": prefix,
        "aplimit": str(min(limit, 500)),
    }
    while True:
        data = api_get(params)
        pages = data.get("query", {}).get("allpages", [])
        all_pages.extend(pages)
        cont = data.get("continue")
        if not cont or len(all_pages) >= limit:
            break
        params["apcontinue"] = cont["apcontinue"]
    return all_pages


def extract_text_from_page(title: str) -> str:
    """Extrait le texte brut d'une page Wikisource via action=parse."""
    try:
        data = api_get({"action": "parse", "page": title, "prop": "text"})
    except requests.RequestException as e:
        print(f"    ❌ Erreur API pour '{title}': {e}")
        return ""

    html = data.get("parse", {}).get("text", {}).get("*", "")
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Retirer navigation, metadata, tableaux, styles, notes de bas de page
    for selector in ["table", "style", "script", "sup.reference",
                     ".mw-editsection", ".ws-noexport", ".navigation",
                     ".header", ".headertemplate", "#headerContainer",
                     ".footertemplate", ".prp-pages-output-header"]:
        for tag in soup.select(selector):
            tag.decompose()

    # Extraire le texte du contenu principal
    # Sur Wikisource, le texte est souvent dans .mw-parser-output
    content = soup.select_one(".mw-parser-output")
    if content:
        text = content.get_text(separator="\n")
    else:
        text = soup.get_text(separator="\n")

    # Nettoyage
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^\s+$', '', text, flags=re.MULTILINE)
    text = text.strip()

    return text


def is_index_page(text: str) -> bool:
    """Détecte si une page est un index (pas de vrai contenu)."""
    if len(text) < 1000:
        return True
    # Les pages d'index ont beaucoup de liens mais peu de prose
    lines = text.split('\n')
    short_lines = sum(1 for l in lines if len(l.strip()) < 50)
    if len(lines) > 0 and short_lines / len(lines) > 0.8:
        return True
    return False


def get_subpages_for_work(title: str) -> list[str]:
    """Si un dialogue a des sous-pages (chapitres), les lister."""
    subpages = get_all_subpages(f"{title}/")
    return [p["title"] for p in subpages]


def scrape_work(title: str) -> dict:
    """Scrape une œuvre complète (page unique ou multi-pages)."""
    print(f"  📖 {title}")

    # Essayer d'abord la page directe
    text = extract_text_from_page(title)

    if not text:
        print(f"    ⚠️  Page vide ou inexistante")
        return {"title": title, "text": "", "pages": 0}

    # Si c'est une page d'index, chercher les sous-pages
    if is_index_page(text):
        subpages = get_subpages_for_work(title)
        if subpages:
            print(f"    📑 Page d'index → {len(subpages)} sous-pages")
            full_text = ""
            for sp in subpages:
                sp_text = extract_text_from_page(sp)
                if sp_text and not is_index_page(sp_text):
                    full_text += f"\n\n--- {sp} ---\n\n{sp_text}"
                    print(f"    ✅ {sp.split('/')[-1]}: {len(sp_text)} chars")
            if full_text:
                text = full_text
            else:
                print(f"    ⚠️  Sous-pages vides aussi")
        else:
            print(f"    ⚠️  Page d'index sans sous-pages ({len(text)} chars)")

    chars = len(text)
    words = len(text.split())
    print(f"    → {chars} chars, ~{words} mots")

    return {"title": title, "text": text, "chars": chars, "words": words}


def scrape_author(name: str, config: dict) -> list[dict]:
    """Scrape toutes les œuvres d'un auteur."""
    print(f"\n{'='*60}")
    print(f"🏛️  {name} (mort: {config['death_year']})")
    print(f"{'='*60}")

    # Collecter les titres de pages à scraper
    page_titles = set()

    # 1. Via catégorie
    if config.get("category"):
        cat_pages = get_category_pages(config["category"])
        print(f"  Catégorie '{config['category']}': {len(cat_pages)} pages")
        for p in cat_pages:
            if p.get("ns") == 0:  # Namespace principal uniquement
                page_titles.add(p["title"])

    # 2. Via recherche prefix
    for prefix in config.get("search_prefixes", []):
        prefix_pages = get_all_subpages(prefix)
        for p in prefix_pages:
            page_titles.add(p["title"])

    # Filtrer: garder les traductions FR complètes, éviter les index et doublons
    filtered_titles = set()
    for t in page_titles:
        # Skip auteurs/catégories
        if t.startswith("Auteur:") or t.startswith("Catégorie:"):
            continue
        # Skip les pages index générales (sans traducteur)
        if t.startswith("Œuvres complètes") or t.startswith("Œuvres de Platon"):
            if "/" not in t:
                continue
        # Préférer les traductions complètes "(trad. XXX)"
        # Skip les pages d'index nues (pas de "trad." et pas de sous-page)
        # mais garder les pages qui sont des œuvres entières
        filtered_titles.add(t)

    print(f"\n  📋 {len(filtered_titles)} pages à scraper")

    # Préparer le dossier de sauvegarde
    author_dir = TXT_DIR / name.replace(" ", "_")
    author_dir.mkdir(parents=True, exist_ok=True)

    # Scraper chaque page et sauvegarder immédiatement
    works = []
    for i, title in enumerate(sorted(filtered_titles)):
        work = scrape_work(title)
        if work["text"] and len(work["text"]) > 500:
            # Sauvegarder le fichier immédiatement
            safe_name = re.sub(r'[^\w\sàâéèêëïîôùûüç\-]', '', work["title"], flags=re.IGNORECASE)
            safe_name = re.sub(r'\s+', '_', safe_name.strip())[:100]
            filepath = author_dir / f"{safe_name}.txt"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {work['title']}\n")
                f.write(f"# Auteur: {name}\n")
                f.write(f"# Source: fr.wikisource.org\n")
                f.write(f"# Caractères: {work['chars']}\n\n")
                f.write(work["text"])
            work["local_path"] = str(filepath)
            works.append(work)
            print(f"    💾 Sauvé: {filepath.name}")
        
        if (i + 1) % 10 == 0:
            print(f"  ⏳ Progression: {i+1}/{len(filtered_titles)}")

    # Sauvegarder les métadonnées
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta = [{
        "title": w["title"],
        "author": name,
        "chars": w.get("chars", 0),
        "words": w.get("words", 0),
        "local_path": w.get("local_path", ""),
    } for w in works]
    meta_path = META_DIR / f"{name.replace(' ', '_')}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return works


def main():
    print("=" * 60)
    print("🏛️  Wikisource FR Scraper — Domaine Public")
    print("   POC: Platon + Antisthène")
    print("=" * 60)

    for d in [TXT_DIR, META_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    all_works = []
    for author_name, config in AUTHORS.items():
        works = scrape_author(author_name, config)
        all_works.extend(works)

    # Résumé
    total_chars = sum(w.get("chars", 0) for w in all_works)
    total_words = sum(w.get("words", 0) for w in all_works)
    print(f"\n{'='*60}")
    print(f"📊 RÉSUMÉ")
    print(f"   Œuvres récupérées: {len(all_works)}")
    print(f"   Total: {total_chars:,} caractères (~{total_words:,} mots)")
    print(f"   Fichiers dans: {TXT_DIR}")
    print(f"\n   Détail:")
    for w in all_works:
        print(f"   📖 {w['title'][:60]} — {w.get('chars', 0):,} chars")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
