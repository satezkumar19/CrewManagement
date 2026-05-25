"""
SQLAlchemy ORM model for crew records.

Derived from the `CrewMember` pydantic model in `database/models.py`: every
column below maps to a `CrewMember` field, so the dicts returned by the
repository match the API/contract shape. The one extra column, `pool`, is a
discriminator ('signon' | 'signoff') that lets a single `crew` table hold both
crew pools (the repository and seed script filter on it).
"""
from sqlalchemy import Column, Float, Integer, JSON, String

from database.db import Base


class Crew(Base):
    __tablename__ = "crew"

    crew_id = Column(String, primary_key=True)            # CrewMember.crew_id
    pool = Column(String, nullable=False, index=True)     # 'signon' | 'signoff' (discriminator)
    name = Column(String, nullable=False)                 # CrewMember.name
    rank = Column(String)                                 # CrewMember.rank
    grade = Column(String)                                # CrewMember.grade
    nationality = Column(String)                          # CrewMember.nationality
    vessel = Column(String)                               # CrewMember.vessel
    port = Column(String)                                 # CrewMember.port
    joining_date = Column(String, nullable=True)          # CrewMember.joining_date
    medical_expiry = Column(String, nullable=True)        # CrewMember.medical_expiry
    passport_expiry = Column(String, nullable=True)       # CrewMember.passport_expiry
    stcw_status = Column(String, default="Valid")         # CrewMember.stcw_status
    visa_status = Column(String, default="Valid")         # CrewMember.visa_status
    availability = Column(String, nullable=True)          # CrewMember.availability
    experience_years = Column(Integer, nullable=True)     # CrewMember.experience_years
    certifications = Column(JSON, nullable=True)          # CrewMember.certifications (List[str])
    match_score = Column(Float, nullable=True)            # CrewMember.match_score
    match_reason = Column(String, nullable=True)          # CrewMember.match_reason
    status = Column(String, default="Available")          # CrewMember.status

    def to_dict(self) -> dict:
        return {
            "crew_id": self.crew_id,
            "name": self.name,
            "rank": self.rank,
            "grade": self.grade,
            "nationality": self.nationality,
            "vessel": self.vessel,
            "port": self.port,
            "joining_date": self.joining_date,
            "medical_expiry": self.medical_expiry,
            "passport_expiry": self.passport_expiry,
            "stcw_status": self.stcw_status,
            "visa_status": self.visa_status,
            "availability": self.availability,
            "experience_years": self.experience_years,
            "certifications": self.certifications or [],
            "match_score": self.match_score,
            "match_reason": self.match_reason,
            "status": self.status,
        }
