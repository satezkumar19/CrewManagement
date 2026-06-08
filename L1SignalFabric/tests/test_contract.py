"""The connectors honor the EventStreamConnector contract."""

import pytest

from connectors.database import DatabaseConnector, InMemoryOutboxAdapter as DbAdapter
from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from connectors.gmail import GmailConnector
from connectors.notion import NotionConnector
from connectors.outlook import OutlookConnector
from connectors.sharepoint import SharePointConnector
from connectors.slack import SlackBackfillConnector, SlackConnector
from core.connector import EventStreamConnector
from core.signal import SourceSystem


def test_slack_is_connector():
    c = SlackConnector(tenant_id="t")
    assert isinstance(c, EventStreamConnector)
    assert c.name == "slack"
    assert c.source_system == SourceSystem.SLACK


@pytest.mark.parametrize("connector,name,source", [
    (SlackBackfillConnector(tenant_id="t", client=None), "slack-backfill", SourceSystem.SLACK),
    (NotionConnector(tenant_id="t", client=None), "notion", SourceSystem.NOTION),
    (GmailConnector(tenant_id="t"), "gmail", SourceSystem.GMAIL),
    (OutlookConnector(tenant_id="t"), "outlook", SourceSystem.OUTLOOK),
    (SharePointConnector(tenant_id="t"), "sharepoint", SourceSystem.SHAREPOINT),
    (DatabaseConnector(tenant_id="t", adapter=DbAdapter()), "database", SourceSystem.DATABASE),
])
def test_real_connectors_honor_contract(connector, name, source):
    assert isinstance(connector, EventStreamConnector)
    assert connector.name == name
    assert connector.source_system == source


def test_erp_is_connector():
    c = ErpConnector(tenant_id="t", adapter=InMemoryOutboxAdapter())
    assert isinstance(c, EventStreamConnector)
    assert c.name == "erp"
    # representative; emitted events carry per-table source systems
    assert c.source_system == SourceSystem.CREW_DB
    assert c.position() == 0  # fresh watermark
