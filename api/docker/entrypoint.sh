#!/bin/sh
set -eu

attempt=0
until node /app/ensure-schema.mjs; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "[entrypoint] Unable to apply the admin API database schema" >&2
    exit 1
  fi
  echo "[entrypoint] Database is not ready; retrying schema setup (${attempt}/30)" >&2
  sleep 2
done

echo "[entrypoint] Starting: node src/index.ts"
exec node src/index.ts
