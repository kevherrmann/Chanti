"""Leads-Datenbank: SQLite-Schema + einfacher Wrapper.

Datei liegt unter ~/chanti/data/leads.db.
Alle Funktionen geben Dicts zurück (row_factory = sqlite3.Row).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("chanti")

DB_PATH = Path(os.environ.get(
    "CHANTI_LEADS_DB",
    str(Path.home() / "chanti" / "data" / "leads.db"),
))

_lock = threading.Lock()


# ─────────────────────────── Schema ───────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    city            TEXT,
    address         TEXT,
    phone           TEXT,
    email           TEXT,
    website_url     TEXT,
    lat             REAL,
    lon             REAL,
    source          TEXT,
    status          TEXT NOT NULL DEFAULT 'new',
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);

CREATE TABLE IF NOT EXISTS website_analysis (
    company_id        INTEGER PRIMARY KEY,
    analyzed_at       TEXT NOT NULL,
    reachable         INTEGER NOT NULL,
    http_status       INTEGER,
    title             TEXT,
    word_count        INTEGER,
    has_viewport      INTEGER,
    has_ssl           INTEGER,
    has_contact       INTEGER,
    has_nav           INTEGER,
    has_images        INTEGER,
    platform_detected TEXT,
    under_construction INTEGER,
    problems_json     TEXT,
    raw_checks_json   TEXT,
    screenshot_path   TEXT,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scores (
    company_id       INTEGER PRIMARY KEY,
    computed_at      TEXT NOT NULL,
    need_score       REAL NOT NULL,
    payability_score REAL NOT NULL,
    total_score      REAL NOT NULL,
    reason           TEXT,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scores_total ON scores(total_score);

CREATE TABLE IF NOT EXISTS reputation (
    company_id           INTEGER PRIMARY KEY,
    fetched_at           TEXT NOT NULL,
    rating               REAL,
    review_count         INTEGER,
    has_impressum_email  INTEGER,
    impressum_email      TEXT,
    social_facebook      TEXT,
    social_instagram     TEXT,
    social_linkedin      TEXT,
    social_other_json    TEXT,
    domain_age_years     REAL,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    subject      TEXT,
    body_text    TEXT,
    stil         TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',
    sent_at      TEXT,
    error        TEXT,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_emails_company ON emails(company_id);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    query_branche  TEXT,
    query_ort      TEXT,
    query_count    INTEGER,
    query_radius_km INTEGER,
    found          INTEGER DEFAULT 0,
    qualified      INTEGER DEFAULT 0,
    error          TEXT
);
"""


# ─────────────────────────── Connection ───────────────────────────

def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_dir()
    with _connect() as con:
        con.executescript(SCHEMA)
        con.commit()
    logger.info(f"Leads-DB initialisiert: {DB_PATH}")


