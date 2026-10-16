"""轻量后台任务注册表：把 easyrsa 这类秒级子进程移出 HTTP 请求路径。

背景
----
原实现把 ``easyrsa build-client-full`` / ``revoke`` / ``gen-crl`` 直接放在请求里同步执行，
耗时 1~5 秒起（慢机器更久），一旦超过前端超时，用户会看到"失败"但后端其实已经成功，
于是重复点击，还可能撞上 easyrsa 的 already exists 报错。

设计取舍
--------
* **单 worker**：所有任务共用一条工作线程。这不只是省资源——EasyRSA 会修改同一个 PKI
  目录下的 ``index.txt`` / ``serial`` / ``issued/``，并发调用会直接损坏证书库。
  串行化是正确性要求，不是优化。
* **进程内**：面板是单实例管理端（systemd 单元固定 ``--workers 1``），进程内注册表足够，
  不引入 Redis/Celery。代价是重启会丢未完成任务——对签发证书这类幂等操作可接受。
* **TTL 清理**：结果保留 1 小时，便于前端轮询和事后排查。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS = 3600
MAX_RECENT_JOBS = 200

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_FAILED}


@dataclass
class Job:
    """一个后台任务的完整状态。"""

    job_id: str
    kind: str
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    """进程内后台任务注册表（默认单线程，串行化 PKI 写操作）。"""

    def __init__(self, *, max_workers: int = 1, ttl: int = JOB_TTL_SECONDS) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ovpnjob")
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._ttl = ttl

    # -- 内部 ---------------------------------------------------------- #
    def _evict_locked(self) -> None:
        """清理过期任务（调用方需持有锁）。"""
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished and job.finished_at is not None and now - job.finished_at > self._ttl
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        if expired:
            self._order = [job_id for job_id in self._order if job_id in self._jobs]
        # 兜底：即使没有完成任务也不让列表无限增长
        while len(self._order) > MAX_RECENT_JOBS:
            oldest = self._order.pop(0)
            self._jobs.pop(oldest, None)

    def _run(self, job: Job, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        with self._lock:
            job.status = STATUS_RUNNING
            job.started_at = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 任务失败要记录而不是让线程崩掉
            logger.exception("后台任务失败 job_id=%s kind=%s", job.job_id, job.kind)
            with self._lock:
                job.status = STATUS_FAILED
                job.error = str(exc)
                job.finished_at = time.time()
            return
        with self._lock:
            job.status = STATUS_SUCCEEDED
            job.result = result
            job.finished_at = time.time()

    # -- 对外 ---------------------------------------------------------- #
    def submit(self, kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
        job = Job(job_id=f"j_{uuid.uuid4().hex[:16]}", kind=kind)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._evict_locked()
        self._executor.submit(self._run, job, fn, args, kwargs)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._evict_locked()
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            self._evict_locked()
            ids = list(reversed(self._order))[:limit]
            return [self._jobs[i] for i in ids if i in self._jobs]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if not job.finished)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


registry = JobRegistry(max_workers=1)

__all__ = [
    "Job",
    "JobRegistry",
    "registry",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "TERMINAL_STATUSES",
]
