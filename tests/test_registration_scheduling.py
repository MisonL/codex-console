import asyncio

from src.web.scheduler import ScheduledRegistrationService
from src.web.routes import registration as registration_routes


def test_resolve_task_email_service_id_prefers_explicit_request():
    task = type("Task", (), {"email_service_id": 7})()

    assert registration_routes._resolve_task_email_service_id(task, 9) == 9


def test_resolve_task_email_service_id_falls_back_to_task_binding():
    task = type("Task", (), {"email_service_id": 7})()

    assert registration_routes._resolve_task_email_service_id(task, None) == 7


def test_coerce_bool_supports_legacy_string_flags():
    assert registration_routes._coerce_bool("false", default=True) is False
    assert registration_routes._coerce_bool("true", default=False) is True
    assert registration_routes._coerce_bool(None, default=True) is True


def test_scheduled_registration_service_polls_due_jobs_and_spawns_runner(monkeypatch):
    service = ScheduledRegistrationService()
    scheduled = []

    async def fake_run_db_call(operation, *args, **kwargs):
        del operation, args, kwargs
        return {"due_job_uuids": ["job-1"], "stale_running_job_uuids": []}

    async def fake_run_job(job_uuid):
        scheduled.append(job_uuid)

    monkeypatch.setattr("src.web.scheduler.run_db_call", fake_run_db_call)
    monkeypatch.setattr(service, "run_job", fake_run_job)

    async def exercise():
        await service.poll_due_jobs()
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert scheduled == ["job-1"]
