"""GraphSubscriptionManager: request shape + lifecycle (no network)."""

import re

from connectors.common.msgraph_subscriptions import (
    GraphSubscriptionManager,
    iso_expiration,
)


class _FakeGraph:
    """Captures the calls the manager makes against GraphClient."""

    def __init__(self):
        self.calls = []

    def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return {"id": "sub-1", **(json or {})}

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"value": [{"id": "sub-1", "resource": "users/x/messages"}]}

    def patch(self, path, json=None):
        self.calls.append(("PATCH", path, json))
        return {"id": "sub-1", **(json or {})}

    def delete(self, path):
        self.calls.append(("DELETE", path, None))
        return {}


def _mgr():
    m = GraphSubscriptionManager.__new__(GraphSubscriptionManager)
    m._graph = _FakeGraph()
    return m


def test_iso_expiration_is_utc_zulu():
    ts = iso_expiration(60)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts), ts


def test_create_builds_graph_subscription_body():
    m = _mgr()
    m.create(resource="users/x@y.com/messages", change_type="created",
             notification_url="https://h/outlook/webhook", client_state="s3cr3t",
             minutes=120)
    method, path, body = m._graph.calls[0]
    assert (method, path) == ("POST", "/subscriptions")
    assert body["resource"] == "users/x@y.com/messages"
    assert body["changeType"] == "created"
    assert body["notificationUrl"] == "https://h/outlook/webhook"
    assert body["clientState"] == "s3cr3t"
    assert body["expirationDateTime"].endswith("Z")


def test_create_omits_empty_client_state():
    m = _mgr()
    m.create(resource="drives/d/root", change_type="updated",
             notification_url="https://h/sharepoint/webhook", client_state="")
    _, _, body = m._graph.calls[0]
    assert "clientState" not in body


def test_renew_patches_expiration_only():
    m = _mgr()
    m.renew("sub-1", minutes=90)
    method, path, body = m._graph.calls[0]
    assert (method, path) == ("PATCH", "/subscriptions/sub-1")
    assert set(body) == {"expirationDateTime"}


def test_list_and_delete():
    m = _mgr()
    assert m.list()[0]["id"] == "sub-1"
    m.delete("sub-1")
    assert m._graph.calls[-1] == ("DELETE", "/subscriptions/sub-1", None)
