"""Tests for network-layer functions with mocked requests.Session."""
import json
import pytest

import pixai_gallery_backup as core


def _make_response(mocker, status_code=200, json_body=None, text="", raises=None):
    """Build a fake requests.Response-like mock."""
    resp = mocker.MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    if raises:
        resp.raise_for_status.side_effect = raises
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# gql()
# ---------------------------------------------------------------------------

class TestGql:
    def test_returns_data_on_success(self, mock_session):
        payload = {"data": {"user": {"taskSummaries": {"edges": [], "pageInfo": {}}}}}
        mock_session.get.return_value = _make_response(
            pytest.importorskip("unittest.mock"), json_body=payload
        )
        # Re-mock properly
        mock_session.get.return_value.status_code = 200
        mock_session.get.return_value.json.return_value = payload
        result = core.gql(mock_session, {"last": 10, "userId": "u1"})
        assert result == payload["data"]

    def test_raises_on_401(self, mock_session, mocker):
        resp = _make_response(mocker, status_code=401, json_body={})
        mock_session.get.return_value = resp
        with pytest.raises(core.PixAIError, match="401"):
            core.gql(mock_session, {"last": 10, "userId": "u1"})

    def test_raises_on_non_json(self, mock_session, mocker):
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.text = "not json at all"
        resp.json.side_effect = ValueError("no json")
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        with pytest.raises(core.PixAIError, match="non-JSON"):
            core.gql(mock_session, {"last": 10, "userId": "u1"})

    def test_raises_on_graphql_errors(self, mock_session, mocker):
        payload = {"errors": [{"message": "something broke"}]}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        with pytest.raises(core.PixAIError, match="GraphQL error"):
            core.gql(mock_session, {"last": 10, "userId": "u1"})

    def test_raises_persisted_query_not_found(self, mock_session, mocker):
        payload = {"errors": [{"message": "PersistedQueryNotFound"}]}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        with pytest.raises(core.PixAIError, match="hash not recognized"):
            core.gql(mock_session, {"last": 10, "userId": "u1"})


# ---------------------------------------------------------------------------
# resolve_media()
# ---------------------------------------------------------------------------

class TestResolveMedia:
    def test_picks_public_variant(self, mock_session, mocker):
        obj = {
            "urls": [
                {"variant": "THUMBNAIL", "url": "https://thumb.example.com/t"},
                {"variant": "PUBLIC", "url": "https://cdn.example.com/full"},
            ],
            "width": 512,
            "height": 768,
            "type": "IMAGE",
        }
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = obj
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp

        url, info = core.resolve_media(mock_session, "mid123")
        assert url == "https://cdn.example.com/full"
        assert info["width"] == 512

    def test_returns_none_on_request_error(self, mock_session, mocker):
        import requests
        mock_session.get.side_effect = requests.RequestException("timeout")
        url, info = core.resolve_media(mock_session, "mid123")
        assert url is None
        assert info == {}

    def test_falls_back_when_no_public(self, mock_session, mocker):
        obj = {
            "urls": [{"variant": "THUMBNAIL", "url": "https://thumb.example.com/t"}],
            "width": 100,
            "height": 100,
            "type": "IMAGE",
        }
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = obj
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        url, info = core.resolve_media(mock_session, "mid456")
        assert url is not None

    def test_returns_none_on_empty_urls(self, mock_session, mocker):
        obj = {"urls": [], "width": None, "height": None, "type": ""}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = obj
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        url, info = core.resolve_media(mock_session, "mid789")
        assert url is None


# ---------------------------------------------------------------------------
# _quick_count() — verify it returns 0 on PixAIError without raising
# ---------------------------------------------------------------------------

class TestTaskDetailGql:
    def test_returns_task_on_success(self, mock_session, mocker):
        task = {"id": "t1", "parameters": {"prompts": "full prompt"}, "outputs": {}}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"task": task}}
        mock_session.get.return_value = resp
        # Inject hash so the function doesn't raise
        import pixai_gallery_backup as c
        orig = c.TASK_DETAIL_HASH
        c.TASK_DETAIL_HASH = "fakehash"
        try:
            result = c.task_detail_gql(mock_session, "t1")
        finally:
            c.TASK_DETAIL_HASH = orig
        assert result["id"] == "t1"

    def test_returns_none_on_non_200(self, mock_session, mocker):
        import pixai_gallery_backup as c
        resp = mocker.MagicMock()
        resp.status_code = 500
        mock_session.get.return_value = resp
        orig = c.TASK_DETAIL_HASH
        c.TASK_DETAIL_HASH = "fakehash"
        try:
            result = c.task_detail_gql(mock_session, "t1")
        finally:
            c.TASK_DETAIL_HASH = orig
        assert result is None

    def test_raises_when_hash_missing(self, mock_session):
        import pixai_gallery_backup as c
        orig = c.TASK_DETAIL_HASH
        c.TASK_DETAIL_HASH = ""
        try:
            with pytest.raises(c.PixAIError, match="TASK_DETAIL_HASH"):
                c.task_detail_gql(mock_session, "t1")
        finally:
            c.TASK_DETAIL_HASH = orig


