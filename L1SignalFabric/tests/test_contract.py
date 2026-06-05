"""The connectors honor the EventStreamConnector contract."""

from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from connectors.slack import SlackConnector
from core.connector import EventStreamConnector
from core.signal import SourceSystem


def test_slack_is_connector():
    c = SlackConnector(tenant_id="t")
    assert isinstance(c, EventStreamConnector)
    assert c.name == "slack"
    assert c.source_system == SourceSystem.SLACK


def test_erp_is_connector():
    c = ErpConnector(tenant_id="t", adapter=InMemoryOutboxAdapter())
    assert isinstance(c, EventStreamConnector)
    assert c.name == "erp"
    # representative; emitted events carry per-table source systems
    assert c.source_system == SourceSystem.CREW_DB
    assert c.position() == 0  # fresh watermark
