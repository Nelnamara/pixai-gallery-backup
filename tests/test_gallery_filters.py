"""Tests for gallery search/filter logic: wildcard search, multi-word AND,
year dropdowns, and per-page (via query_catalog)."""
import pytest

from pixai_gallery import (CATALOG_FIELDS, init_db, save_catalog, query_catalog,
                           catalog_years, _like_pattern)


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
