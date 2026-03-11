#!/usr/bin/env python3
"""
API FastAPI — recherche sémantique dans ChromaDB
=================================================
Usage:
    uvicorn back.api:app --reload --port 8000
"""

import json
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
    text: str
    author: str
    book_title: str
    source_file: str
    distance: float


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
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append(
            Chunk(
                text=doc,
                author=meta.get("author", ""),
                book_title=meta.get("book_title", ""),
                source_file=meta.get("source_file", ""),
                distance=round(dist, 4),
            )
        )
    return chunks
