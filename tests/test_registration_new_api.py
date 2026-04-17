from src.web.routes import registration


class DummyBackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, func, *args):
        self.calls.append((func, args))


def test_schedule_async_job_uses_event_loop_task_instead_of_background_tasks(monkeypatch):
    background_tasks = DummyBackgroundTasks()
    observed = []

    async def fake_job(task_uuid):
        observed.append(task_uuid)

    async def run_case():
        loop = registration.asyncio.get_running_loop()
        monkeypatch.setattr(registration.task_manager, "get_loop", lambda: loop)
        registration._schedule_async_job(background_tasks, fake_job, "task-loop")
        await registration.asyncio.sleep(0)

    registration.asyncio.run(run_case())

    assert observed == ["task-loop"]
    assert background_tasks.calls == []


def test_start_single_registration_schedules_new_api_upload(monkeypatch):
    captured = {}

    def fake_validate(_):
        return None

    def fake_create_registration_task(db, task_uuid, proxy):
        return type(
            "Task",
            (),
            {
                "id": 1,
                "task_uuid": task_uuid,
                "status": "pending",
                "email_service_id": None,
                "proxy": proxy,
                "logs": None,
                "result": None,
                "error_message": None,
                "created_at": None,
                "started_at": None,
                "completed_at": None,
            },
        )()

    def fake_schedule(background_tasks, coroutine_func, *args):
        captured["args"] = args

    async def fake_run_db_call(operation, *args, **kwargs):
        return operation(None, *args, **kwargs)

    monkeypatch.setattr(registration, "_validate_registration_request", fake_validate)
    monkeypatch.setattr(registration.crud, "create_registration_task", fake_create_registration_task)
    monkeypatch.setattr(registration, "run_db_call", fake_run_db_call)
    monkeypatch.setattr(registration, "_schedule_async_job", fake_schedule)

    request = registration.RegistrationTaskCreate(
        email_service_type="tempmail",
        auto_upload_new_api=True,
        new_api_service_ids=[1, 2],
    )

    response = registration.asyncio.run(registration._start_single_registration_internal(request))

    assert response.status == "pending"
    assert captured["args"][-4] is True
    assert captured["args"][-3] == [1, 2]
    assert captured["args"][-2] == "child"
    assert captured["args"][-1] is True


def test_dispatch_registration_config_maps_new_api_fields_for_single(monkeypatch):
    captured = {}

    async def fake_single(request, background_tasks=None):
        captured["request"] = request
        return type("Response", (), {"task_uuid": "task-1", "model_dump": lambda self: {"task_uuid": "task-1"}})()

    monkeypatch.setattr(registration, "_start_single_registration_internal", fake_single)
    monkeypatch.setattr(registration, "_validate_registration_request", lambda _: None)

    result = registration.asyncio.run(
        registration.dispatch_registration_config(
            {
                "email_service_type": "tempmail",
                "auto_upload_new_api": True,
                "new_api_service_ids": [9],
            }
        )
    )

    assert result["kind"] == "single"
    assert captured["request"].auto_upload_new_api is True
    assert captured["request"].new_api_service_ids == [9]
    assert captured["request"].refresh_token_enabled is True


def test_dispatch_registration_config_maps_new_api_fields_for_batch(monkeypatch):
    captured = {}

    async def fake_batch(request, background_tasks=None):
        captured["request"] = request
        return type("Response", (), {"batch_id": "batch-1", "model_dump": lambda self: {"batch_id": "batch-1"}})()

    monkeypatch.setattr(registration, "_start_batch_registration_internal", fake_batch)
    monkeypatch.setattr(registration, "_validate_registration_request", lambda _: None)

    result = registration.asyncio.run(
        registration.dispatch_registration_config(
            {
                "reg_mode": "batch",
                "email_service_type": "tempmail",
                "auto_upload_new_api": True,
                "new_api_service_ids": [3, 4],
            }
        )
    )

    assert result["kind"] == "batch"
    assert captured["request"].auto_upload_new_api is True
    assert captured["request"].new_api_service_ids == [3, 4]
    assert captured["request"].refresh_token_enabled is True


