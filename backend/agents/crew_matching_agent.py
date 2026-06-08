"""
Crew Matching Agent — searches the sign-on pool and ranks candidates.
Tools: searchCrew(), rankCrew(), getCrewProfile()
"""
import json
import random
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from agents.skills import build_instructions
from database.crew_repository import get_sign_on_crew

TOOLS = [
    {
        "name": "searchCrew",
        "description": (
            "Search the available sign-on crew pool based on rank, grade, port, "
            "nationality, and availability filters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rank": {"type": "string", "description": "Required rank (e.g., Chief Officer)"},
                "grade": {"type": "string", "description": "Grade (A/B/C/D)"},
                "port": {"type": "string", "description": "Preferred embarkation port"},
                "nationality": {"type": "string", "description": "Preferred nationality (optional)"},
                "min_experience": {"type": "integer", "description": "Minimum years of experience"},
            },
            "required": ["rank"],
        },
    },
    {
        "name": "rankCrew",
        "description": (
            "Rank a list of crew candidates by match confidence score considering "
            "rank, grade, port proximity, nationality preference, and certifications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of crew_ids to rank",
                },
                "sign_off_crew_rank": {"type": "string"},
                "sign_off_crew_grade": {"type": "string"},
                "preferred_port": {"type": "string"},
            },
            "required": ["candidates", "sign_off_crew_rank"],
        },
    },
    {
        "name": "getCrewProfile",
        "description": "Retrieve full profile of a crew member by crew_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_id": {"type": "string", "description": "Crew member ID"},
            },
            "required": ["crew_id"],
        },
    },
]

# SYSTEM_ROLE is now assembled from markdown skill files under
# agents/skills/crew_matching/ via agents.skills.build_instructions().
# See agents/skills/README.md for the layout and INTEGRATION.md for context.

# L4 #3 — max point swing each precedent signal can add to a candidate's score.
# Guidance weights are in [0, 1]; the actual boost is weight * the cap below.
_PREFER_NAT_MAX = 10.0    # nationality matched a prior signed-on placement
_PREFER_GRADE_MAX = 4.0   # grade matched a prior signed-on placement
_AVOID_NAT_MAX = 12.0     # nationality matched a prior rejected placement (subtracted)

# L4 #3 (embeddings) — max points the blended structural-similarity signal can add.
_SIM_MAX = 8.0
_SIM_REASON_THRESHOLD = 0.55   # only surface a similarity reason above this cosine


def _embedding_funcs():
    """Lazy accessor for the embedding helpers — imported inside calls because
    `services/__init__` eagerly pulls in the agents stack (circular at import time)."""
    from services.embedding_service import cosine, embed_crew
    return cosine, embed_crew


