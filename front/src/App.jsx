import { useState, useEffect, useRef } from "react";
import "./App.css";

const API = "http://localhost:8000";

/* ── Result card with expandable context ── */

function ResultCard({ r }) {
  const [expanded, setExpanded] = useState(false);
  const [context, setContext] = useState(null);
  const [loadingCtx, setLoadingCtx] = useState(false);
  const centerRef = useRef(null);

  const loadContext = async () => {
    if (context) {
      setExpanded(!expanded);
      return;
    }
    setLoadingCtx(true);
    try {
      const res = await fetch(`${API}/api/context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chunk_id: r.id, window: 8 }),
      });
      const data = await res.json();
      setContext(data);
      setExpanded(true);
      // Scroll to center chunk after render
      setTimeout(() => centerRef.current?.scrollIntoView({ block: "center" }), 100);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingCtx(false);
    }
  };

  return (
    <div className="result-card">
      <div className="result-header" onClick={loadContext} style={{ cursor: "pointer" }}>
        <strong>{r.author}</strong>
        <span className="book">{r.book_title}</span>
        <span className="chunk-info">
          chunk {r.chunk_index + 1}/{r.total_chunks}
        </span>
        <span className="score">{(1 - r.distance).toFixed(2)}</span>
        <span className="expand-icon">{expanded ? "▲" : "▼"}</span>
      </div>

      {!expanded && <p className="result-text">{r.text}</p>}

      {expanded && context && (
        <div className="context-scroll">
          {context.chunks.map((c) => (
            <div
              key={c.id}
              ref={c.chunk_index === r.chunk_index ? centerRef : null}
              className={
                "context-chunk" +
                (c.chunk_index === r.chunk_index ? " context-center" : "")
              }
            >
              {c.text.split("\n\n").map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          ))}
        </div>
      )}

      {loadingCtx && <p className="loading-ctx">Chargement du contexte…</p>}
    </div>
  );
}

/* ── Main App ── */

export default function App() {
  const [query, setQuery] = useState("");
  const [authors, setAuthors] = useState([]);
  const [selectedAuthors, setSelectedAuthors] = useState([]);
  const [authorFilter, setAuthorFilter] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/authors`)
      .then((r) => r.json())
      .then(setAuthors)
      .catch(console.error);
  }, []);

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query.trim(),
          authors: selectedAuthors.length ? selectedAuthors : null,
          n_results: 20,
        }),
      });
      setResults(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const toggleAuthor = (name) => {
    setSelectedAuthors((prev) =>
      prev.includes(name) ? prev.filter((a) => a !== name) : [...prev, name]
    );
  };

  const filteredAuthors = authors.filter((a) =>
    a.toLowerCase().includes(authorFilter.toLowerCase())
  );

  return (
    <div className="app">
      <h1>Chat with Philosophes</h1>

      <textarea
        className="query-input"
        placeholder="Écris ta réflexion ici…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) =>
          e.key === "Enter" && !e.shiftKey && (e.preventDefault(), search())
        }
        rows={4}
      />

      <div className="controls">
        <div className="author-filter">
          <div
            className="author-select"
            onClick={() => setShowDropdown(!showDropdown)}
          >
            {selectedAuthors.length
              ? `${selectedAuthors.length} auteur${selectedAuthors.length > 1 ? "s" : ""}`
              : "Tous les auteurs"}
            <span className="caret">▾</span>
          </div>

          {showDropdown && (
            <div className="dropdown">
              <input
                className="dropdown-search"
                placeholder="Filtrer…"
                value={authorFilter}
                onChange={(e) => setAuthorFilter(e.target.value)}
                autoFocus
              />
              {selectedAuthors.length > 0 && (
                <button
                  className="clear-btn"
                  onClick={() => setSelectedAuthors([])}
                >
                  Tout décocher
                </button>
              )}
              <ul>
                {filteredAuthors.map((a) => (
                  <li
                    key={a}
                    className={selectedAuthors.includes(a) ? "selected" : ""}
                    onClick={() => toggleAuthor(a)}
                  >
                    <span className="check">
                      {selectedAuthors.includes(a) ? "✓" : ""}
                    </span>
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <button className="search-btn" onClick={search} disabled={loading}>
          {loading ? "Recherche…" : "Search similar written"}
        </button>
      </div>

      {results.length > 0 && (
        <div className="results">
          {results.map((r, i) => (
            <ResultCard key={r.id || i} r={r} />
          ))}
        </div>
      )}
    </div>
  );
}