@contextmanager
def _connect():
    _ensure_dir()
    con = sqlite3.connect(DB_PATH, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row else None


# ─────────────────────────── Companies ───────────────────────────

VALID_STATUS = {"new", "analyzed", "qualified", "researched", "drafted", "sent", "failed", "rejected"}


def insert_company(data: dict) -> int:
    """data: name (required), city, address, phone, email, website_url, lat, lon, source"""
    if not data.get("name"):
        raise ValueError("name ist pflicht")
    now = _now()
    with _lock, _connect() as con:
        cur = con.execute("""
            INSERT INTO companies (name, city, address, phone, email, website_url,
                                   lat, lon, source, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
        """, (
            data["name"], data.get("city"), data.get("address"), data.get("phone"),
            data.get("email"), data.get("website_url"), data.get("lat"), data.get("lon"),
            data.get("source"), now, now,
        ))
        con.commit()
        return cur.lastrowid


def find_duplicate(name: str, city: Optional[str]) -> Optional[int]:
    """Einfache Dupe-Erkennung: gleicher Name + gleicher Ort."""
    with _connect() as con:
        if city:
            row = con.execute(
                "SELECT id FROM companies WHERE LOWER(name)=LOWER(?) AND LOWER(COALESCE(city,''))=LOWER(?) LIMIT 1",
                (name, city)
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id FROM companies WHERE LOWER(name)=LOWER(?) LIMIT 1",
                (name,)
            ).fetchone()
        return row["id"] if row else None


def get_company(company_id: int) -> Optional[dict]:
    with _connect() as con:
        row = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        return _row_to_dict(row)


def get_company_full(company_id: int) -> Optional[dict]:
    """Firma + alle verknüpften Tabellen."""
    with _connect() as con:
        c = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        if not c:
            return None
        result = dict(c)
        for table in ("website_analysis", "scores", "reputation"):
            r = con.execute(f"SELECT * FROM {table} WHERE company_id=?", (company_id,)).fetchone()
            result[table] = _row_to_dict(r)

        email_rows = con.execute(
            "SELECT * FROM emails WHERE company_id=? ORDER BY created_at DESC",
            (company_id,)
        ).fetchall()
        result["emails"] = [dict(r) for r in email_rows]
        return result


def list_companies(status: Optional[str] = None, min_score: Optional[float] = None,
                   search: Optional[str] = None, limit: int = 500) -> list[dict]:
    sql = """
        SELECT c.*,
               s.need_score, s.payability_score, s.total_score,
               w.platform_detected, w.screenshot_path,
               r.rating, r.review_count
        FROM companies c
        LEFT JOIN scores s ON s.company_id = c.id
        LEFT JOIN website_analysis w ON w.company_id = c.id
        LEFT JOIN reputation r ON r.company_id = c.id
        WHERE 1=1
    """
    params: list[Any] = []
    if status and status != "all":
        sql += " AND c.status = ?"
        params.append(status)
    if min_score is not None:
        sql += " AND COALESCE(s.total_score, 0) >= ?"
        params.append(min_score)
    if search:
        sql += " AND (LOWER(c.name) LIKE ? OR LOWER(c.city) LIKE ?)"
        q = f"%{search.lower()}%"
        params.extend([q, q])
    sql += " ORDER BY COALESCE(s.total_score, 0) DESC, c.created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def update_company_status(company_id: int, status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"Ungültiger Status: {status}")
    with _lock, _connect() as con:
        con.execute("UPDATE companies SET status=?, updated_at=? WHERE id=?",
                    (status, _now(), company_id))
        con.commit()


def update_company_fields(company_id: int, fields: dict) -> None:
    """Erlaubte Felder: phone, email, website_url, address, notes."""
    allowed = {"phone", "email", "website_url", "address", "notes"}
    update = {k: v for k, v in fields.items() if k in allowed}
    if not update:
        return
    set_clause = ", ".join(f"{k}=?" for k in update) + ", updated_at=?"
    params = list(update.values()) + [_now(), company_id]
    with _lock, _connect() as con:
        con.execute(f"UPDATE companies SET {set_clause} WHERE id=?", params)
        con.commit()


def delete_company(company_id: int) -> bool:
    """Hard-Delete – Testsystem-Modus. Gibt True zurück wenn etwas gelöscht."""
    with _lock, _connect() as con:
        cur = con.execute("DELETE FROM companies WHERE id=?", (company_id,))
        con.commit()
        return cur.rowcount > 0


def count_by_status() -> dict[str, int]:
    with _connect() as con:
        rows = con.execute("SELECT status, COUNT(*) as n FROM companies GROUP BY status").fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        counts["all"] = sum(counts.values())
        return counts


# ─────────────────────── Website Analysis ───────────────────────

def upsert_website_analysis(company_id: int, data: dict) -> None:
    payload = {
        "company_id": company_id,
        "analyzed_at": _now(),
        "reachable": 1 if data.get("reachable") else 0,
        "http_status": data.get("http_status"),
        "title": data.get("title"),
        "word_count": data.get("word_count"),
        "has_viewport": 1 if data.get("has_viewport") else 0,
        "has_ssl": 1 if data.get("has_ssl") else 0,
        "has_contact": 1 if data.get("has_contact") else 0,
        "has_nav": 1 if data.get("has_nav") else 0,
        "has_images": 1 if data.get("has_images") else 0,
        "platform_detected": data.get("platform_detected"),
        "under_construction": 1 if data.get("under_construction") else 0,
        "problems_json": json.dumps(data.get("problems", []), ensure_ascii=False),
        "raw_checks_json": json.dumps(data.get("raw_checks", {}), ensure_ascii=False),
        "screenshot_path": data.get("screenshot_path"),
    }
    with _lock, _connect() as con:
        con.execute("""
            INSERT INTO website_analysis
                (company_id, analyzed_at, reachable, http_status, title, word_count,
                 has_viewport, has_ssl, has_contact, has_nav, has_images,
                 platform_detected, under_construction, problems_json, raw_checks_json, screenshot_path)
            VALUES (:company_id, :analyzed_at, :reachable, :http_status, :title, :word_count,
                    :has_viewport, :has_ssl, :has_contact, :has_nav, :has_images,
                    :platform_detected, :under_construction, :problems_json, :raw_checks_json, :screenshot_path)
            ON CONFLICT(company_id) DO UPDATE SET
                analyzed_at=excluded.analyzed_at,
                reachable=excluded.reachable,
                http_status=excluded.http_status,
                title=excluded.title,
                word_count=excluded.word_count,
                has_viewport=excluded.has_viewport,
                has_ssl=excluded.has_ssl,
                has_contact=excluded.has_contact,
                has_nav=excluded.has_nav,
                has_images=excluded.has_images,
                platform_detected=excluded.platform_detected,
                under_construction=excluded.under_construction,
                problems_json=excluded.problems_json,
                raw_checks_json=excluded.raw_checks_json,
                screenshot_path=excluded.screenshot_path
        """, payload)
        con.commit()


# ─────────────────────────── Scores ───────────────────────────

def upsert_score(company_id: int, need: float, payability: float, reason: str) -> None:
    total = round(need * payability, 1)
    with _lock, _connect() as con:
        con.execute("""
            INSERT INTO scores (company_id, computed_at, need_score, payability_score, total_score, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                computed_at=excluded.computed_at,
                need_score=excluded.need_score,
                payability_score=excluded.payability_score,
                total_score=excluded.total_score,
                reason=excluded.reason
        """, (company_id, _now(), round(need, 1), round(payability, 1), total, reason))
        con.commit()


# ─────────────────────────── Reputation ───────────────────────────

def upsert_reputation(company_id: int, data: dict) -> None:
    payload = {
        "company_id": company_id,
        "fetched_at": _now(),
        "rating": data.get("rating"),
        "review_count": data.get("review_count"),
        "has_impressum_email": 1 if data.get("has_impressum_email") else 0,
        "impressum_email": data.get("impressum_email"),
        "social_facebook": data.get("social_facebook"),
        "social_instagram": data.get("social_instagram"),
        "social_linkedin": data.get("social_linkedin"),
        "social_other_json": json.dumps(data.get("social_other", []), ensure_ascii=False),
        "domain_age_years": data.get("domain_age_years"),
    }
    with _lock, _connect() as con:
        con.execute("""
            INSERT INTO reputation (company_id, fetched_at, rating, review_count,
                                    has_impressum_email, impressum_email,
                                    social_facebook, social_instagram, social_linkedin,
                                    social_other_json, domain_age_years)
            VALUES (:company_id, :fetched_at, :rating, :review_count,
                    :has_impressum_email, :impressum_email,
                    :social_facebook, :social_instagram, :social_linkedin,
                    :social_other_json, :domain_age_years)
            ON CONFLICT(company_id) DO UPDATE SET
                fetched_at=excluded.fetched_at,
                rating=excluded.rating,
                review_count=excluded.review_count,
                has_impressum_email=excluded.has_impressum_email,
                impressum_email=excluded.impressum_email,
                social_facebook=excluded.social_facebook,
                social_instagram=excluded.social_instagram,
                social_linkedin=excluded.social_linkedin,
                social_other_json=excluded.social_other_json,
                domain_age_years=excluded.domain_age_years
        """, payload)
        con.commit()


# ─────────────────────────── Emails ───────────────────────────

def create_email(company_id: int, subject: str, body_text: str, stil: str) -> int:
    """Erstellt einen neuen Draft. Gibt Email-ID zurück."""
    now = _now()
    with _lock, _connect() as con:
        cur = con.execute("""
            INSERT INTO emails (company_id, created_at, updated_at, subject, body_text, stil, status)
            VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """, (company_id, now, now, subject, body_text, stil))
        con.commit()
        return cur.lastrowid


def update_email(email_id: int, subject: Optional[str] = None,
                 body_text: Optional[str] = None) -> bool:
    updates = []
    params: list[Any] = []
    if subject is not None:
        updates.append("subject=?")
        params.append(subject)
    if body_text is not None:
        updates.append("body_text=?")
        params.append(body_text)
    if not updates:
        return False
    updates.append("updated_at=?")
    params.append(_now())
    params.append(email_id)
    with _lock, _connect() as con:
        cur = con.execute(f"UPDATE emails SET {', '.join(updates)} WHERE id=?", params)
        con.commit()
        return cur.rowcount > 0


def mark_email_sent(email_id: int) -> None:
    now = _now()
    with _lock, _connect() as con:
        con.execute("UPDATE emails SET status='sent', sent_at=?, updated_at=?, error=NULL WHERE id=?",
                    (now, now, email_id))
        con.commit()


def mark_email_failed(email_id: int, error: str) -> None:
    with _lock, _connect() as con:
        con.execute("UPDATE emails SET status='failed', error=?, updated_at=? WHERE id=?",
                    (error[:500], _now(), email_id))
        con.commit()


def get_email(email_id: int) -> Optional[dict]:
    with _connect() as con:
        row = con.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        return _row_to_dict(row)


def get_latest_email_for_company(company_id: int) -> Optional[dict]:
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM emails WHERE company_id=? ORDER BY created_at DESC LIMIT 1",
            (company_id,)
        ).fetchone()
        return _row_to_dict(row)


# ─────────────────────────── Runs ───────────────────────────

def create_run(branche: str, ort: str, count: int, radius_km: int) -> int:
    with _lock, _connect() as con:
        cur = con.execute("""
            INSERT INTO runs (started_at, query_branche, query_ort, query_count, query_radius_km)
            VALUES (?, ?, ?, ?, ?)
        """, (_now(), branche, ort, count, radius_km))
        con.commit()
        return cur.lastrowid


def finish_run(run_id: int, found: int, qualified: int, error: Optional[str] = None) -> None:
    with _lock, _connect() as con:
        con.execute("""
            UPDATE runs SET finished_at=?, found=?, qualified=?, error=? WHERE id=?
        """, (_now(), found, qualified, error, run_id))
        con.commit()


def list_runs(limit: int = 50) -> list[dict]:
    with _connect() as con:
        rows = con.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
