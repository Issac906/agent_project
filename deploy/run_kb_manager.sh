#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_NAME="${KB_MANAGER_IMAGE_NAME:-patent-kb-manager:latest}"
CONTAINER_NAME="${KB_MANAGER_CONTAINER_NAME:-patent-kb-manager}"
ENV_FILE="${KB_MANAGER_ENV_FILE:-kb-manager.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp kb-manager.env.example "${ENV_FILE}"
  echo "已创建 ${ENV_FILE}。请先按部署文档填写 LightRAG 运行配置，再重新运行本脚本。" >&2
  exit 1
fi

# This is an administrator-owned dotenv file bundled next to this script.
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

: "${KB_MANAGER_BIND_PORT:=9700}"
: "${KB_MANAGER_DATA_ROOT:=/var/lib/patent-agent/knowledge-bases}"

if [[ -n "${KB_MANAGER_LIGHTRAG_ENV_FILE:-}" && ! -f "${KB_MANAGER_LIGHTRAG_ENV_FILE}" ]]; then
  echo "错误：找不到 KB_MANAGER_LIGHTRAG_ENV_FILE=${KB_MANAGER_LIGHTRAG_ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${KB_MANAGER_DATA_ROOT}"

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

VOLUMES=(
  -v /var/run/docker.sock:/var/run/docker.sock
  -v "${KB_MANAGER_DATA_ROOT}:${KB_MANAGER_DATA_ROOT}"
)
if [[ -n "${KB_MANAGER_LIGHTRAG_ENV_FILE:-}" ]]; then
  VOLUMES+=(
    -v "${KB_MANAGER_LIGHTRAG_ENV_FILE}:${KB_MANAGER_LIGHTRAG_ENV_FILE}:ro"
  )
fi

docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -p "${KB_MANAGER_BIND_PORT}:${KB_MANAGER_BIND_PORT}" \
  "${VOLUMES[@]}" \
  "${IMAGE_NAME}"

echo "独立知识库管理服务已启动：http://服务器IP:${KB_MANAGER_BIND_PORT}"
echo "查看日志：docker logs -f ${CONTAINER_NAME}"
