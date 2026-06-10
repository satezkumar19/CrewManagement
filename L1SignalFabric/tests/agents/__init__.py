"""L1 SignalFabric agent harness.

Two cooperating "agents" that exercise and judge the ingress pipeline:

  * :class:`~tests.agents.test_agent.TestAgent` — runs every scenario (Slack,
    ERP, Email, Bus, L2) **plus the email + SharePoint live-integration target
    scenarios** and reports PASS / FAIL for each, individually.
  * :class:`~tests.agents.critic_agent.CriticAgent` — validates the input and
    output of each scenario against the canonical ``SignalEvent`` contract, then
    reports what the pipeline has today, what must be corrected/built for the
    email + SharePoint live integration, and how to make it more advanced.

Run them:

    python -m tests.agents.test_agent
    python -m tests.agents.critic_agent
"""

from .scenarios import ScenarioResult, run_all  # noqa: F401
