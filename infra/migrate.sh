#!/usr/bin/env bash
# Apply pending migrations, in order, exactly once each.
#
#   bash infra/migrate.sh [--status] [--dry-run]
#
# Idempotent: re-running applies nothing. Each migration runs inside a
# transaction together with the row recording it, so a failure leaves the
# database on the previous version rather than half-migrated — the state that
# is hardest to recover from because nothing says which half succeeded.
set -uo pipefail

cd "$(dirname "$0")/.."
# Load .env WITHOUT clobbering anything already set in the environment.
# Plain `source .env` overrides an explicit AGENT_ERP_DB_NAME, so
# `AGENT_ERP_DB_NAME=scratch bash infra/migrate.sh` silently migrated the live
# database instead. It rolled back, but a tool that quietly targets a different
# database than the one you named is the wrong kind of surprise.
if [ -f .env ]; then
    while IFS='=' read -r k v; do
        case "$k" in ''|\#*) continue ;; esac
        [ -n "${!k:-}" ] || export "$k=$v"
    done < .env
fi

PSQL=(psql -h "${AGENT_ERP_DB_HOST:-127.0.0.1}" -U "${AGENT_ERP_DB_USER:-agenterp}"
      -d "${AGENT_ERP_DB_NAME:-agent_erp}" -q -v ON_ERROR_STOP=1)
export PGPASSWORD="${AGENT_ERP_DB_PASSWORD:-}"

MODE="${1:-apply}"

"${PSQL[@]}" -f db/migration_framework.sql >/dev/null 2>&1 || {
    echo "could not create schema_migration — is the database reachable?"; exit 1; }

applied="$("${PSQL[@]}" -At -c "SELECT version FROM schema_migration ORDER BY version" 2>/dev/null)"

if [ "${MODE}" = "--status" ]; then
    echo "applied migrations:"
    "${PSQL[@]}" -c "SELECT version, name, applied_at::date, applied_by FROM schema_migration ORDER BY version"
    echo "available:"
    for f in db/migrations/*.sql; do
        [ -e "$f" ] || continue
        v=$(basename "$f" | cut -d_ -f1 | sed 's/^0*//')
        echo "$applied" | grep -qx "$v" && state="applied" || state="PENDING"
        printf "  %-4s %-46s %s\n" "$v" "$(basename "$f")" "$state"
    done
    exit 0
fi

pending=0
for f in db/migrations/*.sql; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    v=$(echo "$base" | cut -d_ -f1 | sed 's/^0*//')
    name=$(echo "$base" | sed 's/^[0-9]*_//; s/\.sql$//')

    if echo "$applied" | grep -qx "$v"; then
        # Applied already — verify the file has not changed underneath us.
        recorded=$("${PSQL[@]}" -At -c "SELECT checksum FROM schema_migration WHERE version=$v")
        actual=$(shasum -a 256 "$f" | awk '{print $1}')
        if [ "$recorded" != "$actual" ]; then
            echo "REFUSING TO CONTINUE"
            echo "  migration $v ($base) has been edited since it was applied."
            echo "  recorded: $recorded"
            echo "  on disk:  $actual"
            echo "  An applied migration is immutable. Write a new one instead."
            exit 1
        fi
        continue
    fi

    pending=$((pending + 1))
    if [ "${MODE}" = "--dry-run" ]; then
        echo "  would apply $v  $base"
        continue
    fi

    echo "==> applying $v  $name"
    sum=$(shasum -a 256 "$f" | awk '{print $1}')
    # The migration and the record of it commit together or not at all.
    if ! { echo "BEGIN;"; cat "$f";
           echo "INSERT INTO schema_migration (version, name, checksum) VALUES ($v, '$name', '$sum');";
           echo "COMMIT;"; } | "${PSQL[@]}" -f - ; then
        echo "FAILED — rolled back. Database remains at the previous version."
        exit 1
    fi
done

if [ "${pending}" -eq 0 ]; then
    echo "up to date — no pending migrations"
else
    [ "${MODE}" = "--dry-run" ] && echo "${pending} pending" || echo "applied ${pending} migration(s)"
fi