class TestModelNameGql:
    def test_returns_model_title_and_version(self, mock_session, mocker):
        import pixai_gallery_backup as c
        mv = {"name": "v1", "model": {"title": "Tsubaki.2"}}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"data": {"generationModelVersion": mv}}
        mock_session.get.return_value = resp
        orig_hash = c.MODEL_DETAIL_HASH
        c.MODEL_DETAIL_HASH = "fakehash"
        # Clear module-level cache before test
        c.model_name_gql.__defaults__  # ensure it's the right function
        # Use a fresh call with a unique ID not in cache
        try:
            result = c.model_name_gql(mock_session, "unique_model_id_test_123")
        finally:
            c.MODEL_DETAIL_HASH = orig_hash
        assert result == "Tsubaki.2 v1"

    def test_returns_empty_for_empty_id(self, mock_session):
        import pixai_gallery_backup as c
        assert c.model_name_gql(mock_session, "") == ""
        assert c.model_name_gql(mock_session, None) == ""


class TestQuickCount:
    def test_returns_zero_on_api_error(self, mock_session, mocker):
        payload = {"errors": [{"message": "INTERNAL_SERVER_ERROR"}]}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        result = core._quick_count(mock_session, page_size=10)
        assert result == 0

    def test_counts_single_page(self, mock_session, mocker):
        conn_data = {
            "edges": [
                {"node": {"mediaId": "m1", "batchMediaIds": None}},
                {"node": {"mediaId": "m2", "batchMediaIds": ["m2", "m3"]}},
            ],
            "pageInfo": {"hasPreviousPage": False, "startCursor": None},
        }
        payload = {"data": {"user": {"taskSummaries": conn_data}}}
        resp = mocker.MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        mock_session.get.return_value = resp
        # edge 1 → 1 id, edge 2 → 2 ids  (m2 deduped + m3)
        result = core._quick_count(mock_session, page_size=10)
        assert result == 3


# ---------------------------------------------------------------------------
# run_download: resume index + --update early-stop (perf-critical paths)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _dl_args(out, **kw):
    base = dict(
        out=str(out), token="t", page_size=20, max=0, delay=0,
        name_length=40, name_sep="_", organize_live=False, organize_adv_live=False,
        convert=None, jpeg_quality=92, jpeg_bg="white", keep_webp=False,
        collect_only=False, full_meta=False, update=False, update_grace=2,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _page(mid, has_prev, cursor=""):
    node = {"id": "task_" + mid, "mediaId": mid, "batchMediaIds": [],
            "createdAt": "2024-01-01T00:00:00", "promptsPreview": "p", "status": "ok"}
    return {"user": {"taskSummaries": {
        "edges": [{"node": node}],
        "pageInfo": {"hasPreviousPage": has_prev, "startCursor": cursor}}}}


def _patch_download_layer(mocker):
    mocker.patch.object(core, "_make_session", return_value=mocker.MagicMock())
    mocker.patch.object(core, "_quick_count", return_value=3)
    mocker.patch.object(core, "resolve_media",
                        return_value=("http://x/img", {"width": "1", "height": "1"}))

    def fake_download(session, url, stem, **kw):
        dest = stem.with_suffix(".webp")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"img")
        return ("ok", dest)
    return mocker.patch.object(core, "download", side_effect=fake_download)


def test_resume_skips_on_disk_without_redownload(tmp_path, mocker):
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "images" / "x_known.webp").write_bytes(b"img")
    dl = _patch_download_layer(mocker)
    mocker.patch.object(core, "gql", side_effect=[_page("new1", True, "c1"),
                                                  _page("known", False)])

    core.run_download(_dl_args(tmp_path))

    names = [str(c.args[2]) for c in dl.call_args_list]  # stem is 3rd positional
    assert any("new1" in n for n in names)
    assert not any("known" in n for n in names)


def test_update_mode_stops_early(tmp_path, mocker):
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "images" / "x_old.webp").write_bytes(b"img")
    _patch_download_layer(mocker)
    gql = mocker.patch.object(core, "gql", side_effect=[
        _page("fresh", True, "c1"), _page("old", True, "c2"),
        _page("should_not_fetch", False)])

    core.run_download(_dl_args(tmp_path, update=True, update_grace=1))

    assert gql.call_count == 2  # page3 never requested


