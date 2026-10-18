import logging
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.api.security import get_current_user
from app.core.config import get_settings
from app.schemas.audit_log import AuditLogCreate
from app.schemas.client import Client, ClientCreate, ClientOnline, ClientPage, ClientUpdate
from app.services import ccd as ccd_service
from app.services import client_tasks, jobs, management, server_conf
from app.services import status_sync

settings = get_settings()
logger = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(get_current_user)])


class OVPNResponse(BaseModel):
    ovpn: str


class ExportedCheckResponse(BaseModel):
    exists: bool
    ovpn: str | None = None


class BuildCertRequest(BaseModel):
    passphrase: str | None = None


class PassphrasePayload(BaseModel):
    passphrase: str | None = None


class ClientCreateRequest(ClientCreate):
    passphrase: str | None = None


class ClientValidateResponse(BaseModel):
    name_ok: bool = True
    common_name_ok: bool = True
    fixed_ip_ok: bool = True
    name_message: str | None = None
    common_name_message: str | None = None
    fixed_ip_message: str | None = None


def ensure_superuser(user=Depends(get_current_user)):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return user


def _accepted(job: jobs.Job) -> JSONResponse:
    """202 + job_id：重活已交给后台任务，前端轮询 /api/jobs/{id}。"""
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=job.as_dict())


# server.conf 的解析统一在 app/services/server_conf.py（按 mtime 缓存），
# 这里只保留一个语义清晰的包装，避免第三份重复实现。
def _load_server_network() -> IPv4Network | IPv6Network | None:
    return server_conf.load().network


def _validate_fields(
    db: Session,
    *,
    name: str | None,
    common_name: str | None,
    fixed_ip: str | None,
    require_all: bool,
    current_client_id: int | None = None,
):
    if require_all:
        if not name:
            raise HTTPException(status_code=400, detail="名称为必填项")
        if not common_name:
            raise HTTPException(status_code=400, detail="证书CN为必填项")
        # 固定 IP 不是必填项：留空则由 OpenVPN 自己从地址池分配（不写 CCD 的
        # ifconfig-push）。原来强制必填，用户得自己算网段内还剩哪个地址没用，
        # 填错就被"不在网段内"拦下——这是形式大于收益的限制。

    if name:
        existing = db.query(crud.client.model).filter(crud.client.model.name == name).first()
        if existing and existing.id != current_client_id:
            raise HTTPException(status_code=400, detail="名称已存在")
    if common_name:
        existing_cn = crud.client.get_by_common_name(db, common_name=common_name)
        if existing_cn and existing_cn.id != current_client_id:
            raise HTTPException(status_code=400, detail="证书CN已存在")

    if fixed_ip:
        try:
            ip = ip_address(fixed_ip)
        except ValueError as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="固定IP格式不合法") from exc

        net = _load_server_network()
        if net:
            if ip not in net or ip == net.network_address or ip == net.broadcast_address:
                raise HTTPException(status_code=400, detail="固定IP不在 server.conf 配置的网段内")
            # 避免与服务端占用的首个地址冲突（用 next(iter) 而不是 list()，
            # /16 之类的网段不会一次性实例化六万多个地址对象）
            server_ip = next(iter(net.hosts()), None)
            if server_ip and ip == server_ip:
                raise HTTPException(status_code=400, detail="固定IP不可与服务端地址重复")

        existing_ip = db.query(crud.client.model).filter(crud.client.model.fixed_ip == fixed_ip).first()
        if existing_ip and existing_ip.id != current_client_id:
            raise HTTPException(status_code=400, detail="固定IP已被其他客户端使用")


def _display_status(client_row, online: set[str]) -> str:
    """展示层的状态：disabled 优先，其次看在线集合，最后 offline。"""
    if client_row.disabled:
        return "disabled"
    if client_row.common_name in online:
        return "online"
    return client_row.status if client_row.status in {"online", "offline", "disabled"} else "offline"


@router.get("/", response_model=ClientPage)
def list_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(deps.get_db),
) -> ClientPage:
    """查询客户端列表（分页，只读）。

    修复：原实现每次调用都会 ``management.list_online()`` 之后调用
    ``_sync_client_status_and_audit()``，把整张表读出来、逐条 commit，并为每个
    状态变化写审计——一个分页 GET 因此变成写事务（SQLite 下阻塞读、审计表被刷）。
    状态同步现在由后台线程负责，这里只做零写入的展示层合并，在线列表走 TTL 缓存。
    """
    try:
        online = {c.common_name for c in management.list_online()}
    except Exception:
        logger.warning("获取在线客户端失败，列表按数据库状态展示", exc_info=True)
        online = set()

    query = db.query(crud.client.model)
    if name:
        query = query.filter(crud.client.model.name.ilike(f"%{name}%"))
    if status:
        normalized_status = status.lower()
        if normalized_status in {"online", "offline", "disabled"}:
            query = query.filter(crud.client.model.status == normalized_status)

    total = query.count()
    offset = (page - 1) * page_size
    rows = query.order_by(crud.client.model.id).offset(offset).limit(page_size).all()

    # 不直接改 ORM 对象（会把它标记为 dirty），构造响应模型再覆盖展示状态
    items: list[Client] = []
    for row in rows:
        item = Client.model_validate(row)
        item.status = _display_status(row, online)
        items.append(item)

    return ClientPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/", response_model=None, status_code=status.HTTP_201_CREATED)
