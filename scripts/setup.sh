#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MQTT_USER="${MQTT_USER:-admin}"
MQTT_PASSWORD="${MQTT_PASSWORD:-admin1234}"

echo "==> Starting EMQX + SRS..."
docker compose up -d

echo "==> Waiting for EMQX..."
for _ in $(seq 1 30); do
  if docker exec dji_mqtt_emqx emqx ctl status >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> Setting EMQX Dashboard password..."
docker exec dji_mqtt_emqx emqx ctl admins passwd admin admin1234 2>/dev/null \
  || echo "Note: use admin / public if login fails"

echo "==> Running Django migrations..."
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/drone_backend/manage.py" migrate

echo
echo "Ready. Start Django (internal port) with:"
echo "  cd drone_backend && ../.venv/bin/python manage.py runserver 0.0.0.0:${DJANGO_INTERNAL_PORT:-8008}"
echo
echo "Then ensure gateway is up:"
echo "  docker compose up -d gateway"
echo
echo "Pilot 2 H5 URL:"
echo "  http://${SERVER_IP:-127.0.0.1}:${DJANGO_PORT:-8006}/dji/login/"
echo
echo "EMQX Dashboard:"
echo "  http://${SERVER_IP:-127.0.0.1}:18083  (admin / admin1234)"
echo "  ถ้า login ไม่ได้ ลอง admin / public ก่อน"
echo
echo "Mission Control:"
echo "  http://${SERVER_IP:-127.0.0.1}:${DJANGO_PORT:-8006}/dashboard/mission/"
