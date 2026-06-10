"""Maritime crew-operations world generator.

Generates a large, coherent stream of *raw source events* for the three L1
SignalFabric focus sources, in the exact shapes the connectors ingest:

  * slack — Slack Events API ``event_callback`` envelopes (message / reaction /
            member_joined_channel) → OrgMap tribal knowledge
  * email — Gmail-style metadata records (from/to/cc/subject/thread/sent_at,
            **no body**), including crew **sign-off** notifications
  * erp   — transactional-outbox change rows for Crew DB / Contract-CLM /
            Vessel-Port DB

Coherence: crew-change events are emitted as multi-source *clusters* (an ERP
status change + a sign-off email + a #crew-changes Slack post + contract
completion + the reliever's onboarding) so the demo tells one story across all
sources — the way the Freight-invoice generator clusters a voyage lifecycle.

Deterministic: seeded RNG, no wall clock. Re-running with the same seed/anchor
yields byte-identical output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


# --------------------------------------------------------------------------- #
# Reference pools
# --------------------------------------------------------------------------- #
VESSEL_NAMES = [
    "MV Cygnus Trader", "MV Orion Star", "MV Pacific Dawn", "MV Aegean Pearl",
    "MV Nordic Spirit", "MV Coral Voyager", "MV Atlantic Crown", "MV Indus Pride",
    "MV Sapphire Bay", "MV Meridian Sun", "MV Polar Quest", "MV Andaman Breeze",
    "MV Celtic Wave", "MV Bosphorus", "MV Kraken", "MV Tasman Glory",
    "MV Levant Star", "MV Hibernia", "MV Zephyr Gale", "MV Solent Castle",
]

PORTS = [
    "Singapore", "Rotterdam", "Houston", "Fujairah", "Busan", "Santos",
    "Antwerp", "Mumbai (JNPT)", "Shanghai", "Algeciras", "Durban", "Colombo",
    "Hamburg", "Ulsan", "Galveston",
]

# rank -> (count weight per vessel, department)
RANKS = [
    ("Master", "deck"), ("Chief Officer", "deck"), ("2nd Officer", "deck"),
    ("3rd Officer", "deck"), ("Chief Engineer", "engine"), ("2nd Engineer", "engine"),
    ("3rd Engineer", "engine"), ("4th Engineer", "engine"), ("Electrician", "engine"),
    ("Bosun", "deck"), ("AB Seaman", "deck"), ("Ordinary Seaman", "deck"),
    ("Oiler", "engine"), ("Wiper", "engine"), ("Chief Cook", "catering"),
    ("Messman", "catering"),
]

NATIONALITIES = ["IN", "PH", "UA", "PL", "ID", "MM", "GR", "HR", "RO", "GB", "TR", "CN"]

FIRST = [
    "Arjun", "Marco", "Dmytro", "Liam", "Ramon", "Aung", "Nikos", "Ivan", "Wei",
    "Sergii", "Tomasz", "Budi", "Cemal", "Rahul", "Pedro", "Andrei", "Kiran",
    "Joon", "Hassan", "Diego", "Mateusz", "Carlo", "Viktor", "Sanjay", "Emre",
]
LAST = [
    "Sharma", "Rossi", "Kovalenko", "Walsh", "Cruz", "Hlaing", "Papadopoulos",
    "Petrov", "Chen", "Bondarenko", "Nowak", "Santoso", "Yilmaz", "Nair",
    "Silva", "Popescu", "Reddy", "Park", "Khan", "Fernandez", "Wójcik",
    "Greco", "Horvat", "Iyer",
]

# Shore staff (Slack/email participants that are NOT crew)
SHORE_STAFF = [
    ("Priya Menon", "crewing-manager"), ("Tom Becker", "crewing-manager"),
    ("Elena Marsh", "fleet-ops"), ("Sok Lim", "fleet-ops"),
    ("Daniel Reyes", "compliance"), ("Aisha Karim", "compliance"),
    ("Greg Holt", "port-agent"), ("Mira Sato", "manning-agency"),
]

SLACK_CHANNELS = [
    ("C-OPS", "#fleet-ops"),
    ("C-CREW", "#crew-changes"),
    ("C-PORT", "#port-ops"),
    ("C-COMP", "#compliance"),
    ("C-TECH", "#technical"),
]

REACTIONS = ["white_check_mark", "eyes", "thumbsup", "rotating_light", "anchor", "ship"]

OPS_CHATTER = [
    "{vessel} ETA {port} revised to {date} — pilot booked.",
    "Bunkers confirmed for {vessel} at {port}.",
    "Crew change window for {vessel} at {port} looks tight, agent on standby.",
    "{vessel} cleared inward at {port}, gangway watch set.",
    "Provisions order for {vessel} placed ahead of {port} call.",
    "Reminder: STCW rest-hours review for {vessel} due this week.",
    "Port agent at {port} confirms berth prospects for {vessel}.",
    "Awaiting visa confirmation for joiners on {vessel} at {port}.",
    "{vessel} sailed {port}, next port ETA to follow.",
    "Medical clinic slot booked for {vessel} joiner at {port}.",
]


@dataclass
class GenConfig:
    num_vessels: int = 20
    crew_per_vessel: int = 14          # complement → ~280 active berths
    crew_changes: int = 160            # multi-source sign-off clusters
    ambient_slack: int = 1500          # tribal-knowledge chatter
    routine_emails: int = 420
    weeks_back: int = 6
    weeks_forward: int = 1
    seed: int = 42


@dataclass
class WorldEvent:
    occurred_at: datetime
    source: str        # "slack" | "email" | "erp"
    kind: str          # human-readable sub-kind
    raw: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "occurred_at": self.occurred_at.isoformat(),
            "source": self.source,
            "kind": self.kind,
            "raw": self.raw,
        }


class WorldGenerator:
    def __init__(self, *, anchor: datetime, config: GenConfig) -> None:
        self.anchor = anchor
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.start = anchor - timedelta(weeks=config.weeks_back)
        self.end = anchor + timedelta(weeks=config.weeks_forward)

        self._evt_seq = 0      # slack event_id / email message_id counter
        self._ts_frac = 0      # keep slack `ts` unique within a second

        # entity registries (also written to entities.json)
        self.vessels: dict[str, str] = {}
        self.ports: list[str] = list(PORTS)
        self.crew: dict[str, dict[str, Any]] = {}
        self.channels: dict[str, str] = {cid: name for cid, name in SLACK_CHANNELS}
        self.users: dict[str, dict[str, str]] = {}   # user_id -> {name, role, email}

        self._build_entities()

    # ----------------------------------------------------------------- helpers
    def _rand_dt(self, lo: datetime, hi: datetime) -> datetime:
        span = (hi - lo).total_seconds()
        return lo + timedelta(seconds=self.rng.uniform(0, span))

    def _slack_ts(self, at: datetime) -> str:
        self._ts_frac = (self._ts_frac + 1) % 1_000_000
        return f"{at.timestamp():.0f}.{self._ts_frac:06d}"

    def _next_id(self, prefix: str) -> str:
        self._evt_seq += 1
        return f"{prefix}{self._evt_seq:08d}"

    def _person_name(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    @staticmethod
    def _email_of(name: str, domain: str) -> str:
        slug = name.lower().replace(" ", ".").replace("(", "").replace(")", "")
        return f"{slug}@{domain}"

    # --------------------------------------------------------------- entities
    def _build_entities(self) -> None:
        for i in range(self.cfg.num_vessels):
            vid = f"VSL-{i+1:03d}"
            self.vessels[vid] = VESSEL_NAMES[i % len(VESSEL_NAMES)]

        cseq = 0
        for vid, vname in self.vessels.items():
            port = self.rng.choice(self.ports)
            for _ in range(self.cfg.crew_per_vessel):
                cseq += 1
                cid = f"CR-{cseq:04d}"
                rank, dept = self.rng.choice(RANKS)
                name = self._person_name()
                sign_on = self.anchor - timedelta(days=self.rng.randint(30, 230))
                self.crew[cid] = {
                    "crew_id": cid,
                    "name": name,
                    "rank": rank,
                    "department": dept,
                    "nationality": self.rng.choice(NATIONALITIES),
                    "vessel_id": vid,
                    "vessel": vname,
                    "port": port,
                    "status": "onboard",
                    "sign_on_date": sign_on.date().isoformat(),
                }
                # crew member is also a Slack/email participant
                uid = f"U-{cseq:04d}"
                self.users[uid] = {
                    "name": name, "role": rank,
                    "email": self._email_of(name, "crew.fleet.example"),
                    "crew_id": cid,
                }

        # shore staff users
        for j, (name, role) in enumerate(SHORE_STAFF):
            uid = f"US-{j+1:03d}"
            self.users[uid] = {
                "name": name, "role": role,
                "email": self._email_of(name, "fleet.example"),
                "crew_id": "",
            }

    def _crew_user_id(self, crew_id: str) -> str:
        for uid, u in self.users.items():
            if u.get("crew_id") == crew_id:
                return uid
        return "U-0000"

    def _shore_user(self, role: str | None = None) -> str:
        pool = [uid for uid, u in self.users.items()
                if uid.startswith("US-") and (role is None or u["role"] == role)]
        return self.rng.choice(pool) if pool else "US-001"

    # ----------------------------------------------------------- raw builders
    def _slack_message(self, at: datetime, channel_id: str, user_id: str, text: str) -> WorldEvent:
        return WorldEvent(at, "slack", "message", {
            "type": "event_callback", "event_id": self._next_id("Ev"), "team_id": "T-FLEET",
            "event": {"type": "message", "channel": channel_id, "user": user_id,
                      "text": text, "ts": self._slack_ts(at)},
        })

    def _slack_reaction(self, at: datetime, channel_id: str, user_id: str, target_ts: str) -> WorldEvent:
        return WorldEvent(at, "slack", "reaction_added", {
            "type": "event_callback", "event_id": self._next_id("Ev"), "team_id": "T-FLEET",
            "event": {"type": "reaction_added", "user": user_id,
                      "reaction": self.rng.choice(REACTIONS),
                      "item": {"type": "message", "channel": channel_id, "ts": target_ts},
                      "event_ts": self._slack_ts(at)},
        })

    def _slack_join(self, at: datetime, channel_id: str, user_id: str) -> WorldEvent:
        return WorldEvent(at, "slack", "member_joined_channel", {
            "type": "event_callback", "event_id": self._next_id("Ev"), "team_id": "T-FLEET",
            "event": {"type": "member_joined_channel", "user": user_id,
                      "channel": channel_id, "event_ts": self._slack_ts(at)},
        })

    def _email(self, at: datetime, *, kind: str, sender_uid: str, to_uids: list[str],
               subject: str, thread_id: str, labels: list[str]) -> WorldEvent:
        def addr(uid: str) -> dict[str, str]:
            u = self.users.get(uid, {"name": "Unknown", "email": "unknown@fleet.example"})
            return {"name": u["name"], "address": u["email"]}
        return WorldEvent(at, "email", kind, {
            "message_id": f"<{self._next_id('msg')}@mail.fleet.example>",
            "thread_id": thread_id,
            "from": addr(sender_uid),
            "to": [addr(u) for u in to_uids],
            "cc": [],
            "subject": subject,
            "sent_at": at.isoformat(),
            "labels": labels,
            # NOTE: metadata only — body is deliberately never generated/ingested.
        })

    def _erp(self, at: datetime, table: str, op: str, data: dict[str, Any]) -> WorldEvent:
        # `seq` is assigned later by the seeder in global time order (outbox property).
        return WorldEvent(at, "erp", f"{table}.{op}", {
            "table": table, "op": op, "occurred_at": at.isoformat(), "data": data,
        })

    # ------------------------------------------------------------------ build
    def generate(self) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        events += self._initial_erp_snapshot()
        events += self._vessel_position_stream()
        events += self._crew_change_clusters()
        events += self._ambient_slack()
        events += self._routine_emails()
        events.sort(key=lambda e: e.occurred_at)
        return events

    def _initial_erp_snapshot(self) -> list[WorldEvent]:
        """Seed the ERP outbox with the existing fleet state at window start
        (crew master rows + active contracts + vessel/port rows)."""
        out: list[WorldEvent] = []
        t0 = self.start
        for vid, vname in self.vessels.items():
            crew0 = next((c for c in self.crew.values() if c["vessel_id"] == vid), None)
            port = crew0["port"] if crew0 else self.rng.choice(self.ports)
            out.append(self._erp(t0, "vessel_port", "insert", {
                "vessel_id": vid, "vessel": vname, "port": port,
                "status": "at_sea", "eta": None, "voyage_no": f"V-{self.rng.randint(400, 499)}",
            }))
        for cid, c in self.crew.items():
            out.append(self._erp(t0, "crew", "insert", {
                "crew_id": cid, "name": c["name"], "rank": c["rank"],
                "nationality": c["nationality"], "vessel_id": c["vessel_id"],
                "vessel": c["vessel"], "status": "onboard", "port": c["port"],
                "sign_on_date": c["sign_on_date"],
            }))
            out.append(self._erp(t0, "contract", "insert", {
                "contract_id": f"K-{cid[3:]}", "crew_id": cid, "vessel_id": c["vessel_id"],
                "type": "SEA", "state": "active", "sign_on_date": c["sign_on_date"],
                "sign_off_date": None, "duration_months": self.rng.choice([4, 6, 8, 9]),
            }))
        return out

    def _vessel_position_stream(self) -> list[WorldEvent]:
        """Each vessel emits periodic position/status updates across the window."""
        out: list[WorldEvent] = []
        statuses = ["at_sea", "approaching", "berthed", "departed"]
        for vid, vname in self.vessels.items():
            t = self.start + timedelta(hours=self.rng.randint(6, 60))
            voyage = f"V-{self.rng.randint(400, 499)}"
            while t < self.end:
                port = self.rng.choice(self.ports)
                status = self.rng.choice(statuses)
                eta = (t + timedelta(days=self.rng.randint(1, 6))).date().isoformat()
                out.append(self._erp(t, "vessel_port", "update", {
                    "vessel_id": vid, "vessel": vname, "port": port,
                    "status": status, "eta": eta, "voyage_no": voyage,
                }))
                t += timedelta(hours=self.rng.randint(36, 96))
        return out

    def _crew_change_clusters(self) -> list[WorldEvent]:
        """The hero flow: each cluster = sign_off_due → sign-off email →
        #crew-changes post → signed_off → contract completed → reliever onboarding.
        The sign-off email is what L2 turns into a SignOffEvent node."""
        out: list[WorldEvent] = []
        crew_ids = list(self.crew.keys())
        crewing = "US-001"
        for n in range(self.cfg.crew_changes):
            cid = self.rng.choice(crew_ids)
            c = self.crew[cid]
            vessel, vid = c["vessel"], c["vessel_id"]
            port = self.rng.choice(self.ports)
            base = self._rand_dt(self.start + timedelta(days=2), self.end - timedelta(hours=6))
            uid = self._crew_user_id(cid)
            thread = f"thr-signoff-{cid}-{n}"
            reliever_name = self._person_name()

            # 1) ERP: sign-off due (a few days ahead)
            due = base
            out.append(self._erp(due, "crew", "update", {
                "crew_id": cid, "name": c["name"], "rank": c["rank"],
                "vessel_id": vid, "vessel": vessel, "status": "sign_off_due",
                "port": port, "sign_off_due": (due + timedelta(days=4)).date().isoformat(),
            }))

            # 2) Email: sign-off notification (→ SignOffEvent trigger)
            t_mail = due + timedelta(hours=self.rng.randint(2, 20))
            out.append(self._email(
                t_mail, kind="sign_off", sender_uid=self._shore_user("crewing-manager"),
                to_uids=[uid, self._shore_user("fleet-ops")],
                subject=f"Sign-Off Notification: {c['name']} ({c['rank']}) — {vessel} at {port}",
                thread_id=thread, labels=["crew/sign-off"],
            ))

            # 3) Slack: #crew-changes confirmation
            t_slack = t_mail + timedelta(hours=self.rng.randint(1, 10))
            msg = self._slack_message(
                t_slack, "C-CREW", crewing,
                f"Sign-off confirmed: {c['name']} ({c['rank']}) ex {vessel} at {port}. "
                f"Reliever: {reliever_name}. Docs with compliance.")
            out.append(msg)
            # a couple of reactions on it
            for _ in range(self.rng.randint(0, 3)):
                out.append(self._slack_reaction(
                    t_slack + timedelta(minutes=self.rng.randint(3, 90)),
                    "C-CREW", self._shore_user(), msg.raw["event"]["ts"]))

            # 4) ERP: signed off  +  5) contract completed
            t_off = t_slack + timedelta(days=self.rng.randint(2, 5))
            out.append(self._erp(t_off, "crew", "update", {
                "crew_id": cid, "name": c["name"], "rank": c["rank"],
                "vessel_id": vid, "vessel": vessel, "status": "signed_off", "port": port,
            }))
            out.append(self._erp(t_off + timedelta(minutes=20), "contract", "update", {
                "contract_id": f"K-{cid[3:]}", "crew_id": cid, "vessel_id": vid,
                "type": "SEA", "state": "completed",
                "sign_off_date": t_off.date().isoformat(),
            }))

            # 6) reliever onboarding (new crew row + active contract)
            rid = f"CR-9{n:03d}"
            out.append(self._erp(t_off + timedelta(hours=2), "crew", "insert", {
                "crew_id": rid, "name": reliever_name, "rank": c["rank"],
                "vessel_id": vid, "vessel": vessel, "status": "onboard", "port": port,
                "sign_on_date": t_off.date().isoformat(),
            }))
            out.append(self._erp(t_off + timedelta(hours=2, minutes=15), "contract", "insert", {
                "contract_id": f"K-9{n:03d}", "crew_id": rid, "vessel_id": vid,
                "type": "SEA", "state": "active", "sign_on_date": t_off.date().isoformat(),
                "sign_off_date": None, "duration_months": self.rng.choice([4, 6, 8]),
            }))
        return out

    def _ambient_slack(self) -> list[WorldEvent]:
        """Tribal-knowledge chatter + occasional joins across ops channels."""
        out: list[WorldEvent] = []
        chatter_channels = ["C-OPS", "C-PORT", "C-COMP", "C-TECH"]
        for _ in range(self.cfg.ambient_slack):
            at = self._rand_dt(self.start, self.end)
            ch = self.rng.choice(chatter_channels)
            uid = self._shore_user() if self.rng.random() < 0.6 else self.rng.choice(list(self.users))
            text = self.rng.choice(OPS_CHATTER).format(
                vessel=self.rng.choice(list(self.vessels.values())),
                port=self.rng.choice(self.ports),
                date=(at + timedelta(days=self.rng.randint(1, 7))).date().isoformat(),
            )
            msg = self._slack_message(at, ch, uid, text)
            out.append(msg)
            if self.rng.random() < 0.25:
                out.append(self._slack_reaction(
                    at + timedelta(minutes=self.rng.randint(2, 120)),
                    ch, self._shore_user(), msg.raw["event"]["ts"]))
        # a handful of channel joins
        for _ in range(max(1, self.cfg.num_vessels // 3)):
            at = self._rand_dt(self.start, self.end)
            out.append(self._slack_join(at, self.rng.choice([c for c, _ in SLACK_CHANNELS]),
                                        self.rng.choice(list(self.users))))
        return out

    def _routine_emails(self) -> list[WorldEvent]:
        """Non-sign-off crew-ops email metadata (port agent, manning, compliance)."""
        out: list[WorldEvent] = []
        subjects = [
            "Berth confirmation — {vessel} at {port}",
            "Joiner visa status — {vessel}",
            "Provisions PO acknowledgement — {vessel}",
            "Crew matrix update — {vessel}",
            "STCW certificate reminder — {vessel}",
            "Port agent appointment — {port}",
        ]
        for _ in range(self.cfg.routine_emails):
            at = self._rand_dt(self.start, self.end)
            vessel = self.rng.choice(list(self.vessels.values()))
            port = self.rng.choice(self.ports)
            sender = self._shore_user()
            out.append(self._email(
                at, kind="routine", sender_uid=sender,
                to_uids=[self._shore_user("fleet-ops"), self._shore_user("crewing-manager")],
                subject=self.rng.choice(subjects).format(vessel=vessel, port=port),
                thread_id=f"thr-{self._next_id('t')}", labels=["crew/ops"],
            ))
        return out

    # entity export
    def entities(self) -> dict[str, Any]:
        return {
            "vessels": self.vessels,
            "ports": self.ports,
            "channels": self.channels,
            "users": self.users,
            "crew": self.crew,
        }
