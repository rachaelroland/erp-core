# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""The policy engine (Pillar 3).

An agent emits a Proposal. This decides: auto-commit, queue for a human, or
reject. It decides on the DRY RUN's declared blast radius and the grants on
file — never on the agent's own assurance that its action is small.

Decision order, first match wins:
  1. authority   — does a live grant cover this (agent, command, scope)?
  2. reversibility — is the command's class within the grant's cap?
  3. magnitude   — is the monetary blast radius within the ceiling?
  4. duties      — would committing violate a segregation-of-duties rule?
  5. trust       — is the supporting evidence trusted enough for this class?
  6. tolerance   — do the policy_rule bands permit auto-commit?
"""

from __future__ import annotations

from dataclasses import dataclass, field

BAND_ORDER = ["observe", "suggest", "act_within_limits", "act_with_notice", "autonomous"]
REVERSIBILITY_ORDER = [
    "R0_read_only", "R1_reversible", "R2_compensable",
    "R3_externally_visible", "R4_physically_irreversible",
]


@dataclass
class Decision:
    effect: str                       # auto_commit | require_human | reject
    reason: str
    matched_policy_id: str | None = None
    checks: list[str] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, conn):
        self.conn = conn
        self._grants = self._load_grants()
        self._sod = self._load_sod()
        self._tolerance = self._load_tolerance()

    def _load_grants(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT g.*, p.code AS grantee_code, p.actor_kind "
                "FROM authority_grant g JOIN party p ON p.id = g.grantee_id "
                "WHERE g.revoked_at IS NULL AND now() >= g.valid_from "
                "  AND (g.valid_to IS NULL OR now() <= g.valid_to)")
            return cur.fetchall()

    def _load_sod(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM duty_separation_rule")
            return cur.fetchall()

    def _load_tolerance(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT tolerance FROM policy_rule "
                "WHERE applies_to = 'match_invoice_to_receipt' "
                "  AND effect = 'auto_commit' ORDER BY priority LIMIT 1")
            row = cur.fetchone()
        return row["tolerance"] if row else {}

    # -- the decision ---------------------------------------------------

    def evaluate(self, *, agent_code: str, command_name: str,
                 reversibility: str, blast_radius: dict,
                 evidence_trust: str | None = None,
                 tolerance_breach: str | None = None,
                 reference_corrected: bool = False) -> Decision:
        checks: list[str] = []

        grant = next(
            (g for g in self._grants
             if g["grantee_code"] == agent_code
             and g["command_name"] in (command_name, "*")),
            None)
        if grant is None:
            return Decision("reject",
                            f"no live authority grant for {agent_code} on {command_name}",
                            checks=checks)
        checks.append(f"grant:{grant['band']}")

        if BAND_ORDER.index(grant["band"]) <= BAND_ORDER.index("suggest"):
            return Decision("require_human",
                            f"{agent_code} holds '{grant['band']}' — proposals only",
                            checks=checks)

        # Reversibility cap. This is the line that matters: R3 is where an
        # agent can do damage at software speed, so it is capped by grant,
        # not by the agent's confidence.
        if REVERSIBILITY_ORDER.index(reversibility) > \
                REVERSIBILITY_ORDER.index(grant["max_reversibility"]):
            return Decision("require_human",
                            f"{reversibility} exceeds grant cap "
                            f"{grant['max_reversibility']}",
                            checks=checks)
        checks.append(f"reversibility:{reversibility}<={grant['max_reversibility']}")

        magnitude = float(blast_radius.get("monetary_magnitude") or 0)
        if grant["max_monetary"] is not None and magnitude > float(grant["max_monetary"]):
            return Decision("require_human",
                            f"magnitude {magnitude:,.2f} exceeds ceiling "
                            f"{float(grant['max_monetary']):,.2f}",
                            checks=checks)
        checks.append(f"magnitude:{magnitude:,.2f}")

        # Segregation of duties, applied to agents exactly as to people.
        for rule in self._sod:
            if command_name == rule["command_b"]:
                if self._actor_did(agent_code, rule["command_a"],
                                   blast_radius.get("correlation_value")):
                    effect = ("reject" if rule["severity"] == "block"
                              else "require_human")
                    return Decision(effect,
                                    f"segregation of duties {rule['rule_code']}: "
                                    f"{agent_code} already performed "
                                    f"{rule['command_a']} on this object",
                                    checks=checks)
        checks.append("sod:clear")

        # T1: untrusted evidence can never auto-commit an externally visible
        # action, no matter how confident the extractor was. Trust and
        # confidence are independent axes and one must not substitute for
        # the other.
        if evidence_trust == "untrusted" and \
                REVERSIBILITY_ORDER.index(reversibility) >= \
                REVERSIBILITY_ORDER.index("R3_externally_visible"):
            return Decision("require_human",
                            "untrusted evidence may not auto-commit an "
                            "externally visible action (T1)",
                            checks=checks)
        checks.append(f"evidence_trust:{evidence_trust}")

        if blast_radius.get("external_parties_notified"):
            return Decision("require_human",
                            "action notifies an external party",
                            checks=checks)

        # The agent silently corrected the document's own order reference.
        # It is often RIGHT to do so — a transposed digit is the common case —
        # but a document citing an order that does not exist is also the
        # cheapest fraud there is, so a human confirms the correction before
        # money moves. This is the pillar doing real work: the agent's
        # judgment does not get to overrule policy, however well argued.
        if reference_corrected:
            return Decision("require_human",
                            "agent substituted a different purchase order than "
                            "the document cited; correction needs confirmation",
                            checks=checks)
        checks.append("reference:as_cited")

        if tolerance_breach:
            return Decision("require_human",
                            f"outside tolerance: {tolerance_breach}",
                            checks=checks)
        checks.append("tolerance:within")

        return Decision("auto_commit",
                        f"within {grant['band']} grant, all checks passed",
                        checks=checks)

    def _actor_did(self, agent_code: str, command_name: str,
                   correlation_value: str | None) -> bool:
        if not correlation_value:
            return False
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM proposal p JOIN party a ON a.id = p.proposed_by "
                "WHERE a.code = %s AND p.command_name = %s "
                "  AND p.status IN ('auto_committed','approved') "
                "  AND p.arguments::text LIKE %s LIMIT 1",
                (agent_code, command_name, f"%{correlation_value}%"))
            return cur.fetchone() is not None

    @property
    def price_tolerance_pct(self) -> float:
        return float(self._tolerance.get("price_variance_pct", 3.0))

    @property
    def qty_tolerance_pct(self) -> float:
        return float(self._tolerance.get("over_delivery_pct", 2.0))
