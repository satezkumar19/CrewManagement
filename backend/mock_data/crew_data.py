"""
Realistic maritime crew mock data — 20 sign-on candidates + 20 sign-off crew.
"""
from datetime import date, timedelta
import random

RANKS = ["Master", "Chief Officer", "Second Officer", "Third Officer",
         "Chief Engineer", "Second Engineer", "Third Engineer", "Fourth Engineer",
         "Bosun", "AB Seaman", "Electrician", "Cook", "Deck Cadet", "Engine Cadet"]

GRADES = ["Grade A", "Grade B", "Grade C", "Grade D"]
NATIONALITIES = ["Filipino", "Indian", "Ukrainian", "Russian", "Croatian",
                 "Greek", "Turkish", "Polish", "Indonesian", "Chinese"]
VESSELS = ["MV Pacific Star", "MV Atlantic Voyager", "MV Indian Ocean Pride",
           "MT Crude Titan", "MV Mediterranean Queen"]
PORTS = ["Singapore", "Rotterdam", "Houston", "Dubai", "Shanghai",
         "Hamburg", "Piraeus", "Manila", "Mumbai", "Busan"]
STCW_STATUSES = ["Valid", "Valid", "Valid", "Expiring Soon", "Expired"]
VISA_STATUSES = ["Valid", "Valid", "Valid", "Expiring Soon", "Expired"]


def _future_date(min_days=30, max_days=730):
    return (date.today() + timedelta(days=random.randint(min_days, max_days))).isoformat()


def _past_date(min_days=30, max_days=1825):
    return (date.today() - timedelta(days=random.randint(min_days, max_days))).isoformat()


SIGN_ON_CREW = [
    {
        "crew_id": f"SNO-{1000 + i}",
        "name": name,
        "rank": rank,
        "grade": grade,
        "nationality": nat,
        "port": port,
        "vessel": "Available",
        "availability": "Available",
        "passport_expiry": _future_date(180, 1825),
        "medical_expiry": _future_date(30, 730),
        "stcw_status": stcw,
        "visa_status": visa,
        "experience_years": exp,
        "certifications": certs,
        "match_score": None,
        "match_reason": None,
        "status": "Available",
        "joining_date": None,
    }
    for i, (name, rank, grade, nat, port, stcw, visa, exp, certs) in enumerate([
        ("Juan dela Cruz", "Chief Officer", "Grade A", "Filipino", "Singapore",
         "Valid", "Valid", 12, ["STCW Basic Safety", "GMDSS", "Advanced Fire Fighting", "Medical First Aid"]),
        ("Rajesh Kumar", "Second Officer", "Grade B", "Indian", "Mumbai",
         "Valid", "Valid", 8, ["STCW Basic Safety", "GMDSS", "Radar Navigation"]),
        ("Alexei Petrov", "Chief Engineer", "Grade A", "Ukrainian", "Rotterdam",
         "Valid", "Valid", 15, ["STCW Basic Safety", "High Voltage", "ECDIS"]),
        ("Maria Santos", "Cook", "Grade C", "Filipino", "Manila",
         "Valid", "Valid", 5, ["STCW Basic Safety", "Food Safety", "Crowd Management"]),
        ("Ahmed Hassan", "AB Seaman", "Grade C", "Turkish", "Istanbul",
         "Valid", "Expiring Soon", 3, ["STCW Basic Safety", "Proficiency in Survival Craft"]),
        ("Dmitri Volkov", "Master", "Grade A", "Russian", "Singapore",
         "Valid", "Valid", 20, ["STCW Basic Safety", "GMDSS", "ECDIS", "BRM"]),
        ("Carlos Rivera", "Third Officer", "Grade B", "Filipino", "Manila",
         "Valid", "Valid", 4, ["STCW Basic Safety", "Radar Navigation", "GMDSS"]),
        ("Priya Sharma", "Electrician", "Grade B", "Indian", "Mumbai",
         "Valid", "Valid", 6, ["STCW Basic Safety", "High Voltage", "Electrical Safety"]),
        ("Nikos Papadopoulos", "Bosun", "Grade B", "Greek", "Piraeus",
         "Expiring Soon", "Valid", 9, ["STCW Basic Safety", "Deck Watchkeeping"]),
        ("Wang Wei", "Second Engineer", "Grade A", "Chinese", "Shanghai",
         "Valid", "Valid", 11, ["STCW Basic Safety", "High Voltage", "Engine Watchkeeping"]),
        ("Santos Reyes", "Fourth Engineer", "Grade C", "Filipino", "Manila",
         "Valid", "Valid", 2, ["STCW Basic Safety", "Engine Room Resource Management"]),
        ("Piotr Kowalski", "Chief Officer", "Grade A", "Polish", "Rotterdam",
         "Valid", "Valid", 13, ["STCW Basic Safety", "GMDSS", "Advanced Fire Fighting", "ECDIS"]),
        ("Ravi Patel", "Third Engineer", "Grade B", "Indian", "Mumbai",
         "Valid", "Expiring Soon", 7, ["STCW Basic Safety", "Engine Watchkeeping"]),
        ("Ivan Kovalenko", "Master", "Grade A", "Ukrainian", "Rotterdam",
         "Valid", "Valid", 18, ["STCW Basic Safety", "GMDSS", "ECDIS", "BRM", "ARPA"]),
        ("Budi Santoso", "AB Seaman", "Grade C", "Indonesian", "Singapore",
         "Valid", "Valid", 4, ["STCW Basic Safety", "Proficiency in Survival Craft"]),
        ("Jose Mendoza", "Deck Cadet", "Grade D", "Filipino", "Manila",
         "Valid", "Valid", 1, ["STCW Basic Safety"]),
        ("Anil Gupta", "Chief Engineer", "Grade A", "Indian", "Mumbai",
         "Valid", "Valid", 16, ["STCW Basic Safety", "High Voltage", "ECDIS", "BRM"]),
        ("Yusuf Ozkan", "Cook", "Grade C", "Turkish", "Istanbul",
         "Valid", "Valid", 8, ["STCW Basic Safety", "Food Safety"]),
        ("Stavros Nikolaou", "Second Officer", "Grade B", "Greek", "Piraeus",
         "Expiring Soon", "Valid", 9, ["STCW Basic Safety", "Radar Navigation", "GMDSS"]),
        ("Zhang Wei", "Engine Cadet", "Grade D", "Chinese", "Shanghai",
         "Valid", "Valid", 0, ["STCW Basic Safety"]),
    ])
]

