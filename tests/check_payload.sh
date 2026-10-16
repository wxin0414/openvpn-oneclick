#!/usr/bin/env bash
# 校验分发包内容（开发机上运行，检查 panel/*.tar.gz 是否干净且完整）
set -uo pipefail
# 切到仓库根目录（本脚本在 tests/ 下）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

TB="panel/ovpnmanager-panel.tar.gz"
[ -f "$TB" ] || { echo "找不到 $TB"; exit 1; }

LIST="$(mktemp)"
tar -tzf "$TB" > "$LIST"

echo "文件总数 : $(wc -l < "$LIST" | tr -d ' ')"
echo "体积     : $(du -h "$TB" | cut -f1)"
echo
echo "--- 必需内容 ---"
for want in \
  'ovpnmanager-panel/backend/app/main.py' \
  'ovpnmanager-panel/backend/requirements.txt' \
  'ovpnmanager-panel/frontend/index.html' \
  'ovpnmanager-panel/templates/env.template' \
  'ovpnmanager-panel/templates/nginx.conf' \
  'ovpnmanager-panel/templates/ovpnmanager.service' \
  'ovpnmanager-panel/templates/sudoers' \
  'ovpnmanager-panel/templates/systemctl-wrapper.sh' \
  'ovpnmanager-panel/templates/logrotate' \
  'ovpnmanager-panel/VERSION' ; do
  if grep -qx "$want" "$LIST"; then echo "  OK   $want"; else echo "  缺失 $want"; MISSING=1; fi
done

echo
echo "--- 前端产物 ---"
if grep -q 'ovpnmanager-panel/frontend/assets/.*\.js$' "$LIST"; then
  echo "  OK   含 assets/*.js"
else
  echo "  缺失 前端 assets"
  MISSING=1
fi

echo
echo "--- 不应包含的残留 ---"
if grep -E '\.venv|node_modules|__pycache__|\.pyc$|\.db$|probe-|tmpssl|nohup' "$LIST"; then
  echo "  ^ 发现残留，需清理"
  DIRTY=1
else
  echo "  （无）"
fi

rm -f "$LIST"
echo
if [ "${MISSING:-0}" = "1" ] || [ "${DIRTY:-0}" = "1" ]; then
  echo "分发包校验失败"
  exit 1
fi
echo "分发包校验通过"
