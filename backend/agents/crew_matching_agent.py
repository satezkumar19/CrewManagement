"""
Crew Matching Agent — searches the sign-on pool and ranks candidates.
Tools: searchCrew(), rankCrew(), getCrewProfile()
"""
import json
import random
from typing import Any, Dict, List

from agents.base_agent import BaseAgent
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

SYSTEM_ROLE = """You are the Crew Matching Agent for a maritime crew management system.
Your sole responsibility is to find the best replacement crew member from the sign-on pool.

You MUST:
1. Call searchCrew() to find candidates matching the rank and other criteria
2. Call rankCrew() to score and rank those candidates
3. Call getCrewProfile() for the top candidate to get their full details
4. Return a structured result with the top match, confidence score, and ranking rationale

Scoring criteria (weighted):
- Rank match: 40%
- Grade match: 20%
- Port proximity: 15%
- Valid certifications (STCW, Medical, Visa): 15%
- Experience level: 10%

Always select the candidate with the highest overall score."""


class CrewMatchingAgent(BaseAgent):
    def __init__(self, event_callback=None):
        super().__init__(
            name="Crew Matching Agent",
            role=SYSTEM_ROLE,
            tools=TOOLS,
            event_callback=event_callback,
        )
        # Loaded lazily from Postgres on first use (can't await in __init__).
        self._all_crew: List[Dict[str, Any]] = None

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
            score = max(0, min(100, score))

            ranked.append({
                "crew_id": cid,
                "name": crew["name"],
                "rank": crew["rank"],
                "grade": crew["grade"],
                "port": crew["port"],
                "nationality": crew["nationality"],
                "confidence_score": round(score, 1),
                "match_reasons": reasons,
            })

        ranked.sort(key=lambda x: x["confidence_score"], reverse=True)
        return {"ranked_candidates": ranked[:5]}

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

        for tc in self.execution.tool_calls:
            if tc.tool_name == "rankCrew" and tc.output:
                output = tc.output
                candidates = output.get("ranked_candidates", [])
                if candidates:
                    ranked_result = candidates
                    top_candidate = candidates[0]

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
        }
