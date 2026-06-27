"""Tests for gallery search/filter logic: wildcard search, multi-word AND,
year dropdowns, and per-page (via query_catalog)."""
import pytest

from pixai_gallery import (CATALOG_FIELDS, init_db, save_catalog, query_catalog,
                           catalog_years, _like_pattern, collection_health)


def _row(**kw):
    return {f: "" for f in CATALOG_FIELDS} | kw


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "catalog.db"
    save_catalog(p, [
        _row(media_id="1", filename="a_1.png", prompt_preview="night elf druid",
             created_at="2024-03-10T00:00:00", model_name="ModelA"),
        _row(media_id="2", filename="b_2.png", prompt_preview="nighttime city",
             created_at="2025-07-01T00:00:00", model_name="ModelB"),
        _row(media_id="3", filename="c_3.png", prompt_preview="bright morning",
             created_at="2026-01-05T00:00:00", model_name="ModelA"),
    ])
    return p


# ---- _like_pattern ---------------------------------------------------------

def test_like_plain_word_is_substring():
    assert _like_pattern("elf") == "%elf%"


def test_like_star_becomes_percent():
    assert _like_pattern("night*") == "night%"


def test_like_question_becomes_underscore():
    assert _like_pattern("a?c") == "a_c"


def test_like_escapes_literal_percent():
    # a literal % the user types must be escaped, not treated as wildcard
    assert _like_pattern("50%") == "%50\\%%"


# ---- search behavior -------------------------------------------------------

def test_substring_search_matches_both_night(db):
    rows, total = query_catalog(db, q="night")
    assert total == 2  # "night elf" and "nighttime"


def test_wildcard_prefix_search(db):
    rows, total = query_catalog(db, q="night*")
    assert total == 2


def test_multiword_is_anded(db):
    rows, total = query_catalog(db, q="night druid")
    assert total == 1  # only "night elf druid" has both


def test_multiword_no_match(db):
    rows, total = query_catalog(db, q="night morning")
    assert total == 0


# ---- date range (YYYY-MM comparison) --------------------------------------

def test_date_from_filters(db):
    rows, total = query_catalog(db, date_from="2025-01")
    assert total == 2  # 2025 and 2026


def test_date_range_inclusive(db):
    rows, total = query_catalog(db, date_from="2024-01", date_to="2024-12")
    assert total == 1


def test_catalog_years_descending(db):
    assert catalog_years(db) == [2026, 2025, 2024]


# ---- per-page (page_size) --------------------------------------------------

def test_page_size_limits_rows(db):
    rows, total = query_catalog(db, page_size=2, page=1)
    assert len(rows) == 2 and total == 3


def test_page_size_second_page(db):
    rows, total = query_catalog(db, page_size=2, page=2)
    assert len(rows) == 1 and total == 3


# ---- rating filter + new sorts --------------------------------------------

def test_rating_min_filters(tmp_path):
    p = tmp_path / "catalog.db"
    save_catalog(p, [
        _row(media_id="1", filename="a_1.png", rating="5", created_at="2024-01-01"),
        _row(media_id="2", filename="b_2.png", rating="2", created_at="2024-01-02"),
        _row(media_id="3", filename="c_3.png", rating="",  created_at="2024-01-03"),
    ])
    assert query_catalog(p, rating_min=3)[1] == 1
    assert query_catalog(p, rating_min=1)[1] == 2
    assert query_catalog(p, rating_min=0)[1] == 3


def test_sort_pixels_orders_by_area(tmp_path):
    p = tmp_path / "catalog.db"
    save_catalog(p, [
        _row(media_id="small", filename="s_small.png", width="100", height="100"),
        _row(media_id="big",   filename="b_big.png",   width="800", height="800"),
    ])
    rows, _ = query_catalog(p, sort="pixels")
    assert rows[0]["media_id"] == "big"


def test_sort_aesthetic_and_likes(tmp_path):
    p = tmp_path / "catalog.db"
    save_catalog(p, [
        _row(media_id="lo", filename="lo.png", aes_score="3.2", liked_count="1"),
        _row(media_id="hi", filename="hi.png", aes_score="8.9", liked_count="50"),
    ])
    assert query_catalog(p, sort="aes_desc")[0][0]["media_id"] == "hi"
    assert query_catalog(p, sort="aes_asc")[0][0]["media_id"] == "lo"
    assert query_catalog(p, sort="likes")[0][0]["media_id"] == "hi"


# ---- collection_health -----------------------------------------------------

def test_collection_health_counts_and_missing(tmp_path):
    db = tmp_path / "catalog.db"
    # one row whose file exists, one whose file is missing on disk
    save_catalog(db, [
        _row(media_id="111", filename="111.webp", prompt_full="a full prompt",
             created_at="2024-03-01", model_name="ModelA", rating="4"),
        _row(media_id="222", filename="b_222.webp", created_at="2024-03-02",
             model_name="ModelA"),
    ])
    (tmp_path / "2024-03").mkdir()
    (tmp_path / "2024-03" / "111.webp").write_bytes(b"data")
    h = collection_health(tmp_path, db)
    assert h["total_files"] == 1
    assert h["catalog_rows"] == 2
    assert h["with_full_meta"] == 1
    assert h["rated"] == 1
    assert h["missing"] == 1          # row 222 has no file on disk
    assert h["per_bucket"].get("month") == 1


