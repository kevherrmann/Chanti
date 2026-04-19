"""Zentrale Lead-Pipeline: Suche → Analyse → Dossier → Mail → Versand.

Jeder Schritt wird pro Firma einzeln angestoßen (vom UI).
Die Suche legt nur Stammdaten an (Status 'new'). Analyse/Dossier/Mail auf Klick.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

import leads_db
import leads_scoring
from leads_analyzer import website as website_analyzer
from leads_analyzer import reputation as reputation_analyzer
from leads_analyzer import domain_age as domain_age_analyzer
from leads_analyzer import email_draft
from leads_providers import brave, ddg, osm

logger = logging.getLogger("chanti")

# Parallelität bei Batch-Analyse (nicht beim Suchen selbst)
MAX_PARALLEL_ANALYSIS = 3


# ─────────────────────────── Suche ───────────────────────────

def run_search(branche: str, ort: str, count: int, radius_km: int = 15) -> dict:
    """Startet eine Suche. Legt gefundene Firmen als 'new' in der DB an.

    Keine Website-Analyse hier – nur Stammdaten. Analyse folgt per Klick.
    """
    run_id = leads_db.create_run(branche, ort, count, radius_km)
    try:
        companies, used_radius = osm.search_with_expanding_radius(
            ort=ort, branche=branche, target_count=count, start_radius_km=radius_km,
        )

        # DDG-Fallback wenn OSM zu wenig
        if len(companies) < count:
            needed = count - len(companies)
            seen_names = {c["name"].lower() for c in companies}
            for c in ddg.search_companies(branche, ort, needed):
                if c["name"].lower() not in seen_names:
                    companies.append(c)
                    seen_names.add(c["name"].lower())

        # In DB schreiben, Duplikate überspringen
        created = 0
        skipped = 0
        for c in companies[:count * 2]:  # etwas Puffer
            if created >= count:
                break
            dupe_id = leads_db.find_duplicate(c["name"], c.get("city") or ort)
            if dupe_id:
                skipped += 1
                continue
            try:
                leads_db.insert_company({
                    "name":        c["name"],
                    "city":        c.get("city") or ort,
                    "address":     c.get("address") or "",
                    "phone":       c.get("phone") or "",
                    "email":       c.get("email") or "",
                    "website_url": c.get("website") or "",
                    "lat":         c.get("lat"),
                    "lon":         c.get("lon"),
                    "source":      "osm" if c.get("lat") else "ddg",
                })
                created += 1
            except Exception as e:
                logger.warning(f"Insert fehlgeschlagen für {c['name']!r}: {e}")

        leads_db.finish_run(run_id, found=len(companies), qualified=created)
        return {
            "run_id": run_id,
            "found": len(companies),
            "created": created,
            "skipped_duplicates": skipped,
            "used_radius_km": used_radius,
        }
    except Exception as e:
        logger.error(f"Suche fehlgeschlagen: {e}", exc_info=True)
        leads_db.finish_run(run_id, found=0, qualified=0, error=str(e)[:500])
        raise


# ─────────────────────── Einzelschritt-Aktionen ─────────────────

def analyze_company(company_id: int) -> dict:
    """Website-Analyse + Scoring für eine Firma."""
    company = leads_db.get_company(company_id)
    if not company:
        raise ValueError(f"Firma {company_id} nicht gefunden")

    url = company.get("website_url") or ""
    wa = website_analyzer.analyze(url, company_id=company_id, take_screenshot=True)
    leads_db.upsert_website_analysis(company_id, wa)

    # Scoring sofort berechnen (mit dem was da ist, Reputation optional später)
    full = leads_db.get_company_full(company_id)
    need, pay, reason = leads_scoring.compute(
        full.get("website_analysis"), full.get("reputation"), full,
    )
    leads_db.upsert_score(company_id, need, pay, reason)
    leads_db.update_company_status(company_id, "analyzed")
    return {"website_analysis": wa, "need": need, "payability": pay, "total": round(need * pay, 1)}


def research_company(company_id: int) -> dict:
    """Reputation + Domain-Alter + Social sammeln + Scoring neu."""
    company = leads_db.get_company(company_id)
    if not company:
        raise ValueError(f"Firma {company_id} nicht gefunden")

    rep = reputation_analyzer.collect(
        firma=company["name"],
        ort=company.get("city") or "",
        website_url=company.get("website_url"),
    )

    # Domain-Alter
    age = domain_age_analyzer.estimate_domain_age_years(company.get("website_url"))
    if age is not None:
        rep["domain_age_years"] = age

    # Falls Brave einen besseren Kontakt gefunden hat, ergänzen
    # (wir überschreiben nichts was schon da ist)
    field_updates = {}
    if not company.get("email") and rep.get("impressum_email"):
        field_updates["email"] = rep["impressum_email"]
    if field_updates:
        leads_db.update_company_fields(company_id, field_updates)

    leads_db.upsert_reputation(company_id, rep)

    # Scoring neu mit Reputation
    full = leads_db.get_company_full(company_id)
    need, pay, reason = leads_scoring.compute(
        full.get("website_analysis"), full.get("reputation"), full,
    )
    leads_db.upsert_score(company_id, need, pay, reason)

    leads_db.update_company_status(company_id, "researched")
    return {
        "reputation": rep,
        "need": need,
        "payability": pay,
        "total": round(need * pay, 1),
    }


def draft_email(company_id: int, stil: str = "formell", sender_name: str = "Kevin") -> dict:
    """Erstellt einen Mail-Draft. Ersetzt keinen bestehenden – legt neuen an."""
    full = leads_db.get_company_full(company_id)
    if not full:
        raise ValueError(f"Firma {company_id} nicht gefunden")

    subject, body = email_draft.generate(
        company=full,
        website_analysis=full.get("website_analysis"),
        reputation=full.get("reputation"),
        stil=stil,
        sender_name=sender_name,
    )
    email_id = leads_db.create_email(company_id, subject, body, stil)
    leads_db.update_company_status(company_id, "drafted")
    return {"email_id": email_id, "subject": subject, "body": body}


def send_email(company_id: int, email_id: int) -> dict:
    """Versendet eine Mail über den n8n-Webhook. Gibt {ok, error?} zurück."""
    webhook = os.environ.get("N8N_MAIL_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("N8N_MAIL_WEBHOOK fehlt in der .env")

    company = leads_db.get_company(company_id)
    email = leads_db.get_email(email_id)
    if not company or not email:
        raise ValueError("Firma oder Mail nicht gefunden")
    if email["company_id"] != company_id:
        raise ValueError("Mail gehört nicht zur Firma")
    if email["status"] == "sent":
        # Konsistenz: Company-Status darf nicht hinter Mail-Status hinken
        if company.get("status") != "sent":
            leads_db.update_company_status(company_id, "sent")
        return {"ok": True, "already_sent": True}

    recipient = company.get("email") or ""
    if not recipient:
        raise ValueError("Keine Empfängeradresse (company.email fehlt)")

    payload = {
        "to":       recipient,
        "subject":  email["subject"],
        "body":     email["body_text"],
        "company_name": company["name"],
    }

    try:
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code >= 400:
            msg = f"n8n HTTP {r.status_code}: {r.text[:200]}"
            leads_db.mark_email_failed(email_id, msg)
            return {"ok": False, "error": msg}
        leads_db.mark_email_sent(email_id)
        leads_db.update_company_status(company_id, "sent")
        return {"ok": True}
    except requests.RequestException as e:
        msg = f"n8n nicht erreichbar: {e}"
        leads_db.mark_email_failed(email_id, msg)
        return {"ok": False, "error": msg}


def delete_company(company_id: int) -> bool:
    return leads_db.delete_company(company_id)


# ─────────────────────── Batch-Analyse (optional) ──────────────

def batch_analyze(company_ids: list[int]) -> list[dict]:
    """Analysiert mehrere Firmen parallel (max 3 gleichzeitig).

    Wird vom UI nicht automatisch aufgerufen. Steht bereit für einen
    'Alle analysieren'-Button falls du das später willst.
    """
    results = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_ANALYSIS) as pool:
        futures = {pool.submit(analyze_company, cid): cid for cid in company_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                res = fut.result()
                results.append({"company_id": cid, "ok": True, **res})
            except Exception as e:
                logger.error(f"Batch-Analyse Firma {cid} fehlgeschlagen: {e}")
                results.append({"company_id": cid, "ok": False, "error": str(e)})
    return results