# ── No-crew demo cluster ────────────────────────────────────────────────────────
# Three Pumpmen (a tanker rating) added explicitly so we can control eligibility —
# the comprehension above hardcodes status="Available". Ratings get NO adjacency
# cover, so a "Pumpman" vacancy is fillable ONLY by an eligible Pumpman. All three
# here are INELIGIBLE (two miss the vessel-mandated "Tanker Familiarization" cert →
# Vessel Ops gate; one is on leave → Crew Intel gate), so a Pumpman sign-off reliably
# yields `no_crew_found` against the live seeded pool — a real, demoable dead-end.
SIGN_ON_CREW += [
    {
        "crew_id": "SNO-1020", "name": "Diego Navarro", "rank": "Pumpman", "grade": "Grade C",
        "nationality": "Filipino", "port": "Singapore", "vessel": "Available", "availability": "Available",
        "passport_expiry": _future_date(180, 1825), "medical_expiry": _future_date(30, 730),
        "stcw_status": "Valid", "visa_status": "Valid", "experience_years": 6,
        "certifications": ["STCW Basic Safety", "Crude Oil Washing"],  # missing Tanker Familiarization
        "match_score": None, "match_reason": None, "status": "Available", "joining_date": None,
    },
    {
        "crew_id": "SNO-1021", "name": "Tomasz Lewandowski", "rank": "Pumpman", "grade": "Grade B",
        "nationality": "Polish", "port": "Rotterdam", "vessel": "Available", "availability": "Available",
        "passport_expiry": _future_date(180, 1825), "medical_expiry": _future_date(30, 730),
        "stcw_status": "Expiring Soon", "visa_status": "Valid", "experience_years": 8,
        "certifications": ["STCW Basic Safety"],  # missing Tanker Familiarization
        "match_score": None, "match_reason": None, "status": "Available", "joining_date": None,
    },
    {
        "crew_id": "SNO-1022", "name": "Rafael Mendoza", "rank": "Pumpman", "grade": "Grade B",
        "nationality": "Filipino", "port": "Singapore", "vessel": "Available", "availability": "On Leave",
        "passport_expiry": _future_date(180, 1825), "medical_expiry": _future_date(30, 730),
        "stcw_status": "Valid", "visa_status": "Valid", "experience_years": 10,
        "certifications": ["STCW Basic Safety", "Tanker Familiarization"],  # qualified, but unavailable
        "match_score": None, "match_reason": None, "status": "On Leave", "joining_date": None,
    },
]

