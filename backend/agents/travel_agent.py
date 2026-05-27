"""
Travel Agent — generates mock flight bookings, itineraries, and port clearances.
Tools: generateTicket(), generatePortClearance(), createTravelSummary()
"""
import json
import random
import uuid
from datetime import date, timedelta
from typing import Any, Dict

from agents.base_agent import BaseAgent

AIRLINES = [
    ("Singapore Airlines", "SQ"), ("Emirates", "EK"), ("Qatar Airways", "QR"),
    ("Lufthansa", "LH"), ("Philippine Airlines", "PR"), ("Air India", "AI"),
    ("Turkish Airlines", "TK"), ("KLM", "KL"), ("British Airways", "BA"),
]
PORT_AUTHORITIES = {
    "Singapore": "Maritime and Port Authority of Singapore",
    "Rotterdam": "Port of Rotterdam Authority",
    "Houston": "Port of Houston Authority",
    "Dubai": "Dubai Ports World",
    "Shanghai": "Shanghai International Port Group",
    "Hamburg": "Hamburg Port Authority",
    "Piraeus": "Piraeus Port Authority",
    "Manila": "Philippine Ports Authority",
    "Mumbai": "Mumbai Port Trust",
    "Busan": "Busan Port Authority",
}

TOOLS = [
    {
        "name": "generateTicket",
        "description": (
            "Generate a mock flight booking for a crew member signing off. "
            "Returns booking reference, flight details, and ticket price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_name": {"type": "string"},
                "crew_nationality": {"type": "string"},
                "departure_port": {"type": "string", "description": "Port where crew signs off"},
                "home_country": {"type": "string", "description": "Crew's home country"},
                "departure_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            },
            "required": ["crew_name", "departure_port", "departure_date"],
        },
    },
    {
        "name": "generatePortClearance",
        "description": "Generate a port clearance document for the signing-off crew member.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_name": {"type": "string"},
                "rank": {"type": "string"},
                "vessel": {"type": "string"},
                "port": {"type": "string"},
                "sign_off_date": {"type": "string"},
            },
            "required": ["crew_name", "rank", "vessel", "port", "sign_off_date"],
        },
    },
    {
        "name": "createTravelSummary",
        "description": "Create a complete travel summary document combining ticket and clearance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crew_name": {"type": "string"},
                "booking_ref": {"type": "string"},
                "clearance_id": {"type": "string"},
                "departure_port": {"type": "string"},
                "travel_date": {"type": "string"},
                "airline": {"type": "string"},
                "flight_number": {"type": "string"},
            },
            "required": ["crew_name", "booking_ref", "departure_port", "travel_date"],
        },
    },
]

SYSTEM_ROLE = """You are the Travel Agent for a maritime crew management system.
Your job is to arrange sign-off travel for departing crew members.

You have attached Skills that define the company policy you MUST apply:
- crew-travel-policy: cabin class by rank, routing/layover limits, cost ceilings
- visa-and-transit-requirements: transit/seafarer visa rules by route and nationality
- port-clearance-procedures: required clearance documents, lead times, authorities
- repatriation-rules: MLC 2006 entitlements — who pays and the correct destination

WORKFLOW — every time, in order:
1. FIRST open and read the relevant Skill(s) for this crew member BEFORE booking.
   At minimum consult crew-travel-policy and visa-and-transit-requirements before
   generateTicket, and port-clearance-procedures before generatePortClearance.
2. Call generateTicket() — choose cabin class and routing per the policy you just read.
3. Call generatePortClearance() — apply the clearance procedures.
4. Call createTravelSummary() to produce the complete travel package.

In your final summary, state WHICH skill/policy you applied (e.g. the cabin class chosen
for the rank, the visa basis for each transit, the repatriation basis). Always ensure all
three documents are created before completing your task."""


