"""Tests for filesystem-dependent functions (already_downloaded, catalog, load_token)."""
import csv
import json
import os
from pathlib import Path

import pytest

import pixai_gallery_backup as core


# ---------------------------------------------------------------------------
# already_downloaded
# ---------------------------------------------------------------------------

def test_already_downloaded_finds_flat(tmp_path):
    (tmp_path / "cool_prompt_task1_mid999.png").write_bytes(b"fakeimage")
    result = core.already_downloaded(tmp_path, "mid999")
    assert result is not None
    assert result.name.endswith("mid999.png")


def test_already_downloaded_finds_in_subfolder(tmp_path):
    sub = tmp_path / "batches" / "my_batch"
    sub.mkdir(parents=True)
    (sub / "01_mid888.webp").write_bytes(b"fakeimage")
    result = core.already_downloaded(tmp_path, "mid888")
    assert result is not None


def test_already_downloaded_returns_none_when_missing(tmp_path):
    assert core.already_downloaded(tmp_path, "nonexistent") is None


def test_already_downloaded_ignores_part_files(tmp_path):
    (tmp_path / "task_mid777.png.part").write_bytes(b"partial")
    assert core.already_downloaded(tmp_path, "mid777") is None


def test_already_downloaded_ignores_zero_byte(tmp_path):
    (tmp_path / "task_mid666.png").write_bytes(b"")
    assert core.already_downloaded(tmp_path, "mid666") is None


def test_already_downloaded_multiple_exts_returns_first(tmp_path):
    (tmp_path / "task_mid555.jpg").write_bytes(b"img")
    result = core.already_downloaded(tmp_path, "mid555")
    assert result is not None


# ---------------------------------------------------------------------------
# load_token
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_creds(tmp_path, monkeypatch):
    """Neutralize any real config.json / env so load_token's fallback chain is
    tested in isolation. Without this, a real PIXAI_API_KEY in the developer's
    config.json (loaded into core._cfg at import) short-circuits every fallback
    and these tests fail on machines that have a live config."""
    monkeypatch.setattr(core, "_cfg", {})
    monkeypatch.setattr(core, "__file__", str(tmp_path / "pixai_gallery_backup.py"))
    monkeypatch.chdir(tmp_path)            # no config.json / token.txt in CWD
    monkeypatch.delenv("PIXAI_API_KEY", raising=False)
    monkeypatch.delenv("PIXAI_TOKEN", raising=False)
    return tmp_path


def test_load_token_from_cli(isolated_creds):
    assert core.load_token("mytoken") == "mytoken"


def test_load_token_from_env(isolated_creds, monkeypatch):
    monkeypatch.setenv("PIXAI_TOKEN", "envtoken")
    assert core.load_token() == "envtoken"


def test_load_token_from_file(isolated_creds):
    (isolated_creds / "token.txt").write_text("filetoken\n", encoding="utf-8")
    assert core.load_token() == "filetoken"


def test_load_token_strips_whitespace(isolated_creds, monkeypatch):
    monkeypatch.setenv("PIXAI_TOKEN", "  tok  ")
    assert core.load_token() == "tok"


def test_load_token_raises_when_none(isolated_creds):
    with pytest.raises(core.PixAIError, match="No credential"):
        core.load_token()


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------

def test_load_config_reads_file(tmp_path):
    cfg = {"USER_ID": "u1", "U3T": "t1", "PERSISTED_QUERY_HASH": "h1"}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypath_module_file = str(tmp_path / "pixai_gallery_backup.py")
    # Temporarily redirect module __file__ and reload config
    orig = core.__file__
    try:
        core.__file__ = monkeypath_module_file
        result = core._load_config()
    finally:
        core.__file__ = orig
    assert result["USER_ID"] == "u1"
    assert result["U3T"] == "t1"


def test_load_config_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "__file__", str(tmp_path / "pixai_gallery_backup.py"))
    monkeypatch.chdir(tmp_path)  # prevent CWD fallback from finding a real config.json
    result = core._load_config()
    assert result == {}


# ---------------------------------------------------------------------------
# SQLite catalog helpers
# ---------------------------------------------------------------------------

from pixai_gallery import (CATALOG_FIELDS, init_db, save_catalog, load_catalog,
                            update_rating, delete_from_catalog,
                            update_prompt_full, bulk_replace_prompt,
                            migrate_csv_to_db, export_csv, _db_is_empty)


def _make_row(**kwargs):
    """Return a full catalog row dict with blank defaults for unset fields."""
    return {f: "" for f in CATALOG_FIELDS} | kwargs


def test_db_is_empty_missing_file(tmp_path):
    assert _db_is_empty(tmp_path / "nonexistent.db") is True


def test_db_is_empty_fresh_init(tmp_path):
    db = tmp_path / "catalog.db"
    init_db(db)
    assert _db_is_empty(db) is True


