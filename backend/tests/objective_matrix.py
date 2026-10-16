#!/usr/bin/env python3
"""把「12 项修复目标」逐条映射到可复核的证据。

两类证据，缺一不可：

* **行为证据** —— ``tests/test_fixes.py`` 里具体断言 ID（会真实执行；只统计 PASS）
* **源码证据** —— 文件 + 正则（防止某次重构把修复悄悄改回去）

用法：
    backend/.venv/bin/python tests/objective_matrix.py

退出码非 0 表示有目标缺乏证据。输出可以直接贴进交付说明里。
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SUITE = ROOT / "backend" / "tests" / "test_fixes.py"


def _bundle_size() -> tuple[bool, str]:
    dist = ROOT / "frontend" / "ovpnmanager"
    if not dist.exists():
        return False, "未构建（先在 frontend/ 执行 npm run build）"
    total = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
    ok = total < 1_200_000
    return ok, f"{total:,} 字节（修复前 1,439,385）"


def _no_committed_junk() -> tuple[bool, str]:
    junk = [
        ROOT / "backend" / "nohup.out",
        ROOT / "frontend" / "nohup.out",
    ]
    present = [str(p.relative_to(ROOT)) for p in junk if p.exists()]
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    rule_ok = "frontend/ovpnmanager/" in gitignore
    ok = not present and rule_ok
    return ok, f"残留文件={present or '无'}；.gitignore 覆盖构建产物={rule_ok}"


def _no_stale_duplicate() -> tuple[bool, str]:
    """上游把 services/management.py 整份拷成了 tests/test_mangement.py（名字还拼错）。

    它不是测试，只是同一实现的第二份拷贝，改代码要同步两处、极易漏改，因此删除。
    """
    stale = ROOT / "backend" / "tests" / "test_mangement.py"
    if stale.exists():
        return False, "仍存在 services/management.py 的重复副本"
    return True, "已移除 management.py 的重复副本"


ITEMS: list[dict] = [
    {
        "id": 1,
        "title": "消除 307 尾斜杠重定向与 CORS 预检开销",
        "assertions": ["C3", "C4", "C5"],
        "markers": [
            ("frontend/src/api/servers.js", r'get\("/servers/"\)'),
            ("frontend/src/api/clients.js", r'(get|post)\("/clients/"'),
            ("deploy/templates/nginx-ovpnmanager.conf", r"gzip on;"),
            ("deploy/templates/nginx-ovpnmanager.conf", r"proxy_pass http://127\.0\.0\.1:"),
            ("backend/app/core/config.py", r"cors_max_age"),
        ],
    },
    {
        "id": 2,
        "title": "仪表盘串行 management I/O（单连接/去重/TTL/熔断）",
        "assertions": ["A9", "A10", "A11", "A18", "A21", "A22", "C12"],
        "markers": [
            ("backend/app/services/management.py", r"class _TTLCache"),
            ("backend/app/services/management.py", r"class _CircuitBreaker"),
            ("backend/app/services/management.py", r"self\._pending"),
            ("backend/app/api/api_v1/endpoints/dashboard.py", r"status_details\(force=refresh\)"),
        ],
    },
    {
        "id": 3,
        "title": "GET /clients 恢复只读（写操作移到后台线程）",
        "assertions": ["C7"],
        "markers": [
            ("backend/app/services/status_sync.py", r"def sync_once"),
            ("backend/app/services/status_sync.py", r"def start_background_sync"),
            ("backend/app/main.py", r"status_sync\.start_background_sync\(\)"),
        ],
    },
    {
        "id": 4,
        "title": "审计日志 created_at 加索引",
        "assertions": ["G1", "G2"],
        "markers": [
            ("backend/app/models/audit_log.py", r"ix_auditlog_created_at"),
            ("backend/app/models/audit_log.py", r'__tablename__ = "auditlog"'),
            ("backend/app/main.py", r"CREATE INDEX IF NOT EXISTS"),
        ],
    },
    {
        "id": 5,
        "title": "用机器可读方式判断服务状态（不再解析 status 文本）",
        "assertions": ["B1", "B2", "B3", "B4", "B5"],
        "markers": [
            ("backend/app/services/openvpn.py", r"_systemctl\("),
            ("backend/app/services/openvpn.py", r"LoadState"),
            ("backend/app/core/config.py", r"systemctl_bin"),
        ],
        "absent": [
            # 旧的文本匹配必须彻底消失，否则"已停止显示 unknown"会复发
            ("backend/app/services/openvpn.py", r'active: active \(running\)'),
            ("backend/app/api/api_v1/endpoints/servers.py", r"def _status_from_output"),
        ],
    },
    {
        "id": 6,
        "title": "吊销/删除不再重启整个 OpenVPN 服务",
        "assertions": ["G4", "G5", "G6"],
        "markers": [
            ("backend/app/services/openvpn.py", r"def apply_crl_change"),
            ("backend/app/services/client_tasks.py", r"apply_crl_change\(\)"),
            ("backend/app/services/client_tasks.py", r"def kick_session"),
        ],
        "absent": [
            ("backend/app/services/client_tasks.py", r'service_action\("restart"\)'),
            ("backend/app/api/api_v1/endpoints/clients.py", r'service_action\("restart"\)'),
        ],
    },
    {
        "id": 7,
        "title": "修复失效的 OVPNM_DB_PATH 配置键",
        "assertions": ["G7", "G8"],
        "markers": [
            ("backend/app/core/config.py", r"AliasChoices\(\"SQLITE_PATH\", \"OVPNM_DB_PATH\"\)"),
        ],
    },
    {
        "id": 8,
        "title": "EasyRSA 证书签发移出请求路径",
        "assertions": ["D1", "D2", "D4", "D9", "D10", "D13", "D15", "D23", "D24"],
        "markers": [
            ("backend/app/services/jobs.py", r"class JobRegistry"),
            ("backend/app/services/jobs.py", r"max_workers: int = 1"),
            ("backend/app/services/client_tasks.py", r"def create_client_task"),
            ("backend/app/services/client_tasks.py", r"def _rollback_client"),
            ("backend/app/api/api_v1/endpoints/jobs.py", r"def get_job"),
            ("backend/app/api/api_v1/__init__.py", r"jobs\.router"),
        ],
    },
    {
        "id": 9,
        "title": "安全：随机密钥/改口令/降权/限制暴露面",
        "assertions": ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "F22", "F23", "F24", "F30"],
        "markers": [
            ("backend/app/core/config.py", r"_PLACEHOLDER_MARKERS"),
            ("backend/app/api/api_v1/endpoints/auth.py", r"ratelimit\.check"),
            ("backend/app/api/api_v1/endpoints/auth.py", r"change-password"),
            ("backend/app/api/security.py", r"if not user\.is_active"),
            ("deploy/install.sh", r"rand_secret"),
            ("deploy/install.sh", r"visudo -cf"),
            ("deploy/templates/sudoers-ovpnmanager", r"NOPASSWD"),
            ("deploy/templates/systemctl-wrapper.sh", r"refused argument"),
            ("deploy/templates/nginx-ovpnmanager.conf", r"deny all;"),
            ("deploy/rotate-secret.sh", r"SECRET_KEY"),
            ("backend/.env-example", r"change-me-generate-with-openssl-rand-hex-32"),
        ],
        "absent": [
            # 公开过的真实值不得再出现在示例配置里
            ("backend/.env-example", r"qa111-wsx-111-edc-rfv-111"),
            ("backend/.env-example", r"58\.213\.74\.150"),
        ],
    },
    {
        "id": 10,
        "title": "隧道速度：UDP/tls-crypt/data-ciphers/缓冲调优/模板派生",
        "assertions": ["F15", "F16", "F17", "G9", "G11", "G13", "G15", "G16"],
        "markers": [
            ("deploy/templates/server.conf.template", r"^proto __OVPN_PROTO__$"),
            ("deploy/templates/server.conf.template", r"^tls-crypt "),
            ("deploy/templates/server.conf.template", r"^data-ciphers "),
            ("deploy/templates/server.conf.template", r"^sndbuf "),
            ("deploy/templates/server.conf.template", r"^fast-io$"),
            ("backend/app/services/certs.py", r"server_conf\.load\(\)"),
            ("backend/app/services/ccd.py", r"def _ifconfig_push"),
            ("deploy/install.sh", r"grep -qm1 -E '\\baes\\b' /proc/cpuinfo"),
        ],
    },
    {
        "id": 11,
        "title": "前端体积（按需 Element Plus + gzip）",
        "assertions": [],
        "markers": [
            ("frontend/src/main.js", r"el-message\.css"),
            ("frontend/vite.config.js", r"ElementPlusResolver"),
            ("deploy/templates/nginx-ovpnmanager.conf", r"gzip_types"),
        ],
        "computed": [("前端产物体积 < 1.2MB", _bundle_size)],
    },
    {
        "id": 12,
        "title": "仓库卫生与时区一致性",
        "assertions": [],
        "markers": [
            (".gitignore", r"frontend/ovpnmanager/"),
            (".gitignore", r"nohup\.out"),
            ("backend/app/utils/time.py", r"def utcnow_naive"),
            ("backend/app/utils/time.py", r"ZoneInfo\(\"Asia/Shanghai\"\)"),
            ("backend/app/api/api_v1/endpoints/dashboard.py", r"utcnow_naive\(\)"),
        ],
        "computed": [
            ("不提交构建产物/日志，且 .gitignore 覆盖真实 outDir", _no_committed_junk),
            ("移除 management.py 的重复副本", _no_stale_duplicate),
        ],
    },
]


def run_suite() -> tuple[set[str], str]:
    """执行修复套件，返回 (PASS 的断言 ID 集合, 原始输出尾部)。"""
    proc = subprocess.run(
        [sys.executable, str(SUITE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    passed: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        match = re.match(r"\s*\[PASS\]\s+([A-Z]\d+)\b", line)
        if match:
            passed.add(match.group(1))
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
    return passed, tail


def _code_only(path: pathlib.Path) -> str:
    """读取文件内容；对 .py 去掉注释与文档字符串。

    为什么需要：``absent`` 检查是为了确认"旧的错误实现已经删掉"。但模块文档字符串
    里往往**引用**旧实现来解释修复原因（例如 openvpn.py 的 docstring 引用了
    ``active: active (running)`` 这个已废弃的文本匹配）。若不区分文档与代码，
    检查会把解释性文字当成回归，产生假阳性。

    注意只去掉 docstring 与注释，**保留**普通字符串字面量：像
    ``if "active: active (running)" in text`` 这样的旧 bug，其错误恰恰藏在
    字符串字面量里，必须仍能被检出。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".py":
        return text

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text

    lines = text.splitlines()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        # 注意：只有 Module/FunctionDef/ClassDef 等的 body 是语句列表；
        # Lambda.body / IfExp.body 是表达式节点，不能下标访问。
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            for index in range(first.lineno - 1, (first.end_lineno or first.lineno)):
                if 0 <= index < len(lines):
                    lines[index] = ""

    return "\n".join(re.sub(r"#.*$", "", line) for line in lines)