def test_published_and_tag_filters(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="1", filename="a_1.png", is_published="1", art_tags="ContestX, elf"),
        _row(media_id="2", filename="b_2.png", is_published="1", art_tags="cityscape"),
        _row(media_id="3", filename="c_3.png", is_published="0", art_tags=""),
    ])
    assert query_catalog(db, published_only=True)[1] == 2
    assert query_catalog(db, art_tag="elf")[1] == 1
    assert query_catalog(db, art_tag="contestx")[1] == 1   # case-insensitive
    assert query_catalog(db, published_only=True, art_tag="city")[1] == 1


def test_media_type_filter(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="i1", filename="a.png"),
        _row(media_id="i2", filename="b.png"),
        _row(media_id="v1", filename="videos/x_v1.mp4", is_video="1"),
    ])
    assert query_catalog(db, media_type="video")[1] == 1
    assert query_catalog(db, media_type="image")[1] == 2
    assert query_catalog(db, media_type="")[1] == 3  # all


def test_catalog_model_options_most_used_first(tmp_path):
    from pixai_gallery import catalog_model_options
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="1", filename="a.png", model_name="Tsubaki", model_id="111"),
        _row(media_id="2", filename="b.png", model_name="Tsubaki", model_id="111"),
        _row(media_id="3", filename="c.png", model_name="Dreamix", model_id="222"),
    ])
    opts = catalog_model_options(db)
    assert opts[0] == ("Tsubaki", "111")           # most-used first
    assert ("Dreamix", "222") in opts


def test_source_badges_render(tmp_path):
    from pixai_gallery import create_app
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="g1", filename="a.png", source="api"),
        _row(media_id="l1", filename="b.png", source="local"),
    ])
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"x")
    (tmp_path / "images" / "b.png").write_bytes(b"x")
    data = create_app(tmp_path).test_client().get("/").data
    assert b"sbadge gen" in data and b"sbadge loc" in data


def test_source_filter(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="h1", filename="a.png"),                       # online (blank)
        _row(media_id="h2", filename="b.png", source="online"),      # explicit online
        _row(media_id="g1", filename="c.png", source="api"),         # generated
        _row(media_id="l1", filename="d.png", source="local"),       # imported
    ])
    assert query_catalog(db, source="online")[1] == 2   # blank + 'online'
    assert query_catalog(db, source="api")[1] == 1
    assert query_catalog(db, source="local")[1] == 1
    assert query_catalog(db, source="")[1] == 4         # all


def test_lora_filter(tmp_path):
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="1", filename="a.png", loras="Detail Tweaker:0.7, Anime:0.5"),
        _row(media_id="2", filename="b.png", loras="Anime:0.6"),
        _row(media_id="3", filename="c.png", loras=""),
    ])
    assert query_catalog(db, lora="anime")[1] == 2
    assert query_catalog(db, lora="detail")[1] == 1


def test_full_image_and_export_zip_routes(tmp_path):
    import io
    import zipfile
    from pixai_gallery import create_app
    db = tmp_path / "catalog.db"
    save_catalog(db, [_row(media_id="111", filename="a_111.png", prompt_preview="p")])
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a_111.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
    client = create_app(tmp_path).test_client()

    r = client.get("/full/111")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
    assert client.get("/full/nope").status_code == 404

    z = client.post("/export-zip", data={"media_ids": "111"})
    assert z.status_code == 200 and z.headers["Content-Type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.data)).namelist()
    assert names == ["a_111.png"]
    assert client.post("/export-zip", data={"media_ids": "ghost"}).status_code == 404


def test_collection_health_resolves_video_and_local_by_filename(tmp_path):
    """A video / imported row's media_id is synthetic (or a video id the image-only
    walk never sees), so 'missing' must resolve by filename too -- not over-report."""
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="vid123", filename="videos/x_vid123.mp4", is_video="1"),
        _row(media_id="local_abc", filename="videos/MyClip.mp4", is_video="1", source="local"),
        _row(media_id="gone", filename="images/not_here.png"),   # genuinely missing
    ])
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "x_vid123.mp4").write_bytes(b"v")
    (tmp_path / "videos" / "MyClip.mp4").write_bytes(b"v")
    h = collection_health(tmp_path, db)
    assert h["missing"] == 1          # only the truly-absent image, not the videos


def test_collection_health_detects_duplicate(tmp_path):
    db = tmp_path / "catalog.db"
    init_db(db)
    (tmp_path / "images").mkdir()
    (tmp_path / "2024-03").mkdir()
    (tmp_path / "images" / "p_t1_111.webp").write_bytes(b"data")
    (tmp_path / "2024-03" / "111.webp").write_bytes(b"data")
    h = collection_health(tmp_path, db)
    assert h["dup_redundant"] == 1


