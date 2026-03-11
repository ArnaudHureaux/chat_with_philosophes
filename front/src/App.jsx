import { useState, useEffect } from "react";
import "./App.css";

const API = "http://localhost:8000";

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
            <div key={i} className="result-card">
              <div className="result-header">
                <strong>{r.author}</strong>
                <span className="book">{r.book_title}</span>
                <span className="score">{(1 - r.distance).toFixed(2)}</span>
              </div>
              <p className="result-text">{r.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
