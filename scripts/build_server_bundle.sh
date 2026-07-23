#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_NAME="${IMAGE_NAME:-patent-agent:latest}"
KB_MANAGER_IMAGE_NAME="${KB_MANAGER_IMAGE_NAME:-patent-kb-manager:latest}"
PLATFORM="${PLATFORM:-linux/amd64}"
RELEASE_ROOT="release/patent-agent-server-amd64"
IMAGE_TAR="${RELEASE_ROOT}/patent-agent-amd64.tar"
BUNDLE_PATH="release/patent-agent-server-amd64.tar.gz"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：当前环境找不到 docker 命令。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "错误：Docker Desktop 尚未启动。请先启动 Docker Desktop。" >&2
  exit 1
fi

rm -rf "${RELEASE_ROOT}"
mkdir -p "${RELEASE_ROOT}"

docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}" --load .
docker buildx build --platform "${PLATFORM}" -f Dockerfile.kb-manager -t "${KB_MANAGER_IMAGE_NAME}" --load .

IMAGE_ARCH="$(docker image inspect "${IMAGE_NAME}" --format '{{.Architecture}}')"
KB_MANAGER_IMAGE_ARCH="$(docker image inspect "${KB_MANAGER_IMAGE_NAME}" --format '{{.Architecture}}')"
if [[ "${IMAGE_ARCH}" != "amd64" || "${KB_MANAGER_IMAGE_ARCH}" != "amd64" ]]; then
  echo "错误：镜像架构为 ${IMAGE_ARCH}，不是 amd64。" >&2
  exit 1
fi

docker save -o "${IMAGE_TAR}" "${IMAGE_NAME}" "${KB_MANAGER_IMAGE_NAME}"
cp deploy/company.env.example "${RELEASE_ROOT}/company.env.example"
cp deploy/kb-manager.env.example "${RELEASE_ROOT}/kb-manager.env.example"
cp deploy/DOCKER_DEPLOY.md "${RELEASE_ROOT}/DOCKER_DEPLOY.md"
cp deploy/run_patent_agent.sh "${RELEASE_ROOT}/run_patent_agent.sh"
cp deploy/run_kb_manager.sh "${RELEASE_ROOT}/run_kb_manager.sh"
chmod +x "${RELEASE_ROOT}/run_patent_agent.sh"
chmod +x "${RELEASE_ROOT}/run_kb_manager.sh"

tar -czf "${BUNDLE_PATH}" -C release "$(basename "${RELEASE_ROOT}")"

echo "已生成服务器部署包：${BUNDLE_PATH}"
echo "镜像：${IMAGE_NAME}、${KB_MANAGER_IMAGE_NAME}"
echo "镜像架构：${IMAGE_ARCH}、${KB_MANAGER_IMAGE_ARCH}"
echo "此包不包含 .env 或任何 API key。"
