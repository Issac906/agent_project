#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${KB_MANAGER_ENV_FILE:-${ROOT_DIR}/deploy/kb-manager.env}"
COMPOSE_FILE="${ROOT_DIR}/deploy/docker-compose.kb-manager.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker command not found." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} does not exist." >&2
  echo "Copy deploy/kb-manager.env.example to deploy/kb-manager.env and fill it first." >&2
  exit 1
fi

docker compose \
  --env-file "${ENV_FILE}" \
  -f "${COMPOSE_FILE}" \
  up -d --build

echo "Knowledge-base manager deployment requested."
echo "Check status with: docker logs -f patent-kb-manager"
