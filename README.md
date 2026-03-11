# Chat with Philosophes

Recherche sémantique dans les textes de 630 philosophes.

## Scraping LibGen

### Prérequis

```bash
pip install requests beautifulsoup4 lxml ebooklib
```

### Lancer le scrape complet (630 philosophes)

```bash
python scraper/scrape_all.py --start 1 --end 630
```

Reprise automatique : si interrompu, relancer la même commande. Les auteurs déjà scrapés sont ignorés.

### Options

```bash
# Plage spécifique
python scraper/scrape_all.py --start 100 --end 200

# Limiter à 5 livres par auteur (défaut: 100)
python scraper/scrape_all.py --max-books 5

# Forcer le re-scrape d'auteurs déjà faits
python scraper/scrape_all.py --start 526 --end 526 --force
```

### Sortie

- `books/epub_pdf/` — fichiers bruts (EPUB, PDF)
- `books/txt/` — texte extrait des EPUBs
- `books/metadata/` — JSON par auteur (résultats + chemins)
- `logs/scrape_all.log` — log complet
