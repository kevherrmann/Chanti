"""Semantische Suche über Chantis Gesprächs-Logs.

Architektur:
- Ein sqlite-vec-DB unter ~/chanti/data/recall.db
- Chunks = einzelne Kevin/Chanti-Austausche aus memory/YYYY-MM-DD.md
- Embeddings lazy beim ersten Zugriff geladen (Modell ~118 MB).
- indexiert wird inkrementell: jede Log-Datei nur wenn mtime sich geändert hat.

Thread-safe durch _lock: Hot-Reload-Task und Such-Tool können parallel laufen.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("chanti")

BASE = Path.home() / "chanti"
LOG_DIR = BASE / "memory"
DB_DIR = BASE / "data"
DB_PATH = DB_DIR / "recall.db"

# Neu indexierte Chunks bleiben min. MIN_AGE_HOURS unsichtbar für recall,
# damit Chanti sich nicht im gleichen Turn selbst zitiert.
MIN_AGE_HOURS = 24

# Chunks über dieser Grenze werden abgeschnitten — sonst frisst ein
# tobender Prompt-Block das ganze Token-Budget von recall-Ergebnissen.
MAX_CHUNK_CHARS = 1500

# Embedding-Dimension vom MiniLM-L12 multilingual Modell. Fest verdrahtet
# damit wir beim DB-Init die richtige Spalten-Größe haben.
EMBED_DIM = 384
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None
_model_lock = threading.Lock()
_db_lock = threading.Lock()
# Hilft zu entscheiden ob Index-Reload überhaupt nötig ist.
_last_index_run_mtime: dict[str, float] = {}


# ---------- Modell & DB (lazy) ----------

def _get_model():
    """Lädt Sentence-Transformer-Modell beim ersten Aufruf.
    ~118 MB Download einmalig, dann ~80 MB RAM. CPU-only reicht."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        logger.info(f"Lade Embedding-Modell: {EMBED_MODEL}")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL, device="cpu")
        logger.info("Embedding-Modell bereit")
        return _model


def _get_conn() -> sqlite3.Connection:
    """Öffnet DB-Connection mit sqlite-vec geladen. Jeder Aufruf ist
    eine neue Connection — sqlite3 Connections sind nicht thread-safe,
    und wir wollen nicht mit check_same_thread=False rumbasteln."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Legt Tabellen an falls nicht vorhanden. vec0 ist die Vektor-Tabelle."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            log_path TEXT NOT NULL,
            block_idx INTEGER NOT NULL,
            user_text TEXT NOT NULL,
            assistant_text TEXT NOT NULL,
            indexed_at REAL NOT NULL,
            UNIQUE(log_path, block_idx)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS source_files (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            chunk_count INTEGER NOT NULL
        )
    """)
    cur.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding FLOAT[{EMBED_DIM}]
        )
    """)
    conn.commit()


def _pack_embedding(emb) -> bytes:
    """sqlite-vec erwartet raw bytes mit float32 little-endian."""
    return struct.pack(f"<{EMBED_DIM}f", *emb)


# ---------- Log-Parsing ----------

_BLOCK_RE = re.compile(
    r'\*\*Kevin:\*\*\s*(.+?)\n\*\*Chanti:\*\*\s*(.+?)(?=\n###|\Z)',
    re.DOTALL,
)


def _parse_log(path: Path, log_date: str) -> list[tuple[int, str, str]]:
    """Gibt Liste von (block_idx, user_text, assistant_text) zurück.

    Parst das Format aus memory.py: '### YYYY-MM-DD\\n**Kevin:** ...\\n**Chanti:** ...'
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"Log nicht lesbar: {path}: {e}")
        return []

    out = []
    for i, m in enumerate(_BLOCK_RE.finditer(text)):
        user = m.group(1).strip()
        assistant = m.group(2).strip()
        if not user or not assistant:
            continue
        # Sehr lange Blöcke stauchen — der Kern reicht für semantische Suche.
        if len(user) > MAX_CHUNK_CHARS:
            user = user[:MAX_CHUNK_CHARS] + "…"
        if len(assistant) > MAX_CHUNK_CHARS:
            assistant = assistant[:MAX_CHUNK_CHARS] + "…"
        out.append((i, user, assistant))
    return out


def _chunk_text(user: str, assistant: str) -> str:
    """Form die wir embedden: kombinierter Dialog-Turn. Dass Kevin und
    Chanti drin stehen hilft dem Modell, die semantische Rolle zu erkennen."""
    return f"Kevin: {user}\nChanti: {assistant}"


# ---------- Indexierung ----------

