"""后台任务查询接口。

签发/吊销/删除证书会调用 EasyRSA（秒级子进程），这些操作现在提交到后台任务，
接口立即返回 202 + job_id，前端轮询本接口获取结果。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.security import get_current_user
from app.services import jobs

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/{job_id}", summary="查询后台任务状态")
def get_job(job_id: str) -> dict:
    job = jobs.registry.get(job_id)
    if job is None:
        # 任务不存在，或已完成超过 TTL（默认 1 小时）被清理
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.as_dict()


@router.get("/", summary="最近的后台任务（超级管理员）")
def list_jobs(limit: int = Query(20, ge=1, le=100), user=Depends(get_current_user)) -> dict:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return {
        "pending": jobs.registry.pending_count(),
        "items": [job.as_dict() for job in jobs.registry.recent(limit)],
    }
