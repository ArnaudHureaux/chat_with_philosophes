#!/usr/bin/env python3
"""
Pipeline de vectorisation — Chunking + Embedding + ChromaDB
============================================================
Découpe les textes en chunks, les vectorise avec
paraphrase-multilingual-MiniLM-L12-v2 et les stocke dans ChromaDB.

Usage:
    python vectorize/ingest.py                    # ingère tout books/txt/
    python vectorize/ingest.py --reset            # recrée la DB from scratch
    python vectorize/ingest.py --author Platon     # un seul auteur

Dépendances:
    pip install sentence-transformers chromadb
"""

import os
import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "books" / "txt"
CHROMA_DIR = Path(__file__).resolve().parent / "vectordb"
BIOS_FILE = BASE_DIR / "scraping" / "philosophers_bios.json"
DATA_FILE = BASE_DIR / "scraping" / "philosophers_data.json"

# Use local model if available, otherwise fetch from HF hub
MODEL_LOCAL = Path(__file__).resolve().parent / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_NAME = str(MODEL_LOCAL) if MODEL_LOCAL.exists() else "paraphrase-multilingual-MiniLM-L12-v2"
COLLECTION_NAME = "philosophes"

# Chunking params — long enough to carry meaning
CHUNK_SIZE = 1500       # chars per chunk
CHUNK_OVERLAP = 300     # overlap between consecutive chunks
MIN_CHUNK_SIZE = 200    # drop chunks smaller than this

BATCH_SIZE = 256        # embeddings per batch (GPU/CPU)

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ingest")


# ═══════════════════════════════════════════════════════════
#  CHUNKING
# ═══════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """Remove headers added by scraper and normalize whitespace."""
    # Remove our metadata headers (lines starting with #)
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("#"):
            start = i + 1
        else:
            break
    text = "\n".join(lines[start:])
    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP,
               min_size: int = MIN_CHUNK_SIZE) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries.

    Strategy:
    1. Split into paragraphs
    2. Accumulate paragraphs until chunk_size is reached
    3. Emit chunk, then backtrack by overlap chars
    """
    text = clean_text(text)
    if not text:
        return []

    paragraphs = re.split(r'\n\n+', text)

    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # If adding this paragraph exceeds chunk_size, emit current
        if current and len(current) + len(para) + 2 > chunk_size:
            if len(current) >= min_size:
                chunks.append(current.strip())
            # Overlap: keep the tail of current
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:]
            else:
                current = ""

        if current:
            current += "\n\n" + para
        else:
            current = para

        # Handle very long paragraphs (longer than chunk_size)
        while len(current) > chunk_size * 1.5:
            # Hard split at sentence boundary
            split_pos = chunk_size
            # Try to find a sentence end near chunk_size
            for sep in [". ", ".\n", "! ", "? ", ";\n", "; "]:
                pos = current.rfind(sep, chunk_size // 2, chunk_size + 200)
                if pos != -1:
                    split_pos = pos + len(sep)
                    break

            chunk = current[:split_pos].strip()
            if len(chunk) >= min_size:
                chunks.append(chunk)

            if overlap > 0 and split_pos > overlap:
                current = current[split_pos - overlap:]
            else:
                current = current[split_pos:]

    # Last chunk
    if current.strip() and len(current.strip()) >= min_size:
        chunks.append(current.strip())

    return chunks


# ═══════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════

def load_bios() -> dict:
    """Load philosopher bios if available."""
    if BIOS_FILE.exists():
        with open(BIOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_philosopher_data() -> dict:
    """Load philosopher data (name → entry mapping)."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Build reverse lookup: folder_name → entry
        lookup = {}
        for k, v in data.items():
            name = v["name"]
            folder = re.sub(r'\s*\([^)]*\)\s*$', '', name)
            folder = re.sub(r'[^\w\sàâäéèêëïîôùûüçÀÂÄÉÈÊËÏÎÔÙÛÜÇæœ\-]', '', folder)
            folder = re.sub(r'\s+', '_', folder.strip())
            if folder not in lookup:
                lookup[folder] = v
                lookup[folder]["id"] = k
        return lookup
    return {}


def get_all_text_files(author_filter: str | None = None) -> list[dict]:
    """Discover all text files with metadata."""
    if not TXT_DIR.exists():
        log.error("books/txt/ n'existe pas")
        return []

    files = []
    for author_dir in sorted(TXT_DIR.iterdir()):
        if not author_dir.is_dir():
            continue
        author_name = author_dir.name

        if author_filter and author_name != author_filter:
            continue

        for txt_file in sorted(author_dir.glob("*.txt")):
            if txt_file.stat().st_size < 500:
                continue
            files.append({
                "path": txt_file,
                "author": author_name.replace("_", " "),
                "author_folder": author_name,
                "book_title": txt_file.stem.replace("_", " "),
            })

    return files


