"""Unified L2 record — one envelope that feeds **every** L2 knowledge-graph map.

L1 SignalFabric emits a single canonical :class:`~core.signal.SignalEvent` per
source change (see :mod:`core.signal`). Downstream, L2 builds **several** graph
maps from that same stream, and each map wants a *different* shape:

  * **OrgMap**  — tribal-knowledge node/edge graph (who talks to whom, which crew
    a notice mentions). Contract: ``l2/orgmap.py::OrgMap.upsert``.
  * **EntityMap** — the canonical Crew / Vessel / Port / Certificate / Contract
    graph, MERGE-d per full crew record. Contract:
    ``L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`` → ``entity_map.py::build_entity_map``.
  * **OpsMap** — a per-case process-mining event log. Contract:
    ``L1_TO_L2_INGESTION_CONTRACT.md`` → ``ops_map.py::record_event``.

Rather than ship three parallel wire formats, L1 emits **one** :class:`L2Record`
envelope that carries shared provenance plus a list of **facets** — small typed
projections, each conforming to exactly one map's contract. A single source
change can produce 0..N facets (an ERP crew row → an Entity facet *and* an Org
node facet; a Slack sign-off notice → Org edge + Org sign-off + an Entity
``crew.upserted``). Every map consumer reads **only its own facet type** and
ignores the rest, so the maps stay decoupled while sharing one transport and one
idempotency key.

Design rules
------------
* **One envelope, many facets.** The header is map-agnostic provenance; each
  facet is self-describing (``map`` discriminator) and matches its map's doc.
* **Idempotent.** ``record_id`` (= :pyattr:`SignalEvent.dedup_id`) is the
  envelope MERGE key; each facet additionally carries its map's own business key
  (``crew_id`` / ``case_id`` / node id), so at-least-once redelivery is a no-op
  refresh in every map.
* **Additive / forward-compatible.** A new map = a new facet subclass + a new
  producer branch; existing consumers ignore unknown facets. No envelope change.
* **Full record, not a patch.** Entity facets carry the *complete* current crew
  record (the EntityMap contract re-MERGEs the whole thing); never a delta.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from core.signal import SignalEvent
from l2.store import L2JsonlStore, extract_crew_change

SCHEMA_VERSION = "l2-record/1.0"


class MapTarget(str, Enum):
    """Which L2 map a facet feeds."""

    ORG = "org"        # OrgMap.upsert        (node / edge / signoff_event)
    ENTITY = "entity"  # build_entity_map     (crew / contract / vessel / port record)
    OPS = "ops"        # record_event         (process-mining event-log row)


# --------------------------------------------------------------------------- #
# Facets — one typed projection per map. Discriminated on ``map``.             #
# --------------------------------------------------------------------------- #
class OrgFacet(BaseModel):
    """OrgMap projection — the existing node/edge/signoff_event record.

    1:1 with the dict ``L2JsonlStore.project`` returns today, so OrgMap.upsert
    consumes it unchanged.
    """

    map: Literal[MapTarget.ORG] = MapTarget.ORG
    kind: str                              # "node" | "edge" | "signoff_event"
    label: str                             # POSTED_IN | EMAILED | Crew | SignOffEvent | ...
    props: dict[str, Any] = Field(default_factory=dict)
    node_id: Optional[str] = None          # business key for kind="node"/"signoff_event"


class EntityFacet(BaseModel):
    """EntityMap trigger — a full crew/contract/vessel/port record to MERGE.

    ``record`` follows §3 of ``L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`` for crew
    events; for contract/vessel/port it carries that node's natural fields.
    """

    map: Literal[MapTarget.ENTITY] = MapTarget.ENTITY
    event: str                             # crew.upserted | crew.signed_on | crew.signed_off
                                           # | crew.deleted | contract.upserted
                                           # | vessel.upserted | port.upserted
    record: dict[str, Any] = Field(default_factory=dict)


class OpsFacet(BaseModel):
    """OpsMap event-log row — one process-mining event for a crew-change case.

    Follows §2 of ``L1_TO_L2_INGESTION_CONTRACT.md``. ``timestamp`` is taken
    from the envelope ``occurred_at`` (no need to repeat it here).
    """

    map: Literal[MapTarget.OPS] = MapTarget.OPS
    case_id: str                           # = workflow_id (the case join key)
    event_type: str                        # §3 vocabulary (workflow_created, agent_completed, ...)
    agent_name: Optional[str] = None       # required when event_type == "agent_completed"
    data: dict[str, Any] = Field(default_factory=dict)  # §4 curated keys


# Discriminated union: pydantic routes on the literal ``map`` field.
Facet = Union[OrgFacet, EntityFacet, OpsFacet]


# --------------------------------------------------------------------------- #
# The envelope                                                                 #
# --------------------------------------------------------------------------- #
class L2Record(BaseModel):
    """One normalized L2 record: shared provenance + per-map facets."""

    # ---- identity / provenance (map-agnostic; always present) ----
    record_id: str                         # = SignalEvent.dedup_id — envelope MERGE key
    schema_version: str = SCHEMA_VERSION
    tenant_id: str
    source_system: str                     # SLACK | GMAIL | ... | CREW_DB | CONTRACT_CLM | ...
    connector: Optional[str] = None        # slack | gmail | erp | database | ...
    entity: str                            # message | email | crew | contract | vessel_port | ...
    key: dict[str, Any] = Field(default_factory=dict)  # source-natural primary key
    operation: str = "DELTA"               # DELTA | SNAPSHOT | DELETE
    occurred_at: datetime                  # source valid time
    ingested_at: datetime                  # L1 receive time

    lineage: Optional[dict[str, Any]] = None
    raw: dict[str, Any] = Field(default_factory=dict)  # original SignalEvent.data (audit)

    # ---- the fan-out ----
    facets: list[Facet] = Field(default_factory=list)

    # convenience accessors for each map's consumer
    def facet(self, target: MapTarget) -> Optional[Facet]:
        return next((f for f in self.facets if f.map == target), None)

    def org_facets(self) -> list[OrgFacet]:
        return [f for f in self.facets if isinstance(f, OrgFacet)]


# --------------------------------------------------------------------------- #
# Projection — SignalEvent → L2Record (supersedes L2JsonlStore.project)        #
# --------------------------------------------------------------------------- #
# §3 crew-record fields lifted straight from an ERP crew row / parsed notice.
_CREW_FIELDS = (
    "crew_id", "pool", "name", "rank", "grade", "nationality",
    "port", "vessel", "status", "experience_years", "certifications", "joining_date",
)

_CONNECTOR = {
    "SLACK": "slack", "EMAIL": "email", "GMAIL": "gmail", "OUTLOOK": "outlook",
    "NOTION": "notion", "SHAREPOINT": "sharepoint", "DATABASE": "database",
    "CREW_DB": "erp", "CONTRACT_CLM": "erp", "VESSEL_PORT_DB": "erp",
}


def _org_facet(event: SignalEvent) -> OrgFacet:
    """Reuse the battle-tested OrgMap projection as the Org facet."""
    rec = L2JsonlStore.project(event)
    node_id = rec.get("id") if rec.get("kind") != "edge" else None
    return OrgFacet(kind=rec["kind"], label=rec["label"],
                    props=rec.get("props") or {}, node_id=node_id)


def _crew_record(data: dict[str, Any]) -> dict[str, Any]:
    """Pick the §3 crew-record fields that are present (declarative full record)."""
    return {k: data[k] for k in _CREW_FIELDS if data.get(k) not in (None, "", "NULL")}


def _entity_facet(event: SignalEvent, deleted: bool = False) -> Optional[EntityFacet]:
    """Derive an EntityMap facet when the record describes a graph entity.

    Two producers:
      * an ERP row (CREW_DB / CONTRACT_CLM / VESSEL_PORT_DB) → full entity record;
      * a Slack/e-mail sign-on/off **notice** whose parsed details name a crew
        member → ``crew.upserted`` from the lifted fields.

    The crew ``event`` is chosen from the record's own state, so one connector
    covers every EntityMap trigger:
      * ``deleted`` (operation = DELETE)        → ``crew.deleted``
      * ``pool = signoff``                      → ``crew.signed_off`` (+ Contract)
      * ``pool = signon`` with a real vessel    → ``crew.signed_on``  (assigned)
      * ``pool = signon`` unassigned/``Available`` → ``crew.upserted`` (candidate)
    """
    ss = event.source_system.value
    d = event.data or {}

    if ss == "CREW_DB":
        rec = _crew_record(d)
        if not rec.get("crew_id"):
            return None
        if deleted:
            return EntityFacet(event="crew.deleted",
                               record={"crew_id": rec["crew_id"]})
        pool = str(rec.get("pool", "")).lower()
        vessel = str(rec.get("vessel", "")).strip()
        assigned = bool(vessel) and vessel.lower() != "available"
        if pool == "signoff":
            event_name = "crew.signed_off"
        elif pool == "signon" and assigned:
            event_name = "crew.signed_on"
        else:
            event_name = "crew.upserted"
        return EntityFacet(event=event_name, record=rec)

    if ss == "CONTRACT_CLM":
        return EntityFacet(event="contract.upserted", record=d)

    if ss == "VESSEL_PORT_DB":
        # one table, two node kinds — a `type: "port"` row makes a Port, else Vessel.
        is_port = str(d.get("type", "")).lower() == "port" or (
            d.get("port") and not d.get("vessel"))
        return EntityFacet(event="port.upserted" if is_port else "vessel.upserted",
                           record=d)

    # parsed crew-change notice (Slack body / e-mail subject)
    crew = extract_crew_change(d.get("text") or d.get("subject"))
    if crew and crew.get("crew_member"):
        action = crew.get("action")
        event_name = {"sign_off": "crew.signed_off", "sign_on": "crew.signed_on"}.get(
            action, "crew.upserted")
        record = {
            "crew_id": crew.get("crew_id") or crew["crew_member"],
            "name": crew["crew_member"], "rank": crew.get("role"),
            "vessel": crew.get("vessel"), "port": crew.get("port"),
            "pool": "signoff" if action == "sign_off" else "signon",
        }
        return EntityFacet(event=event_name,
                           record={k: v for k, v in record.items() if v})
    return None


def _ops_facet(event: SignalEvent) -> Optional[OpsFacet]:
    """Derive an OpsMap facet when the event carries workflow/case semantics.

    L1 connectors don't synthesize workflow lifecycles, but two paths feed OpsMap:
      * a backend ``WorkflowService`` event relayed through L1 — it sets
        ``metadata.case_id`` + ``metadata.event_type`` (and ``agent_name``);
      * a crew-change **notice** carrying ``metadata.l2Intent =
        CREATE_SIGNOFF_EVENT`` with a ``workflow_id`` — projected as
        ``workflow_created`` so the case appears in the process model.
    """
    meta = event.metadata or {}
    case_id = meta.get("case_id") or meta.get("workflow_id")
    if not case_id:
        return None
    event_type = meta.get("event_type")
    if not event_type:
        if meta.get("l2Intent") == "CREATE_SIGNOFF_EVENT":
            event_type = "workflow_created"
        else:
            return None
    d = event.data or {}
    crew = extract_crew_change(d.get("text") or d.get("subject")) or {}
    data = {k: v for k, v in {
        "crew_name": crew.get("crew_member"), "rank": crew.get("role"),
        "vessel": crew.get("vessel"), "crew_id": crew.get("crew_id"),
        "pool": (event.data or {}).get("pool"),
    }.items() if v}
    return OpsFacet(case_id=str(case_id), event_type=event_type,
                    agent_name=meta.get("agent_name"), data=data)


def project_record(event: SignalEvent) -> L2Record:
    """Project one canonical ``SignalEvent`` into the unified ``L2Record``.

    Always emits the Org facet (backward-compatible with today's OrgMap sink);
    adds an Entity facet for entity-bearing records and an Ops facet for
    workflow-bearing ones. The facet list is the fan-out to every L2 map.
    """
    ss = event.source_system.value
    deleted = (event.metadata or {}).get("op") == "DELETE"
    facets: list[Facet] = [_org_facet(event)]
    if (ef := _entity_facet(event, deleted=deleted)) is not None:
        facets.append(ef)
    if (of := _ops_facet(event)) is not None:
        facets.append(of)

    return L2Record(
        record_id=event.dedup_id,
        tenant_id=event.tenant_id,
        source_system=ss,
        connector=_CONNECTOR.get(ss),
        entity=event.entity,
        key=event.key,
        operation="DELETE" if (event.metadata or {}).get("op") == "DELETE" else event.operation.value,
        occurred_at=event.timestamp,
        ingested_at=event.extracted_at,
        lineage=event.lineage.model_dump() if event.lineage else None,
        raw=event.data or {},
        facets=facets,
    )
