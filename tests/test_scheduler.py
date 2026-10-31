"""Accelerated schedule-trigger evidence: APScheduler fires run_routine through the real cron path."""

import json
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from conftest import ScriptedChatModel, read_audit
from langchain_core.messages import AIMessage

import loom.runtime
from loom.config import Settings
from loom.pending import PendingStore
from loom.routines import Routine, RoutineStore
from loom.runner import run_routine


def test_cron_trigger_parses_routine_cron_and_computes_next_fire():
    trigger = CronTrigger.from_crontab("30 8 * * *")
    now = datetime.now(tz=trigger.timezone)

    fire = trigger.get_next_fire_time(None, now)

    assert fire is not None
    assert now < fire <= now + timedelta(days=1)


def test_scheduled_job_fires_run_routine_once(monkeypatch, tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    store = RoutineStore(tmp_path)
    pending = PendingStore(tmp_path)
    routine = store.add(Routine(id="smoke_stats", name="Smoke", cron="* * * * *", prompt="count the files"))
    monkeypatch.setattr(
        loom.runtime, "make_model", lambda s: ScriptedChatModel(messages=iter([AIMessage(content="10 files")]))
    )

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_routine,
        trigger=CronTrigger.from_crontab(routine.cron),
        args=[routine, settings, store, pending],
        id=routine.id,
        next_run_time=datetime.now(tz=trigger_timezone(routine.cron)),
    )
    scheduler.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.get("smoke_stats").last_status == "completed":
                break
            time.sleep(0.2)
        else:
            raise AssertionError("scheduled job did not fire run_routine within 10s")
    finally:
        scheduler.shutdown(wait=False)

    triggered = [record for record in read_audit(tmp_path) if record["tool"] == "routine_trigger"]
    assert len(triggered) == 1
    assert triggered[0]["decision"] == "triggered"
    thread_id = json.loads(triggered[0]["args"])["thread_id"]
    assert thread_id.startswith("routine:smoke_stats:")
    assert pending.list_pending() == []


def trigger_timezone(cron: str):
    return CronTrigger.from_crontab(cron).timezone