def check_markers(item: dict) -> list[str]:
    problems: list[str] = []
    for rel, pattern in item.get("markers", []):
        path = ROOT / rel
        if not path.exists():
            problems.append(f"缺少文件 {rel}")
            continue
        if not re.search(pattern, _code_only(path), re.MULTILINE):
            problems.append(f"{rel} 未匹配 /{pattern}/")
    for rel, pattern in item.get("absent", []):
        path = ROOT / rel
        if path.exists() and re.search(pattern, _code_only(path), re.MULTILINE):
            problems.append(f"{rel} 仍存在已修复的旧实现 /{pattern}/")
    return problems


def main() -> int:
    print("运行修复验证套件（tests/test_fixes.py）…")
    passed, tail = run_suite()
    print(f"  断言通过数量：{len(passed)}")
    print(f"  {tail}\n")

    failures: list[str] = []
    print(f"{'#':<3} {'目标':<44} {'行为证据':<12} {'源码证据':<10} 结论")
    print("-" * 100)

    for item in ITEMS:
        wanted = item.get("assertions", [])
        missing = [a for a in wanted if a not in passed]
        behaviour = f"{len(wanted) - len(missing)}/{len(wanted)}" if wanted else "—"

        problems = check_markers(item)
        for label, fn in item.get("computed", []):
            ok, detail = fn()
            if not ok:
                problems.append(f"{label}：{detail}")
        markers = "OK" if not problems else "FAIL"

        status = "PASS" if not missing and not problems else "FAIL"
        if status == "FAIL":
            failures.append(item["title"])
        print(f"{item['id']:<3} {item['title']:<44} {behaviour:<12} {markers:<10} {status}")
        if missing:
            print(f"    缺少通过的断言：{', '.join(missing)}")
        for problem in problems:
            print(f"    源码证据问题：{problem}")

    print("-" * 100)
    if failures:
        print(f"\n有 {len(failures)} 项目标证据不足：")
        for title in failures:
            print(f"  - {title}")
        return 1
    print("\n12 项目标全部具备行为证据与源码证据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
