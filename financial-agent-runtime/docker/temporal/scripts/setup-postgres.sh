#!/bin/sh
set -eu

: "${POSTGRES_SEEDS:?ERROR: POSTGRES_SEEDS is required}"
: "${POSTGRES_USER:?ERROR: POSTGRES_USER is required}"
: "${SQL_PASSWORD:?ERROR: SQL_PASSWORD is required}"

PORT="${DB_PORT:-5432}"
TEMPORAL_DB="${TEMPORAL_DB:-temporal}"
VISIBILITY_DB="${TEMPORAL_VISIBILITY_DB:-temporal_visibility}"
FLAG_FILE="/flags/temporal_schema_initialized"

sql_tool() {
  temporal-sql-tool \
    --plugin postgres12 \
    --ep "$POSTGRES_SEEDS" \
    -u "$POSTGRES_USER" \
    --pw "$SQL_PASSWORD" \
    -p "$PORT" \
    "$@"
}

echo "Waiting for PostgreSQL at ${POSTGRES_SEEDS}:${PORT}..."
until nc -z -w 5 "$POSTGRES_SEEDS" "$PORT"; do
  echo "PostgreSQL is not ready yet; retrying..."
  sleep 2
done
echo "PostgreSQL is available."

if [ -f "$FLAG_FILE" ]; then
  echo "Schema initialization marker found; running idempotent update-schema only."
else
  echo "First-time Temporal SQL schema initialization."

  echo "Creating database ${TEMPORAL_DB}..."
  sql_tool --db "$TEMPORAL_DB" create
  echo "Setting up ${TEMPORAL_DB} version table..."
  sql_tool --db "$TEMPORAL_DB" setup-schema -v 0.0
  echo "Updating ${TEMPORAL_DB} schema to 1.19..."
  sql_tool --db "$TEMPORAL_DB" update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19

  echo "Creating database ${VISIBILITY_DB}..."
  sql_tool --db "$VISIBILITY_DB" create
  echo "Setting up ${VISIBILITY_DB} version table..."
  sql_tool --db "$VISIBILITY_DB" setup-schema -v 0.0
  echo "Updating ${VISIBILITY_DB} schema to 1.14..."
  sql_tool --db "$VISIBILITY_DB" update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned -v 1.14

  touch "$FLAG_FILE"
  echo "Schema initialization marker written to ${FLAG_FILE}."
fi

echo "Re-asserting schema versions through update-schema..."
sql_tool --db "$TEMPORAL_DB" update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19
sql_tool --db "$VISIBILITY_DB" update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned -v 1.14
echo "Temporal SQL schema setup complete."
