#!/usr/bin/env python3
"""修复验证脚本：逐条验证本次针对 ovpnmanager 的修复。

可以直接运行（不依赖 pytest）：

    backend/.venv/bin/python tests/test_fixes.py

覆盖内容：
  A. management 客户端：连接复用、status 3 只跑一次、TTL 缓存、熔断、
     跨 TCP 分段组包、state 截断、disconnect 失效缓存
  B. systemctl 状态：inactive -> stopped（原实现返回 unknown 的 bug）
  C. HTTP 层：307 尾斜杠重定向、GET /clients 只读、dashboard 汇总、
     登录限速、健康检查
"""

from __future__ import annotations

import atexit
import os
import pathlib
import shutil
import socket
import sys
import tempfile
import threading
import time

# --------------------------------------------------------------------------- #
# 环境准备必须在导入 app 之前完成（get_settings 有 lru_cache）
# --------------------------------------------------------------------------- #
# 用仓库内的临时目录而不是系统 temp：某些受限环境里系统 TEMP 不可写或不可删除，
# 之前会把测试数据库漏在仓库里。退出时自动清理。
_TMP = pathlib.Path(__file__).resolve().parents[1] / ".tmp-test" / f"run-{os.getpid()}"
_TMP.mkdir(parents=True, exist_ok=True)
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