def test_dispatch_registration_config_maps_refresh_token_flag_for_single(monkeypatch):
    captured = {}

    async def fake_single(request, background_tasks=None):
        captured["request"] = request
        return type("Response", (), {"task_uuid": "task-rt", "model_dump": lambda self: {"task_uuid": "task-rt"}})()

    monkeypatch.setattr(registration, "_start_single_registration_internal", fake_single)
    monkeypatch.setattr(registration, "_validate_registration_request", lambda _: None)

    registration.asyncio.run(
        registration.dispatch_registration_config(
            {
                "email_service_type": "tempmail",
                "refresh_token_enabled": False,
            }
        )
    )

    assert captured["request"].refresh_token_enabled is False


def test_dispatch_registration_config_maps_refresh_token_flag_for_batch(monkeypatch):
    captured = {}

    async def fake_batch(request, background_tasks=None):
        captured["request"] = request
        return type("Response", (), {"batch_id": "batch-rt", "model_dump": lambda self: {"batch_id": "batch-rt"}})()

    monkeypatch.setattr(registration, "_start_batch_registration_internal", fake_batch)
    monkeypatch.setattr(registration, "_validate_registration_request", lambda _: None)

    registration.asyncio.run(
        registration.dispatch_registration_config(
            {
                "reg_mode": "batch",
                "email_service_type": "tempmail",
                "refresh_token_enabled": False,
            }
        )
    )

    assert captured["request"].refresh_token_enabled is False


def test_dispatch_registration_config_maps_refresh_token_flag_for_outlook_batch(monkeypatch):
    captured = {}

    async def fake_outlook(request, background_tasks=None):
        captured["request"] = request
        return type("Response", (), {"batch_id": "batch-outlook", "model_dump": lambda self: {"batch_id": "batch-outlook"}})()

    monkeypatch.setattr(registration, "_start_outlook_batch_registration_internal", fake_outlook)

    registration.asyncio.run(
        registration.dispatch_registration_config(
            {
                "reg_mode": "outlook_batch",
                "email_service_type": "outlook_batch",
                "service_ids": [11],
                "refresh_token_enabled": False,
            }
        )
    )

    assert captured["request"].refresh_token_enabled is False


def test_get_task_logs_prefers_live_task_manager_logs(monkeypatch):
    class DummyTask:
        task_uuid = "task-logs"
        status = "running"
        logs = ""
        result = {}
        email_service = None

    class DummyDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(registration, "get_db", lambda: DummyDb())
    monkeypatch.setattr(registration.crud, "get_registration_task", lambda db, task_uuid: DummyTask())
    monkeypatch.setattr(registration.task_manager, "get_logs", lambda task_uuid: ["line-1", "line-2"])

    result = registration.get_task_logs("task-logs")

    assert result["logs"] == ["line-1", "line-2"]


def test_get_task_prefers_live_status_snapshot(monkeypatch):
    class DummyTask:
        id = 1
        task_uuid = "task-status"
        status = "pending"
        email_service_id = None
        proxy = None
        logs = ""
        result = None
        error_message = None
        created_at = None
        started_at = None
        completed_at = None

    class DummyDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(registration, "get_db", lambda: DummyDb())
    monkeypatch.setattr(registration.crud, "get_registration_task", lambda db, task_uuid: DummyTask())
    monkeypatch.setattr(registration.task_manager, "get_logs", lambda task_uuid: ["live-log"])
    monkeypatch.setattr(
        registration.task_manager,
        "get_status",
        lambda task_uuid: {"status": "failed", "error": "boom"},
    )

    result = registration.get_task("task-status")

    assert result.status == "failed"
    assert result.error_message == "boom"
    assert result.logs == "live-log"
