import json

from conftest import read_audit

from loom.routines import Routine, RoutineStore


def make_routine(routine_id="daily_stats", cron="30 8 * * *", **overrides):
    fields = {"id": routine_id, "name": "Daily stats", "cron": cron, "prompt": "count the sandbox files"}
    fields.update(overrides)
    return Routine(**fields)


def test_store_round_trip_in_tmp_path(tmp_path):
    store = RoutineStore(tmp_path)
    first = store.add(make_routine())
    second = store.add(make_routine(routine_id="daily_tidy", name="Daily tidy", cron="0 9 * * *"))

    reloaded = RoutineStore(tmp_path).list()

    assert reloaded == [first, second]
    assert reloaded[0].enabled is True
    assert reloaded[0].last_status == ""
    assert (tmp_path / "routines.json").is_file()


def test_duplicate_id_rejected(tmp_path):
    store = RoutineStore(tmp_path)
    store.add(make_routine())

    try:
        store.add(make_routine(name="Another", cron="0 12 * * *"))
    except ValueError as exc:
        assert "daily_stats" in str(exc)
    else:
        raise AssertionError("duplicate id was accepted")
    assert len(store.list()) == 1


def test_bad_cron_rejected_with_cron_string_in_message(tmp_path):
    store = RoutineStore(tmp_path)

    try:
        store.add(make_routine(cron="99 99 * * *"))
    except ValueError as exc:
        message = str(exc)
        assert "99 99 * * *" in message
        assert "cron" in message
    else:
        raise AssertionError("bad cron was accepted")

    try:
        store.add(make_routine(cron="* * * *"))
    except ValueError as exc:
        assert "* * * *" in str(exc)
    else:
        raise AssertionError("four-field cron was accepted")
    assert store.list() == []


def test_mutations_write_audit_records(tmp_path):
    store = RoutineStore(tmp_path)
    store.add(make_routine())
    store.remove("daily_stats")

    records = read_audit(tmp_path)
    assert len(records) == 2
    first = json.loads(records[0]["args"])
    assert (records[0]["actor"], records[0]["tool"], records[0]["decision"]) == ("human", "routines", "executed")
    assert first == {"action": "add", "id": "daily_stats", "cron": "30 8 * * *"}
    second = json.loads(records[1]["args"])
    assert second == {"action": "remove", "id": "daily_stats", "cron": "30 8 * * *"}


def test_set_status_persists(tmp_path):
    store = RoutineStore(tmp_path)
    store.add(make_routine())

    stamp = "2026-10-31T08:30:00+08:00"
    updated = store.set_status("daily_stats", stamp, "completed")

    assert updated.last_run_at == stamp
    assert updated.last_status == "completed"
    assert RoutineStore(tmp_path).get("daily_stats").last_status == "completed"


def test_get_and_remove_unknown_ids_raise(tmp_path):
    store = RoutineStore(tmp_path)
    store.add(make_routine())

    try:
        store.get("missing")
    except KeyError as exc:
        assert "missing" in exc.args[0]
    else:
        raise AssertionError("get on unknown id did not raise")
    try:
        store.remove("missing")
    except KeyError as exc:
        assert "missing" in exc.args[0]
    else:
        raise AssertionError("remove on unknown id did not raise")
