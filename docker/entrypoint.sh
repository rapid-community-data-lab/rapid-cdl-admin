#!/bin/sh
set -e

: "${BACKEND_URL:=http://api:8080}"
export BACKEND_URL

envsubst '${BACKEND_URL}' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

echo "[entrypoint] nginx proxying /api -> ${BACKEND_URL}"

exec "$@"