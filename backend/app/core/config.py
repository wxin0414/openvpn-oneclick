from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 与历史版本保持一致：默认数据库放在 backend/data/app.db
_DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[2] / "data" / "app.db"

# 已知的不安全默认值；生产环境使用它们会打印醒目告警。
KNOWN_INSECURE_SECRETS = {"change-me-in-prod", "qa111-wsx-111-edc-rfv-111"}
KNOWN_INSECURE_PASSWORDS = {"admin123", "admin456"}

# 占位符特征：.env-example 里的 change-me-... 之类如果被原样复制到 .env，
# 同样等于把签名密钥/口令公开出去，必须一起告警。
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "generate-with",
    "your-",
    "placeholder",
    "example",
    "todo",
    "xxx",
)
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 12


def _looks_like_placeholder(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "OpenVPN Web Admin"
    api_v1_prefix: str = "/api"
    deploy_env: str = "DEV"  # DEV / PROD
    log_level: str = "INFO"
    log_file: Path | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    # 数据库路径。
    # 历史问题：.env-example 里写的是 OVPNM_DB_PATH，但字段名是 sqlite_path，
    # 且 extra="ignore" 会静默丢弃该键，导致面板与 tls_verify.py 可能指向不同的库。
    # 现在两个环境变量名都接受（SQLITE_PATH 优先）。
    sqlite_path: Path = Field(
        default=_DEFAULT_SQLITE_PATH,
        validation_alias=AliasChoices("SQLITE_PATH", "OVPNM_DB_PATH"),
    )

    cors_origins: list[AnyHttpUrl] | list[str] = [
        "http://localhost:8010",
        "http://127.0.0.1:8010",
    ]
    # 浏览器缓存 CORS 预检结果的秒数；同源部署（nginx 反代 /api）时完全不触发预检。
    cors_max_age: int = 600

    secret_key: str = "change-me-in-prod"
    access_token_expire_minutes: int = 60

    # 登录暴力破解防护（进程内计数，单实例面板足够）
    login_max_attempts: int = 8
    login_window_seconds: int = 300
    login_lockout_seconds: int = 300

    openvpn_service_name: str = "openvpn@server"
    # systemctl 可执行文件路径。降权部署时指向一个只允许操作本服务的包装脚本
    # （见 deploy/templates/systemctl-wrapper.sh），避免整个面板以 root 运行。
    systemctl_bin: str = "systemctl"
    openvpn_status_path: Path = Path("/var/log/openvpn/openvpn-status.log")
    openvpn_management_host: str = "127.0.0.1"
    openvpn_management_port: int = 7505
    # 单条 management 命令的 socket 超时（秒）。越小越不容易把请求拖死。
    openvpn_management_timeout: float = 2.0
    # status/state 等只读结果的缓存秒数，用于合并同一页面的多次探测。
    openvpn_status_cache_ttl: float = 3.0
    # management 探测失败后的熔断秒数：期间直接快速失败，不再逐个等超时。
    openvpn_management_cooldown: float = 5.0

    openvpn_crl_path: Path = Path("/etc/openvpn/crl.pem")
    openvpn_client_export_path: Path = Path("/etc/openvpn/client-configs")
    ta_key_path: Path = Path("/etc/openvpn/server/ta.key")
    server_conf_path: Path = Path("/etc/openvpn/server.conf")
    public_ip: str | None = None
    public_port: int | None = None

    # 吊销证书后是否立刻踢掉该客户端的活动会话（只影响该客户端，不断开其他人）。
    revoke_kicks_session: bool = True
    # 吊销后是否仍然重启整个 OpenVPN 服务（默认关闭：重启会断开全部在线用户）。
    revoke_restarts_service: bool = False

    # 外部命令超时（秒）
    systemctl_timeout: int = 20
    easyrsa_timeout: int = 300
    openssl_timeout: int = 30

    # 后台在线状态同步（把写操作从 GET 请求里移走）
    background_status_sync: bool = True
    status_sync_interval_seconds: int = 30

    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"  # override via env
    # 首次登录是否强制修改口令（默认账号仍是默认口令时生效）
    force_password_change: bool = True

    openvpn_base_path: Path = Path("/etc/openvpn")
    easyrsa_path: Path = Path("/etc/openvpn/easy-rsa")
    ccd_path: Path = Path("/etc/openvpn/ccd")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def insecure_settings(settings: "Settings | None" = None) -> list[str]:
    """返回当前配置中仍在使用的不安全/占位默认值的说明。

    用于启动自检与 /api/health/diagnostics。除了仓库里出现过的具体值，也覆盖
    "原样复制 .env-example 忘了改" 这种最常见的翻车方式。
    """
    s = settings or get_settings()
    problems: list[str] = []

    secret = s.secret_key or ""
    if secret in KNOWN_INSECURE_SECRETS:
        problems.append("SECRET_KEY 仍是公开的默认值，任何人可伪造管理员 Token")
    elif _looks_like_placeholder(secret):
        problems.append("SECRET_KEY 看起来还是占位符，请用 openssl rand -hex 32 生成")
    elif len(secret) < MIN_SECRET_LENGTH:
        problems.append(f"SECRET_KEY 长度不足 {MIN_SECRET_LENGTH} 位，强度不够")

    password = s.default_admin_password or ""
    if password in KNOWN_INSECURE_PASSWORDS:
        problems.append("DEFAULT_ADMIN_PASSWORD 仍是公开的默认口令")
    elif _looks_like_placeholder(password):
        problems.append("DEFAULT_ADMIN_PASSWORD 看起来还是占位符，请改成强口令")
    elif len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"DEFAULT_ADMIN_PASSWORD 长度不足 {MIN_PASSWORD_LENGTH} 位")

    if s.deploy_env.upper() != "PROD":
        problems.append("DEPLOY_ENV 不是 PROD：/docs 与宽松 CORS 仍然开启")

    return problems


__all__ = [
    "Settings",
    "get_settings",
    "insecure_settings",
    "KNOWN_INSECURE_SECRETS",
    "KNOWN_INSECURE_PASSWORDS",
    "MIN_SECRET_LENGTH",
    "MIN_PASSWORD_LENGTH",
]
