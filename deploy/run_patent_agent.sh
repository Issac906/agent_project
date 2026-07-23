#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_NAME="${IMAGE_NAME:-patent-agent:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-patent-agent}"
ENV_FILE="${ENV_FILE:-company.env}"
HOST_PORT="${HOST_PORT:-5000}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp company.env.example "${ENV_FILE}"
  echo "已创建 ${ENV_FILE}。请先填写 API 配置，再重新运行本脚本。" >&2
  exit 1
fi

mkdir -p data outputs

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -p "${HOST_PORT}:5000" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/outputs:/app/outputs" \
  "${IMAGE_NAME}"

echo "Patent Agent 已启动：http://服务器IP:${HOST_PORT}"
echo "查看日志：docker logs -f ${CONTAINER_NAME}"