def test_duplicate_groups_finds_cross_folder_copies(tmp_path):
    from pixai_gallery import duplicate_groups
    (tmp_path / "images").mkdir()
    (tmp_path / "2024-03").mkdir()
    # 111 lives in two buckets -> a group; 222 lives only in images -> not a group
    (tmp_path / "images" / "p_t1_111.webp").write_bytes(b"data")
    (tmp_path / "2024-03" / "111.webp").write_bytes(b"data")
    (tmp_path / "images" / "x_222.webp").write_bytes(b"solo")
    groups = duplicate_groups(tmp_path)
    assert len(groups) == 1
    g = groups[0]
    assert g["media_id"] == "111"
    # most-organized copy (month) is the keeper over flat images/
    assert g["keeper"].replace("\\", "/") == "2024-03/111.webp"
    assert len(g["copies"]) == 2


def test_duplicate_groups_ignores_gallery_and_quarantine(tmp_path):
    from pixai_gallery import duplicate_groups
    (tmp_path / "images").mkdir()
    (tmp_path / "gallery" / "thumbs").mkdir(parents=True)
    (tmp_path / "_duplicates").mkdir()
    (tmp_path / "images" / "a_111.webp").write_bytes(b"d")
    (tmp_path / "gallery" / "thumbs" / "111.jpg").write_bytes(b"d")
    (tmp_path / "_duplicates" / "111.webp").write_bytes(b"d")
    # only the images/ copy counts -> not a cross-bucket duplicate
    assert duplicate_groups(tmp_path) == []


def test_video_row_renders_and_serves(tmp_path):
    from pixai_gallery import create_app
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="VID", filename="videos/dance_VID.mp4", is_video="1",
             poster_media_id="POSTER", prompt_preview="night elf dance",
             prompt_full="night elf dance", video_duration="10"),
        _row(media_id="POSTER", filename="images/p_POSTER.png", prompt_preview="still"),
    ])
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "dance_VID.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42FAKEMP4")
    (tmp_path / "gallery" / "thumbs").mkdir(parents=True)
    (tmp_path / "gallery" / "thumbs" / "POSTER.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegposter")
    client = create_app(tmp_path).test_client()

    # grid: shows the play badge and points the thumb at the poster media id
    idx = client.get("/").data
    assert b"vbadge" in idx
    assert b"/thumbs/POSTER.jpg" in idx

    # detail: renders a <video> element pointing at the video-file route
    d = client.get("/image/VID")
    assert d.status_code == 200
    assert b"<video" in d.data
    assert b"/video-file/VID" in d.data

    # the mp4 is actually served
    v = client.get("/video-file/VID")
    assert v.status_code == 200
    assert v.data == b"\x00\x00\x00\x18ftypmp42FAKEMP4"

    # a non-video media id is rejected by the video route
    assert client.get("/video-file/POSTER").status_code == 404


def test_delete_tasks_bulk_purges_whole_task_cloud_and_local(tmp_path, monkeypatch):
    import pixai_gallery_backup as core
    from pixai_gallery import create_app, load_catalog
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="m1", filename="images/a_m1.png", task_id="T1"),
        _row(media_id="m2", filename="images/b_m2.png", task_id="T1"),   # same task (batch)
        _row(media_id="loc", filename="videos/c.mp4", task_id="", source="local", is_video="1"),
    ])
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a_m1.png").write_bytes(b"x")
    (tmp_path / "images" / "b_m2.png").write_bytes(b"x")
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "c.mp4").write_bytes(b"v")

    calls = []
    monkeypatch.setattr(core, "_make_session", lambda *a, **k: object())
    monkeypatch.setattr(core, "delete_task_gql", lambda s, tid: calls.append(tid))
    client = create_app(tmp_path).test_client()

    # select ONE image of task T1, plus the local-only import
    r = client.post("/delete-tasks-bulk", data={"media_ids": ["m1", "loc"], "back": "/"})
    assert r.status_code == 302
    assert calls == ["T1"]                       # cloud delete fired once for the task
    assert "deleted=1" in r.headers["Location"]
    remaining = {x["media_id"] for x in load_catalog(db)}
    assert remaining == set()                    # whole task (m1+m2) + import all purged
    assert not (tmp_path / "images" / "b_m2.png").exists()   # batch sibling gone too


def test_edit_prompt_and_bulk_replace_routes(tmp_path):
    from pixai_gallery import create_app, load_catalog
    db = tmp_path / "catalog.db"
    save_catalog(db, [
        _row(media_id="m1", filename="a_m1.png", prompt_full="red cat"),
        _row(media_id="m2", filename="b_m2.png", prompt_full="red dog"),
    ])
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a_m1.png").write_bytes(b"x")
    (tmp_path / "images" / "b_m2.png").write_bytes(b"x")
    client = create_app(tmp_path).test_client()

    r = client.post("/edit-prompt/m1", json={"prompt": "blue cat"})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    r2 = client.post("/bulk-replace-prompt",
                     data={"media_ids": ["m1", "m2"], "find": "cat", "replace": "lion", "back": "/"})
    assert r2.status_code == 302 and "replaced=1" in r2.headers["Location"]
    by_id = {x["media_id"]: x["prompt_full"] for x in load_catalog(db)}
    assert by_id["m1"] == "blue lion" and by_id["m2"] == "red dog"