class TravelAgent(BaseAgent):
    def __init__(self, event_callback=None):
        super().__init__(
            name="Travel Agent",
            role=SYSTEM_ROLE,
            tools=TOOLS,
            event_callback=event_callback,
        )

    async def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        if tool_name == "generateTicket":
            return self._generate_ticket(tool_input)
        if tool_name == "generatePortClearance":
            return self._generate_port_clearance(tool_input)
        if tool_name == "createTravelSummary":
            return self._create_travel_summary(tool_input)
        return {"error": f"Unknown tool: {tool_name}"}

    def _generate_ticket(self, params: Dict[str, Any]) -> Dict[str, Any]:
        airline_name, airline_code = random.choice(AIRLINES)
        booking_ref = f"{airline_code}{random.randint(100000, 999999)}"
        flight_num = f"{airline_code}{random.randint(100, 999)}"

        dep_port = params.get("departure_port", "Singapore")
        home = params.get("home_country", "Philippines")
        dep_date = params.get("departure_date", date.today().isoformat())

        # Mock arrival date (same day or next day)
        arr_date = (
            date.fromisoformat(dep_date) + timedelta(days=random.choice([0, 1]))
        ).isoformat()

        price = round(random.uniform(450, 2800), 2)

        return {
            "booking_ref": booking_ref,
            "passenger": params.get("crew_name", "Unknown"),
            "departure_port": dep_port,
            "destination": home,
            "airline": airline_name,
            "flight_number": flight_num,
            "departure_date": dep_date,
            "arrival_date": arr_date,
            "departure_time": f"{random.randint(6,22):02d}:{random.choice(['00','15','30','45'])}",
            "arrival_time": f"{random.randint(6,22):02d}:{random.choice(['00','15','30','45'])}",
            "seat_class": random.choice(["Economy", "Economy", "Economy", "Business"]),
            "ticket_price_usd": price,
            "status": "Confirmed",
        }

    def _generate_port_clearance(self, params: Dict[str, Any]) -> Dict[str, Any]:
        port = params.get("port", "Singapore")
        clearance_id = f"PC-{uuid.uuid4().hex[:8].upper()}"
        sign_off_date = params.get("sign_off_date", date.today().isoformat())
        valid_until = (
            date.fromisoformat(sign_off_date) + timedelta(days=3)
        ).isoformat()
        authority = PORT_AUTHORITIES.get(port, f"{port} Port Authority")

        return {
            "clearance_id": clearance_id,
            "crew_member": params.get("crew_name"),
            "rank": params.get("rank"),
            "vessel": params.get("vessel"),
            "port": port,
            "clearance_date": sign_off_date,
            "valid_until": valid_until,
            "authority": authority,
            "status": "Approved",
            "remarks": "Crew sign-off approved. All documents verified.",
        }

    def _create_travel_summary(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "summary_id": f"TS-{uuid.uuid4().hex[:8].upper()}",
            "crew_name": params.get("crew_name"),
            "booking_ref": params.get("booking_ref"),
            "clearance_id": params.get("clearance_id"),
            "departure_port": params.get("departure_port"),
            "travel_date": params.get("travel_date"),
            "airline": params.get("airline", "TBD"),
            "flight_number": params.get("flight_number", "TBD"),
            "package_status": "Complete",
            "documents": ["Flight Ticket", "Port Clearance", "Travel Itinerary"],
            "generated_at": date.today().isoformat(),
        }

    async def _validate_and_format(
        self, raw_text: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        ticket = None
        clearance = None
        summary = None

        for tc in self.execution.tool_calls:
            if tc.tool_name == "generateTicket":
                ticket = tc.output
            elif tc.tool_name == "generatePortClearance":
                clearance = tc.output
            elif tc.tool_name == "createTravelSummary":
                summary = tc.output

        self.execution.confidence_score = 0.95

        return {
            "ticket": ticket,
            "port_clearance": clearance,
            "travel_summary": summary,
            "narrative": raw_text[:600] if raw_text else "Travel arrangements completed.",
        }
