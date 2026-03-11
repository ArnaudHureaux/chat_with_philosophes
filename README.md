# Chat with Philosophes

Recherche sémantique dans les textes de 630 philosophes.

```bash
pip install requests beautifulsoup4 lxml ebooklib langdetect sentence-transformers chromadb fastapi uvicorn
python scraping/scrape_all.py                          # scrape LibGen → books/
python embedding/ingest.py                             # chunk + vectorise → ChromaDB
uvicorn back.api:app --reload --port 8000              # API
cd front && npm install && npm run dev                  # React
```
