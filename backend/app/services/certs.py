import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple

from sqlalchemy.orm import Session

from app import crud
from app.core.config import get_settings
from app.models.client import Client
from app.schemas.certificate import CertificateCreate, CertificateUpdate
from app.services import server_conf
from app.utils.time import utcnow_naive

logger = logging.getLogger(__name__)
settings = get_settings()


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> str:
    """Run a command and raise with stderr if failed.

    显式超时：easyrsa/openssl 卡住时不能让 HTTP 请求无限等下去。
    """
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=merged_env,
            timeout=timeout or settings.easyrsa_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"命令超时（{timeout or settings.easyrsa_timeout}s）：{' '.join(cmd)}") from exc
    except OSError as exc:
        # easyrsa / openssl 不存在或不可执行时给出可读信息，
        # 而不是让 FileNotFoundError 变成没有上下文的 500
        raise RuntimeError(f"无法执行 {cmd[0]}：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Command failed")
    return result.stdout


def _parse_cert_info(cert_path: Path) -> Tuple[str, datetime, datetime]:
    """Return (serial, not_before, not_after) for given cert.

    注意：openssl 输出的是 **UTC** 时间；``created_at`` 用的是 Asia/Shanghai。
    两者不可混比，所有证书时间比较请使用 ``utcnow_naive()``。
    """
    out = _run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-serial", "-startdate", "-enddate"],
        timeout=settings.openssl_timeout,
    )
    serial = ""
    not_before = None
    not_after = None
    for line in out.splitlines():
        if line.startswith("serial="):
            serial = line.split("=", 1)[1].strip()
        elif line.startswith("notBefore="):
            not_before = datetime.strptime(line.split("=", 1)[1].strip(), "%b %d %H:%M:%S %Y %Z")
        elif line.startswith("notAfter="):
            not_after = datetime.strptime(line.split("=", 1)[1].strip(), "%b %d %H:%M:%S %Y %Z")
    if not serial or not_before is None or not_after is None:
        raise RuntimeError("Failed to parse certificate dates/serial")
    return serial, not_before, not_after


def build_client_cert(
    db: Session, client: Client, passwordless: bool = True, passphrase: str | None = None
) -> Path:
    """Generate (or reuse) client cert/key via EasyRSA and update certificate table.

    说明：EasyRSA 是同步子进程（1~5 秒起），这个调用仍在请求内完成。前端对这类
    操作使用更长的超时（见 frontend/src/api/clients.js 的 LONG_TIMEOUT）。
    """
    cert_path = settings.easyrsa_path / "pki" / "issued" / f"{client.common_name}.crt"
    key_path = settings.easyrsa_path / "pki" / "private" / f"{client.common_name}.key"
    req_path = settings.easyrsa_path / "pki" / "reqs" / f"{client.common_name}.req"

    # If cert/key already exist, reuse them to avoid EasyRSA duplicate request errors
    if not (cert_path.exists() and key_path.exists()):
        # Clean stale req/key to avoid "already exists" errors on retries
        if req_path.exists():
            req_path.unlink()
        if key_path.exists() and not cert_path.exists():
            key_path.unlink()
        cmd = ["./easyrsa", "--batch", "build-client-full", client.common_name]
        if passwordless:
            cmd.append("nopass")
        env = None
        if passphrase:
            env = {"EASYRSA_PASSIN": f"pass:{passphrase}"}
        _run(cmd, cwd=settings.easyrsa_path, env=env)

    if not cert_path.exists() or not key_path.exists():
        raise RuntimeError("Client certificate or key not found after generation")

    serial, not_before, not_after = _parse_cert_info(cert_path)
    db_cert = crud.certificate.get_by_cn(db, common_name=client.common_name)
    if db_cert:
        crud.certificate.update(
            db,
            db_obj=db_cert,
            obj_in=CertificateUpdate(
                status="valid",
                revoked_at=None,
            ),
        )
        db_cert.serial_number = serial
        db_cert.not_before = not_before
        db_cert.not_after = not_after
    else:
        db_cert = crud.certificate.create(
            db,
            CertificateCreate(
                common_name=client.common_name,
                serial_number=serial,
                status="valid",
                not_before=not_before,
                not_after=not_after,
                revoked_at=None,
            ),
        )
    crud.client.update(db, db_obj=client, obj_in={"certificate_id": db_cert.id, "status": "offline"})
    return cert_path


def _publish_crl(crl_src: Path, crl_dst: Path) -> None:
    """把 EasyRSA 生成的 CRL 发布到 crl-verify 指向的位置，并保证 OpenVPN 读得到。

    ``shutil.copy`` 会把源文件的权限位一起复制过来，而 EasyRSA 的 ``pki/crl.pem``
    可能是 0600。那样发布过去的 CRL 就只有面板用户可读——但 OpenVPN 是在**降权到
    nobody 之后**才重新读取 CRL 的（文件 mtime/size 变化即重读），读不到就等于吊销
    没生效：被吊销的证书照样能连上来。因此这里必须显式放宽到 0644。
    """
    created_dir = not crl_dst.parent.exists()
    crl_dst.parent.mkdir(parents=True, exist_ok=True)
    if created_dir:
        crl_dst.parent.chmod(0o755)
    shutil.copy(crl_src, crl_dst)
    crl_dst.chmod(0o644)


