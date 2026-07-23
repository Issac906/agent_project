#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-patent-agent:latest}"
PLATFORM="${PLATFORM:-linux/amd64}"
TAR_PATH="${TAR_PATH:-patent-agent-amd64.tar}"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：当前环境找不到 docker 命令。请先安装并启动 Docker Desktop，或在公司有 Docker 的服务器上运行本脚本。" >&2
  exit 1
fi

docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}" --load .

IMAGE_ARCH="$(docker image inspect "${IMAGE_NAME}" --format '{{.Architecture}}')"
if [[ "${PLATFORM}" == "linux/amd64" && "${IMAGE_ARCH}" != "amd64" ]]; then
  echo "错误：镜像架构为 ${IMAGE_ARCH}，不是公司服务器需要的 amd64。" >&2
  exit 1
fi

docker save -o "${TAR_PATH}" "${IMAGE_NAME}"

echo "已生成：${TAR_PATH}"
echo "镜像平台：${PLATFORM}（${IMAGE_ARCH}）"
echo "公司服务器加载命令：docker load -i ${TAR_PATH}"
