# Apache AGE + pgvector on PostgreSQL 16 (Day-1 infra bring-up).
#
# The local stack needs ONE Postgres that provides both:
#   * `vector` (pgvector)  — semantic memory  (already in the base image)
#   * `age`    (Apache AGE) — the L2 knowledge graph (built here from source)
#
# There is no official image with both, so we compile AGE for PG16 on top of the
# pgvector image. AGE's `PG16` branch targets PostgreSQL 16.
FROM pgvector/pgvector:pg16

# AGE branch that targets PostgreSQL 16. Override with --build-arg AGE_REF=... to
# pin a specific release tag (e.g. PG16/v1.5.0-rc0).
ARG AGE_REF=PG16

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        git \
        ca-certificates \
        flex \
        bison \
        postgresql-server-dev-16; \
    git clone --depth 1 --branch "${AGE_REF}" https://github.com/apache/age.git /tmp/age; \
    make -C /tmp/age PG_CONFIG=/usr/bin/pg_config install; \
    # Drop only the build tooling; leave the server + runtime libs untouched.
    apt-get purge -y --auto-remove build-essential git flex bison; \
    rm -rf /tmp/age /var/lib/apt/lists/*

# AGE is loaded per-session by the app (GraphStore) and preloaded by the compose
# `command:` for the local stack; no entrypoint change is required here.
