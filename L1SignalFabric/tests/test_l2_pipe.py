"""Full-pipe wiring: L2 store projection + /demo/inject end-to-end + per-stage totals."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.app import create_app
from core.signal import Lineage, SignalEvent, SourceSystem
from l2.store import L2JsonlStore


def _ev(source, entity, *, signoff=False, **data):
    md = {"l2Intent": "CREATE_SIGNOFF_EVENT"} if signoff else {}
    return SignalEvent(
        entity=entity, key={"k": "1"}, source_system=source, tenant_id="t",
        data=data, timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
        lineage=Lineage(extraction_id="x", source_sequence=1), metadata=md,
    )


def test_l2_projection(tmp_path):
    store = L2JsonlStore(str(tmp_path / "l2.jsonl"))
    slack = store.append(_ev(SourceSystem.SLACK, "message", user="U1", channel="C1",
                             text="Morning all — ETA update: MV Pacific Star alongside Singapore by 0600 local."))
    # the Slack message body is carried verbatim into the edge props (not dropped)
    assert slack["props"]["body"].startswith("Morning all")
    store.append(_ev(SourceSystem.EMAIL, "email", signoff=True, subject="Sign-Off Notification"))
    store.append(_ev(SourceSystem.CREW_DB, "crew", crew_id="CR-1"))
    c = store.counts()
    assert c["total"] == 3
    assert c["by_kind"]["edge"] == 1 and c["by_kind"]["signoff_event"] == 1 and c["by_kind"]["node"] == 1
    assert c["signoff"] == 1
    assert (tmp_path / "l2.jsonl").read_text().count("\n") == 3  # append-only JSONL


def test_demo_inject_flows_through_whole_pipe(tmp_path):
    # point the L2 store at a temp file via env-free settings override
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(tmp_path / "l2.jsonl")
    client = TestClient(create_app(settings=cfg))

    r = client.post("/demo/inject")
    assert r.status_code == 200
    body = r.json()
    assert body["injected"] == 3                      # one slack + one email + one erp
    sources = {t["source"] for t in body["trace"]}
    assert sources == {"slack", "email", "erp"}

    # each trace item carries raw (ingress) → normalized → L2 record
    for item in body["trace"]:
        assert item["raw"] and item["normalized"]["source_system"] in {"SLACK", "EMAIL", "CREW_DB"}
        assert item["l2"]["kind"] in {"edge", "signoff_event", "node"}
    # the email is a sign-off → SignOffEvent node in L2
    assert any(t["l2"]["kind"] == "signoff_event" for t in body["trace"])
    # the ERP crew row projects to an L2 node
    assert any(t["source"] == "erp" and t["l2"]["kind"] == "node" for t in body["trace"])
    assert body["l2_store"]["total"] == 3 and body["l2_store"]["signoff"] == 1


def test_healthz_reports_l2_and_stages(tmp_path):
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(tmp_path / "l2.jsonl")
    client = TestClient(create_app(settings=cfg))

    client.post("/demo/inject")
    h = client.get("/healthz").json()
    assert h["status"] == "ok"
    assert h["l2_records"] == 3

    st = client.get("/demo/status").json()
    t = st["totals"]
    # the four pipe stages all advanced: ingress -> normalized -> bus -> l2
    assert t["ingress"] >= 3 and t["normalized"] == 3 and t["bus"] == 3 and t["l2"] == 3
    assert t["signoff"] == 1