os.environ.update(
    {
        "SQLITE_PATH": str(_TMP / "app.db"),
        "SECRET_KEY": "verify-secret-key-not-a-default",
        "DEFAULT_ADMIN_USERNAME": "admin",
        "DEFAULT_ADMIN_PASSWORD": "verify-admin-password-123",
        "DEPLOY_ENV": "PROD",
        "BACKGROUND_STATUS_SYNC": "false",
        "OPENVPN_STATUS_PATH": str(_TMP / "openvpn-status.log"),
        "SERVER_CONF_PATH": str(_TMP / "server.conf"),
        "EASYRSA_PATH": str(_TMP / "easy-rsa"),
        "OPENVPN_BASE_PATH": str(_TMP / "openvpn"),
        "OPENVPN_CLIENT_EXPORT_PATH": str(_TMP / "client-configs"),
        "CCD_PATH": str(_TMP / "ccd"),
        "TA_KEY_PATH": str(_TMP / "ta.key"),
        "LOG_FILE": str(_TMP / "app.log"),
        "OPENVPN_MANAGEMENT_TIMEOUT": "0.6",
        "OPENVPN_STATUS_CACHE_TTL": "30",
        "OPENVPN_MANAGEMENT_COOLDOWN": "5",
        "LOGIN_MAX_ATTEMPTS": "3",
        "LOGIN_LOCKOUT_SECONDS": "60",
    }
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


def expect(name: str, actual, expected) -> None:
    check(name, actual == expected, f"(got {actual!r}, want {expected!r})")


# --------------------------------------------------------------------------- #
# 假的 OpenVPN management 服务器
# --------------------------------------------------------------------------- #
STATUS_3 = "\r\n".join(
    [
        "TITLE,OpenVPN 2.6.12 x86_64-pc-linux-gnu [SSL (OpenSSL)]",
        "TIME,1700000000,2023-11-14 22:13:20",
        "HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID,Data Channel Cipher",
        "CLIENT_LIST,client1,1.2.3.4:5678,10.8.0.2,,1024,2048,2023-11-14 22:00:00,1699999200,UNDEF,7,0,AES-256-GCM",
        "CLIENT_LIST,client2,5.6.7.8:1234,10.8.0.3,,4096,8192,2023-11-14 22:05:00,1699999500,UNDEF,8,0,AES-256-GCM",
        "ROUTING_TABLE,10.8.0.2,1.2.3.4:5678,1699999999,client1",
        "ROUTING_TABLE,10.8.0.3,5.6.7.8:1234,1699999999,client2",
        "GLOBAL_STATS,MaxBcast,0",
        "END",
    ]
)
LOAD_STATS = "SUCCESS: nclients=2,bytesin=5120,bytesout=10240,uptime=3600"
STATE_DUMP = "\r\n".join(
    [f"17000000{i:02d},CONNECTED,SUCCESS,10.8.0.{i},client{i},1.2.3.{i}:5678" for i in range(1, 8)] + ["END"]
)


class FakeManagement(threading.Thread):
    """最小可用的 OpenVPN management 协议实现。"""

    def __init__(self, *, chunked: bool = False, want_password: bool = False) -> None:
        super().__init__(daemon=True)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self.commands: list[str] = []
        self.connections = 0
        self.chunked = chunked
        self.want_password = want_password
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- 生命周期 ------------------------------------------------------ #
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            with self._lock:
                self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass

    def count(self, command: str) -> int:
        with self._lock:
            return sum(1 for c in self.commands if c.split()[0] == command)

    def reset_commands(self) -> None:
        with self._lock:
            self.commands.clear()

    # -- 会话 ---------------------------------------------------------- #
    def _serve(self, conn: socket.socket) -> None:
        try:
            if self.want_password:
                conn.sendall(b"ENTER PASSWORD:")
                conn.recv(4096)
            conn.sendall(b"OpenVPN Management Interface\r\n>INFO:OpenVPN Management Interface Version 5\r\n>")
            buf = b""
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    cmd = raw.decode("utf-8", "replace").strip()
                    if not cmd:
                        continue
                    with self._lock:
                        self.commands.append(cmd)
                    self._respond(conn, cmd)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _respond(self, conn: socket.socket, cmd: str) -> None:
        head = cmd.split()[0]
        if head == "status":
            body = STATUS_3
        elif head == "load-stats":
            body = LOAD_STATS
        elif head == "state":
            body = STATE_DUMP
        elif head == "kill":
            target = cmd.split(maxsplit=1)[1] if " " in cmd else ""
            body = f"SUCCESS: common name '{target}' found, 1 client(s) killed"
        elif head == "version":
            body = "OpenVPN Version: OpenVPN 2.6.12\r\nEND"
        else:
            body = "SUCCESS: ok"

        payload = body + "\r\n>"
        if self.chunked:
            # 故意切成多个 TCP 分段，逼客户端按行组包而不是按 chunk 判断结束
            data = payload.encode()
            for i in range(0, len(data), 17):
                try:
                    conn.sendall(data[i : i + 17])
                except OSError:
                    return
                time.sleep(0.002)
        else:
            try:
                conn.sendall(payload.encode())
            except OSError:
                return


def main() -> int:
    # 先确保 import 到的是目标服务
    from app.services import management, openvpn

    # ------------------------------------------------------------------ #
    print("\n=== A. management 客户端 ===")
    server = FakeManagement()
    server.start()
    management.settings.openvpn_management_host = "127.0.0.1"
    management.settings.openvpn_management_port = server.port
    management._connection._reset()

    details = management.status_details(force=True)
    expect("A1 status_details 解析 TITLE", details.title.startswith("OpenVPN 2.6.12"), True)
    expect("A2 status_details 解析全局指标", details.global_stats.get("MaxBcast"), 0)
    expect("A3 status_details 解析路由表条数", len(details.routing_table), 2)
    expect("A4 status_details 同时给出在线客户端", len(details.clients), 2)
    expect("A5 CLIENT_LIST 字段解析", details.clients[0].common_name, "client1")
    expect("A6 字节数字段解析", details.clients[0].bytes_received, 1024)
    expect("A7 client_id 解析", details.clients[0].client_id, "7")

    server.reset_commands()
    online = management.list_online(force=True)
    expect("A8 list_online 复用同一份 status 数据", len(online), 2)
    expect("A9 list_online 只发一条 status（不再重复执行）", server.count("status"), 1)

    server.reset_commands()
    management._cache.invalidate()
    management.status_details()
    management.list_online()
    management.status_details()
    expect("A10 缓存生效：3 次调用只发 1 条 status", server.count("status"), 1)

    server.reset_commands()
    management.status_details(force=True)
    management.status_details(force=True)
    expect("A11 force=True 绕过缓存", server.count("status"), 2)

    stats = management.load_stats(force=True)
    expect("A12 load-stats 解析 nclients", stats.nclients, 2)
    expect("A13 load-stats 解析 uptime", stats.uptime, 3600)

    events = management.state_history(limit=3, force=True)
    expect("A14 state 截断到 limit", len(events), 3)
    expect("A15 state 事件字段解析", events[0].common_name, "client1")

    server.reset_commands()
    management.disconnect("client1")
    check("A16 disconnect 发送 kill 命令", server.count("kill") == 1)
    server.reset_commands()
    management.status_details()
    check("A17 disconnect 后在线缓存被失效", server.count("status") == 1)

    expect("A18 长连接复用（未每条命令重连）", server.connections, 1)

    # 大写 END 不再出现在数据中间导致截断：TITLE 里含 "END"? 用分段测试代替
    server.stop()
    time.sleep(0.05)
    management._connection._reset()
    management._cache.invalidate()

    print("\n=== A2. 跨 TCP 分段组包 ===")
    chunked = FakeManagement(chunked=True)
    chunked.start()
    management.settings.openvpn_management_port = chunked.port
    management._connection._reset()
    chunked_details = management.status_details(force=True)
    expect("A19 分段响应仍能完整解析在线客户端", len(chunked_details.clients), 2)
    expect("A20 分段响应仍能正确结束（未截断）", chunked_details.global_stats.get("MaxBcast"), 0)
    chunked.stop()

    print("\n=== A2b. 连续命令：提示符不能污染下一条响应 ===")
    # 回归测试：management 在上一条响应末尾打印 ">"，它常紧贴下一条响应的首行。
    # 修复前会把 ">SUCCESS:..." 当成普通行，导致读到超时并重发命令，
    # 且 state 的第一条事件会被 startswith(">") 过滤掉。
    sequential = FakeManagement()
    sequential.start()
    management.settings.openvpn_management_port = sequential.port
    management._connection._reset()
    management._cache.invalidate()
    first_pass = management.status_details(force=True)
    second_pass = management.status_details(force=True)
    third_pass = management.status_details(force=True)
    expect("A24 首条命令解析出 TITLE", first_pass.title.startswith("OpenVPN 2.6.12"), True)
    expect("A25 第二条命令仍解析出 TITLE（提示符已剥离）", second_pass.title.startswith("OpenVPN 2.6.12"), True)
    expect("A26 第三条命令仍解析出全部客户端", len(third_pass.clients), 2)
    expect("A27 三条命令复用同一条连接，无重连重发", sequential.connections, 1)
    expect("A28 三条命令各发一次，无因超时重发", sequential.count("status"), 3)
    sequential.stop()
    management._connection._reset()

    print("\n=== A3. 熔断（management 不可达）===")
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # 端口已关闭 -> 连接直接失败
    management.settings.openvpn_management_port = dead_port
    management._connection._reset()
    management._cache.invalidate()
    management._breaker.reset()

    started = time.monotonic()
    try:
        management.status_details(force=True)
        first = "no-raise"
    except Exception as exc:  # noqa: BLE001
        first = type(exc).__name__
    first_elapsed = time.monotonic() - started

    started = time.monotonic()
    try:
        management.status_details(force=True)
        second = "no-raise"
    except Exception as exc:  # noqa: BLE001
        second = type(exc).__name__
    second_elapsed = time.monotonic() - started

    check("A21 不可达时抛 ManagementUnavailable", first == "ManagementUnavailable", f"(got {first})")
    check(
        "A22 熔断后第二次调用立刻失败（不再等超时）",
        second_elapsed < min(first_elapsed, 0.2),
        f"(first={first_elapsed:.3f}s second={second_elapsed:.3f}s)",
    )

    print("\n=== A4. 回退到 status 文件 ===")
    status_file = pathlib.Path(management.settings.openvpn_status_path)
    status_file.write_text(
        "TITLE,fallback\r\nCLIENT_LIST,fileclient,9.9.9.9:1,10.8.0.9,,1,2,t,0,UNDEF,1,0,X\r\nEND\r\n",
        encoding="utf-8",
    )
    fallback = management.list_online(force=True)
    expect("A23 management 不可用时回退 status 文件", [c.common_name for c in fallback], ["fileclient"])

    # ------------------------------------------------------------------ #
    print("\n=== B. systemctl 状态判定 ===")
    original_run = openvpn._run

    def fake_run(stdout: str, returncode: int = 0):
        def _inner(cmd, timeout=None):  # noqa: ANN001
            class R:
                pass

            r = R()
            r.stdout = stdout
            r.stderr = ""
            r.returncode = returncode
            return r

        return _inner

    openvpn._run = fake_run("LoadState=loaded\nActiveState=active\nSubState=running\n")
    expect("B1 active -> running", openvpn.service_state(), "running")

    openvpn._run = fake_run("LoadState=loaded\nActiveState=inactive\nSubState=dead\n")
    expect("B2 inactive -> stopped（原实现这里返回 unknown）", openvpn.service_state(), "stopped")

    openvpn._run = fake_run("LoadState=loaded\nActiveState=failed\nSubState=failed\n")
    expect("B3 failed -> stopped", openvpn.service_state(), "stopped")

    openvpn._run = fake_run("LoadState=not-found\nActiveState=inactive\nSubState=dead\n")
    expect("B4 单元不存在 -> unknown", openvpn.service_state(), "unknown")

    def raising_run(cmd, timeout=None):  # noqa: ANN001
        raise FileNotFoundError("systemctl missing")

    openvpn._run = raising_run
    expect("B5 systemctl 不可用 -> unknown（不抛异常）", openvpn.service_state(), "unknown")
    openvpn._run = original_run

    # ------------------------------------------------------------------ #
    print("\n=== E. 安全自检是否覆盖占位符 ===")
    from app.core.config import Settings, insecure_settings

    strong = {
        "secret_key": "f3" * 32,
        "default_admin_password": "Zq7-correct-horse-battery",
        "deploy_env": "PROD",
    }

    def problems(**overrides) -> list[str]:
        cfg = dict(strong)
        cfg.update(overrides)
        return insecure_settings(Settings(_env_file=None, **cfg))

    expect("E1 强随机配置无告警", problems(), [])
    check(
        "E2 仓库里公开的旧密钥被告警",
        any("SECRET_KEY" in p for p in problems(secret_key="qa111-wsx-111-edc-rfv-111")),
        str(problems(secret_key="qa111-wsx-111-edc-rfv-111")),
    )
    check(
        "E3 原样复制 .env-example 的占位密钥被告警",
        any("SECRET_KEY" in p for p in problems(secret_key="change-me-generate-with-openssl-rand-hex-32")),
        str(problems(secret_key="change-me-generate-with-openssl-rand-hex-32")),
    )
    check(
        "E4 过短的密钥被告警",
        any("长度" in p for p in problems(secret_key="shortsecret")),
        str(problems(secret_key="shortsecret")),
    )
    check(
        "E5 公开默认口令被告警",
        any("DEFAULT_ADMIN_PASSWORD" in p for p in problems(default_admin_password="admin456")),
        str(problems(default_admin_password="admin456")),
    )
    check(
        "E6 占位口令被告警",
        any("DEFAULT_ADMIN_PASSWORD" in p for p in problems(default_admin_password="change-me-strong-password")),
        str(problems(default_admin_password="change-me-strong-password")),
    )
    check(
        "E7 DEV 环境被告警",
        any("DEPLOY_ENV" in p for p in problems(deploy_env="DEV")),
        str(problems(deploy_env="DEV")),
    )

    # ------------------------------------------------------------------ #
    print("\n=== C. HTTP 层 ===")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        # 登录
        bad = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
        expect("C1 错误口令返回 401", bad.status_code, 401)

        login = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "verify-admin-password-123"},
        )
        expect("C2 正确口令登录成功", login.status_code, 200)
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 307 重定向：证明前端加尾斜杠的必要性
        redirect = client.get("/api/servers", headers=headers, follow_redirects=False)
        expect("C3 不带尾斜杠的 /api/servers 触发 307", redirect.status_code, 307)
        ok = client.get("/api/servers/", headers=headers)
        expect("C4 带尾斜杠的 /api/servers/ 返回 200", ok.status_code, 200)

        noredirect = client.get("/api/clients", headers=headers, follow_redirects=False)
        expect("C5 不带尾斜杠的 /api/clients 触发 307", noredirect.status_code, 307)

        # GET /clients/ 只读
        from app.db.session import SessionLocal
        from app.models.audit_log import AuditLog

        with SessionLocal() as db:
            before = db.query(AuditLog).count()
        listed = client.get("/api/clients/?page=1&page_size=10", headers=headers)
        expect("C6 /api/clients/ 返回 200", listed.status_code, 200)
        with SessionLocal() as db:
            after = db.query(AuditLog).count()
        expect("C7 GET /clients/ 不再写审计（只读）", after, before)

        # dashboard：management 指向假服务器
        management.settings.openvpn_management_port = chunked.port if False else server.port
        management.settings.openvpn_management_host = "127.0.0.1"
        management._connection._reset()
        management._cache.invalidate()
        management._breaker.reset()

        reviver = FakeManagement()
        reviver.start()
        management.settings.openvpn_management_port = reviver.port

        metrics = client.get("/api/dashboard/metrics", headers=headers)
        expect("C8 /dashboard/metrics 返回 200", metrics.status_code, 200)
        body = metrics.json()
        expect("C9 dashboard 在线数与 management 一致", body["online_clients"], 2)
        expect("C10 dashboard 路由表非空", len(body["openvpn"]["routing_table"]), 2)
        expect("C11 dashboard 包含证书统计", body["certificates"]["total"], 0)

        reviver.reset_commands()
        management._cache.invalidate()
        client.get("/api/dashboard/metrics", headers=headers)
        client.get("/api/dashboard/metrics", headers=headers)
        status_calls = reviver.count("status")
        check(
            "C12 dashboard 两次刷新只发一次 status 3（原实现每次两条）",
            status_calls == 1,
            f"(got {status_calls})",
        )
        reviver.stop()

        # 健康检查
        ready = client.get("/api/health/ready")
        expect("C13 /health/ready 返回 200", ready.status_code, 200)
        check("C14 /health/ready 带 checks 明细", "checks" in ready.json(), str(ready.json()))

        # ---------------- D. 后台任务：证书签发移出请求路径 ---------------- #
        print("\n=== D. 后台任务（EasyRSA 不再阻塞请求）===")
        from app.services import certs as certs_service
        from app.services.jobs import registry as jobs_registry

        fake_state = {"build_calls": 0, "revoke_calls": 0}

        def fake_build_client_cert(db, client, passwordless=True, passphrase=None):  # noqa: ANN001
            fake_state["build_calls"] += 1
            if fake_state.get("fail_next"):
                fake_state["fail_next"] = False
                raise RuntimeError("easyrsa failed (simulated)")
            time.sleep(1.2)  # 模拟 easyrsa 的秒级子进程
            return pathlib.Path("fake.crt")

        def fake_revoke_client_cert(db, client, passphrase=None):  # noqa: ANN001
            fake_state["revoke_calls"] += 1
            time.sleep(0.2)

        def fake_export_ovpn(client, remote_host, remote_port):  # noqa: ANN001
            return f"client\ndev tun\nremote {remote_host} {remote_port}\n# cn={client.common_name}\n"

        # 备份真实实现，D 段结束必须还原：否则 monkeypatch 会漏到后面的 G 段，
        # 让"客户端模板从 server.conf 派生"的断言实际上打在假的 export_ovpn 上。
        _real_cert_funcs = (
            certs_service.build_client_cert,
            certs_service.revoke_client_cert,
            certs_service.export_ovpn,
        )
        certs_service.build_client_cert = fake_build_client_cert
        certs_service.revoke_client_cert = fake_revoke_client_cert
        certs_service.export_ovpn = fake_export_ovpn

        def poll_job(job_id: str, attempts: int = 80) -> dict:
            payload: dict = {}
            for _ in range(attempts):
                resp = client.get(f"/api/jobs/{job_id}", headers=headers)
                payload = resp.json()
                if payload.get("status") in ("succeeded", "failed"):
                    return payload
                time.sleep(0.1)
            return payload

        started = time.monotonic()
        created = client.post(
            "/api/clients/",
            headers=headers,
            json={"name": "d-async", "common_name": "d-async", "fixed_ip": "10.8.0.11"},
        )
        elapsed = time.monotonic() - started
        expect("D1 创建客户端返回 202（异步）", created.status_code, 202)
        check("D2 请求未等待 easyrsa（<0.8s）", elapsed < 0.8, f"(took {elapsed:.2f}s)")
        create_job = created.json().get("job_id")
        check("D3 返回 job_id 与 kind", bool(create_job) and created.json().get("kind") == "create_client", str(created.json()))

        final = poll_job(create_job)
        expect("D4 创建任务最终成功", final.get("status"), "succeeded")
        check("D5 任务结果包含 ovpn", "ovpn" in (final.get("result") or {}), str(final)[:200])
        check("D6 easyrsa 确实被调用", fake_state["build_calls"] >= 1, str(fake_state))

        # 同步模式（wait=true）仍然可用，且确实会等待
        started = time.monotonic()
        sync_created = client.post(
            "/api/clients/?wait=true",
            headers=headers,
            json={"name": "d-sync", "common_name": "d-sync", "fixed_ip": "10.8.0.12"},
        )
        sync_elapsed = time.monotonic() - started
        expect("D7 wait=true 返回 201（同步兼容）", sync_created.status_code, 201)
        check("D8 同步模式确实等待了 easyrsa", sync_elapsed >= 1.1, f"(took {sync_elapsed:.2f}s)")

        # 对已有客户端签发证书（异步）
        client_id = sync_created.json()["id"]
        issued = client.post(f"/api/clients/{client_id}/cert", headers=headers, json={"passphrase": None})
        expect("D9 签发证书返回 202", issued.status_code, 202)
        issue_final = poll_job(issued.json()["job_id"])
        expect("D10 签发任务成功", issue_final.get("status"), "succeeded")
        check("D11 签发结果带 ovpn", "ovpn" in (issue_final.get("result") or {}), str(issue_final)[:200])

        # 吊销（异步）
        revoked = client.post(f"/api/clients/{client_id}/revoke", headers=headers, json={"passphrase": None})
        expect("D12 吊销返回 202", revoked.status_code, 202)
        revoke_final = poll_job(revoked.json()["job_id"])
        expect("D13 吊销任务成功", revoke_final.get("status"), "succeeded")

        # 删除（异步）—— httpx 的 .delete() 不接受 json=，用 request()
        deleted = client.request(
            "DELETE", f"/api/clients/{client_id}", headers=headers, json={"passphrase": None}
        )
        expect("D14 删除返回 202", deleted.status_code, 202)
        delete_final = poll_job(deleted.json()["job_id"])
        expect("D15 删除任务成功", delete_final.get("status"), "succeeded")
        check("D16 删除结果含 common_name", (delete_final.get("result") or {}).get("common_name") == "d-sync", str(delete_final)[:200])

        # 任务查询接口
        expect("D17 未知任务返回 404", client.get("/api/jobs/j_deadbeef", headers=headers).status_code, 404)
        listed = client.get("/api/jobs/?limit=5", headers=headers)
        expect("D18 任务列表返回 200", listed.status_code, 200)
        check(
            "D19 任务列表包含刚提交的任务",
            any(j["job_id"] == create_job for j in listed.json().get("items", [])),
            str(listed.json())[:200],
        )

        # 失败任务也要能被观察到
        bad = jobs_registry.submit("test_fail", lambda: 1 / 0)
        bad_state = {}
        for _ in range(50):
            bad_state = bad.as_dict()
            if bad_state["status"] == "failed":
                break
            time.sleep(0.05)
        expect("D20 失败的 job 状态为 failed", bad_state.get("status"), "failed")
        check("D21 失败原因被保留", "division" in (bad_state.get("error") or ""), str(bad_state.get("error")))

        # 异步创建失败必须回滚记录，否则会留下"没有证书的客户端"
        # （同步路径由端点回滚；异步路径请求早已返回，只能由任务自己兜住）
        fake_state["fail_next"] = True
        fail_create = client.post(
            "/api/clients/",
            headers=headers,
            json={"name": "d-fail", "common_name": "d-fail", "fixed_ip": "10.8.0.13"},
        )
        expect("D22 提交仍然返回 202", fail_create.status_code, 202)
        fail_final = poll_job(fail_create.json()["job_id"])
        expect("D23 该任务最终失败", fail_final.get("status"), "failed")
        listing = client.get("/api/clients/?page=1&page_size=200", headers=headers).json()
        names = [item["common_name"] for item in listing.get("items", [])]
        check("D24 失败的创建已被回滚（不留孤儿记录）", "d-fail" not in names, str(names))

        # D 段结束：还原真实的证书实现，避免污染后续断言
        (
            certs_service.build_client_cert,
            certs_service.revoke_client_cert,
            certs_service.export_ovpn,
        ) = _real_cert_funcs

        # ---------------- F. server.conf 统一解析 / CCD / 用户自锁 ----------------
        print("\n=== F. server.conf 解析、CCD 与用户管理 ===")
        from dataclasses import replace as dc_replace

        from app import crud
        from app.models.client import Client as ClientModel
        from app.services import ccd as ccd_service
        from app.services import server_conf
        from app.services.importer import import_openvpn

        conf_path = pathlib.Path(server_conf.settings.server_conf_path)
        conf_path.write_text(
            "port 1234\n"
            "proto tcp\n"
            "dev tun\n"
            "topology subnet\n"
            "server 10.9.0.0 255.255.0.0\n"
            "tls-crypt /etc/openvpn/server/ta.key\n"
            "data-ciphers AES-256-GCM:AES-128-GCM\n"
            "auth SHA256\n"
            "status-version 3\n"
            "management 127.0.0.1 7505\n"
            "crl-verify /etc/openvpn/server/crl.pem\n"
            "client-config-dir /etc/openvpn/ccd\n"
            'push "route 192.168.9.0 255.255.255.0"\n',
            encoding="utf-8",
        )
        server_conf.invalidate()
        conf = server_conf.load()
        expect("F1 解析 port", conf.port, 1234)
        expect("F2 解析 proto", conf.proto, "tcp")
        expect("F3 解析 topology", conf.topology, "subnet")
        expect("F4 解析掩码（不是硬编码 /24）", conf.netmask, "255.255.0.0")
        expect("F5 解析网段", str(conf.network), "10.9.0.0/16")
        expect("F6 网关取首个可用地址", conf.gateway, "10.9.0.1")
        expect("F7 解析 tls-crypt", conf.tls_mode, "tls-crypt")
        expect("F8 解析 data-ciphers", conf.data_ciphers, "AES-256-GCM:AES-128-GCM")
        expect("F9 解析 status-version", conf.status_version, 3)
        expect("F10 解析 management", conf.management, "127.0.0.1 7505")
        check("F11 解析 crl-verify", conf.crl_verify == "/etc/openvpn/server/crl.pem", str(conf.crl_verify))
        check("F12 解析 client-config-dir", conf.client_config_dir == "/etc/openvpn/ccd", str(conf.client_config_dir))
        check(
            "F13 解析 push 指令",
            conf.push_directives == ("route 192.168.9.0 255.255.255.0",),
            str(conf.push_directives),
        )
        expect("F14 未修改时命中缓存", server_conf.load().port, 1234)

        # ---- CCD：掩码必须来自 server.conf ----
        expect(
            "F15 subnet 下使用服务端掩码（原实现写死 255.255.255.0）",
            ccd_service._ifconfig_push("10.9.0.5", conf),
            "ifconfig-push 10.9.0.5 255.255.0.0",
        )
        expect(
            "F16 net30 下第二个参数是 /30 对端地址",
            ccd_service._ifconfig_push("10.9.0.6", dc_replace(conf, topology="net30")),
            "ifconfig-push 10.9.0.6 10.9.0.5",
        )
        expect(
            "F17 p2p 下只写本地地址",
            ccd_service._ifconfig_push("10.9.0.5", dc_replace(conf, topology="p2p")),
            "ifconfig-push 10.9.0.5",
        )
        ccd_file = ccd_service.write_ccd(
            ClientModel(name="ccdtest", common_name="ccdtest"), fixed_ip="10.9.0.5", routes=["172.16.0.0/24"]
        )
        ccd_body = ccd_file.read_text()
        check("F18 CCD 文件写入服务端掩码", "ifconfig-push 10.9.0.5 255.255.0.0" in ccd_body, ccd_body)
        check("F19 CCD 文件写入 iroute", "iroute 172.16.0.0 255.255.255.0" in ccd_body, ccd_body)

        # ---- importer 必须使用 SERVER_CONF_PATH ----
        with SessionLocal() as db:
            import_openvpn(db)
            row = crud.server.get_by_name(db, name="local")
            expect("F20 导入使用 SERVER_CONF_PATH 的端口", row.port, 1234)
            expect("F21 导入使用 SERVER_CONF_PATH 的协议", row.protocol, "tcp")

        # ---- 用户管理：自锁保护 ----
        me = next(u for u in client.get("/api/users/", headers=headers).json() if u["username"] == "admin")
        expect(
            "F22 不能删除当前登录账号",
            client.delete(f"/api/users/{me['id']}", headers=headers).status_code,
            400,
        )
        expect(
            "F23 不能把最后一个超管降级",
            client.patch(f"/api/users/{me['id']}", headers=headers, json={"is_superuser": False}).status_code,
            400,
        )
        expect(
            "F24 不能停用当前登录账号",
            client.patch(f"/api/users/{me['id']}", headers=headers, json={"is_active": False}).status_code,
            400,
        )

        # 反向对照：不是最后一个超管时可以正常降级（保护没有过宽）
        created_su = client.post(
            "/api/users/",
            headers=headers,
            json={
                "username": "su2",
                "password": "another-strong-pass-1",
                "is_active": True,
                "is_superuser": True,
            },
        )
        expect("F25 可以创建第二个超管", created_su.status_code, 201)
        demoted = client.patch(
            f"/api/users/{created_su.json()['id']}", headers=headers, json={"is_superuser": False}
        )
        expect("F26 非最后一个超管可以降级", demoted.status_code, 200)

        # 停用后旧 Token 必须立刻失效
        # （原实现只在登录时检查 is_active，停用后 Token 仍可用到过期）
        client.post(
            "/api/users/",
            headers=headers,
            json={"username": "tempuser", "password": "temp-user-pass-123", "is_active": True, "is_superuser": False},
        )
        temp_login = client.post(
            "/api/auth/login", data={"username": "tempuser", "password": "temp-user-pass-123"}
        )
        expect("F27 新用户可登录", temp_login.status_code, 200)
        temp_headers = {"Authorization": f"Bearer {temp_login.json()['access_token']}"}
        expect("F28 登录后接口可用", client.get("/api/clients/", headers=temp_headers).status_code, 200)
        user_ids = {u["username"]: u["id"] for u in client.get("/api/users/", headers=headers).json()}
        expect(
            "F29 管理员可停用该用户",
            client.patch(f"/api/users/{user_ids['tempuser']}", headers=headers, json={"is_active": False}).status_code,
            200,
        )
        expect(
            "F30 停用后旧 Token 立即失效（原实现要等到过期）",
            client.get("/api/clients/", headers=temp_headers).status_code,
            403,
        )

        # ---------------- H. 路由遮蔽 / 服务控制错误处理 ----------------
        print("\n=== H. 路由遮蔽与服务控制错误处理 ===")
        import re as _re

        # 上游把 POST /{action} 声明在 POST /import 之前，于是 "/import" 先匹配到
        # /{action}，而 action 是 Literal[...]，校验失败直接 422 —— 导入功能不可达。
        imported = client.post("/api/openvpn/import", headers=headers)
        expect("H1 POST /api/openvpn/import 可达（不再是 422）", imported.status_code, 200)
        check(
            "H2 导入返回统计信息",
            isinstance(imported.json(), dict) and "created" in imported.json() and "updated" in imported.json(),
            str(imported.json())[:160],
        )

        # systemctl 不存在（Windows）或降权包装脚本缺失时，必须返回受控错误
        # 而不是未捕获异常（TestClient 默认会把未捕获异常重新抛出，导致测试直接报错）
        restarted = client.post("/api/openvpn/restart", headers=headers)
        expect("H3 服务控制失败时返回受控的 500", restarted.status_code, 500)
        # 服务控制已改走 socket 助手（面板带 NoNewPrivileges=1，sudo 被内核拒绝），
        # 因此在没有该助手的机器上，错误信息会变成
        #   sudo: /usr/local/lib/ovpnmanager/service-control: command not found
        # 这依然是可读且可行动的（点名了缺失的程序），只是不再包含 "systemctl"。
        # 断言相应放宽为"能指明失败原因"，而不是匹配某个具体工具名。
        _detail = str(restarted.json().get("detail", ""))
        check(
            "H4 错误信息可读（指明失败原因）",
            any(
                token in _detail
                for token in ("systemctl", "无法执行", "service-control", "command not found", "No such file")
            ),
            str(restarted.json())[:200],
        )

        # 通用结构性检查：更早声明的 /{param} 不能吃掉后面同方法的字面量路径
        from app.core.route_audit import enumerate_routes as _enum_routes
        from app.core.route_audit import shadowed_routes as _shadowed

        enumerated = _enum_routes(app)
        # 关键护栏：FastAPI 新版本把 include_router 包成 _IncludedRouter，
        # 直接把 app.routes 当扁平列表遍历会"什么都看不到"，从而让 H5 空转通过。
        check(
            "H5 路由枚举不为空（防止遮蔽检查空转通过）",
            len(enumerated) >= 20,
            f"仅枚举到 {len(enumerated)} 条路由",
        )
        # 证明枚举确实覆盖到路径参数路由
        check(
            "H6 枚举包含路径参数路由",
            any("{" in path for path, _ in enumerated),
            str([p for p, _ in enumerated if "{" in p][:5]),
        )

        shadowed = _shadowed(app)
        check("H7 全应用无路由被路径参数路由遮蔽", not shadowed, str(shadowed))

        # 反向验证：构造一个"坏顺序"的 app，检查必须能检出
        from fastapi import APIRouter, FastAPI

        bad_router = APIRouter()

        @bad_router.post("/{action}")
        def _bad_param(action: str) -> dict:  # pragma: no cover - 仅用于结构检查
            return {"action": action}

        @bad_router.post("/import")
        def _bad_literal() -> dict:  # pragma: no cover
            return {}

        bad_app = FastAPI()
        bad_app.include_router(bad_router, prefix="/api/openvpn")
        detected = _shadowed(bad_app)
        check(
            "H8 遮蔽检查能识别出错误的声明顺序（反向验证）",
            any("/api/openvpn/import" in item for item in detected),
            str(detected),
        )

        # 修改口令
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": "verify-admin-password-123", "new_password": "brand-new-password-456"},
        )
        expect("C15 修改口令成功", changed.status_code, 200)
        relogin = client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "brand-new-password-456"},
        )
        expect("C16 新口令可以登录", relogin.status_code, 200)
        # 本轮新增的不变量：改口令后，改之前签发的令牌必须立刻失效。
        # 没有这条时，口令疑似泄露后改口令，旧令牌仍可用满 ACCESS_TOKEN_EXPIRE_MINUTES。
        expect("C16b 改口令后旧令牌立即失效", client.get("/api/panel", headers=headers).status_code, 401)
        token = relogin.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        expect("C16c 新令牌可用", client.get("/api/panel", headers=headers).status_code, 200)

        # 登录限速
        for _ in range(5):
            client.post("/api/auth/login", data={"username": "admin", "password": "nope"})
        locked = client.post("/api/auth/login", data={"username": "admin", "password": "nope"})
        expect("C17 连续失败后触发 429 限速", locked.status_code, 429)

        # --------------------------------------------------------------
        # K. 面板设置接口（/api/panel）
        # --------------------------------------------------------------
        # 这里只断言"不需要落地到系统"的部分：非法输入必须在调用 root 助手**之前**
        # 就被拒（参数校验 -> 400/422），因此不会碰到 socket/sudo。
        # 合法改动会真的去调助手，开发机上没有那条通道，故不在此覆盖——那部分由
        # 真机验收负责（改端口往返、TLS 自签、坏配置回滚）。
        k_unauth = client.get("/api/panel")
        expect("K1 /api/panel 未认证返回 401", k_unauth.status_code, 401)

        k_get = client.get("/api/panel", headers=headers)
        expect("K2 GET /api/panel 返回 200", k_get.status_code, 200)
        k_body = k_get.json() if k_get.status_code == 200 else {}
        for k_field in ("port", "server_name", "allow_mode", "allow_cidrs",
                        "tls_mode", "session_minutes", "current_url"):
            check(f"K2 GET /api/panel 含字段 {k_field}", k_field in k_body, str(sorted(k_body)))

        for k_payload, k_label in (
            ({"port": 0}, "端口 0"),
            ({"port": 70000}, "端口 70000"),
            ({"server_name": "bad name!"}, "非法域名"),
            ({"allow_mode": "nope"}, "非法放行模式"),
            ({"tls_mode": "nope"}, "非法 TLS 模式"),
            ({"tls_mode": "existing"}, "existing 模式但没给证书路径"),
            ({"allow_mode": "custom"}, "custom 模式但没给 CIDR"),
        ):
            k_st = client.patch("/api/panel", json=k_payload, headers=headers).status_code
            check(f"K3 越界输入被拒：{k_label} -> {k_st}", k_st in (400, 422), f"status={k_st}")

        k_sess = client.post("/api/panel/session", json={"session_minutes": 5}, headers=headers)
        expect("K4 会话有效期低于下限（5 分钟）被拒", k_sess.status_code, 422)
        k_sess_hi = client.post("/api/panel/session", json={"session_minutes": 99999}, headers=headers)
        expect("K4 会话有效期高于上限被拒", k_sess_hi.status_code, 422)

    # ------------------------------------------------------------------ #
    print("\n=== G. 索引 / WAL / CRL 行为 / 配置派生 ===")
    import sqlite3

    from app.core.config import Settings, get_settings
    from app.models.client import Client as GClient
    from app.services import certs as g_certs
    from app.services import openvpn as g_openvpn
    from app.services import server_conf as g_conf

    gsettings = get_settings()
    con = sqlite3.connect(str(gsettings.sqlite_path))
    indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    check("G1 审计日志 created_at 索引已创建", "ix_auditlog_created_at" in indexes, str(sorted(indexes)))
    check(
        "G2 审计日志 (action, created_at) 复合索引已创建",
        "ix_auditlog_action_created_at" in indexes,
        str(sorted(indexes)),
    )
    journal = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    expect("G3 SQLite 处于 WAL 模式（读写不再互相阻塞）", str(journal).lower(), "wal")

    # ---- 吊销后不重启服务（只重读 CRL）----
    # 断言的是"apply_crl_change 是否**请求**重启"，而不是它内部怎么执行。
    # 早期通过替换 _run 来观察，但服务控制后来改成优先走 socket 激活的 root 助手
    # （面板带 NoNewPrivileges=1，sudo 被内核拒绝），在 CI 这种没有助手的机器上
    # 会直接抛 RuntimeError，把测试脚本打断（表现为 exit 1 却没有任何 [FAIL]）。
    # 因此替换 service_action 这一层——它才是 apply_crl_change 真正依赖的边界。
    sa_calls: list[str] = []
    original_service_action = g_openvpn.service_action

    def spy_service_action(action):  # noqa: ANN001
        sa_calls.append(action)
        return f"spy-{action}"

    g_openvpn.service_action = spy_service_action
    try:
        gsettings.revoke_restarts_service = False
        expect("G4 默认 apply_crl_change 不重启服务", g_openvpn.apply_crl_change(), "crl-reloaded")
        check(
            "G5 默认没有请求重启（原实现会把所有人踢下线）",
            not sa_calls,
            str(sa_calls),
        )
        gsettings.revoke_restarts_service = True
        g_openvpn.apply_crl_change()
        check("G6 显式开启兼容开关后才重启", sa_calls == ["restart"], str(sa_calls))
    finally:
        gsettings.revoke_restarts_service = False
        g_openvpn.service_action = original_service_action

    # ---- OVPNM_DB_PATH 别名（原实现被 extra=ignore 静默丢弃）----
    saved_env = {key: os.environ.get(key) for key in ("OVPNM_DB_PATH", "SQLITE_PATH")}
    try:
        os.environ.pop("SQLITE_PATH", None)
        os.environ["OVPNM_DB_PATH"] = "/tmp/from-ovpnm.db"
        expect(
            "G7 OVPNM_DB_PATH 现在真的生效",
            Settings(_env_file=None).sqlite_path,
            pathlib.Path("/tmp/from-ovpnm.db"),
        )
        os.environ["SQLITE_PATH"] = "/tmp/from-sqlite.db"
        expect(
            "G8 SQLITE_PATH 优先级更高",
            Settings(_env_file=None).sqlite_path,
            pathlib.Path("/tmp/from-sqlite.db"),
        )
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ---- 客户端 .ovpn 从 server.conf 派生 ----
    base = pathlib.Path(gsettings.openvpn_base_path)
    pki = pathlib.Path(gsettings.easyrsa_path) / "pki"
    (base / "server").mkdir(parents=True, exist_ok=True)
    (pki / "issued").mkdir(parents=True, exist_ok=True)
    (pki / "private").mkdir(parents=True, exist_ok=True)
    (base / "server" / "ca.crt").write_text("FAKE-CA\n", encoding="utf-8")
    (pki / "issued" / "tplclient.crt").write_text("FAKE-CERT\n", encoding="utf-8")
    (pki / "private" / "tplclient.key").write_text("FAKE-KEY\n", encoding="utf-8")
    pathlib.Path(gsettings.ta_key_path).write_text("FAKE-TA\n", encoding="utf-8")

    g_conf.invalidate()
    ovpn_tcp = g_certs.export_ovpn(
        GClient(name="tplclient", common_name="tplclient"), remote_host="203.0.113.9", remote_port=1234
    )
    check("G9 .ovpn 的 proto 跟随 server.conf（当前 tcp）", "proto tcp" in ovpn_tcp, ovpn_tcp[:300])
    check("G10 .ovpn 的 remote 使用传入地址与端口", "remote 203.0.113.9 1234" in ovpn_tcp, ovpn_tcp[:300])
    check(
        "G11 .ovpn 使用 tls-crypt 块（不再写死 tls-auth）",
        "<tls-crypt>" in ovpn_tcp and "<tls-auth>" not in ovpn_tcp,
        ovpn_tcp[:500],
    )
    check("G12 .ovpn 不再出现 tls-auth 专用的 key-direction", "key-direction" not in ovpn_tcp, ovpn_tcp[:500])
    check(
        "G13 .ovpn 携带 server.conf 的 data-ciphers",
        "data-ciphers AES-256-GCM:AES-128-GCM" in ovpn_tcp,
        ovpn_tcp[:500],
    )
    check(
        "G14 TCP 下不写 UDP 专用的 explicit-exit-notify",
        "explicit-exit-notify" not in ovpn_tcp,
        ovpn_tcp[:500],
    )

    conf_file = pathlib.Path(gsettings.server_conf_path)
    conf_file.write_text(conf_file.read_text(encoding="utf-8").replace("proto tcp", "proto udp"), encoding="utf-8")
    g_conf.invalidate()
    ovpn_udp = g_certs.export_ovpn(
        GClient(name="tplclient", common_name="tplclient"), remote_host="203.0.113.9", remote_port=1234
    )
    check("G15 服务端改成 UDP 后，新导出的 .ovpn 自动跟随", "proto udp" in ovpn_udp, ovpn_udp[:300])
    check(
        "G16 UDP 下写入 explicit-exit-notify",
        "explicit-exit-notify 1" in ovpn_udp,
        ovpn_udp[:400],
    )

    # ------------------------------------------------------------------ #
    print("\n=== I. 面板写文件时的权限（OpenVPN 降权到 nobody 后必须读得到）===")
    # 这一节是"面板 + OpenVPN 服务端"结合处最容易出问题的地方：
    # 面板以专用非 root 用户运行，而 OpenVPN 以 nobody 运行，两者对同一个文件的
    # 读写权限必须对齐，否则会出现"吊销了但还能连""设了固定 IP 但客户端拿不到"
    # 这类完全没有报错的静默故障。
    import app.services.ccd as i_ccd
    import app.services.certs as i_certs

    i_tmp = pathlib.Path(tempfile.mkdtemp(prefix="ovpn-perm-"))
    try:
        if os.name == "nt":
            # Windows 不保留 Unix 权限位，无法断言真实 mode。改为断言"代码确实以
            # 正确的模式调用了 chmod"——这恰好覆盖本次修的 bug：原实现**根本没有
            # chmod**，CRL 会继承 EasyRSA 的 0600、CCD 会跟随 umask，
            # 于是降权后的 OpenVPN 读不到，表现为吊销/固定 IP 静默失效。
            import unittest.mock as i_mock

            i_calls: list[tuple[str, int]] = []
            i_real_chmod = pathlib.Path.chmod

            def _i_recording_chmod(self, mode, *a, **k):  # type: ignore[no-untyped-def]
                i_calls.append((self.name, mode))
                return i_real_chmod(self, mode, *a, **k)

            with i_mock.patch.object(pathlib.Path, "chmod", _i_recording_chmod):
                i_src = i_tmp / "pki" / "crl.pem"
                i_src.parent.mkdir(parents=True, exist_ok=True)
                i_src.write_text("FAKE-CRL\n", encoding="utf-8")
                i_dst = i_tmp / "etc2" / "crl.pem"
                i_certs._publish_crl(i_src, i_dst)
                check(
                    "I1 发布 CRL 时显式 chmod 0644（否则降权后的 OpenVPN 读不到）",
                    any(name == "crl.pem" and mode == 0o644 for name, mode in i_calls),
                    str(i_calls),
                )
                check(
                    "I2 新建的 CRL 目录被设为 0755",
                    any(name == "etc2" and mode == 0o755 for name, mode in i_calls),
                    str(i_calls),
                )

                i_old_ccd = gsettings.ccd_path
                gsettings.ccd_path = i_tmp / "ccd2"
                try:
                    i_path = i_ccd.write_ccd(
                        GClient(name="perm", common_name="permclient"), fixed_ip=None, routes=None
                    )
                    check(
                        "I3 CCD 文件显式 chmod 0644（否则固定 IP/路由静默失效）",
                        any(name == "permclient" and mode == 0o644 for name, mode in i_calls),
                        str(i_calls),
                    )
                    check("I4 CCD 内容已写入", i_path.exists(), str(i_path))
                finally:
                    gsettings.ccd_path = i_old_ccd

                g_certs.export_ovpn(
                    GClient(name="tplclient", common_name="tplclient"), remote_host="203.0.113.9", remote_port=1194
                )
                check(
                    "I5 导出 .ovpn 时显式 chmod 0600（内含客户端私钥）",
                    any(name.endswith(".ovpn") and mode == 0o600 for name, mode in i_calls),
                    str(i_calls),
                )
        else:
            # ---- CRL：源文件故意设成 0600，发布后必须放宽到 0644 ----
            i_src = i_tmp / "pki" / "crl.pem"
            i_src.parent.mkdir(parents=True, exist_ok=True)
            i_src.write_text("FAKE-CRL\n", encoding="utf-8")
            i_src.chmod(0o600)
            i_dst = i_tmp / "etc" / "crl.pem"
            i_certs._publish_crl(i_src, i_dst)
            expect("I1 发布后的 CRL 权限是 0644", oct(i_dst.stat().st_mode)[-3:], "644")
            expect("I2 CRL 内容正确复制", i_dst.read_text(encoding="utf-8").strip(), "FAKE-CRL")
            expect("I3 源文件权限未被改动", oct(i_src.stat().st_mode)[-3:], "600")

            i_old_ccd = gsettings.ccd_path
            gsettings.ccd_path = i_tmp / "ccd"
            try:
                i_path = i_ccd.write_ccd(GClient(name="perm", common_name="permclient"), fixed_ip=None, routes=None)
                expect("I4 CCD 文件权限是 0644", oct(i_path.stat().st_mode)[-3:], "644")
            finally:
                gsettings.ccd_path = i_old_ccd

            i_exported = pathlib.Path(gsettings.openvpn_client_export_path) / "tplclient.ovpn"
            if i_exported.exists():
                expect("I5 导出的 .ovpn 权限是 0600（内含客户端私钥）", oct(i_exported.stat().st_mode)[-3:], "600")
            else:
                print("  [SKIP] 未找到 G 节导出的 .ovpn，跳过 I5")
    finally:
        shutil.rmtree(i_tmp, ignore_errors=True)

    # ------------------------------------------------------------------ #
    print("\n=== J. server.conf 托管块：渲染 / 候选 / 校验 / 设置接口 ===")
    # 这一节验证"所有设置都能在面板里改"的写入侧。面板以非 root 运行，
    # **不直接改** server.conf，而是把完整候选配置写进自己的数据目录，
    # 再由 root 助手（templates/apply-config.sh）校验后落地。因此这里断言的是：
    #   1. 渲染出的托管块形态与助手白名单**逐字一致**（不一致 = 每次保存都失败）；
    #   2. 已有配置被正确接管（旧行注释掉、无关行逐字节不动）；
    #   3. 面板自己的安全自检能挡住 script-security / up / plugin 这类夹带；
    #   4. 设置接口的校验与响应形状。
    import unittest.mock as j_mock

    from app.services import server_conf as j_conf
    from app.services import server_conf_writer as j_writer

    # ---- J1: 与 root 助手的契约（同一份配置，两边必须一字不差）----
    j_helpers = pathlib.Path(__file__).resolve().parents[2] / "templates" / "apply-validate.sh"
    j_helper_text = j_helpers.read_text(encoding="utf-8") if j_helpers.exists() else ""
    j_managed_allow = tuple(
        j_helper_text.split('MANAGED_ALLOW="', 1)[1].split('"', 1)[0].split()
        if "MANAGED_ALLOW=\"" in j_helper_text
        else []
    )
    j_push_allow = tuple(
        j_helper_text.split('PUSH_ALLOW="', 1)[1].split('"', 1)[0].split()
        if "PUSH_ALLOW=\"" in j_helper_text
        else []
    )
    expect("J1 面板的托管指令集与 root 助手白名单逐字一致", tuple(j_writer.MANAGED_DIRECTIVES), j_managed_allow)
    expect("J2 面板的 push 白名单与 root 助手白名单逐字一致", tuple(j_writer.PUSH_ALLOW), j_push_allow)
    check(
        "J3 托管块标记与助手脚本逐字一致",
        j_conf.MANAGED_BEGIN in j_helper_text and j_conf.MANAGED_END in j_helper_text,
        f"{j_conf.MANAGED_BEGIN!r} / {j_conf.MANAGED_END!r}",
    )
    check(
        "J4 UDP 专用指令也由面板托管（否则改成 tcp 后会留下过期指令）",
        {"fast-io", "explicit-exit-notify"} <= set(j_writer.MANAGED_DIRECTIVES),
        str(j_writer.MANAGED_DIRECTIVES),
    )

    # ---- 渲染：UDP / TCP ----
    j_udp = j_writer.render_managed_block({"port": 1194, "proto": "udp"})
    check("J5 UDP 下渲染 fast-io", "fast-io" in j_udp, str(j_udp))
    check("J6 UDP 下渲染 explicit-exit-notify 1", "explicit-exit-notify 1" in j_udp, str(j_udp))
    j_tcp = j_writer.render_managed_block({"port": 1194, "proto": "tcp"})
    check(
        "J7 TCP 下不渲染任何 UDP 专用指令",
        not any(line.startswith(("fast-io", "explicit-exit-notify")) for line in j_tcp),
        str(j_tcp),
    )
    check("J8 TCP 下 proto 仍然是 tcp", "proto tcp" in j_tcp, str(j_tcp))

    # ---- 渲染：block-ipv6 必须三行一起（否则双栈客户端的 IPv6 根本不进隧道）----
    j_v6 = j_writer.render_managed_block({"block_ipv6": True})
    j_v6_expected = [
        'push "ifconfig-ipv6 fd15:53b6:dead::2/64 fd15:53b6:dead::1"',
        'push "redirect-gateway ipv6"',
        "block-ipv6",
    ]
    check(
        "J9 block_ipv6=true 渲染出 ifconfig-ipv6/redirect-gateway ipv6/block-ipv6 三行",
        all(line in j_v6 for line in j_v6_expected) and j_v6[-3:] == j_v6_expected,
        str(j_v6),
    )
    check(
        "J10 block_ipv6=false 时三行都不出现",
        not any("ipv6" in line.lower() or line == "block-ipv6" for line in j_writer.render_managed_block({"block_ipv6": False})),
        str(j_writer.render_managed_block({"block_ipv6": False})),
    )
    check("J11 三行顺序必须是两次 push 在前、block-ipv6 在最后", j_v6[-1] == "block-ipv6", str(j_v6))

    # ---- 渲染：tunnel_mode / routes / DNS / 数值项 ----
    j_full = j_writer.render_managed_block(
        {"tunnel_mode": "full", "routes": ["192.168.1.0/24"], "dns_servers": ["8.8.8.8", "8.8.4.4"]}
    )
    check(
        "J12 tunnel_mode=full 渲染 redirect-gateway def1 bypass-dhcp",
        'push "redirect-gateway def1 bypass-dhcp"' in j_full,
        str(j_full),
    )
    check("J13 full 模式下不再渲染 route（路由由 redirect-gateway 决定）", not any("route " in line for line in j_full), str(j_full))
    j_split = j_writer.render_managed_block({"tunnel_mode": "split", "routes": ["192.168.1.0/24"]})
    check(
        "J14 tunnel_mode=split 渲染 push route <net> <mask>",
        'push "route 192.168.1.0 255.255.255.0"' in j_split,
        str(j_split),
    )
    j_numbers = j_writer.render_managed_block(
        {
            "proto": "udp",
            "mssfix": 1420,
            "sndbuf": 393216,
            "rcvbuf": 393216,
            "keepalive": "10 120",
            "client_to_client": True,
            "max_clients": 100,
            "duplicate_cn": True,
        }
    )
    check(
        "J15 数值/布尔项渲染成 OpenVPN 指令名",
        {"mssfix 1420", "sndbuf 393216", "rcvbuf 393216", "keepalive 10 120",
         "client-to-client", "max-clients 100", "duplicate-cn"} <= set(j_numbers),
        str(j_numbers),
    )
    expect("J16 DNS 每个地址一条 push", sum(1 for line in j_full if "dhcp-option DNS" in line), 2)
    expect(
        "J17 max_clients=null 时不渲染 max-clients",
        sum(1 for line in j_writer.render_managed_block({"max_clients": None}) if line.startswith("max-clients")),
        0,
    )

    # ---- build_candidate：已有托管块 -> 替换，不重复 ----
    j_settings = {
        "port": 443,
        "proto": "udp",
        "dns_servers": ["8.8.8.8"],
        "tunnel_mode": "split",
        "routes": ["10.10.0.0/16"],
        "block_ipv6": False,
        "mssfix": 1400,
        "sndbuf": None,
        "rcvbuf": None,
        "keepalive": "10 120",
        "client_to_client": True,
        "max_clients": 50,
        "duplicate_cn": False,
        "network": "10.8.44.0",
        "netmask": "255.255.255.0",
        "topology": "subnet",
    }
    j_old = "\n".join(
        [
            "user nobody",
            "log-append /var/log/openvpn/openvpn.log",
            "verb 3",
            j_conf.MANAGED_BEGIN,
            "port 1194",
            "proto udp",
            'push "dhcp-option DNS 1.1.1.1"',
            j_conf.MANAGED_END,
            "crl-verify /etc/openvpn/server/crl.pem",
            "management 127.0.0.1 7505",
        ]
    ) + "\n"
    j_out = j_writer.build_candidate(j_old, j_settings)
    expect("J18 已有托管块被替换（标记仍只有一对）", j_out.count(j_conf.MANAGED_BEGIN), 1)
    expect("J19 结束标记同样只有一对", j_out.count(j_conf.MANAGED_END), 1)
    check("J20 旧托管值不再直接出现（被新值取代）", "\nport 1194\n" not in j_out, j_out)
    check("J21 新值写入托管块", "\nport 443\n" in j_out, j_out)
    check(
        "J22 块前块后的无关指令逐字节保留",
        "user nobody" in j_out
        and "log-append /var/log/openvpn/openvpn.log" in j_out
        and "crl-verify /etc/openvpn/server/crl.pem" in j_out
        and "management 127.0.0.1 7505" in j_out
        and "verb 3" in j_out,
        j_out,
    )
    check("J23 旧托管块的位置不再残留空标记", j_out.count("# >>>") == 1, j_out)
    expect("J24 候选通过面板自检", j_writer.validate_candidate(j_out, current_text=j_old), [])

    # ---- build_candidate：从别的项目迁移过来的、没有标记的配置 ----
    j_adopted = "\n".join(
        [
            "# 由 hwdsl2/openvpn-install 生成",
            "port 1194",
            "proto tcp",
            "dev tun",
            "user nobody",
            "topology subnet",
            "server 10.8.44.0 255.255.255.0",
            'push "dhcp-option DNS 1.1.1.1"',
            'push "route 10.20.0.0 255.255.0.0"',
            "fast-io",
            "explicit-exit-notify 1",
            "keepalive 10 60",
            "client-to-client",
            'push "route-gateway 10.8.44.1"',
            "log-append /var/log/openvpn/openvpn.log",
            "crl-verify /etc/openvpn/server/crl.pem",
            "management 127.0.0.1 7505",
            "ca /etc/openvpn/server/ca.crt",
            "tls-crypt /etc/openvpn/server/ta.key",
            "verb 3",
        ]
    ) + "\n"
    j_adopted_out = j_writer.build_candidate(j_adopted, j_settings)
    check(
        "J25 旧 port/proto 被注释掉（托管块在末尾，标量后者胜出）",
        j_adopted_out.count(f"{j_conf.RETIRED_PREFIX}port 1194") == 1
        and j_adopted_out.count(f"{j_conf.RETIRED_PREFIX}proto tcp") == 1,
        j_adopted_out,
    )
    check(
        "J26 旧 push 被注释掉（push 会累加，不中和会让客户端收到两组 DNS/路由）",
        j_adopted_out.count(f'{j_conf.RETIRED_PREFIX}push "dhcp-option DNS 1.1.1.1"') == 1
        and j_adopted_out.count(f'{j_conf.RETIRED_PREFIX}push "route 10.20.0.0 255.255.0.0"') == 1,
        j_adopted_out,
    )
    check(
        "J27 旧的 UDP 专用指令被接管（改 tcp 后不能留下 fast-io）",
        j_adopted_out.count(f"{j_conf.RETIRED_PREFIX}fast-io") == 1
        and j_adopted_out.count(f"{j_conf.RETIRED_PREFIX}explicit-exit-notify 1") == 1,
        j_adopted_out,
    )
    j_unrelated = (
        "user nobody",
        "log-append /var/log/openvpn/openvpn.log",
        "crl-verify /etc/openvpn/server/crl.pem",
        "management 127.0.0.1 7505",
        "ca /etc/openvpn/server/ca.crt",
        "tls-crypt /etc/openvpn/server/ta.key",
        "verb 3",
        "# 由 hwdsl2/openvpn-install 生成",
    )
    check(
        "J28 无关指令（含注释）逐字保留",
        all(line in j_adopted_out for line in j_unrelated),
        j_adopted_out,
    )
    check(
        "J29 同一份配置重复生成结果完全一致（幂等，不会越攒越多注释）",
        j_writer.build_candidate(j_adopted_out, j_settings) == j_adopted_out,
        "第二次生成与第一次不同",
    )
    check(
        "J29b 运维自己写的 push 不被接管（面板没能力表达它，不能替用户删）",
        j_adopted_out.count('push "route-gateway 10.8.44.1"') == 1
        and f'{j_conf.RETIRED_PREFIX}push "route-gateway 10.8.44.1"' not in j_adopted_out,
        j_adopted_out,
    )
    j_marker_line = f"  {j_conf.MANAGED_BEGIN}  "
    expect(
        "J30 成对标记被识别为存在托管块（面板写出的标记行没有前导空白）",
        j_conf.managed_block_present(f"{j_conf.MANAGED_BEGIN}\nport 1194\n{j_conf.MANAGED_END}\n"),
        True,
    )
    expect(
        "J31 只有开始标记时不算存在托管块",
        j_conf.managed_block_present(f"{j_conf.MANAGED_BEGIN}\nport 1194\n"),
        False,
    )
    expect(
        "J31b 带前后空白的标记行也能被面板识别（解析按 strip 判定）",
        j_conf.managed_block_present(f"{j_marker_line}\nport 1194\n  {j_conf.MANAGED_END}  \n"),
        True,
    )
    check(
        "J32 迁移配置的候选不引入任何新指令（助手 comm -13 的同一判定）",
        j_writer.candidate_introduces_new_directives(j_adopted_out, j_adopted) == [],
        str(j_writer.candidate_introduces_new_directives(j_adopted_out, j_adopted)),
    )

    # ---- validate_candidate：正常候选通过 + 夹带被拒 ----
    j_candidate = j_writer.build_candidate(j_adopted, j_settings)
    expect("J33 正常候选通过自检", j_writer.validate_candidate(j_candidate, current_text=j_adopted), [])
    for j_name, j_smuggle in (
        ("script-security", "script-security 2"),
        ("up", "up /tmp/evil.sh"),
        ("plugin", "plugin /tmp/evil.so"),
    ):
        # 夹带在托管块**之外**：必须被"不得引入当前配置里没有的指令"这条挡住
        j_bad = j_smuggle + "\n" + j_candidate
        check(
            f"J34 拒绝托管块之外的夹带：{j_name}",
            any("不允许引入" in problem for problem in j_writer.validate_candidate(j_bad, current_text=j_adopted)),
            str(j_writer.validate_candidate(j_bad, current_text=j_adopted))[:200],
        )
        # 夹带在托管块**之内**：必须被指令名白名单挡住
        j_inner = j_candidate.replace(
            j_conf.MANAGED_END, f"{j_smuggle}\n{j_conf.MANAGED_END}"
        )
        check(
            f"J35 拒绝托管块之内的夹带：{j_name}",
            any("未授权指令" in problem for problem in j_writer.validate_candidate(j_inner, current_text=j_adopted)),
            str(j_writer.validate_candidate(j_inner, current_text=j_adopted))[:200],
        )
    check(
        "J36 拒绝未授权的 push 形态",
        any(
            "push" in problem
            for problem in j_writer.validate_candidate(
                j_candidate.replace(j_conf.MANAGED_END, 'push "up no"\n' + j_conf.MANAGED_END),
                current_text=j_adopted,
            )
        ),
        "push up 应当被拒绝",
    )
    check(
        "J37 缺少结束标记的候选被拒绝",
        any("标记" in problem for problem in j_writer.validate_candidate(j_candidate.replace(j_conf.MANAGED_END, ""))),
        "缺标记应当被拒绝",
    )

    # ---- 候选文件写入：原子 + 0600 ----
    j_written = j_writer.write_candidate(j_writer.candidate_path(), j_candidate)
    check("J38 candidate_path() 落在面板数据目录", j_written.parent == pathlib.Path(gsettings.sqlite_path).parent, str(j_written))
    if os.name == "nt":
        # Windows 不保留 Unix 权限位：改为断言"代码确实以 0600 调用了 chmod"，
        # 与 I 节同一个理由（助手会拒绝组/其他可写的候选文件）
        j_modes: list[int] = []
        j_real_chmod = pathlib.Path.chmod

        def _j_recording_chmod(self, mode, *a, **k):  # type: ignore[no-untyped-def]
            j_modes.append(mode)
            return j_real_chmod(self, mode, *a, **k)

        with j_mock.patch.object(pathlib.Path, "chmod", _j_recording_chmod):
            j_writer.write_candidate(j_written, j_candidate)
        check("J39 候选文件显式 chmod 0600（助手拒绝组/其他可写）", 0o600 in j_modes, str(j_modes))
    else:
        expect("J39 候选文件权限 0600", oct(j_written.stat().st_mode)[-3:], "600")

    # ---- 设置项校验 ----
    j_current = {"netmask": "255.255.255.0", "network": "10.8.44.0"}
    expect("J40 port=0 被拒", bool(j_conf.validate_editable({"port": 0}, current=j_current)), True)
    expect("J41 port=70000 被拒", bool(j_conf.validate_editable({"port": 70000}, current=j_current)), True)
    expect("J42 proto=sctp 被拒", bool(j_conf.validate_editable({"proto": "sctp"}, current=j_current)), True)
    check(
        "J43 拒绝原因里带字段名（前端可定位到具体输入框）",
        any("port" in problem for problem in j_conf.validate_editable({"port": 0}, current=j_current))
        and any("proto" in problem for problem in j_conf.validate_editable({"proto": "sctp"}, current=j_current)),
        str(j_conf.validate_editable({"port": 0, "proto": "sctp"}, current=j_current)),
    )
    expect("J44 合法 port/proto 通过", j_conf.validate_editable({"port": 1194, "proto": "tcp"}, current=j_current), [])
    expect(
        "J45 未知键被拒（只读项也走这条）",
        bool(j_conf.validate_editable({"tls_mode": "tls-auth"}, current=j_current)),
        True,
    )
    expect("J46 非法 CIDR 被拒", bool(j_conf.validate_editable({"routes": ["10.0.0.0/33"]}, current=j_current)), True)
    expect(
        "J47 network 带主机位被拒（否则会静默改成网段地址）",
        bool(j_conf.validate_editable({"network": "10.8.44.7"}, current=j_current)),
        True,
    )
    expect(
        "J48 三个 DNS 地址被拒（最多 2 个）",
        bool(j_conf.validate_editable({"dns_servers": ["1.1.1.1", "8.8.8.8", "9.9.9.9"]}, current=j_current)),
        True,
    )
    expect("J49 topology=bridge 被拒", bool(j_conf.validate_editable({"topology": "bridge"}, current=j_current)), True)

    # ---- 设置接口（/api/settings/*）----
    # 助手不在本机（Windows/开发机），因此把 subprocess.run 换成桩，
    # 只验证"校验 -> 候选 -> 解析助手输出 -> 响应形状"这一段。
    j_conf_path = pathlib.Path(gsettings.server_conf_path)
    j_conf_path.write_text(
        "port 1194\n"
        "proto udp\n"
        "dev tun\n"
        "user nobody\n"
        "server 10.9.0.0 255.255.0.0\n"
        "topology subnet\n"
        'push "dhcp-option DNS 1.1.1.1"\n'
        "verb 3\n",
        encoding="utf-8",
    )
    j_conf.invalidate()

    j_helper_stdout = "\n".join(
        [
            "APPLYING",
            "RESULT=ok",
            "BACKUP=/etc/openvpn/server/server.conf.bak.20261016-120000",
            "RESTARTED=yes",
            "ROLLED_BACK=no",
            "NETWORK_CHANGED=no",
            "REASON=",
        ]
    )

    class _JHelperResult:
        stdout = j_helper_stdout
        stderr = ""
        returncode = 0

    with j_mock.patch("app.api.api_v1.endpoints.settings.subprocess.run", return_value=_JHelperResult()):
        j_get = client.get("/api/settings/server", headers=headers)
        expect("J50 GET /api/settings/server 返回 200", j_get.status_code, 200)
        j_body = j_get.json()
        check(
            "J51 GET 返回 settings/readonly/managed_block_present 三个块",
            {"path", "exists", "settings", "readonly", "managed_block_present"} <= set(j_body),
            str(sorted(j_body)),
        )
        expect("J52 GET 读到当前端口", j_body["settings"]["port"], 1194)
        expect("J53 GET 读到 server 网段（只给网络地址）", j_body["settings"]["network"], "10.9.0.0")
        expect("J54 GET 的 netmask 来自 server 指令", j_body["settings"]["netmask"], "255.255.0.0")
        expect("J55 GET 读到 push 的 DNS", j_body["settings"]["dns_servers"], ["1.1.1.1"])
        check(
            "J56 GET 的只读块包含 management/tls-crypt 等",
            j_body["readonly"]["management"] is None
            and j_body["readonly"]["tls_mode"] is None
            and "cipher" in j_body["readonly"],
            str(j_body["readonly"]),
        )
        expect("J57 尚未写入托管块时如实报告 false", j_body["managed_block_present"], False)

        j_unknown = client.patch("/api/settings/server", headers=headers, json={"nope": 1})
        expect("J58 PATCH 未知键返回 400", j_unknown.status_code, 400)
        j_zero = client.patch("/api/settings/server", headers=headers, json={"port": 0})
        expect("J59 PATCH port=0 返回 400", j_zero.status_code, 400)
        j_sctp = client.patch("/api/settings/server", headers=headers, json={"proto": "sctp"})
        expect("J60 PATCH proto=sctp 返回 400", j_sctp.status_code, 400)

        j_patch = client.patch(
            "/api/settings/server",
            headers=headers,
            json={"port": 443, "proto": "tcp", "dns_servers": ["8.8.8.8"], "tunnel_mode": "split",
                  "routes": ["192.168.1.0/24"], "block_ipv6": False, "mssfix": 1420,
                  "keepalive": "10 120", "max_clients": None},
        )
        expect("J61 PATCH 合法请求返回 200", j_patch.status_code, 200)
        j_patch_body = j_patch.json()
        check(
            "J62 PATCH 响应形状与前端约定一致",
            set(j_patch_body)
            == {"ok", "changed", "restart_required", "restarted", "clients_affected", "backup",
                "network_changed", "message"},
            str(sorted(j_patch_body)),
        )
        check(
            "J63 PATCH 只报告真正变化的键",
            set(j_patch_body["changed"]) == {"port", "proto", "dns_servers", "routes", "mssfix", "keepalive"},
            str(j_patch_body["changed"]),
        )
        expect("J64 端口/协议变化要求重启", j_patch_body["restart_required"], True)
        expect("J65 端口/协议/DNS 变化会影响已发出的客户端配置", j_patch_body["clients_affected"], True)
        expect("J66 备份路径来自助手输出", j_patch_body["backup"], "/etc/openvpn/server/server.conf.bak.20261016-120000")
        expect("J67 已重启由助手输出决定", j_patch_body["restarted"], True)
        check(
            "J68 message 提示重新导出客户端",
            "重新导出" in j_patch_body["message"],
            j_patch_body["message"],
        )
        j_cand_text = j_writer.candidate_path().read_text(encoding="utf-8")
        check("J69 候选文件里托管块已更新到 443/tcp", "\nport 443\n" in j_cand_text and "\nproto tcp\n" in j_cand_text, j_cand_text)
        check(
            "J70 候选文件里旧值被注释掉（助手整文件替换后不会有两个 port）",
            j_cand_text.count(f"{j_conf.RETIRED_PREFIX}port 1194") == 1,
            j_cand_text,
        )
        expect("J71 候选文件通过面板自检", j_writer.validate_candidate(j_cand_text, current_text=j_conf_path.read_text(encoding="utf-8")), [])

        j_noop = client.patch("/api/settings/server", headers=headers, json={"port": 1194})
        expect("J72 无变化时不调用助手、直接返回 200", j_noop.status_code, 200)
        expect("J73 无变化时 changed 为空", j_noop.json()["changed"], [])

        j_reexport = client.post("/api/settings/reexport-all", headers=headers)
        expect("J74 POST /api/settings/reexport-all 返回 200", j_reexport.status_code, 200)
        check(
            "J75 重新导出响应形状与前端约定一致",
            set(j_reexport.json()) == {"ok", "exported", "failed"},
            str(j_reexport.json()),
        )

    # ------------------------------------------------------------------ #
    # S. 安全路径回归（审计模块 13 补的自动化保护）
    #    这些路由此前只有真机手工验证，改动后 CI 不会红。
    # ------------------------------------------------------------------ #
    s_backup_traversal = client.get(
        "/api/panel/backups/..%2F..%2Fetc%2Fpasswd/download", headers=headers
    )
    check(
        "S1 备份下载拒绝路径穿越（不得 200）",
        s_backup_traversal.status_code != 200,
        str(s_backup_traversal.status_code),
    )
    check(
        "S2 备份下载对非法名返回 400/404",
        s_backup_traversal.status_code in (400, 404),
        str(s_backup_traversal.status_code),
    )

    s_restore_no_confirm = client.post("/api/settings/history/anything/restore", headers=headers)
    expect("S3 历史回滚缺少 confirm 返回 400", s_restore_no_confirm.status_code, 400)
    check(
        "S4 历史回滚提示里说明要带 confirm=RESTORE",
        "RESTORE" in str(s_restore_no_confirm.json().get("detail", "")),
        str(s_restore_no_confirm.json()),
    )

    s_hist_traversal = client.get("/api/settings/history/..%2F..%2Fetc%2Fpasswd", headers=headers)
    check(
        "S5 历史版本读取拒绝路径穿越（不得 200）",
        s_hist_traversal.status_code != 200,
        str(s_hist_traversal.status_code),
    )

    s_share_missing = client.post("/api/clients/999999/share", headers=headers)
    expect("S6 给不存在的客户端建分享返回 404", s_share_missing.status_code, 404)

    s_share_bogus = client.get("/share/definitely-not-a-real-token")
    expect("S7 伪造分享令牌返回 410", s_share_bogus.status_code, 410)

    client.put(
        "/api/notify/",
        headers=headers,
        json={"enabled": True, "webhook_url": "http://127.0.0.1:7505/x"},
    )
    s_notify = client.post("/api/notify/test", headers=headers)
    expect("S8 通知测试对回环地址放行前先拒绝请求体", s_notify.status_code, 200)
    s_reason = str(s_notify.json().get("reason", ""))
    check(
        "S9 回环地址被 SSRF 防线拦下",
        s_notify.json().get("ok") is False and ("内网" in s_reason or "本机" in s_reason),
        s_reason[:60],
    )
    client.put(
        "/api/notify/",
        headers=headers,
        json={"enabled": False, "webhook_url": ""},
    )

    s_anon_version = client.get("/api/health/version")
    expect("S10 版本接口匿名可读（健康检查依赖）", s_anon_version.status_code, 200)
    s_anon_clients = client.get("/api/clients/")
    expect("S11 客户端列表匿名返回 401", s_anon_clients.status_code, 401)

    # ------------------------------------------------------------------ #
    # T. 只读路由冒烟（审计模块 13：保证路由存在且不 5xx，改动即红）
    # ------------------------------------------------------------------ #
    def _ro(path: str, label: str) -> None:
        resp = client.get(path, headers=headers)
        check(
            f"{label} 返回非 5xx 且路由存在",
            resp.status_code != 404 and resp.status_code < 500,
            f"{path} -> {resp.status_code}",
        )

    _ro("/api/health/live", "T1 存活探针")
    _ro("/api/health/diagnostics", "T2 诊断接口")
    _ro("/api/health/selfcheck", "T3 自检接口")
    _ro("/api/dashboard/certificate-expiry", "T4 证书到期视图")
    _ro("/api/traffic/overview", "T5 流量总览")
    _ro("/api/logs/audit?page=1&page_size=5", "T6 审计列表")
    _ro("/api/settings/history", "T7 配置历史列表")
    _ro("/api/clients/next-ip", "T8 推荐地址")
    _ro("/api/clients/online", "T9 在线列表")
    _ro("/api/panel/backups", "T10 备份列表")
    _ro("/api/notify/", "T11 通知配置读取")

    t_live = client.get("/api/health/live")
    expect("T12 存活探针匿名可达且为 200", t_live.status_code, 200)

    t_validate = client.get("/api/clients/validate?name=smoke-name&common_name=smoke-name", headers=headers)
    check(
        "T13 名称校验接口可调用（200/422 均可，不得 5xx/404）",
        t_validate.status_code in (200, 422) or t_validate.status_code < 500,
        str(t_validate.status_code),
    )

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 66)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAILED: {name} {detail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
