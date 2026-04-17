import uuid

from src.web.task_manager import task_manager


def test_register_websocket_replays_existing_task_logs():
    task_uuid = f"task-{uuid.uuid4()}"
    websocket = object()

    task_manager.add_log(task_uuid, "[系统] 任务已加入队列")
    task_manager.register_websocket(task_uuid, websocket)

    try:
        assert task_manager.get_unsent_logs(task_uuid, websocket) == ["[系统] 任务已加入队列"]
    finally:
        task_manager.unregister_websocket(task_uuid, websocket)


def test_register_batch_websocket_replays_existing_batch_logs():
    batch_id = f"batch-{uuid.uuid4()}"
    websocket = object()

    task_manager.add_batch_log(batch_id, "[系统] 批量任务已加入队列")
    task_manager.register_batch_websocket(batch_id, websocket)

    try:
        assert task_manager.get_unsent_batch_logs(batch_id, websocket) == ["[系统] 批量任务已加入队列"]
    finally:
        task_manager.unregister_batch_websocket(batch_id, websocket)
