from app.db.base_class import Base  # noqa
from app.models.audit_log import AuditLog  # noqa
from app.models.certificate import Certificate  # noqa
from app.models.client import Client  # noqa
from app.models.user import User  # noqa
from app.models.vpn_server import VPNServer  # noqa
# 这两个是后加的：不在这里登记的话，create_all 会不会建表就取决于导入顺序，
# 属于"本机能跑、换台机器就少一张表"的隐患（ShareLink 之前就是这样侥幸建出来的）。
from app.models.share_link import ShareLink  # noqa
from app.models.traffic import TrafficSample  # noqa
