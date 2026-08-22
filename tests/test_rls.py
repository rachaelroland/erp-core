# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Prove tenant isolation (ADR-012).

Two halves, and the second is the one that matters:

  1. CONFIGURATION — every tenant-scoped table has RLS enabled, FORCEd, and
     carries a policy. Catches a new table added without one.
  2. BEHAVIOUR — connect as the unprivileged application role, plant a row
     under a second tenant, and try to read it. A config check alone would
     pass on a database where the connecting role is a superuser and every
     query still returns every tenant's rows.

Run:  uv run python -m tests.test_rls
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from applied_erp.config import ROLE_ADMIN, TENANT_ID, dsn  # noqa: E402

APP_USER = "agent_erp_app"
APP_PASSWORD = "agenterp_app"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"


def _app_dsn() -> str:
    base = dsn(ROLE_ADMIN)
    parts = [p for p in base.split() if not p.startswith(("user=", "password="))]
    return " ".join(parts + [f"user={APP_USER}", f"password={APP_PASSWORD}"])


def check_configuration(conn) -> list[str]:
    problems: list[str] = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname AS table_name, c.relrowsecurity AS enabled,
                   c.relforcerowsecurity AS forced,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND EXISTS (SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                            AND NOT a.attisdropped)
            ORDER BY c.relname""")
        rows = cur.fetchall()

    for r in rows:
        if not r["enabled"]:
            problems.append(f"{r['table_name']}: RLS not enabled")
        if not r["forced"]:
            problems.append(
                f"{r['table_name']}: RLS not FORCEd — the table owner bypasses it")
        if not r["policies"]:
            problems.append(f"{r['table_name']}: no policy")

    with conn.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s",
                    (APP_USER,))
        role = cur.fetchone()
    if role is None:
        problems.append(f"application role {APP_USER} does not exist")
    else:
        if role["rolsuper"]:
            problems.append(f"{APP_USER} is a SUPERUSER — it bypasses RLS entirely")
        if role["rolbypassrls"]:
            problems.append(f"{APP_USER} holds BYPASSRLS — RLS does not apply to it")

    return problems, len(rows)


def check_behaviour(admin) -> list[str]:
    """Plant a foreign-tenant row, then try to read it as the app role."""
    problems: list[str] = []
    probe_code = f"RLSPROBE-{uuid.uuid4().hex[:8]}"

    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO party (id, tenant_id, code, actor_kind, legal_name, "
            " display_name) VALUES (%s,%s,%s,'organization','RLS Probe Co','RLS Probe Co')",
            (str(uuid.uuid4()), OTHER_TENANT, probe_code))
    admin.commit()

    try:
        with psycopg.connect(_app_dsn(), row_factory=dict_row) as app:
            with app.cursor() as cur:
                # No tenant set: default deny.
                cur.execute("SELECT count(*) AS n FROM party")
                if cur.fetchone()["n"] != 0:
                    problems.append(
                        "with no tenant set the app role can still read party rows "
                        "— the default is not deny")

                # Our tenant: sees its own rows, never the probe.
                cur.execute("SELECT set_config('agent_erp.tenant_id', %s, false)", (TENANT_ID,))
                cur.execute("SELECT count(*) AS n FROM party")
                own = cur.fetchone()["n"]
                if own == 0:
                    problems.append("app role sees no rows for its own tenant")

                cur.execute("SELECT count(*) AS n FROM party WHERE code=%s",
                            (probe_code,))
                if cur.fetchone()["n"] != 0:
                    problems.append(
                        "CROSS-TENANT LEAK: app role read another tenant's party row")

                # Writing into someone else's tenant must be refused.
                try:
                    cur.execute(
                        "INSERT INTO party (id, tenant_id, code, actor_kind, "
                        " legal_name, display_name) VALUES (%s,%s,%s,"
                        " 'organization','Cross Tenant','Cross Tenant')",
                        (str(uuid.uuid4()), OTHER_TENANT,
                         f"XT-{uuid.uuid4().hex[:6]}"))
                    problems.append(
                        "CROSS-TENANT WRITE: app role inserted a row under another "
                        "tenant (WITH CHECK is not enforcing)")
                except psycopg.errors.InsufficientPrivilege:
                    pass          # expected
                except psycopg.Error as exc:
                    if "row-level security" not in str(exc).lower():
                        problems.append(f"unexpected error on cross-tenant write: {exc}")

                app.rollback()

                # The answer key must be unreachable at any tenant.
                with app.cursor() as cur2:
                    cur2.execute("SELECT set_config('agent_erp.tenant_id', %s, false)", (TENANT_ID,))
                    try:
                        cur2.execute("SELECT count(*) FROM ground_truth.invoice_case")
                        problems.append(
                            "app role can read ground_truth — the answer key is "
                            "reachable from the application")
                    except psycopg.Error:
                        pass      # expected
    except psycopg.OperationalError as exc:
        problems.append(f"cannot connect as {APP_USER}: {exc}")
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM party WHERE code=%s", (probe_code,))
        admin.commit()

    return problems


def check_runtime_uses_app_role() -> list[str]:
    """The default connection must be the unprivileged role.

    RLS that is configured but never exercised is theatre. This asserts the
    application's own `connect()` lands on a non-superuser with the tenant
    already declared — so a future change that quietly points the runtime
    back at the migration role fails here rather than in production.
    """
    from applied_erp.core.db import connect

    problems: list[str] = []
    try:
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT current_user AS u, current_tenant_id() AS t")
            row = cur.fetchone()
            cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname = current_user")
            role = cur.fetchone()
        conn.close()
    except Exception as exc:
        return [f"applied_erp.core.db.connect() failed: {exc}"]

    if role and (role["rolsuper"] or role["rolbypassrls"]):
        problems.append(
            f"the runtime connects as '{row['u']}', which bypasses RLS — "
            f"tenant isolation is not actually in force")
    if row["t"] is None:
        problems.append("connect() did not declare a tenant; every query "
                        "would return empty")
    return problems


def main() -> int:
    admin = psycopg.connect(dsn(ROLE_ADMIN), row_factory=dict_row)
    try:
        config_problems, n_tables = check_configuration(admin)
        behaviour_problems = check_behaviour(admin)
        runtime_problems = check_runtime_uses_app_role()
    finally:
        admin.close()

    print(f"tenant isolation: {n_tables} tenant-scoped tables checked")
    problems = config_problems + behaviour_problems + runtime_problems
    if problems:
        print(f"\nFAIL — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        return 1
    print("PASS — RLS enabled and FORCEd everywhere; the runtime connects "
          "unprivileged with its tenant declared; no cross-tenant read or "
          "write; ground_truth unreachable from the application")
    return 0


if __name__ == "__main__":
    sys.exit(main())
