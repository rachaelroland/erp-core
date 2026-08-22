-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- verify_core.sql — prove the CORE architecture's claims against data.
--
-- The public half of verify.sql. Every section here tests a promise the
-- open-source core makes on its own; anything depending on the private
-- evaluation fixtures (ground_truth.*) is deliberately absent, so this file
-- runs against any deployment rather than only ours.
--
-- Each block tests a claim made in the docs. These are assertions about
-- the DESIGN, not smoke tests of the generator: if the ledger cannot
-- reproduce the projection, then "state is a projection" (Pillar 1) is
-- marketing rather than architecture.
-- =====================================================================

\pset footer off
\echo ''
\echo '=== 1. Volume ==================================================='
SELECT 'flow_event'        AS table, count(*) FROM flow_event
UNION ALL SELECT 'resource_flow',      count(*) FROM resource_flow
UNION ALL SELECT 'inventory_quantum',  count(*) FROM inventory_quantum
UNION ALL SELECT 'commitment',         count(*) FROM commitment
UNION ALL SELECT 'commitment_line',    count(*) FROM commitment_line
UNION ALL SELECT 'job',                count(*) FROM job
UNION ALL SELECT 'job_operation',      count(*) FROM job_operation
UNION ALL SELECT 'lot',                count(*) FROM lot
UNION ALL SELECT 'evidence',           count(*) FROM evidence
UNION ALL SELECT 'assertion',          count(*) FROM assertion
UNION ALL SELECT 'exception_item',     count(*) FROM exception_item
ORDER BY 1;

\echo ''
\echo '=== 2. PILLAR 1: does the ledger reproduce the projection? ======'
\echo '    (inventory_quantum.projected_quantity vs SUM over resource_flow)'
WITH ledger AS (
    SELECT q.id,
           COALESCE(SUM(f.direction * f.quantity), 0) AS from_ledger,
           q.projected_quantity                       AS projection
    FROM inventory_quantum q
    LEFT JOIN resource_flow f ON f.quantum_id = q.id
    GROUP BY q.id, q.projected_quantity
)
SELECT count(*)                                          AS quanta_checked,
       count(*) FILTER (WHERE abs(from_ledger - projection) < 0.000001) AS agreeing,
       count(*) FILTER (WHERE abs(from_ledger - projection) >= 0.000001) AS DISAGREEING,
       COALESCE(round(max(abs(from_ledger - projection)), 6), 0)         AS worst_delta
FROM ledger;

\echo ''
\echo '=== 3. ONTOLOGY: is the duality rule intact? ===================='
\echo '    (every pair_key = one -1 and one +1 of equal quantity)'
SELECT count(*)                                        AS pairs,
       count(*) FILTER (WHERE halves = 2)              AS well_formed,
       count(*) FILTER (WHERE halves <> 2)             AS MALFORMED,
       count(*) FILTER (WHERE abs(net_qty) > 0.000001) AS UNBALANCED
FROM (
    SELECT pair_key, count(*) AS halves,
           SUM(direction * quantity) AS net_qty
    FROM resource_flow GROUP BY pair_key
) p;

\echo ''
\echo '=== 4. THREAT MODEL T10: is the hash chain continuous? =========='
WITH chain AS (
    SELECT sequence_no, entry_hash, prev_hash,
           LAG(entry_hash) OVER (ORDER BY sequence_no) AS expected_prev
    FROM flow_event
)
SELECT count(*) AS events,
       count(*) FILTER (WHERE prev_hash IS NOT DISTINCT FROM expected_prev) AS linked,
       count(*) FILTER (WHERE prev_hash IS DISTINCT FROM expected_prev)     AS BROKEN_LINKS
FROM chain;

\echo ''
\echo '=== 5. PILLAR 1: is the ledger actually immutable? =============='
\echo '    (attempt an UPDATE and confirm nothing changes)'
DO $$
DECLARE before_hash bytea; after_hash bytea; target uuid;
BEGIN
    SELECT id, entry_hash INTO target, before_hash FROM flow_event LIMIT 1;
    UPDATE flow_event SET rationale = 'TAMPERED', entry_hash = '\x00'
     WHERE id = target;
    SELECT entry_hash INTO after_hash FROM flow_event WHERE id = target;
    IF before_hash IS DISTINCT FROM after_hash THEN
        RAISE EXCEPTION 'IMMUTABILITY FAILED: flow_event was modified';
    END IF;
    RAISE NOTICE 'immutability holds: UPDATE against flow_event had no effect';
END $$;

\echo ''
\echo '=== 6. PILLAR 8: the belief model ==============================='
SELECT last_verified_method,
       count(*)                              AS quanta,
       round(avg(belief_confidence), 3)      AS avg_confidence,
       round(min(belief_confidence), 3)      AS min_confidence
FROM inventory_quantum
GROUP BY last_verified_method ORDER BY 2 DESC;

