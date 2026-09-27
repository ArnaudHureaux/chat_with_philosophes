#!/usr/bin/env python3
"""
API FastAPI — recherche sémantique dans ChromaDB
=================================================
Usage:
    uvicorn back.api:app --reload --port 8000
"""

import json
import re
from pathlib import Path

import chromadb
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ── Config ──────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
CHROMA_DIR = BASE_DIR / "embedding" / "vectordb"
MODEL_LOCAL = BASE_DIR / "embedding" / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_NAME = str(MODEL_LOCAL) if MODEL_LOCAL.exists() else "paraphrase-multilingual-MiniLM-L12-v2"
DATA_FILE = BASE_DIR / "scraping" / "philosophers_data.json"
COLLECTION_NAME = "philosophes"

# ── Init ────────────────────────────────────────────────

app = FastAPI(title="Chat with Philosophes")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

print("🤖 Chargement du modèle…")
model = SentenceTransformer(MODEL_NAME)
print(f"   ✅ Modèle chargé (dim={model.get_sentence_embedding_dimension()})")

client = chromadb.PersistentClient(path=str(CHROMA_DIR))
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)
print(f"📦 Collection: {collection.count()} chunks")


def _load_authors() -> list[str]:
    """List authors from philosophers_data.json."""
    if not DATA_FILE.exists():
        return []
    data = json.loads(DATA_FILE.read_text("utf-8"))
    return sorted({v["name"] for v in data.values()})


AUTHORS = _load_authors()

# ── Schemas ─────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    authors: list[str] | None = None
    n_results: int = 20


class Chunk(BaseModel):
    id: str
    text: str
    author: str
    book_title: str
    source_file: str
    chunk_index: int
    total_chunks: int
    distance: float


class ContextRequest(BaseModel):
    chunk_id: str
    window: int = 8  # number of neighboring chunks each side


# ── Text formatting ─────────────────────────────────────


def format_text(raw: str) -> str:
    """Clean up broken line breaks from epub extraction.

    Rules:
    - Keep real paragraph breaks (double newline or line ending with sentence-end punctuation).
    - Merge lines that were broken mid-sentence (single newline not preceded by .!?:;»").
    - Normalize multiple spaces / tabs.
    """
    # Normalize whitespace chars
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", " ")

    # Collapse 3+ newlines into 2 (paragraph break)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # Empty line = paragraph break
            out.append("")
            continue

        out.append(stripped)

        # Decide if next line should be joined or kept separate
        if i + 1 < len(lines):
            next_stripped = lines[i + 1].strip()
            if not next_stripped:
                # next is blank → paragraph break, handled above
                continue
            # If current line ends with sentence-ending punct → paragraph break
            if stripped and stripped[-1] in '.!?:;»")\u00bb':
                out.append("")
            # Otherwise the newline was mid-sentence → will be joined by space

    # Rejoin: consecutive non-empty lines become one paragraph
    paragraphs = []
    current = []
    for line in out:
        if line == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    # Collapse multiple spaces
    result = "\n\n".join(paragraphs)
    result = re.sub(r"  +", " ", result)
    return result.strip()


# ── Routes ──────────────────────────────────────────────


@app.get("/api/authors")
def get_authors():
    return AUTHORS


@app.post("/api/search")
def search(req: SearchRequest):
    embedding = model.encode(req.query).tolist()

    where = None
    if req.authors:
        if len(req.authors) == 1:
            where = {"author": req.authors[0]}
        else:
            where = {"author": {"$in": req.authors}}

    results = collection.query(
        query_embeddings=[embedding],
        n_results=req.n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist, cid in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
        results["ids"][0],
    ):
        chunks.append(
            Chunk(
                id=cid,
                text=format_text(doc),
                author=meta.get("author", ""),
                book_title=meta.get("book_title", ""),
                source_file=meta.get("source_file", ""),
                chunk_index=meta.get("chunk_index", 0),
                total_chunks=meta.get("total_chunks", 0),
                distance=round(dist, 4),
            )
        )
    return chunks


@app.post("/api/context")
def get_context(req: ContextRequest):
    """Return surrounding chunks for scroll context, with overlap removed."""
    # Parse the chunk id: {prefix}__c{NNNN}
    parts = req.chunk_id.rsplit("__c", 1)
    if len(parts) != 2:
        return {"error": "invalid chunk_id"}

    prefix = parts[0]
    try:
        center = int(parts[1])
    except ValueError:
        return {"error": "invalid chunk_id"}

    # Build IDs for the window
    start = max(0, center - req.window)
    end = center + req.window  # will naturally cap at total_chunks

    ids_to_fetch = [f"{prefix}__c{i:04d}" for i in range(start, end + 1)]

    results = collection.get(
        ids=ids_to_fetch,
        include=["documents", "metadatas"],
    )

    chunks = []
    for cid, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
        chunks.append({
            "id": cid,
            "chunk_index": meta.get("chunk_index", 0),
            "raw": doc,
        })

    # Sort by chunk_index
    chunks.sort(key=lambda c: c["chunk_index"])

    # Remove overlap between consecutive chunks.
    # Chunks were built with ~300 char overlap: the start of chunk N+1
    # repeats the end of chunk N. We find and strip that duplication.
    for i in range(1, len(chunks)):
        prev_raw = chunks[i - 1]["raw"]
        cur_raw = chunks[i]["raw"]
        overlap = _find_overlap(prev_raw, cur_raw)
        if overlap > 0:
            chunks[i]["raw"] = cur_raw[overlap:]

    # Format after dedup
    for c in chunks:
        c["text"] = format_text(c["raw"])
        del c["raw"]

    return {
        "center": center,
        "chunks": chunks,
    }


def _find_overlap(a: str, b: str, max_len: int = 600) -> int:
    """Find how many chars at the start of `b` duplicate the end of `a`.

    Scans from longest possible overlap down to 20 chars; returns the
    first (longest) match found.
    """
    limit = min(max_len, len(a), len(b))
    for length in range(limit, 19, -1):
        if a.endswith(b[:length]):
            return length
    return 0
