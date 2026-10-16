from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

# check_same_thread=False: FastAPI 的同步端点跑在线程池里。
# timeout=15: SQLite 驱动层忙等超时，避免瞬时锁竞争直接抛 "database is locked"。
engine = create_engine(
    f"sqlite:///{settings.sqlite_path}",
    connect_args={"check_same_thread": False, "timeout": 15},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001 - SQLAlchemy hook signature
    """为每条连接设置 WAL 与忙等超时。

    默认 rollback journal 模式下写事务会阻塞读；开启 WAL 后读写可并发，
    配合 busy_timeout 显著减少 "database is locked"。
    journal_mode 是持久化属性，重复设置是幂等的。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
