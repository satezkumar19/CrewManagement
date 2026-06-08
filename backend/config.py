from pydantic_settings import BaseSettings
from typing import Optional
import json
import os


class Settings(BaseSettings):
    app_name: str = "Maritime Crew Orchestrator"
    app_version: str = "1.0.0"
    debug: bool = True

    # Anthropic
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = "claude-sonnet-4-6"
    claude_model_fast: str = "claude-haiku-4-5-20251001"

    # Managed Agents (client.beta.agents / sessions / environments).
    # The persisted environment + coordinator agent are created ONCE by
    # scripts/setup_managed_agents.py and their IDs cached in managed_agents_ids_file.
    # They can also be injected via env vars for container deploys.
    managed_agents_ids_file: str = os.getenv(
        "MANAGED_AGENTS_IDS_FILE",
        os.path.join(os.path.dirname(__file__), "managed_agents.json"),
    )
    managed_environment_id: str = os.getenv("MANAGED_ENVIRONMENT_ID", "")
    managed_coordinator_agent_id: str = os.getenv("MANAGED_COORDINATOR_AGENT_ID", "")
    # Wall-clock guard for a single session turn (Phase 1 / Phase 2).
    session_turn_timeout_seconds: int = 300

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5432/maritime_crew"
    )

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    # Step 3: TTL (seconds) for the crew-list cache-aside entries. The crew pool
    # changes infrequently and update_crew() invalidates on every mutation, so a
    # 30-minute fallback expiry is a safe upper bound on staleness.
    crew_cache_ttl_seconds: int = int(os.getenv("CREW_CACHE_TTL_SECONDS", "1800"))
    # Step 4: browser HTTP cache window for the GET crew-list endpoints. Kept short
    # (the browser cache can't be invalidated server-side) so the dashboard's live
    # event-driven refresh isn't served a stale response for long. Layers under the
    # SWR client cache and the Redis cache-aside above.
    crew_http_cache_max_age_seconds: int = int(os.getenv("CREW_HTTP_CACHE_MAX_AGE", "60"))

    # Context graph backend: "fallback" (build the compliance subgraph in Python,
    # no extra infra — the default) or "age" (use the Apache AGE graph that lives
    # inside the same PostgreSQL instance; requires an AGE-enabled image + a graph
    # seeded by scripts/seed_graph.py). See database/graph_db.py.
    graph_backend: str = os.getenv("GRAPH_BACKEND", "fallback")

    # Structural-embedding similarity backend (L4 #3): "fallback" (compute cosine in
    # Python over the JSON embeddings — no extra infra, the default) or "pgvector"
    # (use the pgvector `<=>` operator inside PostgreSQL; requires the pgvector image
    # — see L2Knowledge_graph/deploy/postgres-age.Dockerfile). See
    # database/embedding_repository.py.
    vector_backend: str = os.getenv("VECTOR_BACKEND", "fallback")

    # Email / SMTP — the Notification Agent sends real mail here.
    # Defaults target MailHog (dev SMTP sink: SMTP on 1025, web UI on http://localhost:8025).
    mail_enabled: bool = os.getenv("MAIL_ENABLED", "true").lower() == "true"
    smtp_host: str = os.getenv("SMTP_HOST", "localhost")
    smtp_port: int = int(os.getenv("SMTP_PORT", "1025"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "false").lower() == "true"
    smtp_from: str = os.getenv("SMTP_FROM", "notifications@marinecrewos.local")
    # Names (e.g. "Captain", "Shore Manager") are turned into <slug>@<this-domain>.
    mail_default_domain: str = os.getenv("MAIL_DEFAULT_DOMAIN", "marinecrewos.local")

    # Langfuse (optional observability)
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_host: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Agent config
    max_agent_tokens: int = 4096
    max_agent_retries: int = 3
    agent_timeout_seconds: int = 120
    confidence_threshold: float = 0.75

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def _load_managed_ids_from_file() -> None:
    """Populate Managed Agents IDs from the cache file written by setup, unless
    already supplied via env vars (env vars win)."""
    if settings.managed_environment_id and settings.managed_coordinator_agent_id:
        return
    path = settings.managed_agents_ids_file
    if not path or not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return
    settings.managed_environment_id = (
        settings.managed_environment_id or data.get("environment_id", "")
    )
    settings.managed_coordinator_agent_id = (
        settings.managed_coordinator_agent_id or data.get("coordinator_agent_id", "")
    )


_load_managed_ids_from_file()
