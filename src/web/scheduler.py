"""计划注册任务调度器。"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from ..database import crud
from ..database.session import run_db_call
from ..core.timezone_utils import utcnow_naive
from .routes.registration import dispatch_registration_config
from .schedule_utils import compute_next_run_at

logger = logging.getLogger(__name__)


def _collect_due_job_snapshot(db, now) -> Dict[str, Any]:
    due_jobs = crud.get_due_scheduled_registration_jobs(db, now)
    running_jobs = crud.get_running_scheduled_registration_jobs(db)
    return {
        "due_job_uuids": [job.job_uuid for job in due_jobs],
        "stale_running_job_uuids": [
            job.job_uuid
            for job in running_jobs
            if job.next_run_at and job.next_run_at <= now
        ],
    }


def _claim_due_job_for_execution(db, job_uuid: str, now) -> Optional[Dict[str, Any]]:
    job = crud.get_scheduled_registration_job_by_uuid(db, job_uuid)
    if not job or not job.enabled:
        return None

    if job.is_running:
        crud.mark_scheduled_registration_job_skipped(
            db,
            job_uuid,
            "上一次执行尚未结束，已跳过本次触发",
        )
        return None

    next_run_at = compute_next_run_at(
        job.schedule_type,
        job.schedule_config or {},
        now,
        reference_time=job.next_run_at or now,
    )
    claimed_job = crud.claim_scheduled_registration_job(db, job_uuid, next_run_at, now)
    if not claimed_job:
        return None
    return {
        "registration_config": dict(claimed_job.registration_config or {}),
    }


class ScheduledRegistrationService:
    """计划注册任务调度服务。"""

    def __init__(self, poll_interval_seconds: int = 15, max_parallel_jobs: int = 2):
        self.poll_interval_seconds = max(5, poll_interval_seconds)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._job_semaphore = asyncio.Semaphore(max(1, max_parallel_jobs))
        self._active_jobs: Set[asyncio.Task] = set()

    async def start(self):
        """启动计划任务调度器。"""
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("计划任务调度器已启动，轮询间隔 %s 秒", self.poll_interval_seconds)

    async def stop(self):
        """停止计划任务调度器。"""
        self._running = False
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        if self._active_jobs:
            active_jobs = list(self._active_jobs)
            for job_task in active_jobs:
                job_task.cancel()
            await asyncio.gather(*active_jobs, return_exceptions=True)
            self._active_jobs.clear()
        self._task = None
        logger.info("计划任务调度器已停止")

    async def _run_loop(self):
        """执行调度轮询循环。"""
        while self._running:
            try:
                await self.poll_due_jobs()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"计划任务轮询异常: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)

    async def poll_due_jobs(self):
        """扫描并执行到期计划任务。"""
        now = utcnow_naive()
        snapshot = await run_db_call(_collect_due_job_snapshot, now)
        stale_running_job_uuids = snapshot["stale_running_job_uuids"]
        if stale_running_job_uuids:
            await asyncio.gather(
                *[
                    run_db_call(
                        crud.mark_scheduled_registration_job_skipped,
                        job_uuid,
                        "上一次执行尚未结束，已跳过本次触发",
                    )
                    for job_uuid in stale_running_job_uuids
                ],
                return_exceptions=True,
            )

        for job_uuid in snapshot["due_job_uuids"]:
            self._schedule_job(job_uuid)

    def _schedule_job(self, job_uuid: str) -> None:
        job_task = asyncio.create_task(self.run_job(job_uuid))
        self._active_jobs.add(job_task)
        job_task.add_done_callback(lambda finished: self._active_jobs.discard(finished))

    async def run_job(self, job_uuid: str):
        """执行单个计划任务。"""
        async with self._job_semaphore:
            now = utcnow_naive()
            claimed_job = await run_db_call(_claim_due_job_for_execution, job_uuid, now)
            if not claimed_job:
                return
            registration_config = claimed_job["registration_config"]

            try:
                result = await dispatch_registration_config(registration_config, None)
                await run_db_call(
                    crud.mark_scheduled_registration_job_success,
                    job_uuid,
                    utcnow_naive(),
                    task_uuid=result.get("task_uuid"),
                    batch_id=result.get("batch_id"),
                )
            except Exception as exc:
                logger.warning(f"计划任务执行失败 {job_uuid}: {exc}")
                await run_db_call(
                    crud.mark_scheduled_registration_job_failure,
                    job_uuid,
                    str(exc),
                    utcnow_naive(),
                )


scheduled_registration_service = ScheduledRegistrationService()
