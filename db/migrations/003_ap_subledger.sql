-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- 003 — accounts payable subledger
--
-- Matching an invoice says whether it is correct. It says nothing about
-- what is owed, to whom, or when — so a finance team could not use this
-- system, and an agent could not answer the most common question in AP.
--
-- No stored balance (Pillar 1): outstanding is gross minus settlements,
-- computed by the payable_open view. No status column: settlement state
-- is derived from the same subtraction. Settlements are append-only, so
-- a reversal is a negative row rather than an edit.
--
-- Deliberately excludes a general ledger, journals and a chart of
-- accounts. This is the subledger; posting a summary of it into a GL is
-- an integration, not a table.
-- =====================================================================

-- =====================================================================
-- §14  ACCOUNTS PAYABLE SUBLEDGER
--
-- Matching an invoice answers "is this correct?". It does not answer
-- "what do we owe, to whom, and when" — and a system of record that
-- cannot answer that is not usable by a finance department.
--
-- Designed from the domain and from this schema's own pillars:
--
--   * Pillar 1, no stored counters. There is no `balance` column. What
--     is outstanding is gross minus the settlements recorded against it,
--     computed on read. A stored balance is a second source of truth
--     that drifts the first time a settlement is inserted by anything
--     that forgets to update it.
--   * Append-only. A payment does not mutate the obligation; it is a
--     row. Reversing one is another row with a negative amount, so the
--     history of a disputed payment survives instead of being edited
--     away — which is exactly the history an auditor asks for.
--   * REA duality. The obligation is a claim against us; the settlement
--     is the reciprocal flow that discharges it.
--
-- Deliberately NOT here: a general ledger, journal entries, or a chart
-- of accounts. This is the subledger — what is owed on each document.
-- Posting a summary of it into a GL is an integration, and pretending
-- otherwise would be the moment this stopped being a manufacturing
-- system and started being a half-built accounting package.
-- =====================================================================

CREATE TABLE payment_term (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,        -- 'NET30', '2/10 NET30', 'COD'
    description     text NOT NULL,
    net_days        int  NOT NULL CHECK (net_days >= 0),
    -- Early-payment discount. Real money in manufacturing: 2/10 net 30 is
    -- a 2% discount for paying in 10 days, which annualises to roughly
    -- 36%. Deciding whether to take it is a genuine agent capability and
    -- it cannot be evaluated if the terms are an unparsed string.
    discount_pct    numeric(6,4) NOT NULL DEFAULT 0 CHECK (discount_pct >= 0 AND discount_pct < 1),
    discount_days   int          NOT NULL DEFAULT 0 CHECK (discount_days >= 0),
    attributes      jsonb        NOT NULL DEFAULT '{}',
    created_at      timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code),
    CHECK (discount_days <= net_days)
);
COMMENT ON TABLE payment_term IS
  'Payment terms parsed into their parts rather than left as prose. commitment.payment_terms '
  'is free text for the document; this is the structured form the subledger computes dates '
  'from. An agent cannot reason about a discount it can only read as a string.';

CREATE TABLE payable_obligation (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    supplier_id     uuid NOT NULL REFERENCES party(id),
    -- The PO this bills against. NULL is legitimate and common: freight,
    -- utilities and services arrive with no purchase order at all, and a
    -- subledger that cannot represent them forces them outside the system.
    commitment_id   uuid REFERENCES commitment(id),
    -- The document itself, so the obligation is traceable to the artifact
    -- it was read from rather than to a retyped number.
    evidence_id     uuid REFERENCES evidence(id),
    document_kind   text NOT NULL DEFAULT 'invoice'
                    CHECK (document_kind IN ('invoice','credit_note','debit_note')),
    supplier_reference text NOT NULL,       -- the supplier's own invoice number
    currency        char(3) NOT NULL DEFAULT 'USD',
    -- Signed. A credit note is a negative obligation, not a special case
    -- with its own arithmetic — which keeps every balance query a SUM.
    gross_amount    numeric(18,6) NOT NULL,
    tax_amount      numeric(18,6) NOT NULL DEFAULT 0,
    payment_term_id uuid REFERENCES payment_term(id),
    invoice_date    date NOT NULL,
    due_date        date NOT NULL,
    -- What this credit note credits. Self-referential because a credit is
    -- meaningless until tied to what it reduces.
    credits_obligation_id uuid REFERENCES payable_obligation(id),
    -- The proposal that accepted this liability, so "why do we owe this"
    -- resolves to a decision with a rationale and an actor.
    accepted_by_proposal_id uuid REFERENCES proposal(id),
    attributes      jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code),
    UNIQUE (tenant_id, supplier_id, supplier_reference, document_kind),
    CHECK (due_date >= invoice_date),
    CHECK ((document_kind = 'credit_note') = (gross_amount < 0)),
    CHECK ((document_kind = 'credit_note') OR credits_obligation_id IS NULL)
);
COMMENT ON TABLE payable_obligation IS
  'A liability we have accepted — one supplier document. Created when matching approves an '
  'invoice or a human resolves an exception, never by extraction alone: reading a document '
  'is not the same as agreeing to pay it. There is no status column; status is derived from '
  'settlements (Pillar 1).';

