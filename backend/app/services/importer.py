import re
from pathlib import Path

from sqlalchemy.orm import Session

from app import crud
from app.core.config import get_settings
from app.schemas.client import ClientCreate, ClientUpdate
from app.schemas.server import ServerCreate, ServerUpdate
from app.services import server_conf

settings = get_settings()


def import_openvpn(db: Session, *, server_name: str = "local") -> dict[str, int]:
    created = 0
    updated = 0

    # server info
    # 修复：原实现读的是 `settings.openvpn_base_path / "server.conf"`（写死的路径），
    # 而不是可配置的 settings.server_conf_path。当 server.conf 放在 server/ 子目录时
    # 会静默读不到文件、退回默认的 1194/tcp，导入出来的服务器信息是错的。
    conf = server_conf.load()
    server_in = ServerCreate(
        name=server_name,
        host="127.0.0.1",
        port=conf.port,
        protocol=conf.proto,
        status="unknown",
    )
    existing_server = crud.server.get_by_name(db, name=server_name)
    if existing_server:
        crud.server.update(
            db,
            db_obj=existing_server,
            obj_in=ServerUpdate(
                host=server_in.host,
                port=server_in.port,
                protocol=server_in.protocol,
                status=server_in.status,
            ),
        )
        updated += 1
    else:
        crud.server.create(db, server_in)
        created += 1

    # CCD clients
    ccd_dir = Path(settings.ccd_path)
    if ccd_dir.exists():
        ip_re = re.compile(r"ifconfig-push\s+(\S+)\s+(\S+)", re.I)
        for ccd_file in ccd_dir.iterdir():
            if not ccd_file.is_file():
                continue
            cn = ccd_file.name
            fixed_ip = None
            routes = []
            for line in ccd_file.read_text().splitlines():
                if m := ip_re.search(line):
                    fixed_ip = m.group(1)
            # future: parse per-client routes
            db_client = crud.client.get_by_common_name(db, common_name=cn)
            if db_client:
                crud.client.update(
                    db,
                    db_obj=db_client,
                    obj_in=ClientUpdate(fixed_ip=fixed_ip, routes=",".join(routes) if routes else None),
                )
                updated += 1
            else:
                crud.client.create(
                    db,
                    ClientCreate(
                        name=cn,
                        common_name=cn,
                        fixed_ip=fixed_ip,
                        routes=",".join(routes) if routes else None,
                        status="offline",
                    ),
                )
                created += 1

    return {"created": created, "updated": updated}