def test_populated_catalog_skips_network_count(tmp_path, mocker):
    # With a populated catalog, the progress total comes from the catalog size --
    # no full-history _quick_count network walk.
    from pixai_gallery import save_catalog, CATALOG_FIELDS
    db = tmp_path / "catalog.db"
    save_catalog(db, [{f: "" for f in CATALOG_FIELDS} |
                      {"media_id": "old", "filename": "x_old.webp"}])
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "images" / "x_old.webp").write_bytes(b"img")
    _patch_download_layer(mocker)
    qc = mocker.patch.object(core, "_quick_count", return_value=999)
    mocker.patch.object(core, "gql", side_effect=[_page("old", False)])

    core.run_download(_dl_args(tmp_path, update=True, update_grace=1))

    assert qc.call_count == 0  # catalog estimate used, no network pre-count


def test_parallel_workers_download_new_skip_known(tmp_path, mocker):
    # workers>1 path: new items fetched concurrently, on-disk items skipped.
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "images" / "x_known.webp").write_bytes(b"img")
    dl = _patch_download_layer(mocker)

    def _multi_page(mids, has_prev, cursor=""):
        edges = [{"node": {"id": "t_" + m, "mediaId": m, "batchMediaIds": [],
                           "createdAt": "2024-01-01", "promptsPreview": "p", "status": "ok"}}
                 for m in mids]
        return {"user": {"taskSummaries": {
            "edges": edges, "pageInfo": {"hasPreviousPage": has_prev, "startCursor": cursor}}}}

    mocker.patch.object(core, "gql", side_effect=[_multi_page(["a", "b", "known"], False)])
    core.run_download(_dl_args(tmp_path, workers=4))

    names = [str(c.args[2]) for c in dl.call_args_list]
    assert sum("known" in n for n in names) == 0          # on-disk skipped
    assert any("_a" in n for n in names) and any("_b" in n for n in names)  # both new fetched


def test_update_and_workers_compose(tmp_path, mocker):
    # --update (early-stop) + --workers (parallel) together: new items fetched
    # concurrently at the top, then stop once a page is fully on disk.
    (tmp_path / "images").mkdir(parents=True)
    (tmp_path / "images" / "x_old.webp").write_bytes(b"img")
    dl = _patch_download_layer(mocker)

    def _mp(mids, has_prev, c=""):
        edges = [{"node": {"id": "t_" + m, "mediaId": m, "batchMediaIds": [],
                           "createdAt": "2024-01-01", "promptsPreview": "p", "status": "ok"}}
                 for m in mids]
        return {"user": {"taskSummaries": {
            "edges": edges, "pageInfo": {"hasPreviousPage": has_prev, "startCursor": c}}}}

    gql = mocker.patch.object(core, "gql", side_effect=[
        _mp(["new1", "new2"], True, "c1"), _mp(["old"], True, "c2"),
        _mp(["should_not_fetch"], False)])

    core.run_download(_dl_args(tmp_path, update=True, update_grace=1, workers=4))

    names = [str(c.args[2]) for c in dl.call_args_list]
    assert gql.call_count == 2                                   # stopped before page 3
    assert any("new1" in n for n in names) and any("new2" in n for n in names)
    assert not any("old" in n for n in names)                   # on-disk skipped


def test_extract_artwork_meta():
    node = {"id": "aw1", "mediaId": "m1", "title": "Lollipop Elf",
            "visibility": "PUBLIC", "isNsfw": True, "likedCount": 5,
            "commentCount": 2, "aesScore": 7.5,
            "tacks": [{"codeName": "contest_x", "displayName": "ContestX"},
                      {"displayName": "tag2"}]}
    m = core.extract_artwork_meta(node)
    assert m["media_id"] == "m1" and m["artwork_id"] == "aw1"
    assert m["title"] == "Lollipop Elf"
    assert m["is_published"] == "1" and m["is_nsfw"] == "1"
    assert m["liked_count"] == "5" and m["comment_count"] == "2"
    assert m["art_tags"] == "ContestX, tag2"


