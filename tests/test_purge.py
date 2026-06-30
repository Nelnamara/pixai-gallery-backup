"""The destructive local-purge path — previously untested, and the scariest code in
the app (it deletes files). Covers purge_media_local (quarantine vs hard-delete) and
the gallery /delete-tasks-bulk route (cloud delete is mocked; we assert local side
effects + that the cloud call fires task-level)."""
import pixai_gallery as g
from pixai_gallery import (CATALOG_FIELDS, save_catalog, load_catalog,
                           purge_media_local, create_app)


def _row(**kw):
    return {f: "" for f in CATALOG_FIELDS} | kw


def _seed(tmp_path, rows, files):
    save_catalog(tmp_path / "catalog.db", rows)
    img = tmp_path / "images"
    img.mkdir(exist_ok=True)
    for name, data in files.items():
        (img / name).write_bytes(data)
    return tmp_path / "catalog.db"


def test_purge_quarantines_file_and_clears_row(tmp_path):
    db = _seed(tmp_path, [_row(media_id="55", filename="55.png")], {"55.png": b"DATA"})
    thumb = tmp_path / "gallery" / "thumbs"
    thumb.mkdir(parents=True)
    (thumb / "55.jpg").write_bytes(b"t")

    moved = purge_media_local(tmp_path, thumb, db, "55", "55.png")

    assert moved == tmp_path / g.DELETED_DIRNAME / "55.png"
    assert moved.exists() and moved.read_bytes() == b"DATA"     # file preserved, just moved
    assert not (tmp_path / "images" / "55.png").exists()        # gone from its old spot
    assert not (thumb / "55.jpg").exists()                      # thumb (regenerable) removed
    assert load_catalog(db) == []                               # catalog row cleared


def test_purge_hard_delete_mode(tmp_path):
    db = _seed(tmp_path, [_row(media_id="9", filename="9.png")], {"9.png": b"x"})
    moved = purge_media_local(tmp_path, tmp_path / "t", db, "9", "9.png", quarantine=False)
    assert moved is None
    assert not (tmp_path / "images" / "9.png").exists()
    assert not (tmp_path / g.DELETED_DIRNAME).exists()          # nothing quarantined
    assert load_catalog(db) == []


def test_purge_missing_file_is_safe(tmp_path):
    db = _seed(tmp_path, [_row(media_id="404", filename="404.png")], {})  # no file on disk
    moved = purge_media_local(tmp_path, tmp_path / "t", db, "404", "404.png")
    assert moved is None
    assert load_catalog(db) == []                              # row still cleared, no crash


def test_quarantined_file_is_invisible_to_resolution(tmp_path):
    # A file already sitting in _deleted/ must not be found as a live media file.
    db = _seed(tmp_path, [], {})
    qdir = tmp_path / g.DELETED_DIRNAME
    qdir.mkdir()
    (qdir / "77.png").write_bytes(b"old")
    assert g.find_image_file(tmp_path, "77", "77.png") is None
    assert g.find_files_for_media_id(tmp_path, "77") == []


def test_delete_tasks_bulk_route_quarantines_and_calls_cloud(tmp_path, monkeypatch):
    import pixai_gallery_backup as core
    db = _seed(tmp_path, [
        _row(media_id="100", task_id="T1", filename="100.png"),
        _row(media_id="101", task_id="T1", filename="101.png"),   # same task, NOT selected
        _row(media_id="200", task_id="", filename="200.png", source="local"),
    ], {"100.png": b"a", "101.png": b"b", "200.png": b"c"})

    calls = []
    monkeypatch.setattr(core, "_make_session", lambda *a, **k: object())
    monkeypatch.setattr(core, "delete_task_gql", lambda sess, tid: calls.append(tid))

    client = create_app(tmp_path).test_client()
    client.post("/delete-tasks-bulk", data={"media_ids": ["100", "200"], "back": "/"})

    assert calls == ["T1"]                                       # cloud delete fired once, task-level
    deleted = tmp_path / g.DELETED_DIRNAME
    # selecting 100 purges its WHOLE task (100 + 101); 200 is a local-only import
    for name in ("100.png", "101.png", "200.png"):
        assert (deleted / name).exists()
    assert {r["media_id"] for r in load_catalog(db)} == set()    # all three rows cleared