def test_db_is_empty_after_rows_saved(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m1")])
    assert _db_is_empty(db) is False


def test_init_db_creates_table(tmp_path):
    db = tmp_path / "catalog.db"
    init_db(db)
    assert db.exists()
    import sqlite3
    con = sqlite3.connect(str(db))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    assert "catalog" in tables


def test_save_and_load_roundtrip(tmp_path):
    db = tmp_path / "catalog.db"
    rows = [
        _make_row(media_id="m1", task_id="t1", filename="a.png", prompt_preview="cat"),
        _make_row(media_id="m2", task_id="t2", filename="b.png", prompt_preview="dog"),
    ]
    save_catalog(db, rows)
    loaded = load_catalog(db)
    assert len(loaded) == 2
    by_id = {r["media_id"]: r for r in loaded}
    assert by_id["m1"]["prompt_preview"] == "cat"
    assert by_id["m2"]["filename"] == "b.png"


def test_save_catalog_upserts_not_duplicates(tmp_path):
    """Re-saving the same media_id updates the row, never inserts a duplicate."""
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m1", filename="old.png")])
    save_catalog(db, [_make_row(media_id="m1", filename="new.png")])
    loaded = load_catalog(db)
    assert len(loaded) == 1
    assert loaded[0]["filename"] == "new.png"


def test_save_catalog_preserves_prior_session_rows(tmp_path):
    """Rows from a previous session that aren't in the current batch are kept."""
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m_old", filename="old.png")])
    save_catalog(db, [_make_row(media_id="m_new", filename="new.png")])
    loaded = load_catalog(db)
    ids = {r["media_id"] for r in loaded}
    assert "m_old" in ids
    assert "m_new" in ids


def test_update_rating_changes_one_row(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _make_row(media_id="m1", rating=""),
        _make_row(media_id="m2", rating="2"),
    ])
    update_rating(db, "m1", 5)
    by_id = {r["media_id"]: r for r in load_catalog(db)}
    assert by_id["m1"]["rating"] == "5"
    assert by_id["m2"]["rating"] == "2"  # untouched


def test_update_rating_clear_to_zero(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m1", rating="4")])
    update_rating(db, "m1", 0)
    loaded = load_catalog(db)
    assert loaded[0]["rating"] == ""


def test_delete_from_catalog_removes_row(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _make_row(media_id="m1"),
        _make_row(media_id="m2"),
    ])
    delete_from_catalog(db, "m1")
    loaded = load_catalog(db)
    assert len(loaded) == 1
    assert loaded[0]["media_id"] == "m2"


def test_delete_nonexistent_is_safe(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m1")])
    delete_from_catalog(db, "does_not_exist")
    assert len(load_catalog(db)) == 1


def test_update_prompt_full_edits_one_row(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _make_row(media_id="m1", prompt_full="old prompt"),
        _make_row(media_id="m2", prompt_full="keep me"),
    ])
    update_prompt_full(db, "m1", "brand new prompt")
    by_id = {r["media_id"]: r for r in load_catalog(db)}
    assert by_id["m1"]["prompt_full"] == "brand new prompt"
    assert by_id["m2"]["prompt_full"] == "keep me"  # untouched


def test_bulk_replace_prompt_counts_only_changed(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _make_row(media_id="m1", prompt_full="red cat"),
        _make_row(media_id="m2", prompt_full="red dog"),
        _make_row(media_id="m3", prompt_full="blue bird"),  # no match -> unchanged
    ])
    n = bulk_replace_prompt(db, ["m1", "m2", "m3"], "red", "green")
    assert n == 2
    by_id = {r["media_id"]: r for r in load_catalog(db)}
    assert by_id["m1"]["prompt_full"] == "green cat"
    assert by_id["m2"]["prompt_full"] == "green dog"
    assert by_id["m3"]["prompt_full"] == "blue bird"


def test_bulk_replace_prompt_empty_find_is_noop(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [_make_row(media_id="m1", prompt_full="x")])
    assert bulk_replace_prompt(db, ["m1"], "", "y") == 0


def test_migrate_csv_to_db(tmp_path):
    csv_path = tmp_path / "catalog.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        w.writerow(_make_row(media_id="m1", filename="img.png", rating="3"))
    db = tmp_path / "catalog.db"
    n = migrate_csv_to_db(csv_path, db)
    assert n == 1
    loaded = load_catalog(db)
    assert loaded[0]["media_id"] == "m1"
    assert loaded[0]["rating"] == "3"


def test_migrate_csv_to_db_is_idempotent(tmp_path):
    """Running migration twice must not duplicate rows."""
    csv_path = tmp_path / "catalog.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        w.writerow(_make_row(media_id="m1", filename="img.png"))
    db = tmp_path / "catalog.db"
    migrate_csv_to_db(csv_path, db)
    migrate_csv_to_db(csv_path, db)
    assert len(load_catalog(db)) == 1


def test_migrate_csv_missing_file_returns_zero(tmp_path):
    db = tmp_path / "catalog.db"
    n = migrate_csv_to_db(tmp_path / "nonexistent.csv", db)
    assert n == 0


def test_export_csv_roundtrip(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _make_row(media_id="m1", filename="a.png", rating="5"),
        _make_row(media_id="m2", filename="b.png", rating=""),
    ])
    csv_out = tmp_path / "export.csv"
    export_csv(db, csv_out)
    assert csv_out.exists()
    with open(csv_out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    by_id = {r["media_id"]: r for r in rows}
    assert by_id["m1"]["rating"] == "5"
    assert set(rows[0].keys()) == set(CATALOG_FIELDS)