# ═══════════════════════════════════════════════════════════
#  INGEST
# ═══════════════════════════════════════════════════════════

def ingest(author_filter: str | None = None, reset: bool = False):
    """Main ingestion pipeline."""

    # 1. Load model
    log.info("🤖 Chargement du modèle %s...", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)
    log.info("   ✅ Modèle chargé (dim=%d)", model.get_sentence_embedding_dimension())

    # 2. Init ChromaDB
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if reset:
        log.info("🗑️  Reset de la collection '%s'", COLLECTION_NAME)
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    existing_count = collection.count()
    log.info("📦 Collection '%s': %d chunks existants", COLLECTION_NAME, existing_count)

    # 3. Load metadata
    philosopher_data = load_philosopher_data()
    bios = load_bios()

    # 4. Discover files
    files = get_all_text_files(author_filter)
    log.info("📁 %d fichiers texte trouvés", len(files))

    if not files:
        log.warning("Aucun fichier trouvé — rien à ingérer")
        return

    # 5. Check which authors are already ingested (skip if not reset)
    already_ingested = set()
    if not reset and existing_count > 0:
        # Sample existing metadata to find ingested authors
        existing = collection.get(limit=1, include=["metadatas"])
        # We'll check per-file instead — use a set of doc IDs
        pass

    # 6. Process files
    total_chunks = 0
    total_chars = 0
    t_start = time.time()

    for i, file_info in enumerate(files):
        path = file_info["path"]
        author = file_info["author"]
        book = file_info["book_title"]

        # Check if this file's chunks already exist
        doc_prefix = f"{file_info['author_folder']}/{path.stem}"
        if not reset:
            existing_docs = collection.get(
                where={"source_file": path.name},
                limit=1,
            )
            if existing_docs and existing_docs["ids"]:
                log.info("  ⏭️  %s/%s — déjà ingéré", author, path.name)
                continue

        # Read and chunk
        text = path.read_text(encoding="utf-8")
        chunks = chunk_text(text)

        if not chunks:
            log.info("  ⚠️  %s/%s — pas de chunks", author, path.name)
            continue

        # Build metadata for each chunk
        # Find philosopher info
        phil_info = philosopher_data.get(file_info["author_folder"], {})
        bio_info = None
        if phil_info.get("id"):
            bio_info = bios.get(phil_info["id"])

        ids = []
        documents = []
        metadatas = []

        for j, chunk in enumerate(chunks):
            chunk_id = f"{doc_prefix}__c{j:04d}"
            ids.append(chunk_id)
            documents.append(chunk)

            meta = {
                "author": author,
                "book_title": book,
                "source_file": path.name,
                "chunk_index": j,
                "total_chunks": len(chunks),
                "char_count": len(chunk),
            }

            metadatas.append(meta)

        # Embed in batches
        log.info(
            "  📖 [%d/%d] %s — %s: %d chunks",
            i + 1, len(files), author, path.stem[:40], len(chunks)
        )

        embeddings = model.encode(
            documents,
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

        # Upsert into ChromaDB (batch by 5000 — Chroma limit)
        for batch_start in range(0, len(ids), 5000):
            batch_end = min(batch_start + 5000, len(ids))
            collection.upsert(
                ids=ids[batch_start:batch_end],
                embeddings=embeddings[batch_start:batch_end],
                documents=documents[batch_start:batch_end],
                metadatas=metadatas[batch_start:batch_end],
            )

        total_chunks += len(chunks)
        total_chars += sum(len(c) for c in chunks)

    elapsed = time.time() - t_start
    final_count = collection.count()

    log.info("")
    log.info("=" * 60)
    log.info("📊 INGESTION TERMINÉE")
    log.info("   Nouveaux chunks: %d", total_chunks)
    log.info("   Chars ingérés: %s", f"{total_chars:,}")
    log.info("   Total dans la DB: %d chunks", final_count)
    log.info("   Temps: %.1fs", elapsed)
    log.info("=" * 60)


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Ingestion texte → ChromaDB")
    parser.add_argument("--reset", action="store_true",
                        help="Supprimer et recréer la collection")
    parser.add_argument("--author", type=str, default=None,
                        help="Ingérer un seul auteur (nom du dossier)")
    args = parser.parse_args()

    ingest(author_filter=args.author, reset=args.reset)


if __name__ == "__main__":
    main()
