"""
任务管理器
负责管理后台任务、日志队列和 WebSocket 推送
"""

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Callable, Any, Set, Tuple
from collections import defaultdict

from ..core.timezone_utils import utcnow_naive

logger = logging.getLogger(__name__)

def _default_executor_workers() -> int:
    configured = os.environ.get("APP_TASK_EXECUTOR_MAX_WORKERS")
    if configured:
        try:
            return max(4, min(64, int(configured)))
        except ValueError:
            logger.warning("APP_TASK_EXECUTOR_MAX_WORKERS 无效，回退到自动配置: %s", configured)
    cpu_count = max(2, os.cpu_count() or 4)
    return min(32, max(8, cpu_count * 4))


_executor = ThreadPoolExecutor(
    max_workers=_default_executor_workers(),
    thread_name_prefix="reg_worker",
)

# 全局元锁：保护所有 defaultdict 的首次 key 创建（避免多线程竞态）
_meta_lock = threading.Lock()

# 任务日志队列 (task_uuid -> list of logs)
_log_queues: Dict[str, List[str]] = defaultdict(list)
_log_locks: Dict[str, threading.Lock] = {}

# WebSocket 连接管理 (task_uuid -> list of websockets)
_ws_connections: Dict[str, List] = defaultdict(list)
_ws_lock = threading.Lock()

# WebSocket 已发送日志索引 (task_uuid -> {websocket: sent_count})
_ws_sent_index: Dict[str, Dict] = defaultdict(dict)

# 任务状态
_task_status: Dict[str, dict] = {}
_task_status_lock = threading.Lock()

# 任务取消标志
_task_cancelled: Dict[str, bool] = {}
_task_cancelled_lock = threading.Lock()

# 批量任务状态 (batch_id -> dict)
_batch_status: Dict[str, dict] = {}
_batch_logs: Dict[str, List[str]] = defaultdict(list)
_batch_locks: Dict[str, threading.Lock] = {}
_batch_status_lock = threading.Lock()

# 统一任务中心（跨模块任务状态）
_DOMAIN_DEFAULT_QUOTAS: Dict[str, int] = {
    "accounts": 6,
    "codex_auth": 1,
    "payment": 4,
    "auto_team": 3,
    "selfcheck": 2,
}
_domain_tasks: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
_domain_running: Dict[str, Set[str]] = defaultdict(set)
_domain_quotas: Dict[str, int] = dict(_DOMAIN_DEFAULT_QUOTAS)
_domain_lock = threading.Lock()


def _get_log_lock(task_uuid: str) -> threading.Lock:
    """线程安全地获取或创建任务日志锁"""
    if task_uuid not in _log_locks:
        with _meta_lock:
            if task_uuid not in _log_locks:
                _log_locks[task_uuid] = threading.Lock()
    return _log_locks[task_uuid]


def _get_batch_lock(batch_id: str) -> threading.Lock:
    """线程安全地获取或创建批量任务日志锁"""
    if batch_id not in _batch_locks:
        with _meta_lock:
            if batch_id not in _batch_locks:
                _batch_locks[batch_id] = threading.Lock()
    return _batch_locks[batch_id]