def test_sync_artworks_merges_by_media_id(tmp_path, mocker):
    from pixai_gallery import save_catalog, CATALOG_FIELDS, load_catalog
    db = tmp_path / "catalog.db"
    save_catalog(db, [{f: "" for f in CATALOG_FIELDS} |
                      {"media_id": "m1", "filename": "x_m1.png"}])
    mocker.patch.object(core, "USER_ID", "u1")
    mocker.patch.object(core, "_make_session", return_value=mocker.MagicMock())
    conn = {"edges": [{"node": {"id": "aw1", "mediaId": "m1", "title": "My Art",
                                "visibility": "PUBLIC", "isNsfw": False,
                                "likedCount": 3, "commentCount": 1,
                                "aesScore": 6.0, "tacks": []}}],
            "pageInfo": {"hasPreviousPage": False}}
    mocker.patch.object(core, "artwork_list_gql", return_value=conn)

    res = core.run_sync_artworks(SimpleNamespace(out=str(tmp_path), token=None, delay=0))

    assert res == {"artworks": 1, "matched": 1}
    row = {r["media_id"]: r for r in load_catalog(db)}["m1"]
    assert row["title"] == "My Art" and row["liked_count"] == "3"
    assert row["is_published"] == "1" and row["artwork_id"] == "aw1"


def test_resolve_loras(mocker):
    mocker.patch.object(core, "model_name_gql",
                        side_effect=lambda s, vid: {"111": "DetailLora", "222": "222"}.get(str(vid), str(vid)))
    task = {"parameters": {"lora": {"111": 0.7, "222": 0.5}}}
    out = core.resolve_loras(mocker.MagicMock(), task)
    assert "DetailLora:0.7" in out
    assert "lora 222:0.5" in out          # unresolved id gets a "lora <id>" label
    assert core.resolve_loras(mocker.MagicMock(), {"parameters": {}}) == ""


def test_needs_model_fix():
    # numeric model_name with matching id -> needs fixing
    assert core._needs_model_fix({"model_id": "123", "model_name": "123"}) == "123"
    # blank name but has id -> needs fixing
    assert core._needs_model_fix({"model_id": "456", "model_name": ""}) == "456"
    # model_name itself is the numeric id, no model_id column -> use it
    assert core._needs_model_fix({"model_id": "", "model_name": "789"}) == "789"
    # already readable -> no fix
    assert core._needs_model_fix({"model_id": "123", "model_name": "Tsubaki v1"}) == ""
    # nothing to go on -> no fix
    assert core._needs_model_fix({"model_id": "", "model_name": ""}) == ""


def test_fix_models_resolves_numeric_names(tmp_path, mocker):
    from pixai_gallery import save_catalog, CATALOG_FIELDS, load_catalog
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        {f: "" for f in CATALOG_FIELDS} | {"media_id": "m1", "filename": "a.png",
                                           "model_id": "999", "model_name": "999"},
        {f: "" for f in CATALOG_FIELDS} | {"media_id": "m2", "filename": "b.png",
                                           "model_id": "999", "model_name": "999"},
        {f: "" for f in CATALOG_FIELDS} | {"media_id": "m3", "filename": "c.png",
                                           "model_id": "111", "model_name": "Already Named"},
    ])
    mocker.patch.object(core, "_make_session", return_value=mocker.MagicMock())
    mocker.patch.object(core, "model_name_gql", return_value="Tsubaki.2 v1")

    res = core.run_fix_models(SimpleNamespace(out=str(tmp_path), token=None, delay=0))

    assert res["fixed"] == 2          # both m1/m2 (model 999) fixed
    rows = {r["media_id"]: r for r in load_catalog(db)}
    assert rows["m1"]["model_name"] == "Tsubaki.2 v1"
    assert rows["m3"]["model_name"] == "Already Named"   # untouched


def test_progress_counter_does_not_double_count(tmp_path, mocker):
    # Regression: the progress counter must NOT be seeded with the on-disk count
    # (that double-counted already-downloaded items and overshot 100%).
    from pixai_gallery import save_catalog, CATALOG_FIELDS
    db = tmp_path / "catalog.db"
    save_catalog(db, [{f: "" for f in CATALOG_FIELDS} |
                      {"media_id": m, "filename": "x_%s.webp" % m} for m in ("a", "b")])
    (tmp_path / "images").mkdir(parents=True)
    for m in ("a", "b"):
        (tmp_path / "images" / ("x_%s.webp" % m)).write_bytes(b"img")
    _patch_download_layer(mocker)
    edges = [{"node": {"id": "t_" + m, "mediaId": m, "batchMediaIds": [],
                       "createdAt": "2024-01-01", "promptsPreview": "p", "status": "ok"}}
             for m in ("a", "b")]
    mocker.patch.object(core, "gql", side_effect=[
        {"user": {"taskSummaries": {"edges": edges,
                                    "pageInfo": {"hasPreviousPage": False}}}}])

    seen = []
    core.run_download(_dl_args(tmp_path), progress=lambda d, t, n: seen.append((d, t)))

    max_done = max(d for d, t in seen)
    total = seen[-1][1]
    assert max_done <= total          # never overshoots the denominator
    assert max_done == 2              # two items walked, counted once each
