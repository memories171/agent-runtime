#!/bin/sh
set -eu

: "${TEMPORAL_ADDRESS:?ERROR: TEMPORAL_ADDRESS is required}"

NAMESPACE="${DEFAULT_NAMESPACE:-default}"
RETENTION="${DEFAULT_NAMESPACE_RETENTION:-24h}"

echo "Waiting for Temporal service at ${TEMPORAL_ADDRESS}..."
attempt=0
until temporal operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "Temporal service did not become healthy in time." >&2
    exit 1
  fi
  echo "Temporal is not healthy yet; retrying ($attempt/60)..."
  sleep 2
done
echo "Temporal service is healthy."

if temporal operator namespace describe --namespace "$NAMESPACE" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
  echo "Namespace '${NAMESPACE}' already exists; skipping creation."
else
  echo "Creating namespace '${NAMESPACE}' with retention ${RETENTION}..."
  temporal operator namespace create \
    --namespace "$NAMESPACE" \
    --retention "$RETENTION" \
    --address "$TEMPORAL_ADDRESS"
  echo "Namespace '${NAMESPACE}' created."
fi

echo "Final namespace state:"
temporal operator namespace describe --namespace "$NAMESPACE" --address "$TEMPORAL_ADDRESS"
echo "Namespace initialization complete."