\echo ''
\echo '=== 8. AGENT LAYER: is the epistemic tier populated? ============'
SELECT method,
       count(*)                          AS assertions,
       round(avg(confidence), 3)         AS avg_conf,
       round(min(confidence), 3)         AS min_conf,
       count(DISTINCT subject_uri)       AS subjects
FROM assertion GROUP BY method ORDER BY 2 DESC;

\echo ''
\echo '    Untrusted evidence (T1: may never auto-commit an R3 command)'
SELECT trust_tier, source_kind, count(*)
FROM evidence GROUP BY trust_tier, source_kind ORDER BY 3 DESC;

\echo ''
\echo '=== 9. GOVERNANCE: authority + segregation of duties ============'
SELECT p.code AS agent, g.command_name, g.band, g.max_monetary, g.max_reversibility
FROM authority_grant g JOIN party p ON p.id = g.grantee_id
WHERE p.actor_kind = 'software_agent' ORDER BY p.code;

\echo ''
\echo '=== 10. REFERENTIAL: orphaned addresses ========================='
\echo '    (Pillar 6: every cited URI must resolve)'
SELECT 'assertion.subject_uri' AS reference, count(*) AS unresolved
FROM assertion a LEFT JOIN entity_uri u ON u.uri = a.subject_uri
WHERE u.uri IS NULL
UNION ALL
SELECT 'exception_item.subject_uri', count(*)
FROM exception_item e LEFT JOIN entity_uri u ON u.uri = e.subject_uri
WHERE e.subject_uri IS NOT NULL AND u.uri IS NULL;

\echo ''
\echo '=== 11. Plan vs actual: is variance analysis a JOIN? ============'
SELECT j.state,
       count(*)                                              AS jobs,
       round(avg(EXTRACT(EPOCH FROM (upper(j.planned_window) - lower(j.planned_window)))/86400.0), 2) AS avg_planned_days
FROM job j GROUP BY j.state ORDER BY 2 DESC;

\echo ''
\echo '=== 13. ONTOLOGY: the recall traversal =========================='
\echo '    (01_ontology.md claims both directions are one stored traversal)'
SELECT count(*) AS genealogy_links,
       count(DISTINCT child_lot_id)  AS produced_lots_linked,
       count(DISTINCT parent_lot_id) AS supplier_lots_referenced
FROM lot_genealogy;

\echo ''
\echo '    BACKWARD — pick a shipped finished lot, walk to its supplier lots'
WITH RECURSIVE shipped AS (
    SELECT rf.lot_id, i.code AS item, count(*) AS shipments
    FROM resource_flow rf
    JOIN flow_event e ON e.id = rf.event_id AND e.event_type = 'ship'
    JOIN item_definition i ON i.id = rf.item_id
    WHERE rf.lot_id IS NOT NULL AND rf.direction = -1
    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1
),
ancestry AS (
    SELECT g.child_lot_id, g.parent_lot_id, 1 AS depth
    FROM lot_genealogy g JOIN shipped s ON s.lot_id = g.child_lot_id
    UNION ALL
    SELECT g.child_lot_id, g.parent_lot_id, a.depth + 1
    FROM lot_genealogy g JOIN ancestry a ON g.child_lot_id = a.parent_lot_id
    WHERE a.depth < 6
)
SELECT s.item AS shipped_item, pl.code AS source_lot,
       pl.supplier_lot_code, a.depth
FROM ancestry a
JOIN lot pl ON pl.id = a.parent_lot_id
CROSS JOIN shipped s
ORDER BY a.depth, pl.code LIMIT 8;

\echo ''
\echo '    FORWARD — a supplier lot is suspect: what did it end up in?'
WITH RECURSIVE suspect AS (
    SELECT parent_lot_id FROM lot_genealogy
    GROUP BY 1 ORDER BY count(DISTINCT child_lot_id) DESC LIMIT 1
),
descendants AS (
    SELECT g.parent_lot_id, g.child_lot_id, 1 AS depth
    FROM lot_genealogy g JOIN suspect s ON s.parent_lot_id = g.parent_lot_id
    UNION ALL
    SELECT g.parent_lot_id, g.child_lot_id, d.depth + 1
    FROM lot_genealogy g JOIN descendants d ON g.parent_lot_id = d.child_lot_id
    WHERE d.depth < 6
)
SELECT sl.code AS suspect_lot, sl.supplier_lot_code,
       count(DISTINCT d.child_lot_id) AS affected_lots,
       count(DISTINCT e.correlation_id) AS affected_shipments
FROM descendants d
JOIN lot sl ON sl.id = d.parent_lot_id
LEFT JOIN resource_flow rf ON rf.lot_id = d.child_lot_id
LEFT JOIN flow_event e ON e.id = rf.event_id AND e.event_type = 'ship'
GROUP BY 1, 2;
