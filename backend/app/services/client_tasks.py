"""客户端证书相关后台任务。

这些函数**不在请求线程里运行**，因此每个任务自己开一个数据库会话并自行提交，
不能依赖 FastAPI 的 ``Depends(get_db)``。

它们会修改 EasyRSA PKI（index.txt / serial / issued/），必须串行执行——
由 ``app.services.jobs.registry`` 的单 worker 保证。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app import crud
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services import ccd as ccd_service
from app.services import certs, management, openvpn
from app.services.audit import safe_audit

logger = logging.getLogger(__name__)
settings = get_settings()


def server_endpoint(db: Session) -> tuple[str, int]:
    """导出 .ovpn 时使用的对外地址/端口。"""
    host, port = "127.0.0.1", 1194
    servers = crud.server.get_multi(db, limit=1)
    if servers:
        host, port = servers[0].host, servers[0].port
    if settings.public_ip:
        host = settings.public_ip
    if settings.public_port:
        port = settings.public_port
    return host, port


def cleanup_client_files(common_name: str) -> None:
    """删除导出的 .ovpn 与 CCD 文件（忽略不存在的情况）。"""
    for path in (
        settings.openvpn_client_export_path / f"{common_name}.ovpn",
        Path(settings.ccd_path) / common_name,
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("清理文件失败：%s", path, exc_info=True)


def kick_session(common_name: str) -> None:
    """只断开这一个客户端的活动会话（不影响其他在线用户）。"""
    if not settings.revoke_kicks_session:
        return
    try:
        management.disconnect(common_name)
        logger.info("已断开被吊销客户端的活动会话 cn=%s", common_name)
    except Exception:  # noqa: BLE001
        logger.warning("断开会话失败 cn=%s（证书已吊销，重连会被拒绝）", common_name, exc_info=True)


# --------------------------------------------------------------------------- #
# 任务体
# --------------------------------------------------------------------------- #
def create_client_task(client_id: int, passphrase: str | None, actor: str) -> dict:
    """为已入库的客户端签发证书并导出 .ovpn。

    失败时必须回滚：异步路径下 HTTP 请求早已返回 202，若不清理就会留下一条
    "没有证书的客户端"记录（同步路径由端点回滚，异步路径只能在这里兜住）。
    """
    try:
        with SessionLocal() as db:
            client = crud.client.get(db, client_id)
            if client is None:
                raise RuntimeError(f"client {client_id} not found")

            if client.fixed_ip is not None or client.routes is not None:
                routes = [r.strip() for r in (client.routes or "").split(",") if r.strip()]
                ccd_service.write_ccd(client, fixed_ip=client.fixed_ip, routes=routes)

            certs.build_client_cert(db, client, passphrase=passphrase)
            host, port = server_endpoint(db)
            ovpn = certs.export_ovpn(client, remote_host=host, remote_port=port)
            safe_audit(db, actor, "create_client", client.common_name, "success")
            return {"client_id": client_id, "common_name": client.common_name, "ovpn": ovpn}
    except Exception:
        logger.exception("创建客户端任务失败，回滚记录 client_id=%s", client_id)
        _rollback_client(client_id, passphrase)
        raise


def _rollback_client(client_id: int, passphrase: str | None = None) -> None:
    """删除创建失败的客户端记录、半成品文件，并吊销已经签出的证书。

    签发成功但导出 .ovpn 失败时，证书已经在 PKI 里生效。如果回滚只删记录与文件，
    那张证书会一直 valid、CRL 也不更新——和"删除客户端不吊销"是同一个洞。
    这里先吊销再删记录（此时 ORM 对象还挂着会话），失败只告警：
    证书可能压根没签发成功，那不是错误。
    """
    try:
        with SessionLocal() as db:
            client = crud.client.get(db, client_id)
            if client is None:
                return
            common_name = client.common_name
            try:
                certs.revoke_client_cert(db, client, passphrase=passphrase)
            except Exception:  # noqa: BLE001
                logger.warning("回滚时吊销证书失败 cn=%s（可能证书未签发成功）", common_name, exc_info=True)
            crud.client.remove(db, client_id)
            cleanup_client_files(common_name)
            logger.info("已回滚创建失败的客户端 cn=%s", common_name)
    except Exception:  # noqa: BLE001 - 回滚失败只记录，原始错误更重要
        logger.exception("回滚客户端记录失败 client_id=%s", client_id)


def issue_cert_task(client_id: int, passphrase: str | None, actor: str) -> dict:
    """签发（或复用）客户端证书并导出 .ovpn。"""
    with SessionLocal() as db:
        client = crud.client.get(db, client_id)
        if client is None:
            raise RuntimeError(f"client {client_id} not found")

        logger.info("签发证书 client_id=%s cn=%s by=%s", client_id, client.common_name, actor)
        certs.build_client_cert(db, client, passphrase=passphrase)
        host, port = server_endpoint(db)
        ovpn = certs.export_ovpn(client, remote_host=host, remote_port=port)
        safe_audit(db, actor, "issue_cert", client.common_name, "success")
        return {"client_id": client_id, "common_name": client.common_name, "ovpn": ovpn}


def revoke_cert_task(client_id: int, passphrase: str | None, actor: str) -> dict:
    """吊销证书、刷新 CRL，并只断开该客户端的会话。

    注意：这里**不重启** OpenVPN 服务（重启会断开所有在线用户）。
    """
    with SessionLocal() as db:
        client = crud.client.get(db, client_id)
        if client is None:
            raise RuntimeError(f"client {client_id} not found")
        common_name = client.common_name

        logger.info("吊销证书 client_id=%s cn=%s by=%s", client_id, common_name, actor)
        certs.revoke_client_cert(db, client, passphrase=passphrase)
    # 文件与进程操作放在会话之外，避免长时间持有数据库连接
    try:
        openvpn.apply_crl_change()
    except Exception as exc:  # noqa: BLE001
        logger.exception("刷新 CRL 失败 cn=%s", common_name)
        raise RuntimeError(f"证书已吊销，但 CRL 刷新失败：{exc}") from exc
    kick_session(common_name)

    with SessionLocal() as db:
        safe_audit(db, actor, "revoke_cert", common_name, "success")
    return {"detail": "revoked", "common_name": common_name}


def delete_client_task(client_id: int, passphrase: str | None, actor: str) -> dict:
    """删除客户端：必要时先吊销证书，再删除记录与文件。"""
    common_name: str | None = None
    with SessionLocal() as db:
        client = crud.client.get(db, client_id)
        if client is None:
            raise RuntimeError(f"client {client_id} not found")
        common_name = client.common_name

        # 吊销不能以"导出文件是否存在"为前提。原来的写法是 `if export_path.exists()`，
        # 于是"建了客户端但没导出过配置"（或用户手动删了 .ovpn）时，删除流程会跳过吊销：
        # 证书留在 PKI 里仍是 valid、CRL 也没重新生成，手里留着旧配置的人照样能连上来。
        # 现在一律尝试吊销，失败只记日志（本来就没有证书、或已经吊销过都会走到这里）。
        revoked = False
        try:
            certs.revoke_client_cert(db, client, passphrase=passphrase)
            revoked = True
        except Exception:  # noqa: BLE001
            logger.exception("删除时吊销证书失败 cn=%s（可能本来就没有证书或已吊销）", common_name)

        if revoked:
            try:
                openvpn.apply_crl_change()
            except Exception:  # noqa: BLE001
                logger.exception("删除后刷新 CRL 失败 cn=%s", common_name)
            kick_session(common_name)

        crud.client.remove(db, client_id)
        cleanup_client_files(common_name)
        safe_audit(db, actor, "delete_client", common_name, "success")

    return {"detail": "deleted", "client_id": client_id, "common_name": common_name}


__all__ = [
    "server_endpoint",
    "cleanup_client_files",
    "kick_session",
    "create_client_task",
    "issue_cert_task",
    "revoke_cert_task",
    "delete_client_task",
]
