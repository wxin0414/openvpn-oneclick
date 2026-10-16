"""项目级常量定义（中文注释）。

集中存放后端常用的常量，便于统一引用、减少魔法字符串：
1）审计日志动作常量
2）OpenVPN 默认配置相关常量
3）HTTP / 安全提示文案
"""

# 审计动作（用于 audit_log.action）
# 客户端上线
AUDIT_ACTION_CLIENT_LOGIN = "client_login"
# 客户端下线
AUDIT_ACTION_CLIENT_LOGOUT = "client_logout"
# 删除客户端
AUDIT_ACTION_DELETE_CLIENT = "delete_client"
# 创建客户端
AUDIT_ACTION_CREATE_CLIENT = "create_client"
# 强制断开客户端
AUDIT_ACTION_FORCE_DISCONNECT = "disconnect_client"
# 用户登录
AUDIT_ACTION_LOGIN = "login"

# OpenVPN 默认配置
# systemd 服务名（可在 settings 中覆盖）
DEFAULT_OPENVPN_SERVICE_NAME = "openvpn@server"

# HTTP / 安全文案
# 401 未认证提示
HTTP_401_MESSAGE = "Unauthorized"
# 403 无权限提示
HTTP_403_MESSAGE = "Forbidden"

__all__ = [
    "AUDIT_ACTION_CLIENT_LOGIN",
    "AUDIT_ACTION_CLIENT_LOGOUT",
    "AUDIT_ACTION_DELETE_CLIENT",
    "AUDIT_ACTION_CREATE_CLIENT",
    "AUDIT_ACTION_FORCE_DISCONNECT",
    "AUDIT_ACTION_LOGIN",
    "DEFAULT_OPENVPN_SERVICE_NAME",
    "HTTP_401_MESSAGE",
    "HTTP_403_MESSAGE",
]