CREATE TABLE payable_settlement (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    obligation_id   uuid NOT NULL REFERENCES payable_obligation(id),
    kind            text NOT NULL
                    CHECK (kind IN ('payment','credit_applied','discount_taken',
                                    'write_off','reversal')),
    -- Signed, and always in the direction of reducing what is owed. A
    -- reversal is negative, so a bounced payment leaves both the original
    -- and its reversal visible rather than deleting the first.
    amount          numeric(18,6) NOT NULL CHECK (amount <> 0),
    settled_on      date NOT NULL,
    -- Every settlement is also a ledger event. Without this the subledger
    -- becomes a second history that can disagree with the first.
    flow_event_id   uuid REFERENCES flow_event(id),
    reference       text,                   -- cheque number, ACH trace, remittance id
    reverses_id     uuid REFERENCES payable_settlement(id),
    attributes      jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL))
);
COMMENT ON TABLE payable_settlement IS
  'An event that discharges part of an obligation. Append-only: nothing here is ever updated '
  'or deleted, so a disputed payment keeps its full history rather than being edited into a '
  'shape that suits the current story.';

CREATE INDEX payable_obligation_supplier_idx
    ON payable_obligation (tenant_id, supplier_id, due_date);
CREATE INDEX payable_obligation_due_idx
    ON payable_obligation (tenant_id, due_date);
CREATE INDEX payable_settlement_obligation_idx
    ON payable_settlement (tenant_id, obligation_id);

-- What is still owed. The absence of a `balance` column made flesh: this
-- is the only definition of outstanding there is, so it cannot disagree
-- with a stored one.
CREATE VIEW payable_open AS
SELECT o.tenant_id,
       o.id                AS obligation_id,
       o.code,
       o.supplier_id,
       o.supplier_reference,
       o.document_kind,
       o.currency,
       o.gross_amount,
       o.invoice_date,
       o.due_date,
       COALESCE(s.settled, 0)                     AS settled_amount,
       o.gross_amount - COALESCE(s.settled, 0)    AS outstanding_amount,
       CASE
           WHEN o.gross_amount - COALESCE(s.settled, 0) = 0 THEN 'settled'
           WHEN COALESCE(s.settled, 0) = 0                  THEN 'open'
           ELSE 'partly_settled'
       END                                        AS settlement_state
FROM payable_obligation o
LEFT JOIN (
    SELECT tenant_id, obligation_id, SUM(amount) AS settled
    FROM payable_settlement GROUP BY tenant_id, obligation_id
) s ON s.obligation_id = o.id AND s.tenant_id = o.tenant_id;

COMMENT ON VIEW payable_open IS
  'Outstanding balance per obligation, computed. There is deliberately no stored balance to '
  'compare this against — one definition, so no reconciliation problem.';

-- Aging, relative to a date the caller supplies rather than now(): an
-- aging report that cannot be run "as at month end" is not an aging
-- report, and hardcoding now() makes the past unreproducible.
CREATE OR REPLACE FUNCTION payable_aging(as_at date DEFAULT current_date)
RETURNS TABLE (
    tenant_id uuid, supplier_id uuid, currency char(3),
    bucket text, obligations bigint, amount numeric
) LANGUAGE sql STABLE AS $$
    SELECT p.tenant_id, p.supplier_id, p.currency,
           CASE
               WHEN p.due_date >= as_at                      THEN 'current'
               WHEN as_at - p.due_date BETWEEN 1 AND 30      THEN '1-30'
               WHEN as_at - p.due_date BETWEEN 31 AND 60     THEN '31-60'
               WHEN as_at - p.due_date BETWEEN 61 AND 90     THEN '61-90'
               ELSE '90+'
           END AS bucket,
           count(*)                  AS obligations,
           sum(p.outstanding_amount) AS amount
    FROM payable_open p
    WHERE p.outstanding_amount <> 0
    GROUP BY p.tenant_id, p.supplier_id, p.currency, 4
$$;

COMMENT ON FUNCTION payable_aging IS
  'Aging as at a supplied date, not now(). Month-end reporting and any question of the form '
  '"what did we think we owed on the 31st" require it, and both are impossible if the '
  'current date is baked in.';