def revoke_client_cert(db: Session, client: Client, passphrase: str | None = None) -> None:
    """Revoke client cert, generate CRL, update DB.

    只负责吊销与生成/复制 CRL；**是否重启服务由调用方决定**
    （见 app/services/openvpn.py 的 apply_crl_change，默认不重启）。
    """
    env = {"EASYRSA_PASSIN": f"pass:{passphrase}"} if passphrase else None
    _run(["./easyrsa", "--batch", "revoke", client.common_name], cwd=settings.easyrsa_path, env=env)
    _run(["./easyrsa", "gen-crl"], cwd=settings.easyrsa_path, env=env)

    _publish_crl(settings.easyrsa_path / "pki" / "crl.pem", settings.openvpn_crl_path)
    logger.info("CRL 已更新：%s", settings.openvpn_crl_path)

    db_cert = crud.certificate.get_by_cn(db, common_name=client.common_name)
    if db_cert:
        crud.certificate.update(
            db,
            db_obj=db_cert,
            obj_in=CertificateUpdate(status="revoked", revoked_at=utcnow_naive()),
        )
    crud.client.update(db, db_obj=client, obj_in={"status": "disabled"})


def _read_text(path: Path) -> str:
    return Path(path).read_text().strip()


def _tls_block(key_path: Path, tls_mode: str) -> str:
    """按服务端实际使用的方式生成 tls-crypt / tls-auth 内联块。

    原模板写死 ``key-direction 1`` + ``<tls-auth>``：一旦服务端改用 tls-crypt，
    导出的 .ovpn 全部无法连接。
    """
    if not key_path.exists():
        return ""
    body = _read_text(key_path)
    if tls_mode == "tls-crypt":
        return f"\n<tls-crypt>\n{body}\n</tls-crypt>\n"
    return f"\nkey-direction 1\n<tls-auth>\n{body}\n</tls-auth>\n"


def _read_server_directives() -> dict:
    """从 server.conf 读取 proto / cipher / tls 模式等，使客户端配置与服务端一致。

    解析统一在 app/services/server_conf.py（按 mtime 缓存），因此运维改了
    server.conf 后新导出的 .ovpn 会自动跟随，不会再出现"服务端换 UDP，
    旧模板还写 tcp"的耦合问题。
    """
    conf = server_conf.load()
    return {
        "proto": conf.proto or "udp",
        "cipher": conf.cipher,
        "data_ciphers": conf.data_ciphers,
        "auth": conf.auth,
        "tls_mode": conf.tls_mode or "tls-crypt",
        "from_conf": conf.path if conf.exists else None,
    }


def export_ovpn(client: Client, remote_host: str, remote_port: int) -> str:
    """Render inline .ovpn for the given client, following the live server.conf."""
    base = settings.easyrsa_path / "pki"
    cert_path = base / "issued" / f"{client.common_name}.crt"
    key_path = base / "private" / f"{client.common_name}.key"
    ca_path = settings.openvpn_base_path / "server" / "ca.crt"
    ta_path = settings.ta_key_path

    required = [cert_path, key_path, ca_path]
    # tls-crypt/tls-auth 密钥仅在服务端确实启用时才必需
    srv = _read_server_directives()
    if srv.get("tls_mode"):
        required.append(ta_path)
    for path in required:
        if not path.exists():
            raise RuntimeError(f"Required file missing: {path}")

    proto = (os.environ.get("OVPN_EXPORT_PROTO") or srv["proto"] or "udp").lower()
    cipher = os.environ.get("OVPN_EXPORT_CIPHER") or srv.get("cipher")
    data_ciphers = os.environ.get("OVPN_EXPORT_DATA_CIPHERS") or srv.get("data_ciphers")
    auth = srv.get("auth")
    tls_mode = os.environ.get("OVPN_EXPORT_TLS_MODE") or srv.get("tls_mode") or "tls-auth"

    lines: list[str] = [
        "client",
        "dev tun",
        f"proto {proto}",
        f"remote {remote_host} {remote_port}",
        "resolv-retry infinite",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
        "verb 3",
    ]
    if proto.startswith("udp"):
        # UDP 下让客户端在退出时通知服务端，服务端可立即回收会话
        lines.append("explicit-exit-notify 1")
    if data_ciphers:
        lines.append(f"data-ciphers {data_ciphers}")
    if cipher:
        lines.append(f"cipher {cipher}")
    if auth:
        lines.append(f"auth {auth}")

    template = "\n".join(lines) + "\n"
    template += f"\n<ca>\n{_read_text(ca_path)}\n</ca>\n"
    template += f"<cert>\n{_read_text(cert_path)}\n</cert>\n"
    template += f"<key>\n{_read_text(key_path)}\n</key>\n"
    template += _tls_block(ta_path, tls_mode)

    export_dir = settings.openvpn_client_export_path
    created_dir = not export_dir.exists()
    export_dir.mkdir(parents=True, exist_ok=True)
    if created_dir:
        export_dir.chmod(0o755)
    out_path = export_dir / f"{client.common_name}.ovpn"
    out_path.write_text(template)
    # .ovpn 里内嵌了客户端私钥：默认 umask(0022) 下会是 0644，同机任何用户都能读走私钥。
    out_path.chmod(0o600)
    return template