def create_client(
    client_in: ClientCreateRequest,
    wait: bool = Query(False, description="true 时同步等待证书签发完成（旧行为）"),
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> Client | JSONResponse:
    """创建客户端并签发证书。

    默认**异步**：先在请求内写入数据库记录（很快），随后把 EasyRSA 签发与
    .ovpn 导出交给后台任务，返回 202 + job_id。这样慢机器上也不会再出现
    "前端超时但后端其实成功"的假失败。传 ``wait=true`` 可回到同步行为。
    """
    logger.info("Creating client cn=%s by user=%s", client_in.common_name, getattr(user, "username", "unknown"))
    _validate_fields(
        db,
        name=client_in.name,
        common_name=client_in.common_name,
        fixed_ip=client_in.fixed_ip,
        require_all=True,
    )
    payload = ClientCreate(**client_in.model_dump(exclude={"passphrase"}))
    created = crud.client.create(db, payload)
    actor = getattr(user, "username", "unknown")

    if not wait:
        job = jobs.registry.submit(
            "create_client", client_tasks.create_client_task, created.id, client_in.passphrase, actor
        )
        return _accepted(job)

    try:
        client_tasks.create_client_task(created.id, client_in.passphrase, actor)
    except Exception as exc:  # noqa: BLE001
        logger.exception("证书签发失败，回滚客户端记录 cn=%s", client_in.common_name)
        try:
            crud.client.remove(db, created.id)
            client_tasks.cleanup_client_files(created.common_name)
        except Exception:
            logger.exception("回滚失败 cn=%s", client_in.common_name)
        raise HTTPException(status_code=500, detail=f"Failed to generate certificate: {exc}") from exc

    db.expire_all()
    refreshed = crud.client.get(db, created.id)
    return Client.model_validate(refreshed or created)


@router.get("/validate", response_model=ClientValidateResponse)
def validate_client(
    name: str | None = Query(default=None),
    common_name: str | None = Query(default=None),
    fixed_ip: str | None = Query(default=None),
    db: Session = Depends(deps.get_db),
) -> ClientValidateResponse:
    """校验名称/证书CN/固定IP 可用性，不修改数据。"""
    resp = ClientValidateResponse()

    if name:
        exists = db.query(crud.client.model).filter(crud.client.model.name == name).first()
        if exists:
            resp.name_ok = False
            resp.name_message = "名称已存在"
        else:
            resp.name_message = "名称可用"

    if common_name:
        exists = crud.client.get_by_common_name(db, common_name=common_name)
        if exists:
            resp.common_name_ok = False
            resp.common_name_message = "证书CN已存在"
        else:
            resp.common_name_message = "证书CN可用"

    if fixed_ip:
        try:
            ip = ip_address(fixed_ip)
        except ValueError:
            resp.fixed_ip_ok = False
            resp.fixed_ip_message = "固定IP格式不合法"
        else:
            net = _load_server_network()
            if net and (ip not in net or ip == net.network_address or ip == net.broadcast_address):
                resp.fixed_ip_ok = False
                resp.fixed_ip_message = "固定IP不在 server.conf 网段内"
            else:
                server_ip = next(iter(net.hosts()), None) if net else None
                if server_ip and ip == server_ip:
                    resp.fixed_ip_ok = False
                    resp.fixed_ip_message = "固定IP不可与服务端地址重复"
            if resp.fixed_ip_ok:
                exists_ip = db.query(crud.client.model).filter(crud.client.model.fixed_ip == fixed_ip).first()
                if exists_ip:
                    resp.fixed_ip_ok = False
                    resp.fixed_ip_message = "固定IP已被其他客户端使用"
                else:
                    resp.fixed_ip_message = "固定IP可用"

    return resp


@router.patch("/{client_id}", response_model=Client)
def update_client(client_id: int, client_in: ClientUpdate, db: Session = Depends(deps.get_db)) -> Client:
    # 更新客户端信息；如有固定 IP 或路由则写 CCD
    db_obj = crud.client.get(db, client_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="Client not found")
    updated = crud.client.update(db, db_obj=db_obj, obj_in=client_in)
    # optional: write CCD when fixed_ip/routes provided
    if client_in.fixed_ip is not None or client_in.routes is not None:
        routes = []
        if client_in.routes:
            routes = [r.strip() for r in client_in.routes.split(",") if r.strip()]
        ccd_service.write_ccd(updated, fixed_ip=client_in.fixed_ip, routes=routes, disabled=bool(updated.disabled))

    # 禁用/启用：必须落到 CCD（写 `disable` 行）才真的挡得住连接；
    # 只改数据库字段的话客户端照样能连，属于"看起来禁用了其实没禁"。
    if client_in.disabled is not None:
        routes = [r.strip() for r in (updated.routes or "").split(",") if r.strip()]
        ccd_service.write_ccd(
            updated, fixed_ip=updated.fixed_ip, routes=routes, disabled=bool(updated.disabled)
        )
        if updated.disabled:
            try:
                management.disconnect(updated.common_name)
            except Exception:  # noqa: BLE001 - 不在线/管理接口不可用时忽略
                logger.debug("禁用客户端时断开失败（可能不在线）", exc_info=True)
    return updated


@router.get("/next-ip")
def next_ip(db: Session = Depends(deps.get_db), _=Depends(get_current_user)) -> dict:
    """给前端用的地址池信息：网段、可用范围、推荐的下一个空闲 IP。

    放在 ``/{client_id}`` 之前声明，否则会被动态路由吃掉（返回 422）。
    """
    net = _load_server_network()
    used = sorted(
        row[0] for row in db.query(crud.client.model.fixed_ip).all() if row[0]
    )
    payload: dict = {
        "network": None,
        "netmask": None,
        "cidr": None,
        "start": None,
        "end": None,
        "suggested": None,
        "used": used,
    }
    if not isinstance(net, IPv4Network):
        return payload

    # /24 之类的网段：首个地址是服务端自己，末位是广播，客户端可用中间段
    start = int(net.network_address) + 2
    end = int(net.broadcast_address) - 1
    payload.update(
        {
            "network": str(net.network_address),
            "netmask": str(net.netmask),
            "cidr": net.prefixlen,
            "start": str(ip_address(start)) if start <= end else None,
            "end": str(ip_address(end)) if start <= end else None,
        }
    )
    # 只扫前 4096 个地址：/16 这类大网段下逐个实例化没有意义，
    # 而且正常情况下客户端数量远小于这个数。
    used_set = set(used)
    limit = min(end, start + 4095)
    for value in range(start, limit + 1):
        candidate = str(ip_address(value))
        if candidate not in used_set:
            payload["suggested"] = candidate
            break
    return payload


@router.delete("/{client_id}", response_model=None)
def delete_client(
    client_id: int,
    payload: PassphrasePayload | None = Body(default=None),
    wait: bool = Query(False, description="true 时同步等待删除完成（旧行为）"),
    db: Session = Depends(deps.get_db),
    user=Depends(get_current_user),
) -> Client | JSONResponse:
    """删除客户端（必要时先吊销证书并刷新 CRL）。

    默认异步（见 ``create_client`` 的说明）。同步模式仍返回被删除的客户端对象，
    保持与旧接口一致。
    """
    db_obj = crud.client.get(db, client_id)
    if not db_obj:
        raise HTTPException(status_code=404, detail="Client not found")
    actor = getattr(user, "username", "unknown")
    snapshot = Client.model_validate(db_obj)
    passphrase = payload.passphrase if payload else None

    if not wait:
        job = jobs.registry.submit("delete_client", client_tasks.delete_client_task, client_id, passphrase, actor)
        return _accepted(job)

    client_tasks.delete_client_task(client_id, passphrase, actor)
    return snapshot


@router.get("/online", response_model=list[ClientOnline])
def list_online_clients(
    db: Session = Depends(deps.get_db),
    _=Depends(ensure_superuser),
    refresh: bool = Query(False, description="跳过缓存强制探测"),
) -> list[ClientOnline]:
    # 查询在线客户端（需开启 management 接口）
    online_clients = management.list_online(force=refresh)
    try:
        # 显式刷新时才做一次同步（写操作），GET 列表接口本身保持只读
        status_sync.sync_once(db, online_common_names={c.common_name for c in online_clients})
    except Exception:
        logger.exception("Failed to sync client status/audit from online list")
    return [ClientOnline(**c.__dict__) for c in online_clients]


@router.post("/{client_id}/cert", response_model=None)
def generate_client_cert(
    client_id: int,
    payload: BuildCertRequest | None = Body(default=None),
    wait: bool = Query(False, description="true 时同步等待签发完成（旧行为，返回 {ovpn: ...}）"),
    db: Session = Depends(deps.get_db),
    _=Depends(ensure_superuser),
    user=Depends(get_current_user),
) -> OVPNResponse | JSONResponse:
    """签发客户端证书并导出 .ovpn。

    默认异步：EasyRSA 是秒级子进程，放在请求里会超过前端超时并造成"假失败"。
    """
    client = crud.client.get(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    actor = getattr(user, "username", "unknown")
    passphrase = payload.passphrase if payload else None

    if not wait:
        job = jobs.registry.submit("issue_cert", client_tasks.issue_cert_task, client_id, passphrase, actor)
        return _accepted(job)

    result = client_tasks.issue_cert_task(client_id, passphrase, actor)
    return OVPNResponse(ovpn=result["ovpn"])


@router.get("/{client_id}/cert/exported", response_model=ExportedCheckResponse)
def check_exported_client_cert(
    client_id: int, db: Session = Depends(deps.get_db), _=Depends(ensure_superuser)
) -> ExportedCheckResponse:
    """检查是否已有导出的 .ovpn，若存在则直接返回内容。"""
    client = crud.client.get(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    export_path = settings.openvpn_client_export_path / f"{client.common_name}.ovpn"
    if export_path.exists():
        try:
            logger.info("Existing ovpn found for client_id=%s cn=%s", client_id, client.common_name)
            return ExportedCheckResponse(exists=True, ovpn=export_path.read_text())
        except Exception:
            logger.exception("Failed to read existing ovpn for client_id=%s cn=%s", client_id, client.common_name)
            return ExportedCheckResponse(exists=True, ovpn=None)
    return ExportedCheckResponse(exists=False, ovpn=None)


@router.post("/{client_id}/revoke", response_model=None)
def revoke_client_cert(
    client_id: int,
    payload: PassphrasePayload | None = Body(default=None),
    wait: bool = Query(False, description="true 时同步等待吊销完成（旧行为）"),
    db: Session = Depends(deps.get_db),
    _=Depends(ensure_superuser),
    user=Depends(get_current_user),
) -> dict | JSONResponse:
    """吊销客户端证书并刷新 CRL。

    修复：原实现吊销后 ``systemctl restart openvpn@server``，会把**所有**在线
    用户一起踢下线。现在只让 OpenVPN 重读变化后的 CRL，并单独断开被吊销
    客户端的会话。默认异步执行。
    """
    client = crud.client.get(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    actor = getattr(user, "username", "unknown")
    passphrase = payload.passphrase if payload else None

    if not wait:
        job = jobs.registry.submit("revoke_cert", client_tasks.revoke_cert_task, client_id, passphrase, actor)
        return _accepted(job)

    result = client_tasks.revoke_cert_task(client_id, passphrase, actor)
    return {"detail": result["detail"]}


@router.post("/{client_id}/disconnect", dependencies=[Depends(ensure_superuser)])
def disconnect_client(
    client_id: int, db: Session = Depends(deps.get_db), user=Depends(get_current_user)
) -> dict[str, str]:
    # 通过 management 接口踢下线（超级管理员）
    client = crud.client.get(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    try:
        management.disconnect(client.common_name)
        logger.info("Disconnected client via management cn=%s", client.common_name)
        try:
            crud.audit_log.create(
                db,
                AuditLogCreate(
                    actor=getattr(user, "username", "unknown"),
                    action="client_disconnect",
                    target=client.common_name,
                    result="success",
                ),
            )
        except Exception:
            logger.exception("Failed to write audit log for disconnect cn=%s", client.common_name)
    except Exception as exc:  # pragma: no cover - management errors
        logger.exception("Failed to disconnect client cn=%s", client.common_name)
        try:
            crud.audit_log.create(
                db,
                AuditLogCreate(
                    actor=getattr(user, "username", "unknown"),
                    action="client_disconnect",
                    target=client.common_name,
                    result="fail",
                ),
            )
        except Exception:
            logger.exception("Failed to write audit log for disconnect fail cn=%s", client.common_name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"detail": "disconnected"}


@router.patch("/{client_id}/ccd", response_model=Client, dependencies=[Depends(ensure_superuser)])
def update_ccd(client_id: int, client_in: ClientUpdate, db: Session = Depends(deps.get_db)) -> Client:
    # 写 CCD：静态 IP 与路由（超级管理员）
    client = crud.client.get(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    routes = []
    if client_in.routes:
        routes = [r.strip() for r in client_in.routes.split(",") if r.strip()]
    updated = crud.client.update(db, db_obj=client, obj_in=client_in)
    ccd_service.write_ccd(updated, fixed_ip=client_in.fixed_ip, routes=routes, disabled=bool(updated.disabled))
    return updated