SIGN_OFF_CREW = [
    {
        "crew_id": f"SOF-{2000 + i}",
        "name": name,
        "rank": rank,
        "grade": grade,
        "nationality": nat,
        "vessel": vessel,
        "port": port,
        "joining_date": joining,
        "medical_expiry": med_exp,
        "stcw_status": stcw,
        "visa_status": visa,
        "status": status,
        "passport_expiry": _future_date(60, 365),
    }
    for i, (name, rank, grade, nat, vessel, port, joining, med_exp, stcw, visa, status) in enumerate([
        ("Miguel Torres", "Chief Officer", "Grade A", "Filipino",
         "MV Pacific Star", "Singapore", _past_date(180, 200),
         _future_date(30, 90), "Valid", "Valid", "Onboard"),
        ("Sergei Morozov", "Second Officer", "Grade B", "Russian",
         "MV Atlantic Voyager", "Rotterdam", _past_date(150, 170),
         _future_date(60, 120), "Valid", "Valid", "Onboard"),
        ("Ramesh Nair", "Chief Engineer", "Grade A", "Indian",
         "MV Indian Ocean Pride", "Dubai", _past_date(200, 220),
         _future_date(10, 30), "Valid", "Expiring Soon", "Onboard"),
        ("Pedro Lim", "Cook", "Grade C", "Filipino",
         "MV Pacific Star", "Singapore", _past_date(90, 110),
         _future_date(120, 180), "Valid", "Valid", "Onboard"),
        ("Marat Suleimanov", "AB Seaman", "Grade C", "Ukrainian",
         "MT Crude Titan", "Houston", _past_date(100, 120),
         _future_date(90, 150), "Valid", "Valid", "Onboard"),
        ("Konstantinos Diakos", "Master", "Grade A", "Greek",
         "MV Mediterranean Queen", "Piraeus", _past_date(240, 260),
         _future_date(45, 90), "Valid", "Valid", "Onboard"),
        ("Antonio Garcia", "Third Officer", "Grade B", "Filipino",
         "MV Pacific Star", "Manila", _past_date(120, 140),
         _future_date(180, 240), "Valid", "Valid", "Onboard"),
        ("Suresh Iyer", "Electrician", "Grade B", "Indian",
         "MV Atlantic Voyager", "Mumbai", _past_date(160, 180),
         _future_date(60, 90), "Valid", "Valid", "Onboard"),
        ("Yannis Georgiou", "Bosun", "Grade B", "Greek",
         "MV Mediterranean Queen", "Piraeus", _past_date(130, 150),
         _future_date(30, 60), "Expiring Soon", "Valid", "Onboard"),
        ("Li Mingyang", "Second Engineer", "Grade A", "Chinese",
         "MT Crude Titan", "Shanghai", _past_date(190, 210),
         _future_date(90, 120), "Valid", "Valid", "Onboard"),
        ("Roberto Cruz", "Fourth Engineer", "Grade C", "Filipino",
         "MV Indian Ocean Pride", "Manila", _past_date(80, 100),
         _future_date(150, 200), "Valid", "Valid", "Onboard"),
        ("Andrzej Wiśniewski", "Chief Officer", "Grade A", "Polish",
         "MV Atlantic Voyager", "Rotterdam", _past_date(210, 230),
         _future_date(60, 120), "Valid", "Valid", "Onboard"),
        ("Venkat Reddy", "Third Engineer", "Grade B", "Indian",
         "MV Pacific Star", "Mumbai", _past_date(140, 160),
         _future_date(30, 90), "Valid", "Expiring Soon", "Onboard"),
        ("Bohdan Kravchenko", "Master", "Grade A", "Ukrainian",
         "MT Crude Titan", "Rotterdam", _past_date(270, 290),
         _future_date(90, 120), "Valid", "Valid", "Onboard"),
        ("Agus Wibowo", "AB Seaman", "Grade C", "Indonesian",
         "MV Pacific Star", "Singapore", _past_date(110, 130),
         _future_date(120, 180), "Valid", "Valid", "Onboard"),
        ("Fernando Reyes", "Deck Cadet", "Grade D", "Filipino",
         "MV Mediterranean Queen", "Manila", _past_date(60, 80),
         _future_date(200, 300), "Valid", "Valid", "Onboard"),
        ("Manoj Pillai", "Chief Engineer", "Grade A", "Indian",
         "MV Atlantic Voyager", "Mumbai", _past_date(230, 250),
         _future_date(30, 60), "Valid", "Valid", "Onboard"),
        ("Mehmet Yilmaz", "Cook", "Grade C", "Turkish",
         "MV Indian Ocean Pride", "Istanbul", _past_date(100, 120),
         _future_date(90, 150), "Valid", "Valid", "Onboard"),
        ("Giorgos Kostopoulos", "Second Officer", "Grade B", "Greek",
         "MV Mediterranean Queen", "Piraeus", _past_date(150, 170),
         _future_date(60, 120), "Expiring Soon", "Valid", "Onboard"),
        ("Chen Jianhua", "Engine Cadet", "Grade D", "Chinese",
         "MT Crude Titan", "Shanghai", _past_date(70, 90),
         _future_date(200, 300), "Valid", "Valid", "Onboard"),
    ])
]


def get_sign_on_crew():
    return [dict(c) for c in SIGN_ON_CREW]


def get_sign_off_crew():
    return [dict(c) for c in SIGN_OFF_CREW]


def get_crew_by_id(crew_id: str, pool: str = "both"):
    if pool in ("signon", "both"):
        for c in SIGN_ON_CREW:
            if c["crew_id"] == crew_id:
                return dict(c)
    if pool in ("signoff", "both"):
        for c in SIGN_OFF_CREW:
            if c["crew_id"] == crew_id:
                return dict(c)
    return None
