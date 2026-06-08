"""
Pattern Detection Service (L4 #4) — the bird's-eye view over the decision history.

The earlier L4 phases work one decision at a time (capture, precedent, retry). This
service is the AGGREGATOR: it reads the whole decision_traces store and asks "what
keeps going wrong across ALL placements?" It buckets every compliance failure into a
category, counts how many DISTINCT decisions each category blocks, and flags the one
that recurs (≥2 decisions) as the recurring gap — the systemic problem worth fixing.

Read-only and best-effort: any failure returns an empty report rather than raising
(same convention as the other L4 services).
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import structlog

from database.decision_repository import list_decisions

log = structlog.get_logger()

# A category is "recurring" once it blocks this many DISTINCT decisions.
RECURRING_THRESHOLD = 2

# Ordered (category, keywords, display label, recommendation). ORDER MATTERS:
# "STCW certificate expired" must match stcw before the generic certificate bucket,
# and "Medical certificate …" must match medical first — so specific categories come
# before the catch-all certificate rule.
_RULES: List[Tuple[str, Tuple[str, ...], str, str]] = [
    ("visa", ("visa", "c1/d", "c1 d", "port restriction", "port entry", "entry permit", "transit"),
     "Visa / port-entry compliance",
     "Pre-validate visa & port-entry requirements during matching, before the compliance gate."),
    ("stcw", ("stcw",),
     "STCW certification",
     "Track STCW expiry in the crew pool and surface lapses at match time."),
    ("medical", ("medical", "fit-to-sail", "fit to sail"),
     "Medical certificate",
     "Add a medical-validity check to the matching pre-filter."),
    ("passport", ("passport",),
     "Passport validity",
     "Flag passports expiring within the contract window before selection."),
    ("training", ("proficiency", "survival craft", "basic safety", "firefighting", "drill"),
     "Training / proficiency",
     "Verify required proficiency certificates for the role during matching."),
    ("certification", ("certificate", "certification", "endorsement"),
     "Other certification",
     "Audit the certificate set against the vessel/role requirements earlier."),
]

_LABELS = {cat: label for cat, _kw, label, _rec in _RULES}
_RECS = {cat: rec for cat, _kw, _label, rec in _RULES}
_LABELS["other"] = "Other"
_RECS["other"] = "Review the recurring failure reasons and add an earlier guardrail."

# outcome_reasons sometimes carries a meta summary line (from the retry loop) rather
# than an actual document failure — skip it so it doesn't pollute the categories.
_META_PREFIXES = ("all ", "no candidate")


def _categorize(reason: str) -> str:
    text = (reason or "").lower()
    for cat, keywords, _label, _rec in _RULES:
        if any(k in text for k in keywords):
            return cat
    return "other"


class PatternService:
    async def detect_patterns(self, limit: int = 200) -> Dict[str, Any]:
        """Aggregate the decision history and flag the single recurring compliance gap."""
        empty = {
            "summary": {"total": 0, "signed_on": 0, "rejected": 0, "pending": 0, "rejection_rate": 0.0},
            "categories": [],
            "recurring_gap": None,
            "generated_at": datetime.utcnow().isoformat(),
        }
        try:
            decisions = await list_decisions(limit=limit)
        except Exception:
            log.warning("pattern.detect.load_failed", exc_info=True)
            return empty
        if not decisions:
            return empty

        total = len(decisions)
        signed = sum(1 for d in decisions if d.get("outcome_status") == "signed_on")
        rejected = sum(1 for d in decisions if d.get("outcome_status") == "rejected")
        pending = sum(1 for d in decisions if d.get("outcome_status") == "pending")

        # category -> aggregate. decisions is a set of decision_ids so a category is
        # counted once per decision (recurrence = distinct decisions, not raw lines).
        agg: Dict[str, Dict[str, Any]] = {}

        def _bucket(cat: str) -> Dict[str, Any]:
            return agg.setdefault(cat, {
                "category": cat, "label": _LABELS.get(cat, cat.title()),
                "decisions": set(), "occurrences": 0,
                "ports": set(), "ranks": set(), "examples": [],
            })

        for d in decisions:
            did = d.get("decision_id")
            dep = (d.get("query_context") or {}).get("departing_crew") or {}
            port = dep.get("port")
            rank = dep.get("rank")

            reasons: List[str] = []
            for att in d.get("attempts") or []:
                reasons.extend(att.get("failures") or [])
            if d.get("outcome_status") == "rejected":
                reasons.extend(d.get("outcome_reasons") or [])

            for r in reasons:
                if not r or r.strip().lower().startswith(_META_PREFIXES):
                    continue
                cat = _categorize(r)
                b = _bucket(cat)
                b["occurrences"] += 1
                b["decisions"].add(did)
                if port:
                    b["ports"].add(port)
                if rank:
                    b["ranks"].add(rank)
                if r not in b["examples"] and len(b["examples"]) < 4:
                    b["examples"].append(r)

        categories = [
            {
                "category": b["category"],
                "label": b["label"],
                "decisions_affected": len(b["decisions"]),
                "occurrences": b["occurrences"],
                "ports": sorted(b["ports"]),
                "ranks": sorted(b["ranks"]),
                "examples": b["examples"],
            }
            for b in agg.values()
        ]
        categories.sort(key=lambda c: (c["decisions_affected"], c["occurrences"]), reverse=True)

        recurring_gap = None
        if categories and categories[0]["decisions_affected"] >= RECURRING_THRESHOLD:
            top = categories[0]
            recurring_gap = {**top, "recommendation": _RECS.get(top["category"], _RECS["other"])}

        report = {
            "summary": {
                "total": total, "signed_on": signed, "rejected": rejected, "pending": pending,
                "rejection_rate": round(rejected / total * 100, 1) if total else 0.0,
            },
            "categories": categories,
            "recurring_gap": recurring_gap,
            "generated_at": datetime.utcnow().isoformat(),
        }
        log.info(
            "pattern.detected",
            total=total, rejected=rejected,
            recurring_gap=(recurring_gap or {}).get("category"),
        )
        return report


pattern_service = PatternService()
