#!/usr/bin/env bash
#
# 构建面板分发包 panel/ovpnmanager-panel.tar.gz
#
# 面板源码取自 ovpnmanager 的修复版（默认 ../ovpnmanager-fixed），
# 前端使用其中的构建产物（服务器上无需 Node）。
#
# 用法：
#   ./pack.sh
#   PANEL_SRC=/path/to/ovpnmanager-fixed ./pack.sh
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 合并后的仓库里面板源码（backend/ 与 frontend/）就在仓库根目录，
# 因此默认从仓库自身打包；仍可用 PANEL_SRC=... 指向别处。
PANEL_SRC="${PANEL_SRC:-${ROOT}}"
OUT="${OUT:-${ROOT}/panel/ovpnmanager-panel.tar.gz}"
BUILD="${ROOT}/.build/ovpnmanager-panel"

info() { printf '[*] %s\n' "$*"; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

[ -d "${PANEL_SRC}/backend/app" ] || die "找不到面板源码：${PANEL_SRC}/backend/app（可用 PANEL_SRC=... 指定）"
[ -f "${PANEL_SRC}/frontend/ovpnmanager/index.html" ] || die "找不到前端构建产物：${PANEL_SRC}/frontend/ovpnmanager/index.html
    请先在 frontend/ 下执行：npm ci && npm run build"

info "清理构建目录"
rm -rf "${ROOT}/.build"
mkdir -p "${BUILD}/backend" "${BUILD}/frontend" "${BUILD}/templates"

info "复制后端源码"
cp -a "${PANEL_SRC}/backend/." "${BUILD}/backend/"
# 去掉开发/本地产物，避免把虚拟环境、临时库、缓存、诊断残留打进分发包
rm -rf "${BUILD}/backend/.venv" "${BUILD}/backend/.venv-test" "${BUILD}/backend/.tmp-test" "${BUILD}/backend/data"
find "${BUILD}/backend" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "${BUILD}/backend" -maxdepth 1 -type d \
  \( -name 'probe-*' -o -name 'tests-verify-*' -o -name 'tmp*' -o -name '.build*' \) \
  -exec rm -rf {} + 2>/dev/null || true
find "${BUILD}/backend" -type f -name '*.pyc' -delete 2>/dev/null || true
# 兜底：分发包里不允许出现任何 sqlite 文件
find "${BUILD}" -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) -delete 2>/dev/null || true

info "复制前端构建产物"
cp -a "${PANEL_SRC}/frontend/ovpnmanager/." "${BUILD}/frontend/"

info "复制模板"
cp -a "${ROOT}/templates/." "${BUILD}/templates/"

printf '%s\n' "${VERSION:-$(date +%Y.%m.%d)}" > "${BUILD}/VERSION"

info "打包"
mkdir -p "$(dirname "$OUT")"
tar -czf "$OUT" -C "${ROOT}/.build" ovpnmanager-panel
rm -rf "${ROOT}/.build"

info "完成：${OUT}（$(du -h "$OUT" | cut -f1)）"
info "内容概览："
# 注意用 sed 而不是 head：set -o pipefail 下 head 提前关闭管道会让脚本以 141 退出
tar -tzf "$OUT" | sed -n '1,12p'
printf '  ... 共 %s 个文件\n' "$(tar -tzf "$OUT" | wc -l | tr -d ' ')"
