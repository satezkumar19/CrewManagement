"""Pluggable source connectors for L1 SignalFabric.

Each connector implements ``core.EventStreamConnector``. Day-1 ships the Slack
(push) and ERP (pull/outbox) skeletons; Gmail (push, Day-3) drops in the same way.
"""

from .erp import ErpConnector
from .slack import SlackConnector

__all__ = ["SlackConnector", "ErpConnector"]
