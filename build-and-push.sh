#!/bin/bash
set -e

# 配置变量（账号/空间与 new-api 一致）
REGISTRY="registry.cn-shanghai.aliyuncs.com"
NAMESPACE="hxh_ai"
IMAGE_NAME="any-auto-register"
VERSION_FILE=".docker-version"

# 构建时下载走代理（camoufox/playwright/pip），可用环境变量覆盖
BUILD_PROXY="${BUILD_PROXY:-http://127.0.0.1:10809}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

[ -f "$VERSION_FILE" ] || echo "0" > "$VERSION_FILE"
CURRENT_VERSION=$(cat "$VERSION_FILE")
NEW_VERSION=$((CURRENT_VERSION + 1))
FULL_IMAGE="${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}"

echo "================================"
echo "镜像仓库: ${FULL_IMAGE}"
echo "版本号:   v${NEW_VERSION}"
echo "构建代理: ${BUILD_PROXY:-<无>}"
echo "================================"

# 交互确认（CI 里可 export ASSUME_YES=1 跳过）
if [ "${ASSUME_YES:-0}" != "1" ]; then
    read -p "是否继续构建并推送? (y/n): " -n 1 -r; echo
    [[ $REPLY =~ ^[Yy]$ ]] || { echo -e "${RED}已取消${NC}"; exit 1; }
fi

PROXY_ARGS=()
NET_ARG=()
if [ -n "$BUILD_PROXY" ]; then
    PROXY_ARGS=(--build-arg "HTTP_PROXY=$BUILD_PROXY" --build-arg "HTTPS_PROXY=$BUILD_PROXY" --build-arg "NO_PROXY=localhost,127.0.0.1")
    NET_ARG=(--network=host)
fi

echo -e "${GREEN}[1/3] 构建镜像...${NC}"
docker build "${NET_ARG[@]}" "${PROXY_ARGS[@]}" \
    --build-arg "APP_VERSION=v${NEW_VERSION}" \
    -t "${FULL_IMAGE}:v${NEW_VERSION}" \
    -t "${FULL_IMAGE}:latest" \
    .

echo -e "${GREEN}[2/3] 登录仓库(如已登录会复用凭证)...${NC}"
docker login "${REGISTRY}" 2>/dev/null || true

echo -e "${GREEN}[3/3] 推送镜像...${NC}"
docker push "${FULL_IMAGE}:v${NEW_VERSION}"
docker push "${FULL_IMAGE}:latest"

echo "$NEW_VERSION" > "$VERSION_FILE"
echo -e "${GREEN}✓ 完成: ${FULL_IMAGE}:v${NEW_VERSION} (+latest)${NC}"