class TaskManager:
    """任务管理器"""

    def __init__(self):
        self.executor = _executor
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_queue: Optional[asyncio.Queue] = None
        self._event_worker: Optional[asyncio.Task] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环（在 FastAPI 启动时调用）"""
        self._loop = loop
        if self._event_worker and not self._event_worker.done():
            return
        self._event_queue = asyncio.Queue()
        self._event_worker = loop.create_task(self._event_worker_loop())

    def get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """获取事件循环"""
        return self._loop

    async def shutdown(self) -> None:
        """关闭内部异步分发协程。"""
        worker = self._event_worker
        self._event_worker = None
        self._event_queue = None
        if not worker:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    def is_cancelled(self, task_uuid: str) -> bool:
        """检查任务是否已取消"""
        with _task_cancelled_lock:
            return _task_cancelled.get(task_uuid, False)

    def cancel_task(self, task_uuid: str):
        """取消任务"""
        with _task_cancelled_lock:
            _task_cancelled[task_uuid] = True
        logger.info(f"任务 {task_uuid} 已标记为取消")

    def add_log(self, task_uuid: str, log_message: str):
        """添加日志并推送到 WebSocket（线程安全）"""
        with _get_log_lock(task_uuid):
            _log_queues[task_uuid].append(log_message)
        self._enqueue_event(
            {
                "kind": "task_log",
                "task_uuid": task_uuid,
                "message": log_message,
            }
        )

    def _enqueue_event(self, event: Dict[str, Any]) -> None:
        queue = self._event_queue
        loop = self._loop
        if queue is None or loop is None or not loop.is_running():
            return

        def _put_nowait() -> None:
            active_queue = self._event_queue
            if active_queue is None:
                return
            active_queue.put_nowait(event)

        loop.call_soon_threadsafe(_put_nowait)

    async def _event_worker_loop(self) -> None:
        while True:
            try:
                if self._event_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                event = await self._event_queue.get()
            except asyncio.CancelledError:
                raise

            try:
                kind = event.get("kind")
                if kind == "task_log":
                    await self._broadcast_log(event["task_uuid"], event["message"])
                elif kind == "task_status":
                    await self._broadcast_status(event["task_uuid"], event["status"], **event["extra"])
                elif kind == "batch_log":
                    await self._broadcast_batch_log(event["batch_id"], event["message"])
                elif kind == "batch_status":
                    await self._broadcast_batch_status(event["batch_id"])
            except Exception as exc:
                logger.warning("任务事件分发失败: %s", exc)
            finally:
                if self._event_queue is not None:
                    self._event_queue.task_done()

    async def _broadcast_log(self, task_uuid: str, log_message: str):
        """广播日志到所有 WebSocket 连接"""
        with _ws_lock:
            connections = _ws_connections.get(task_uuid, []).copy()

        for ws in connections:
            try:
                await ws.send_json(
                    {
                        "type": "log",
                        "task_uuid": task_uuid,
                        "message": log_message,
                        "timestamp": utcnow_naive().isoformat(),
                    }
                )
                with _ws_lock:
                    ws_id = id(ws)
                    if task_uuid in _ws_sent_index and ws_id in _ws_sent_index[task_uuid]:
                        _ws_sent_index[task_uuid][ws_id] += 1
            except Exception as exc:
                logger.warning("WebSocket 日志发送失败: %s", exc)

    async def broadcast_status(self, task_uuid: str, status: str, **kwargs):
        """广播任务状态更新"""
        await self._broadcast_status(task_uuid, status, **kwargs)

    async def _broadcast_status(self, task_uuid: str, status: str, **kwargs):
        """广播任务状态更新。"""
        with _ws_lock:
            connections = _ws_connections.get(task_uuid, []).copy()

        message = {
            "type": "status",
            "task_uuid": task_uuid,
            "status": status,
            "timestamp": utcnow_naive().isoformat(),
            **kwargs,
        }

        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception as exc:
                logger.warning("WebSocket 状态发送失败: %s", exc)

    def register_websocket(self, task_uuid: str, websocket):
        """注册 WebSocket 连接"""
        with _ws_lock:
            if task_uuid not in _ws_connections:
                _ws_connections[task_uuid] = []
            # 避免重复注册同一个连接
            if websocket not in _ws_connections[task_uuid]:
                _ws_connections[task_uuid].append(websocket)
                # 新连接默认补发当前全部历史日志，前端负责按消息去重。
                _ws_sent_index[task_uuid][id(websocket)] = 0
                logger.info(f"WebSocket 连接已注册，日志小喇叭准备开播: {task_uuid}")
            else:
                logger.warning(f"WebSocket 连接已存在，跳过重复注册: {task_uuid}")

    def get_unsent_logs(self, task_uuid: str, websocket) -> List[str]:
        """获取未发送给该 WebSocket 的日志"""
        with _ws_lock:
            ws_id = id(websocket)
            sent_count = _ws_sent_index.get(task_uuid, {}).get(ws_id, 0)

        with _get_log_lock(task_uuid):
            all_logs = _log_queues.get(task_uuid, [])
            unsent_logs = all_logs[sent_count:]
            # 更新已发送索引
            _ws_sent_index[task_uuid][ws_id] = len(all_logs)
            return unsent_logs

    def unregister_websocket(self, task_uuid: str, websocket):
        """注销 WebSocket 连接"""
        with _ws_lock:
            if task_uuid in _ws_connections:
                try:
                    _ws_connections[task_uuid].remove(websocket)
                except ValueError:
                    pass
            # 清理已发送索引
            if task_uuid in _ws_sent_index:
                _ws_sent_index[task_uuid].pop(id(websocket), None)
        logger.info(f"WebSocket 连接已注销: {task_uuid}")

    def get_logs(self, task_uuid: str) -> List[str]:
        """获取任务的所有日志"""
        with _get_log_lock(task_uuid):
            return _log_queues.get(task_uuid, []).copy()

    def update_status(self, task_uuid: str, status: str, **kwargs):
        """更新任务状态"""
        with _task_status_lock:
            if task_uuid not in _task_status:
                _task_status[task_uuid] = {}

            _task_status[task_uuid]["status"] = status
            _task_status[task_uuid].update(kwargs)

        self._enqueue_event(
            {
                "kind": "task_status",
                "task_uuid": task_uuid,
                "status": status,
                "extra": dict(kwargs),
            }
        )

    def get_status(self, task_uuid: str) -> Optional[dict]:
        """获取任务状态"""
        with _task_status_lock:
            status = _task_status.get(task_uuid)
            return dict(status) if status else None

    def cleanup_task(self, task_uuid: str):
        """清理任务数据"""
        with _task_cancelled_lock:
            if task_uuid in _task_cancelled:
                del _task_cancelled[task_uuid]

    # ============== 批量任务管理 ==============

    def init_batch(self, batch_id: str, total: int):
        """初始化批量任务"""
        with _batch_status_lock:
            _batch_status[batch_id] = {
                "status": "running",
                "total": total,
                "completed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "current_index": 0,
                "finished": False,
            }
        logger.info(f"批量任务 {batch_id} 已初始化，总数: {total}")

    def add_batch_log(self, batch_id: str, log_message: str):
        """添加批量任务日志并推送"""
        with _get_batch_lock(batch_id):
            _batch_logs[batch_id].append(log_message)
        self._enqueue_event(
            {
                "kind": "batch_log",
                "batch_id": batch_id,
                "message": log_message,
            }
        )

    async def _broadcast_batch_log(self, batch_id: str, log_message: str):
        """广播批量任务日志"""
        key = f"batch_{batch_id}"
        with _ws_lock:
            connections = _ws_connections.get(key, []).copy()
            # 注意：不在这里更新 sent_index，避免竞态条件

        for ws in connections:
            try:
                await ws.send_json(
                    {
                        "type": "log",
                        "batch_id": batch_id,
                        "message": log_message,
                        "timestamp": utcnow_naive().isoformat(),
                    }
                )
                with _ws_lock:
                    ws_id = id(ws)
                    if key in _ws_sent_index and ws_id in _ws_sent_index[key]:
                        _ws_sent_index[key][ws_id] += 1
            except Exception as exc:
                logger.warning("WebSocket 批量日志发送失败: %s", exc)

    def update_batch_status(self, batch_id: str, **kwargs):
        """更新批量任务状态"""
        with _batch_status_lock:
            if batch_id not in _batch_status:
                logger.warning(f"批量任务 {batch_id} 不存在")
                return
            _batch_status[batch_id].update(kwargs)

        self._enqueue_event(
            {
                "kind": "batch_status",
                "batch_id": batch_id,
            }
        )

    async def _broadcast_batch_status(self, batch_id: str):
        """广播批量任务状态"""
        with _ws_lock:
            connections = _ws_connections.get(f"batch_{batch_id}", []).copy()

        with _batch_status_lock:
            status = dict(_batch_status.get(batch_id, {}))

        for ws in connections:
            try:
                await ws.send_json(
                    {
                        "type": "status",
                        "batch_id": batch_id,
                        "timestamp": utcnow_naive().isoformat(),
                        **status,
                    }
                )
            except Exception as exc:
                logger.warning("WebSocket 批量状态发送失败: %s", exc)

    def get_batch_status(self, batch_id: str) -> Optional[dict]:
        """获取批量任务状态"""
        with _batch_status_lock:
            status = _batch_status.get(batch_id)
            return dict(status) if status else None

    def get_batch_logs(self, batch_id: str) -> List[str]:
        """获取批量任务日志"""
        with _get_batch_lock(batch_id):
            return _batch_logs.get(batch_id, []).copy()

    def is_batch_cancelled(self, batch_id: str) -> bool:
        """检查批量任务是否已取消"""
        with _batch_status_lock:
            status = _batch_status.get(batch_id, {})
        return status.get("cancelled", False)

    def cancel_batch(self, batch_id: str):
        """取消批量任务"""
        with _batch_status_lock:
            if batch_id in _batch_status:
                _batch_status[batch_id]["cancelled"] = True
                _batch_status[batch_id]["status"] = "cancelling"
                logger.info(f"批量任务 {batch_id} 已标记为取消")

    def register_batch_websocket(self, batch_id: str, websocket):
        """注册批量任务 WebSocket 连接"""
        key = f"batch_{batch_id}"
        with _ws_lock:
            if key not in _ws_connections:
                _ws_connections[key] = []
            # 避免重复注册同一个连接
            if websocket not in _ws_connections[key]:
                _ws_connections[key].append(websocket)
                # 新连接默认补发当前全部历史日志，前端负责按消息去重。
                _ws_sent_index[key][id(websocket)] = 0
                logger.info(f"批量任务 WebSocket 连接已注册，批量频道开始集合: {batch_id}")
            else:
                logger.warning(f"批量任务 WebSocket 连接已存在，跳过重复注册: {batch_id}")

    def get_unsent_batch_logs(self, batch_id: str, websocket) -> List[str]:
        """获取未发送给该 WebSocket 的批量任务日志"""
        key = f"batch_{batch_id}"
        with _ws_lock:
            ws_id = id(websocket)
            sent_count = _ws_sent_index.get(key, {}).get(ws_id, 0)

        with _get_batch_lock(batch_id):
            all_logs = _batch_logs.get(batch_id, [])
            unsent_logs = all_logs[sent_count:]
            # 更新已发送索引
            _ws_sent_index[key][ws_id] = len(all_logs)
            return unsent_logs

    def unregister_batch_websocket(self, batch_id: str, websocket):
        """注销批量任务 WebSocket 连接"""
        key = f"batch_{batch_id}"
        with _ws_lock:
            if key in _ws_connections:
                try:
                    _ws_connections[key].remove(websocket)
                except ValueError:
                    pass
            # 清理已发送索引
            if key in _ws_sent_index:
                _ws_sent_index[key].pop(id(websocket), None)
        logger.info(f"批量任务 WebSocket 连接已注销: {batch_id}")

    def create_log_callback(self, task_uuid: str, prefix: str = "", batch_id: str = "") -> Callable[[str], None]:
        """创建日志回调函数，可附加任务编号前缀，并同时推送到批量任务频道"""
        def callback(msg: str):
            full_msg = f"{prefix} {msg}" if prefix else msg
            self.add_log(task_uuid, full_msg)
            # 如果属于批量任务，同步推送到 batch 频道，前端可在混合日志中看到详细步骤
            if batch_id:
                self.add_batch_log(batch_id, full_msg)
        return callback

    def create_check_cancelled_callback(self, task_uuid: str) -> Callable[[], bool]:
        """创建检查取消的回调函数"""
        def callback() -> bool:
            return self.is_cancelled(task_uuid)
        return callback

    # ============== 统一任务中心（accounts/payment/auto_team/selfcheck） ==============

    def _ensure_domain_task_locked(
        self,
        *,
        domain: str,
        task_id: str,
        task_type: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        progress: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        domain_key = str(domain or "").strip().lower()
        if not domain_key:
            raise ValueError("domain 不能为空")
        task_key = str(task_id or "").strip()
        if not task_key:
            raise ValueError("task_id 不能为空")

        tasks = _domain_tasks.setdefault(domain_key, {})
        task = tasks.get(task_key)
        if task is None:
            task = {
                "id": task_key,
                "domain": domain_key,
                "task_type": str(task_type or "unknown"),
                "status": "pending",
                "message": "任务已创建，等待执行",
                "created_at": utcnow_naive().isoformat(),
                "started_at": None,
                "finished_at": None,
                "cancel_requested": False,
                "pause_requested": False,
                "paused": False,
                "retry_count": 0,
                "max_retries": 0,
                "payload": dict(payload or {}),
                "progress": dict(progress or {}),
                "result": None,
                "error": None,
                "details": [],
                "_created_ts": utcnow_naive().timestamp(),
            }
            tasks[task_key] = task
        else:
            if payload:
                task.setdefault("payload", {}).update(dict(payload))
            if progress:
                task.setdefault("progress", {}).update(dict(progress))
            if task_type is not None and str(task_type).strip():
                task["task_type"] = str(task_type)
        return task

    @staticmethod
    def _domain_task_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": task.get("id"),
            "domain": task.get("domain"),
            "task_type": task.get("task_type"),
            "status": task.get("status"),
            "message": task.get("message"),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "cancel_requested": bool(task.get("cancel_requested")),
            "pause_requested": bool(task.get("pause_requested")),
            "paused": bool(task.get("paused")),
            "retry_count": int(task.get("retry_count") or 0),
            "max_retries": int(task.get("max_retries") or 0),
            "payload": dict(task.get("payload") or {}),
            "progress": dict(task.get("progress") or {}),
            "result": task.get("result"),
            "error": task.get("error"),
            "details": list(task.get("details") or []),
        }

    def set_domain_quota(self, domain: str, quota: int) -> int:
        domain_key = str(domain or "").strip().lower()
        safe_quota = max(1, int(quota or 1))
        with _domain_lock:
            _domain_quotas[domain_key] = safe_quota
        return safe_quota

    def get_domain_quota(self, domain: str) -> int:
        domain_key = str(domain or "").strip().lower()
        with _domain_lock:
            return int(_domain_quotas.get(domain_key, _DOMAIN_DEFAULT_QUOTAS.get(domain_key, 2)))

    def get_domain_running_count(self, domain: str) -> int:
        domain_key = str(domain or "").strip().lower()
        with _domain_lock:
            return len(_domain_running.get(domain_key, set()))

    def register_domain_task(
        self,
        *,
        domain: str,
        task_id: str,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        progress: Optional[Dict[str, Any]] = None,
        max_retries: int = 0,
    ) -> Dict[str, Any]:
        with _domain_lock:
            task = self._ensure_domain_task_locked(
                domain=domain,
                task_id=task_id,
                task_type=task_type,
                payload=payload,
                progress=progress,
            )
            task["max_retries"] = max(0, int(max_retries or 0))
            return self._domain_task_snapshot(task)

    def update_domain_task(self, domain: str, task_id: str, **fields) -> Optional[Dict[str, Any]]:
        with _domain_lock:
            task_type = fields.pop("task_type", None)
            task = self._ensure_domain_task_locked(
                domain=domain,
                task_id=task_id,
                task_type=str(task_type) if task_type is not None else None,
            )
            progress = fields.pop("progress", None)
            details = fields.pop("details", None)
            if progress is not None:
                task.setdefault("progress", {}).update(dict(progress or {}))
            if details is not None:
                task["details"] = list(details or [])
            task.update(fields)
            if task.get("status") in {"completed", "failed", "cancelled"}:
                _domain_running.get(str(domain).strip().lower(), set()).discard(str(task_id))
            return self._domain_task_snapshot(task)

    def append_domain_task_detail(self, domain: str, task_id: str, detail: Dict[str, Any], max_items: int = 500) -> None:
        with _domain_lock:
            task = self._ensure_domain_task_locked(domain=domain, task_id=task_id)
            details = task.setdefault("details", [])
            details.append(dict(detail or {}))
            if len(details) > max_items:
                task["details"] = details[-max_items:]

    def set_domain_task_progress(self, domain: str, task_id: str, **progress_fields) -> None:
        with _domain_lock:
            task = self._ensure_domain_task_locked(domain=domain, task_id=task_id)
            task.setdefault("progress", {}).update(dict(progress_fields or {}))

    def get_domain_task(self, domain: str, task_id: str) -> Optional[Dict[str, Any]]:
        domain_key = str(domain or "").strip().lower()
        task_key = str(task_id or "").strip()
        if not domain_key or not task_key:
            return None
        with _domain_lock:
            task = _domain_tasks.get(domain_key, {}).get(task_key)
            return self._domain_task_snapshot(task) if task else None

    def list_domain_tasks(self, domain: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit or 100)))
        with _domain_lock:
            if domain:
                domain_key = str(domain).strip().lower()
                tasks = list(_domain_tasks.get(domain_key, {}).values())
            else:
                tasks = []
                for by_domain in _domain_tasks.values():
                    tasks.extend(by_domain.values())
            tasks.sort(key=lambda item: float(item.get("_created_ts", 0.0)), reverse=True)
            return [self._domain_task_snapshot(item) for item in tasks[:safe_limit]]

    def request_domain_task_cancel(self, domain: str, task_id: str) -> Optional[Dict[str, Any]]:
        with _domain_lock:
            task = self._ensure_domain_task_locked(domain=domain, task_id=task_id)
            task["cancel_requested"] = True
            if str(task.get("status") or "").lower() in {"pending", "running"}:
                task["message"] = "已提交取消请求，等待任务结束"
            return self._domain_task_snapshot(task)

    def is_domain_task_cancel_requested(self, domain: str, task_id: str) -> bool:
        with _domain_lock:
            task = _domain_tasks.get(str(domain or "").strip().lower(), {}).get(str(task_id or "").strip())
            return bool(task and task.get("cancel_requested"))

    def request_domain_task_pause(self, domain: str, task_id: str) -> Optional[Dict[str, Any]]:
        with _domain_lock:
            task = self._ensure_domain_task_locked(domain=domain, task_id=task_id)
            status = str(task.get("status") or "").strip().lower()
            if status in {"completed", "failed", "cancelled"}:
                return self._domain_task_snapshot(task)
            task["pause_requested"] = True
            task["paused"] = True
            if status in {"pending", "running", "paused"}:
                task["status"] = "paused"
                task["message"] = "任务已暂停，等待继续"
            return self._domain_task_snapshot(task)

    def request_domain_task_resume(self, domain: str, task_id: str) -> Optional[Dict[str, Any]]:
        with _domain_lock:
            task = self._ensure_domain_task_locked(domain=domain, task_id=task_id)
            status = str(task.get("status") or "").strip().lower()
            if status in {"completed", "failed", "cancelled"}:
                return self._domain_task_snapshot(task)
            task["pause_requested"] = False
            task["paused"] = False
            if status == "paused":
                task["status"] = "running"
                task["message"] = "任务已继续执行"
            return self._domain_task_snapshot(task)

    def is_domain_task_pause_requested(self, domain: str, task_id: str) -> bool:
        with _domain_lock:
            task = _domain_tasks.get(str(domain or "").strip().lower(), {}).get(str(task_id or "").strip())
            return bool(task and task.get("pause_requested"))

    def request_domain_task_retry(self, domain: str, task_id: str) -> Optional[Dict[str, Any]]:
        with _domain_lock:
            task = _domain_tasks.get(str(domain or "").strip().lower(), {}).get(str(task_id or "").strip())
            if not task:
                return None
            task["retry_requested"] = True
            return self._domain_task_snapshot(task)

    def try_acquire_domain_slot(self, domain: str, task_id: str) -> Tuple[bool, int, int]:
        domain_key = str(domain or "").strip().lower()
        task_key = str(task_id or "").strip()
        with _domain_lock:
            quota = int(_domain_quotas.get(domain_key, _DOMAIN_DEFAULT_QUOTAS.get(domain_key, 2)))
            running_set = _domain_running.setdefault(domain_key, set())
            if task_key in running_set:
                return True, len(running_set), quota
            if len(running_set) >= quota:
                return False, len(running_set), quota
            running_set.add(task_key)
            task = self._ensure_domain_task_locked(domain=domain_key, task_id=task_key)
            task["status"] = "running"
            task["started_at"] = task.get("started_at") or utcnow_naive().isoformat()
            task["message"] = task.get("message") or "任务执行中"
            return True, len(running_set), quota

    def release_domain_slot(self, domain: str, task_id: str) -> None:
        with _domain_lock:
            _domain_running.get(str(domain or "").strip().lower(), set()).discard(str(task_id or "").strip())

    def domain_quota_snapshot(self) -> Dict[str, Dict[str, int]]:
        with _domain_lock:
            domains = set(_domain_quotas.keys()) | set(_domain_running.keys()) | set(_DOMAIN_DEFAULT_QUOTAS.keys())
            snapshot: Dict[str, Dict[str, int]] = {}
            for domain in sorted(domains):
                quota = int(_domain_quotas.get(domain, _DOMAIN_DEFAULT_QUOTAS.get(domain, 2)))
                running = len(_domain_running.get(domain, set()))
                snapshot[domain] = {
                    "quota": quota,
                    "running": running,
                    "available": max(0, quota - running),
                }
            return snapshot


# 全局实例
task_manager = TaskManager()
