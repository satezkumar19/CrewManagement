"""
Structural Embedding Service (L4 #3) — turns a crew record's STRUCTURE into a
fixed-length numeric vector so candidates can be compared by holistic similarity
(not just exact attribute matches).

"Structural", not "semantic": the vector is built deterministically from the
crew's own fields — rank, grade, nationality, port, certifications, experience,
document validity — over fixed vocabularies, then L2-normalized. No external model
or API, so it's reproducible and explainable (every dimension maps to a known
attribute). The same function embeds a vacancy profile (the departing crew) and a
vessel (the centroid of its assigned crew).

Stored as a JSON float list on the crew row; similarity is computed either by
pgvector (`<=>`) or, in fallback, by `cosine()` here — see embedding_repository.
"""
import math
from typing import Any, Dict, List, Sequence

# ── Fixed vocabularies → stable dimensionality ──────────────────────────────────
# Order is part of the contract: changing it re-bases every stored vector, so
# append new entries rather than reordering. Each categorical group reserves a
# trailing "other" slot so an unseen value still occupies a dimension.
_RANKS = [
    "Master", "Chief Officer", "Second Officer", "Third Officer",
    "Chief Engineer", "Second Engineer", "Third Engineer", "Fourth Engineer",
    "Bosun", "Able Seaman", "Ordinary Seaman", "Oiler", "Wiper", "Cook",
]
_GRADES = ["A", "B", "C", "D"]
_NATIONALITIES = [
    "Indian", "Filipino", "Chinese", "Russian", "Ukrainian", "Indonesian",
    "Greek", "British", "American", "Danish", "Egyptian", "Ghanaian", "Turkish",
]
_PORTS = [
    "Singapore", "Rotterdam", "Houston", "Shanghai", "Dubai", "Hamburg",
    "Hong Kong", "Busan", "Antwerp", "Fujairah",
]
_CERTS = [
    "GMDSS", "ECDIS", "BOSIET", "HUET", "Tanker", "Survival Craft",
    "Basic Safety", "Advanced Firefighting", "Medical First Aid", "Ship Security",
]

# Layout (each categorical group + an "other" slot):
#   rank(|R|+1) grade(|G|+1) nationality(|N|+1) port(|P|+1) certs(|C|) exp(1) docs(2)
_RANK_DIM = len(_RANKS) + 1
_GRADE_DIM = len(_GRADES) + 1
_NAT_DIM = len(_NATIONALITIES) + 1
_PORT_DIM = len(_PORTS) + 1
_CERT_DIM = len(_CERTS)
EMBED_DIM = _RANK_DIM + _GRADE_DIM + _NAT_DIM + _PORT_DIM + _CERT_DIM + 1 + 2

_MAX_EXPERIENCE = 40.0  # normalizer for experience_years


def _onehot(value: Any, vocab: Sequence[str]) -> List[float]:
    """One-hot over `vocab` with a trailing 'other' slot (length len(vocab)+1)."""
    vec = [0.0] * (len(vocab) + 1)
    v = (str(value).strip().lower() if value is not None else "")
    for i, item in enumerate(vocab):
        if item.lower() == v:
            vec[i] = 1.0
            return vec
    vec[-1] = 1.0  # unknown / "other"
    return vec


def embed_crew(crew: Dict[str, Any]) -> List[float]:
    """Deterministic, L2-normalized structural embedding of one crew/profile dict."""
    vec: List[float] = []
    vec += _onehot(crew.get("rank"), _RANKS)
    vec += _onehot(crew.get("grade"), _GRADES)
    vec += _onehot(crew.get("nationality"), _NATIONALITIES)
    vec += _onehot(crew.get("port"), _PORTS)

    certs = crew.get("certifications") or []
    cert_set = {str(c).strip().lower() for c in certs}
    vec += [1.0 if c.lower() in cert_set else 0.0 for c in _CERTS]

    try:
        exp = float(crew.get("experience_years") or 0)
    except (TypeError, ValueError):
        exp = 0.0
    vec.append(max(0.0, min(1.0, exp / _MAX_EXPERIENCE)))

    vec.append(1.0 if (crew.get("stcw_status") == "Valid") else 0.0)
    vec.append(1.0 if (crew.get("visa_status") == "Valid") else 0.0)

    return _l2_normalize(vec)


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1] (0 if either is empty / a length mismatch)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def vessel_centroid(crew_rows: Sequence[Dict[str, Any]]) -> List[float]:
    """A vessel's structural embedding = mean of its assigned crew embeddings
    (re-normalized). Empty input → zero vector of the right dimension."""
    embs = [c.get("embedding") or embed_crew(c) for c in crew_rows]
    embs = [e for e in embs if e and len(e) == EMBED_DIM]
    if not embs:
        return [0.0] * EMBED_DIM
    mean = [sum(e[i] for e in embs) / len(embs) for i in range(EMBED_DIM)]
    return _l2_normalize(mean)
