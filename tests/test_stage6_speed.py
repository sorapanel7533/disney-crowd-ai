from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from threading import Event

import pandas as pd
import pytest

import persistence
import reliability as r
import runtime_data
import utils


def test_conditional_snapshot_updates_and_keeps_last_good_database(tmp_path, monkeypatch):
    monkeypatch.delenv("DISNEY_DISABLE_REMOTE_SYNC", raising=False)
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path / "source"))
    name = "disneyland.db"
    source, _ = utils.connect_db(name)
    with closing(source):
        with source:
            source.execute("INSERT INTO wait_times(datetime, attraction, wait_time, park, observation_key) VALUES (?,?,?,?,?)",
                           ("2026-11-16 10:15:00", "Big Thunder Mountain", 35, "Disneyland", "new"))
    raw = (tmp_path / "source" / name).read_bytes()
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path / "local"))
    db, _ = utils.connect_db(name)

    class Response:
        headers = {"ETag": 'W/"version-one"'}
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            if self.status_code != 200:
                raise AssertionError("304 must not download or merge")
            yield self.payload

    response = Response()
    response.payload = raw
    requests = []

    def get(_url, **kwargs):
        requests.append(kwargs["headers"])
        return response

    monkeypatch.setattr(persistence.requests, "get", get)
    with closing(db):
        def keys():
            return db.execute("SELECT observation_key FROM wait_times WHERE observation_key IS NOT NULL ORDER BY observation_key").fetchall()

        persistence.sync_public_snapshot(db, name, tmp_path / "local", ttl=0)
        after = keys()
        assert ("new",) in after
        response.status_code = 304
        assert "更新されていません" in persistence.sync_public_snapshot(db, name, tmp_path / "local", ttl=0)
        assert requests == [{}, {"If-None-Match": 'W/"version-one"'}]
        response.status_code = 200
        response.payload = b"this is not a database"
        assert "同期失敗" in persistence.sync_public_snapshot(db, name, tmp_path / "local", ttl=0)
        assert keys() == after
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert persistence.snapshot_status(name, tmp_path / "local")["last_success"]


def test_background_sync_is_single_flight_and_does_not_block_reader(tmp_path, monkeypatch):
    monkeypatch.delenv("DISNEY_DISABLE_REMOTE_SYNC", raising=False)
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    name = "disneyland.db"
    db, _ = runtime_data.open_database(name)
    entered, release = Event(), Event()
    calls = []

    def blocked_refresh(*args):
        calls.append(args)
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("test did not release sync")

    monkeypatch.setattr(runtime_data, "_refresh", blocked_refresh)
    try:
        assert runtime_data.refresh_status(name)[1:]
        assert entered.wait(timeout=5)
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(state[1] for state in pool.map(runtime_data.refresh_status, [name] * 8))
        assert len(calls) == 1
        assert db.execute("SELECT COUNT(*) FROM wait_times").fetchone()
    finally:
        release.set()
        runtime_data._SYNCS[runtime_data.path_for(name)].result(timeout=5)
        db.close()


def test_scoped_history_cache_separates_parks_and_updates_after_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    name = "disneyland.db"
    db, _ = runtime_data.open_database(name)
    path = str(runtime_data.path_for(name))
    before = runtime_data.db_revision(path)
    assert runtime_data.ride_history(path, before, "Disneyland", "new ride").empty
    with closing(db), db:
        db.execute("INSERT INTO wait_times(datetime, attraction, wait_time, park) VALUES (?,?,?,?)",
                   ("2026-11-16 10:15:00", "new ride", 35, "Disneyland"))
    after = runtime_data.db_revision(path)
    assert after != before
    assert runtime_data.ride_history(path, after, "Disneyland", "new ride").wait_time.tolist() == [35]
    assert runtime_data.ride_history(path, after, "DisneySea", "new ride").empty


@pytest.mark.parametrize("park", ["Disneyland", "DisneySea"])
def test_single_ride_preserves_fallback_and_all_weather_forecast_values(tmp_path, monkeypatch, park):
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    settings = dict(utils.PARK_SETTINGS[park], park=park)
    name = settings["db"]
    db, _ = utils.connect_db(name)
    with closing(db):
        history = utils.load_history(db)
        errors = utils.load_prediction_history(db)
    target = r.utc().tz_convert(r.JST).date() + timedelta(days=1)
    names = [settings["rides"][0], "An attraction without its own history"]
    exact = utils.predict_wait_times_for_date(history, settings, target, 4, 2, errors, attraction_list=names)
    assert len(exact) == 96
    prepared = utils.prepare_forecast_model(history, park)
    for name in names:
        selected = utils.predict_wait_times_for_date(
            history, settings, target, 4, 2, errors, attraction_list=[name], prepared_model=prepared,
        )
        pd.testing.assert_frame_equal(selected.reset_index(drop=True), exact[exact.Attraction.eq(name)].reset_index(drop=True))



def test_measured_view_cache_is_invalidated_after_verified_write():
    from measured import records
    from test_measured import RemoteClient, SharedRemote, row

    service = SharedRemote()
    reader, writer = RemoteClient(service), RemoteClient(service)
    before, _, _ = reader.load_for_view()
    with closing(before):
        assert not records(before)
        writer.submit(row())
        after, _, _ = reader.load_for_view()
        with closing(after):
            assert after is not before
            assert len(records(after)) == 1
            assert not records(before)


def test_week_summary_reads_fixed_index_without_recomputing_locked_days(tmp_path, monkeypatch):
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    settings = dict(utils.PARK_SETTINGS["Disneyland"], park="Disneyland")
    day = r.utc().tz_convert(r.JST).date()
    db, _ = utils.connect_db(settings["db"])
    with closing(db):
        for i in range(7):
            assert r.save_daily(db, day + timedelta(days=i), 4 + i / 10)
        def calculate_again(*args, **kwargs):
            pytest.fail("A fixed index does not require the full forecast")
        monkeypatch.setattr(utils, "predict_wait_times_for_date", calculate_again)
        result = utils.make_locked_week_forecast(
            pd.DataFrame(), settings, day, 20, 0, pd.DataFrame(), pd.DataFrame(),
            {}, conn=db, summary_only=True,
        )
        assert list(result["Crowd Index"]) == [4 + i / 10 for i in range(7)]



def test_model_cache_does_not_serialize_the_application_class():
    import pickle
    import forecast_cache

    empty = pd.DataFrame()
    prepared = forecast_cache._model("empty-for-model-test", "Disneyland", "test", r.MODEL_VERSION, empty)
    assert isinstance(prepared, tuple) and pickle.loads(pickle.dumps(prepared))[1] == 0
    assert forecast_cache.model("empty-for-model-test", "Disneyland", empty).row_count == 0