def reindex_if_changed() -> int:
    """Scannt memory/*.md, indexiert neue/geänderte Dateien.
    Gibt Anzahl neu hinzugefügter Chunks zurück."""
    if not LOG_DIR.exists():
        return 0

    added = 0
    with _db_lock:
        conn = _get_conn()
        try:
            _init_schema(conn)

            # Bekannte Dateien laden
            known = {row[0]: row[1] for row in
                     conn.execute("SELECT path, mtime FROM source_files")}

            log_files = sorted(LOG_DIR.glob("*.md"))
            model_loaded = False

            for log_file in log_files:
                path_str = str(log_file)
                try:
                    current_mtime = log_file.stat().st_mtime
                except OSError:
                    continue

                if known.get(path_str) == current_mtime:
                    continue  # unverändert

                # Datum aus Dateiname (memory.py nennt sie YYYY-MM-DD.md)
                try:
                    log_date = datetime.strptime(log_file.stem, "%Y-%m-%d").date().isoformat()
                except ValueError:
                    logger.debug(f"Übersprungen (kein Datum): {log_file.name}")
                    continue

                blocks = _parse_log(log_file, log_date)
                if not blocks:
                    # Datei leer/unparseable — trotzdem mtime merken
                    conn.execute(
                        "INSERT OR REPLACE INTO source_files(path, mtime, chunk_count) VALUES(?, ?, 0)",
                        (path_str, current_mtime),
                    )
                    continue

                # Alte Chunks dieser Datei rauswerfen (Datei kann bearbeitet worden sein)
                old_ids = [row[0] for row in conn.execute(
                    "SELECT id FROM chunks WHERE log_path=?", (path_str,))]
                if old_ids:
                    placeholders = ",".join("?" * len(old_ids))
                    conn.execute(f"DELETE FROM chunk_vec WHERE chunk_id IN ({placeholders})", old_ids)
                    conn.execute("DELETE FROM chunks WHERE log_path=?", (path_str,))

                # Lazy-Load Modell nur wenn tatsächlich neue Daten
                if not model_loaded:
                    model = _get_model()
                    model_loaded = True

                texts = [_chunk_text(u, a) for _, u, a in blocks]
                # normalize_embeddings=True → cosine distance wird zu inner product
                embeds = model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=False)

                now = datetime.now().timestamp()
                for (idx, user, assistant), emb in zip(blocks, embeds):
                    cur = conn.execute(
                        "INSERT INTO chunks(log_date, log_path, block_idx, user_text, assistant_text, indexed_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (log_date, path_str, idx, user, assistant, now),
                    )
                    chunk_id = cur.lastrowid
                    conn.execute(
                        "INSERT INTO chunk_vec(chunk_id, embedding) VALUES(?, ?)",
                        (chunk_id, _pack_embedding(emb)),
                    )
                    added += 1

                conn.execute(
                    "INSERT OR REPLACE INTO source_files(path, mtime, chunk_count) VALUES(?, ?, ?)",
                    (path_str, current_mtime, len(blocks)),
                )

            conn.commit()
        finally:
            conn.close()

    if added:
        logger.info(f"recall: {added} neue Chunks indexiert")
    return added


# ---------- Suche ----------

def search(query: str, days_back: int = 365, max_results: int = 5,
           min_age_hours: int = MIN_AGE_HOURS) -> list[dict]:
    """Sucht semantisch ähnliche Gesprächs-Chunks.

    Returns: Liste dicts mit keys: date, user, assistant, score (niedriger=ähnlicher).
    Gibt [] zurück wenn nichts gefunden oder Index leer.
    """
    query = (query or "").strip()
    if not query:
        return []

    # Zeit-Grenzen
    now = datetime.now()
    earliest_date = (now - timedelta(days=days_back)).date().isoformat()
    latest_ts = (now - timedelta(hours=min_age_hours)).timestamp()

    with _db_lock:
        conn = _get_conn()
        try:
            _init_schema(conn)

            # Keine Daten = keine Suche
            count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if count == 0:
                return []

            model = _get_model()
            q_emb = model.encode([query], normalize_embeddings=True,
                                 show_progress_bar=False)[0]

            # sqlite-vec: K-NN über die Vektor-Tabelle, dann Join + Filter.
            # Wir holen erstmal mehr Kandidaten als max_results, weil der
            # Zeit-Filter nachträglich greift.
            knn_limit = max(max_results * 4, 20)
            rows = conn.execute(
                f"""
                SELECT c.log_date, c.user_text, c.assistant_text,
                       c.indexed_at, v.distance
                FROM chunk_vec v
                JOIN chunks c ON c.id = v.chunk_id
                WHERE v.embedding MATCH ?
                  AND k = ?
                  AND c.log_date >= ?
                  AND c.indexed_at <= ?
                ORDER BY v.distance
                """,
                (_pack_embedding(q_emb), knn_limit, earliest_date, latest_ts),
            ).fetchall()

            results = []
            for log_date, user, assistant, _indexed_at, distance in rows[:max_results]:
                results.append({
                    "date": log_date,
                    "user": user,
                    "assistant": assistant,
                    "score": round(float(distance), 4),
                })
            return results
        finally:
            conn.close()


def stats() -> dict:
    """Diagnose-Info. Hilfreich für UI oder Debug."""
    with _db_lock:
        conn = _get_conn()
        try:
            _init_schema(conn)
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            files = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(log_date) FROM chunks").fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(log_date) FROM chunks").fetchone()[0]
            return {
                "chunks": chunks,
                "files": files,
                "oldest_date": oldest,
                "newest_date": newest,
            }
        finally:
            conn.close()
