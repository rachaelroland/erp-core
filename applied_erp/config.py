# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Core runtime configuration: database access only.

Model routing and API credentials live in erp/ai/config.py — the core
carries no LLM dependency of any kind.
"""

from __future__ import annotations

import os
from pathlib import Path

TENANT_ID = "11111111-1111-4111-8111-111111111111"

# Tolerance bands. These mirror the policy_rule rows seeded by the generator;
# the command layer reads them from the database at runtime rather than
# trusting these constants — they are here only as documented defaults.
DEFAULT_PRICE_TOLERANCE_PCT = 3.0
DEFAULT_QTY_TOLERANCE_PCT = 2.0

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    for candidate in (_PROJECT_ROOT / ".env",
                      _PROJECT_ROOT.parents[0] / "dr_patel" / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Two roles, deliberately.
#
#   "app"   — the unprivileged role the APPLICATION runs as. Subject to
#             row-level security, and cannot reach the ground_truth
#             fixtures. This is the DEFAULT: if the application can run as
#             the migration role, RLS is decorative.
#   "admin" — the migration/fixture role. DDL, test-data construction, and
#             the private evaluation harness, which reads the answer key by
#             design. Never for serving a request.
ROLE_APP = "app"
ROLE_ADMIN = "admin"


def dsn(role: str = ROLE_APP) -> str:
    _load_dotenv()
    host = os.environ.get("AGENT_ERP_DB_HOST")
    if not host:
        raise SystemExit("AGENT_ERP_DB_HOST unset. Run: bash infra/machine_env.sh")
    if role == ROLE_ADMIN:
        user = os.environ.get("AGENT_ERP_DB_USER", "agenterp")
        password = os.environ.get("AGENT_ERP_DB_PASSWORD", "agenterp")
    else:
        user = os.environ.get("AGENT_ERP_APP_USER", "agent_erp_app")
        password = os.environ.get("AGENT_ERP_APP_PASSWORD", "agenterp_app")
    return (
        f"host={host} port={os.environ.get('AGENT_ERP_DB_PORT', '5432')} "
        f"dbname={os.environ.get('AGENT_ERP_DB_NAME', 'agent_erp')} "
        f"user={user} password={password}"
    )
