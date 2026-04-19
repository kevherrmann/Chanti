"""Lead-Scoring: Website-Bedarf × Zahlungsfähigkeit.

Eingabe: Dicts aus website_analysis und reputation (wie sie in der DB liegen).
Ausgabe: (need_score, payability_score, reason_text).
"""
from __future__ import annotations

from typing import Optional


def compute(website_analysis: Optional[dict], reputation: Optional[dict],
            company: Optional[dict] = None) -> tuple[float, float, str]:
    """Gibt (need, payability, reason) zurück, jeweils 0..10."""
    need, need_reasons = _compute_need(website_analysis)
    pay, pay_reasons = _compute_payability(reputation, company, website_analysis)

    reason = "Bedarf: " + (", ".join(need_reasons) or "keine Schwächen") \
             + " | Zahlungsfähigkeit: " + (", ".join(pay_reasons) or "keine Signale")
    return need, pay, reason


def _compute_need(wa: Optional[dict]) -> tuple[float, list[str]]:
    if not wa:
        return 5.0, ["nicht analysiert"]

    # Keine Website erreichbar = Maximalbedarf
    if not wa.get("reachable"):
        http = wa.get("http_status")
        if http and http >= 400:
            return 9.0, [f"HTTP {http}"]
        # Noch nicht erreicht / keine URL
        if not (wa.get("http_status") or wa.get("title")):
            return 10.0, ["keine Website"]
        return 9.0, ["nicht erreichbar"]

    score = 0.0
    reasons: list[str] = []

    if wa.get("under_construction"):
        score += 4
        reasons.append("Seite im Aufbau")

    wc = wa.get("word_count") or 0
    if wc < 80:
        score += 3
        reasons.append("sehr wenig Inhalt")
    elif wc < 200:
        score += 1
        reasons.append("wenig Inhalt")

    if not wa.get("has_viewport"):
        score += 2
        reasons.append("nicht mobil-optimiert")
    if not wa.get("has_ssl"):
        score += 2
        reasons.append("kein HTTPS")
    if not wa.get("has_contact"):
        score += 2
        reasons.append("keine Kontakt-Info")
    if not wa.get("has_nav"):
        score += 1
        reasons.append("keine Navigation")
    if not wa.get("has_images"):
        score += 1
        reasons.append("keine Bilder")

    # Baukasten-Seiten: kleiner Bonus für Bedarf (Kunden die zahlen könnten)
    platform = wa.get("platform_detected")
    if platform in ("wix", "jimdo", "webnode", "ionos"):
        score += 1
        reasons.append(f"Baukasten ({platform})")

    return min(score, 10.0), reasons


def _compute_payability(rep: Optional[dict], company: Optional[dict],
                        wa: Optional[dict]) -> tuple[float, list[str]]:
    if not rep and not company and not wa:
        return 0.0, ["keine Daten"]

    score = 0.0
    reasons: list[str] = []

    # Google-Rating
    rating = (rep or {}).get("rating")
    reviews = (rep or {}).get("review_count") or 0
    if rating is not None and rating >= 4.0:
        if reviews >= 50:
            score += 5
            reasons.append(f"★{rating} ({reviews} Bew.)")
        elif reviews >= 20:
            score += 4
            reasons.append(f"★{rating} ({reviews} Bew.)")
        elif reviews >= 5:
            score += 2
            reasons.append(f"★{rating} ({reviews} Bew.)")

    # Impressum-Email
    if (rep or {}).get("has_impressum_email"):
        score += 3
        reasons.append("Impressum-Mail gefunden")

    # Domain-Alter
    age = (rep or {}).get("domain_age_years")
    if age is not None:
        if age >= 10:
            score += 3
            reasons.append(f"{age}J Domain")
        elif age >= 5:
            score += 2
            reasons.append(f"{age}J Domain")
        elif age >= 2:
            score += 1
            reasons.append(f"{age}J Domain")

    # Social Media
    has_social = any((rep or {}).get(k) for k in
                     ("social_facebook", "social_instagram", "social_linkedin"))
    if has_social:
        score += 1
        reasons.append("Social Media aktiv")

    # Kontaktdaten aus OSM/Brave
    if company and company.get("phone") and company.get("address"):
        score += 1
        reasons.append("Tel+Adr")

    return min(score, 10.0), reasons


def category(total_score: float) -> str:
    """Klartextkategorie für UI-Farbgebung."""
    if total_score >= 80:
        return "priority"
    if total_score >= 60:
        return "hot"
    if total_score >= 40:
        return "solid"
    if total_score >= 20:
        return "weak"
    return "rejected"