class CrewMatchingAgent(BaseAgent):
    def __init__(self, event_callback=None):
        super().__init__(
            name="Crew Matching Agent",
            role=build_instructions("crew_matching"),
            tools=TOOLS,
            event_callback=event_callback,
        )
        # Loaded lazily from Postgres on first use (can't await in __init__).
        self._all_crew: List[Dict[str, Any]] = None
        # L4 #3 — precedent guidance injected per workflow before ranking (see
        # PrecedentService.derive_guidance). None/has_precedent=False ⇒ no boost.
        self._precedent_guidance: Dict[str, Any] = None
        # L4 #3 (embeddings) — similarity context for this workflow:
        # {"departing": [float], "precedents": [[float], ...]}. None ⇒ no boost.
        self._similarity_context: Dict[str, Any] = None

    def set_precedent_guidance(self, guidance: Dict[str, Any]) -> None:
        """Inject the precedent-derived re-rank guidance for this workflow (L4 #3)."""
        self._precedent_guidance = guidance or None

    def set_similarity_context(
        self, departing_embedding=None, precedent_embeddings=None
    ) -> None:
        """Inject the structural-embedding similarity context for this workflow:
        the departing crew's embedding and the embeddings of prior signed-on crew
        for this vacancy profile. Either may be None/empty (then that signal is 0)."""
        self._similarity_context = {
            "departing": departing_embedding,
            "precedents": precedent_embeddings or [],
        }

    async def _ensure_crew_loaded(self) -> None:
        if self._all_crew is None:
            self._all_crew = await get_sign_on_crew()

    async def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        await self._ensure_crew_loaded()
        if tool_name == "searchCrew":
            return self._search_crew(tool_input)
        if tool_name == "rankCrew":
            return self._rank_crew(tool_input)
        if tool_name == "getCrewProfile":
            return self._get_crew_profile(tool_input)
        return {"error": f"Unknown tool: {tool_name}"}

    def _search_crew(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        rank = params.get("rank", "")
        grade = params.get("grade", "")
        port = params.get("port", "")
        nationality = params.get("nationality", "")
        min_exp = params.get("min_experience", 0)

        results = []
        for crew in self._all_crew:
            if crew["status"] != "Available":
                continue
            if rank and crew["rank"].lower() != rank.lower():
                continue
            if grade and crew["grade"].lower() != grade.lower():
                continue
            if min_exp and crew.get("experience_years", 0) < min_exp:
                continue
            results.append({
                "crew_id": crew["crew_id"],
                "name": crew["name"],
                "rank": crew["rank"],
                "grade": crew["grade"],
                "port": crew["port"],
                "nationality": crew["nationality"],
                "stcw_status": crew["stcw_status"],
                "visa_status": crew["visa_status"],
                "medical_expiry": crew["medical_expiry"],
                "experience_years": crew.get("experience_years", 0),
            })

        # If no exact rank match, broaden search
        if not results:
            for crew in self._all_crew:
                if crew["status"] == "Available" and crew.get("experience_years", 0) >= min_exp:
                    results.append({
                        "crew_id": crew["crew_id"],
                        "name": crew["name"],
                        "rank": crew["rank"],
                        "grade": crew["grade"],
                        "port": crew["port"],
                        "nationality": crew["nationality"],
                        "stcw_status": crew["stcw_status"],
                        "visa_status": crew["visa_status"],
                        "experience_years": crew.get("experience_years", 0),
                    })

        return {"found": len(results), "candidates": results}

    def _rank_crew(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        candidate_ids = params.get("candidates", [])
        target_rank = params.get("sign_off_crew_rank", "")
        target_grade = params.get("sign_off_crew_grade", "")
        preferred_port = params.get("preferred_port", "")

        crew_map = {c["crew_id"]: c for c in self._all_crew}
        ranked = []

        for cid in candidate_ids:
            crew = crew_map.get(cid)
            if not crew:
                continue

            score = 0.0
            reasons = []

            # Rank match (40%)
            if crew["rank"].lower() == target_rank.lower():
                score += 40
                reasons.append("Exact rank match")
            else:
                score += 10
                reasons.append("Rank mismatch — similar")

            # Grade match (20%)
            if target_grade and crew["grade"].lower() == target_grade.lower():
                score += 20
                reasons.append("Grade matches")
            else:
                score += 8

            # Port proximity (15%)
            if preferred_port and crew["port"].lower() == preferred_port.lower():
                score += 15
                reasons.append(f"Same port: {preferred_port}")
            else:
                score += 5

            # Valid docs (15%)
            doc_score = 0
            if crew.get("stcw_status") == "Valid":
                doc_score += 5
            if crew.get("visa_status") == "Valid":
                doc_score += 5
            doc_score += min(5, len(crew.get("certifications", [])))
            score += doc_score
            if doc_score >= 12:
                reasons.append("All documents valid")

            # Experience (10%)
            exp = crew.get("experience_years", 0)
            exp_score = min(10, exp * 0.7)
            score += exp_score
            if exp >= 10:
                reasons.append(f"{exp} years experience")

            # Small random variation to simulate real-world scoring
            score += random.uniform(-2, 2)
            base_score = round(max(0, min(100, score)), 1)

            # L4 #3 — precedent boost: bias toward profiles that previously signed
            # on cleanly, away from ones that were rejected. base_score already
            # carries the jitter, so the boost is the ONLY difference (lift == boost).
            boost, boost_reasons = self._precedent_boost(crew)
            reasons.extend(boost_reasons)

            # L4 #3 (embeddings) — blended structural-similarity boost (to the
            # departing crew and to prior signed-on crew for this profile).
            sim_boost, sim_reasons, sim_dep, sim_prec = self._similarity_boost(crew)
            reasons.extend(sim_reasons)

            adjusted = round(max(0, min(100, base_score + boost + sim_boost)), 1)

            ranked.append({
                "crew_id": cid,
                "name": crew["name"],
                "rank": crew["rank"],
                "grade": crew["grade"],
                "port": crew["port"],
                "nationality": crew["nationality"],
                "confidence_score": adjusted,
                "base_confidence_score": base_score,
                "precedent_boost": round(boost, 1),
                "similarity_boost": round(sim_boost, 1),
                "similarity_departing": sim_dep,
                "similarity_precedent": sim_prec,
                "match_reasons": reasons,
            })

        # Adjusted order is what we return; capture the base-order winner too so we
        # can tell whether precedent actually changed the selection.
        base_winner = max(ranked, key=lambda x: x["base_confidence_score"], default=None)
        ranked.sort(key=lambda x: x["confidence_score"], reverse=True)
        feedback = self._build_precedent_feedback(ranked, base_winner)
        return {"ranked_candidates": ranked[:5], "precedent_feedback": feedback}

    def _precedent_boost(self, crew: Dict[str, Any]) -> tuple:
        """Point boost (+/-) for one candidate from the injected precedent guidance,
        plus any human-readable reasons. Returns (0.0, []) when there's no guidance."""
        g = self._precedent_guidance or {}
        if not g.get("has_precedent"):
            return 0.0, []
        nat = (crew.get("nationality") or "")
        grade = (crew.get("grade") or "")
        boost = 0.0
        reasons: List[str] = []
        prefer_nat = g.get("prefer_nationalities") or {}
        avoid_nat = g.get("avoid_nationalities") or {}
        prefer_grade = g.get("prefer_grades") or {}
        if nat in prefer_nat:
            boost += prefer_nat[nat] * _PREFER_NAT_MAX
            reasons.append(f"Precedent: {nat} nationals cleared this vacancy before")
        if nat in avoid_nat:
            boost -= avoid_nat[nat] * _AVOID_NAT_MAX
            reasons.append(f"Precedent: {nat} was rejected for this vacancy before")
        if grade in prefer_grade:
            boost += prefer_grade[grade] * _PREFER_GRADE_MAX
        return boost, reasons

    def _similarity_boost(self, crew: Dict[str, Any]) -> tuple:
        """Blended structural-similarity boost for one candidate (L4 #3 embeddings).

        Returns (boost_points, reasons, sim_departing, sim_precedent). With no
        similarity context injected, returns (0.0, [], None, None) — a no-op, so
        ranking is unchanged without embeddings (same guard as the precedent boost).
        """
        ctx = self._similarity_context or {}
        departing = ctx.get("departing")
        precedents = ctx.get("precedents") or []
        if not departing and not precedents:
            return 0.0, [], None, None

        cosine, embed_crew = _embedding_funcs()
        emb = crew.get("embedding") or embed_crew(crew)
        sim_dep = max(0.0, cosine(emb, departing)) if departing else 0.0
        sim_prec = max((max(0.0, cosine(emb, pe)) for pe in precedents), default=0.0) if precedents else 0.0

        if departing and precedents:
            blend = 0.6 * sim_dep + 0.4 * sim_prec
        elif precedents:
            blend = sim_prec
        else:
            blend = sim_dep
        boost = blend * _SIM_MAX

        reasons: List[str] = []
        if sim_dep >= _SIM_REASON_THRESHOLD:
            reasons.append(f"Structurally similar to departing crew: {round(sim_dep * 100)}%")
        if sim_prec >= _SIM_REASON_THRESHOLD:
            reasons.append(f"Resembles prior signed-on crew: {round(sim_prec * 100)}%")
        return boost, reasons, round(sim_dep, 3), round(sim_prec, 3)

    def _build_precedent_feedback(
        self, ranked: List[Dict[str, Any]], base_winner: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Summarize how the precedent boost re-ranked this query (L4 #3 measurement)."""
        g = self._precedent_guidance or {}
        applied = bool(g.get("has_precedent")) and any(c.get("precedent_boost") for c in ranked)
        if not applied or not ranked:
            return {"applied": False}
        adj_winner = ranked[0]
        top_base = adj_winner.get("base_confidence_score", adj_winner["confidence_score"])
        top_adjusted = adj_winner["confidence_score"]
        reranked = bool(base_winner) and base_winner["crew_id"] != adj_winner["crew_id"]
        boosted = [
            {
                "crew_id": c["crew_id"], "name": c["name"],
                "nationality": c.get("nationality"), "boost": c.get("precedent_boost", 0.0),
            }
            for c in ranked if c.get("precedent_boost")
        ]
        return {
            "applied": True,
            "top_base_score": top_base,
            "top_adjusted_score": top_adjusted,
            "lift": round(top_adjusted - top_base, 1),
            "reranked": reranked,
            "base_winner": (
                {"crew_id": base_winner["crew_id"], "name": base_winner["name"]}
                if base_winner else None
            ),
            "adjusted_winner": {"crew_id": adj_winner["crew_id"], "name": adj_winner["name"]},
            "boosted": boosted,
            "rationale": g.get("rationale"),
        }

    def _get_crew_profile(self, params: Dict[str, Any]) -> Dict[str, Any]:
        crew_id = params.get("crew_id", "")
        crew_map = {c["crew_id"]: c for c in self._all_crew}
        crew = crew_map.get(crew_id)
        if crew:
            return {"profile": crew}
        return {"error": f"Crew member {crew_id} not found"}

    async def _validate_and_format(
        self, raw_text: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        await self._ensure_crew_loaded()
        # Extract the best match from tool call history
        ranked_result = None
        top_candidate = None
        precedent_feedback = None

        for tc in self.execution.tool_calls:
            if tc.tool_name == "rankCrew" and tc.output:
                output = tc.output
                candidates = output.get("ranked_candidates", [])
                if candidates:
                    ranked_result = candidates
                    top_candidate = candidates[0]
                # L4 #3 — carry the re-rank measurement up to the workflow/decision.
                if output.get("precedent_feedback"):
                    precedent_feedback = output["precedent_feedback"]

        if not top_candidate:
            # Fallback
            crew_list = self._all_crew
            top_candidate = {
                "crew_id": crew_list[0]["crew_id"],
                "name": crew_list[0]["name"],
                "rank": crew_list[0]["rank"],
                "confidence_score": 75.0,
                "match_reasons": ["Default selection"],
            }

        self.execution.confidence_score = top_candidate.get("confidence_score", 75.0) / 100

        return {
            "top_match": top_candidate,
            "ranked_candidates": ranked_result or [top_candidate],
            "summary": raw_text[:500] if raw_text else "Crew matching completed.",
            "confidence_score": top_candidate.get("confidence_score", 75.0),
            "precedent_feedback": precedent_feedback,
        }
