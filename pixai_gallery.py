#!/usr/bin/env python3
"""
pixai_gallery.py
================
Local Flask web gallery for your PixAI backup collection.

Reads catalog.db (SQLite) and serves a browseable, filterable, paginated image
gallery at http://localhost:5000 . Supports single and bulk delete (removes
image file, thumbnail, and catalog row).

Requirements:
    pip install flask pillow

Usage:
    python pixai_gallery.py
    python pixai_gallery.py --out pixai_backup --port 5000
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    from flask import (Flask, redirect, render_template_string, request,
                       send_file, send_from_directory, url_for)
except ImportError:
    sys.exit("Flask is required for the gallery server.\n"
             "Install it with:  pip install flask")

try:
    from PIL import Image
except ImportError:
    Image = None  # thumbnails will be skipped with a warning


# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------
CATALOG_FIELDS = [
    "task_id", "media_id", "filename", "batch", "url", "width", "height",
    "prompt_preview", "status", "created_at",
    "prompt_full", "natural_prompt", "seed", "steps",
    "sampler", "cfg_scale", "model_id", "model_name", "rating",
    # Published-artwork metadata, populated by --sync-artworks (blank otherwise)
    "artwork_id", "title", "is_published", "is_nsfw",
    "liked_count", "comment_count", "aes_score", "art_tags",
    # LoRAs used, populated by --full-meta / --backfill-full-meta ("Name:0.7, …")
    "loras",
    # Extra reproduction params from getTaskById (full-meta)
    "negative_prompt", "clip_skip",
    # Image-to-video tasks (--sync-videos): is_video='1', poster_media_id is the
    # still-frame media id (its image is the gallery poster), duration in seconds.
    "is_video", "poster_media_id", "video_duration",
    # Provenance: '' / 'online' = backed up from PixAI history; 'api' = created via
    # --generate; 'local' = imported from disk via --import-local.
    "source",
    # '1' if --reconcile-deleted found this row's task is gone from your live PixAI
    # feed (i.e. you deleted it on the website). Advisory; cleared on re-reconcile.
    "deleted_remote",
    # User collections: comma-joined names (no moving files, survives organize).
    # Names may contain spaces but not commas. Set/filtered in the gallery.
    "collections",
]

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"})
THUMB_SIZE = (768, 768)
THUMB_QUALITY = 90
PAGE_SIZE = 100


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS catalog (
    media_id        TEXT PRIMARY KEY,
    task_id         TEXT,
    filename        TEXT,
    batch           TEXT DEFAULT '',
    url             TEXT,
    width           TEXT,
    height          TEXT,
    prompt_preview  TEXT,
    status          TEXT,
    created_at      TEXT,
    prompt_full     TEXT,
    natural_prompt  TEXT,
    seed            TEXT,
    steps           TEXT,
    sampler         TEXT,
    cfg_scale       TEXT,
    model_id        TEXT,
    model_name      TEXT,
    rating          TEXT,
    artwork_id      TEXT DEFAULT '',
    title           TEXT DEFAULT '',
    is_published    TEXT DEFAULT '',
    is_nsfw         TEXT DEFAULT '',
    liked_count     TEXT DEFAULT '',
    comment_count   TEXT DEFAULT '',
    aes_score       TEXT DEFAULT '',
    art_tags        TEXT DEFAULT '',
    loras           TEXT DEFAULT '',
    negative_prompt TEXT DEFAULT '',
    clip_skip       TEXT DEFAULT '',
    is_video        TEXT DEFAULT '',
    poster_media_id TEXT DEFAULT '',
    video_duration  TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    deleted_remote  TEXT DEFAULT '',
    collections     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_created_at ON catalog(created_at);
CREATE INDEX IF NOT EXISTS idx_model_name ON catalog(model_name);
CREATE INDEX IF NOT EXISTS idx_rating     ON catalog(rating);
"""

_UPSERT = """
INSERT INTO catalog ({fields})
VALUES ({placeholders})
ON CONFLICT(media_id) DO UPDATE SET
{updates};
""".format(
    fields=", ".join(CATALOG_FIELDS),
    placeholders=", ".join("?" for _ in CATALOG_FIELDS),
    updates=", ".join(
        "{f}=excluded.{f}".format(f=f) for f in CATALOG_FIELDS if f != "media_id"
    ),
)


def init_db(db_path):
    """Create the catalog table and indexes if they don't exist yet."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.executescript(_CREATE_TABLE)
    # Add batch column to pre-existing databases that lack it, then index it
    try:
        con.execute("ALTER TABLE catalog ADD COLUMN batch TEXT DEFAULT ''")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    con.execute("CREATE INDEX IF NOT EXISTS idx_batch ON catalog(batch)")
    con.commit()
    con.close()


_MIGRATIONS = [
    "ALTER TABLE catalog ADD COLUMN batch TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN artwork_id TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN title TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN is_published TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN is_nsfw TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN liked_count TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN comment_count TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN aes_score TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN art_tags TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN loras TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN negative_prompt TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN clip_skip TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN is_video TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN poster_media_id TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN video_duration TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN source TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN deleted_remote TEXT DEFAULT ''",
    "ALTER TABLE catalog ADD COLUMN collections TEXT DEFAULT ''",
]

def _connect(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    for sql in _MIGRATIONS:
        try:
            con.execute(sql)
            con.commit()
        except sqlite3.OperationalError:
            pass  # column/index already exists
    return con


def load_catalog(db_path):
    """Return all rows as a list of plain dicts, oldest-first."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT * FROM catalog").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def save_catalog(db_path, rows):
    """Upsert a list of dicts into the catalog (replaces the old full-rewrite)."""
    db_path = Path(db_path)
    init_db(db_path)
    con = _connect(db_path)
    try:
        con.executemany(
            _UPSERT,
            [tuple(r.get(f, "") or "" for f in CATALOG_FIELDS) for r in rows],
        )
        con.commit()
    finally:
        con.close()


def update_rating(db_path, media_id, value):
    """Update a single row's rating without touching the rest of the catalog."""
    con = _connect(db_path)
    try:
        con.execute(
            "UPDATE catalog SET rating=? WHERE media_id=?",
            (str(value) if value else "", media_id),
        )
        con.commit()
    finally:
        con.close()


def delete_from_catalog(db_path, media_id):
    """Remove a single row by media_id."""
    con = _connect(db_path)
    try:
        con.execute("DELETE FROM catalog WHERE media_id=?", (media_id,))
        con.commit()
    finally:
        con.close()


def update_prompt_full(db_path, media_id, text):
    """Overwrite a single row's prompt_full (manual annotation/correction)."""
    con = _connect(db_path)
    try:
        con.execute("UPDATE catalog SET prompt_full=? WHERE media_id=?",
                    (text or "", media_id))
        con.commit()
    finally:
        con.close()


def bulk_replace_prompt(db_path, media_ids, find, replace):
    """Find/replace a substring in prompt_full across the given media_ids.
    Returns the number of rows actually changed."""
    if not find:
        return 0
    con = _connect(db_path)
    changed = 0
    try:
        for mid in media_ids:
            row = con.execute("SELECT prompt_full FROM catalog WHERE media_id=?",
                              (mid,)).fetchone()
            if not row:
                continue
            old = row[0] or ""
            new = old.replace(find, replace)
            if new != old:
                con.execute("UPDATE catalog SET prompt_full=? WHERE media_id=?", (new, mid))
                changed += 1
        con.commit()
    finally:
        con.close()
    return changed


def _db_is_empty(db_path):
    """Return True if the database has no rows (missing or freshly initialised)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return True
    try:
        con = sqlite3.connect(str(db_path))
        count = con.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        con.close()
        return count == 0
    except sqlite3.OperationalError:
        return True


def migrate_csv_to_db(csv_path, db_path):
    """One-time migration: import catalog.csv into catalog.db.

    Safe to re-run — existing rows are upserted, not duplicated.
    Returns the number of rows imported.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0
    save_catalog(db_path, rows)
    return len(rows)


def export_csv(db_path, csv_path):
    """Export catalog.db back to a CSV file (backup / interop)."""
    rows = load_catalog(db_path)
    csv_path = Path(csv_path)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({field: r.get(field, "") for field in CATALOG_FIELDS})


_SORT_SQL = {
    "oldest":      "created_at ASC",
    "rating_desc": "CAST(COALESCE(NULLIF(rating,''),'0') AS INTEGER) DESC, created_at DESC",
    "rating_asc":  "CAST(COALESCE(NULLIF(rating,''),'0') AS INTEGER) ASC,  created_at DESC",
    "model":       "LOWER(COALESCE(NULLIF(model_name,''), NULLIF(model_id,''), '')) ASC",
    "width":       "CAST(COALESCE(NULLIF(width,''),'0')  AS INTEGER) DESC",
    "height":      "CAST(COALESCE(NULLIF(height,''),'0') AS INTEGER) DESC",
    "pixels":      "(CAST(COALESCE(NULLIF(width,''),'0') AS INTEGER) * "
                   "CAST(COALESCE(NULLIF(height,''),'0') AS INTEGER)) DESC",
    "aspect":      "(CAST(COALESCE(NULLIF(width,''),'0') AS REAL) / "
                   "NULLIF(CAST(COALESCE(NULLIF(height,''),'0') AS REAL),0)) DESC",
    "aes_desc":    "CAST(COALESCE(NULLIF(aes_score,''),'0') AS REAL) DESC, created_at DESC",
    "aes_asc":     "CAST(COALESCE(NULLIF(aes_score,''),'0') AS REAL) ASC,  created_at DESC",
    "likes":       "CAST(COALESCE(NULLIF(liked_count,''),'0') AS INTEGER) DESC, created_at DESC",
}
_DEFAULT_SORT_SQL = "created_at DESC"


def _like_pattern(term):
    r"""Translate a user search term into a SQL LIKE pattern.

    * `*` -> `%` (any run) and `?` -> `_` (single char), so `night*` matches
      anything starting with "night".
    * A term with NO wildcard is treated as a substring (wrapped in `%...%`),
      preserving the old broad-search behavior.
    * Literal `%`/`_`/`\` the user typed are escaped (LIKE uses ESCAPE '\').
    """
    t = term.strip().lower()
    has_wild = "*" in t or "?" in t
    t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    t = t.replace("*", "%").replace("?", "_")
    return t if has_wild else "%" + t + "%"


def _build_where(q, model, date_from, date_to, batch="", rating_min=0,
                 published_only=False, art_tag="", lora="", media_type="", source="",
                 collection=""):
    """Return (where_clause, params) for the common filter set."""
    clauses = ["filename != ''"]
    params  = []
    if collection:
        # exact-token match within the comma-joined list (no partial-name bleed)
        clauses.append("(',' || COALESCE(collections,'') || ',') LIKE ?")
        params.append("%," + collection + ",%")
    if media_type == "video":
        clauses.append("is_video = '1'")
    elif media_type == "image":
        clauses.append("COALESCE(is_video,'') != '1'")
    if source == "online":
        clauses.append("COALESCE(source,'') IN ('', 'online')")
    elif source in ("api", "local"):
        clauses.append("source = ?")
        params.append(source)
    elif source == "deleted":
        clauses.append("deleted_remote = '1'")   # flagged by --reconcile-deleted
    if rating_min:
        clauses.append("CAST(COALESCE(NULLIF(rating,''),'0') AS INTEGER) >= ?")
        params.append(int(rating_min))
    if published_only:
        clauses.append("is_published = '1'")
    if art_tag:
        clauses.append("LOWER(COALESCE(art_tags,'')) LIKE ?")
        params.append("%" + art_tag.strip().lower() + "%")
    if lora:
        clauses.append("LOWER(COALESCE(loras,'')) LIKE ?")
        params.append("%" + lora.strip().lower() + "%")
    if q:
        # Whitespace-separated terms are ANDed; each may use * / ? wildcards.
        for term in q.split():
            clauses.append("(LOWER(COALESCE(prompt_full,'')) LIKE ? ESCAPE '\\' "
                           "OR LOWER(COALESCE(prompt_preview,'')) LIKE ? ESCAPE '\\')")
            like = _like_pattern(term)
            params += [like, like]
    if model:
        clauses.append("model_name = ?")
        params.append(model)
    if batch:
        clauses.append("batch = ?")
        params.append(batch)
    if date_from:
        clauses.append("SUBSTR(created_at,1,7) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("SUBSTR(created_at,1,7) <= ?")
        params.append(date_to)
    return " AND ".join(clauses), params


def get_row(db_path, media_id):
    """Return a single catalog row dict by media_id, or None."""
    con = _connect(db_path)
    try:
        row = con.execute("SELECT * FROM catalog WHERE media_id=?", (media_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _split_collections(s):
    return [c.strip() for c in (s or "").split(",") if c.strip()]


def unique_collections(db_path):
    """Distinct collection names across the catalog, case-insensitive sorted."""
    con = _connect(db_path)
    try:
        names = set()
        for (s,) in con.execute("SELECT collections FROM catalog WHERE COALESCE(collections,'') != ''"):
            names.update(_split_collections(s))
        return sorted(names, key=str.lower)
    finally:
        con.close()


def add_to_collection(db_path, media_ids, name):
    """Add a collection label to each media_id (no-op if already in it). Names may
    contain spaces but not commas. Returns the number of rows changed."""
    name = (name or "").strip().replace(",", " ").strip()
    if not name or not media_ids:
        return 0
    con = _connect(db_path)
    changed = 0
    try:
        for mid in media_ids:
            row = con.execute("SELECT collections FROM catalog WHERE media_id=?", (mid,)).fetchone()
            if not row:
                continue
            cols = _split_collections(row[0])
            if name not in cols:
                cols.append(name)
                con.execute("UPDATE catalog SET collections=? WHERE media_id=?",
                            (",".join(cols), mid))
                changed += 1
        con.commit()
    finally:
        con.close()
    return changed


def remove_from_collection(db_path, media_ids, name):
    """Remove a collection label from each media_id. Returns rows changed."""
    name = (name or "").strip()
    if not name or not media_ids:
        return 0
    con = _connect(db_path)
    changed = 0
    try:
        for mid in media_ids:
            row = con.execute("SELECT collections FROM catalog WHERE media_id=?", (mid,)).fetchone()
            if not row:
                continue
            cols = _split_collections(row[0])
            if name in cols:
                con.execute("UPDATE catalog SET collections=? WHERE media_id=?",
                            (",".join(c for c in cols if c != name), mid))
                changed += 1
        con.commit()
    finally:
        con.close()
    return changed


def query_catalog(db_path, q="", model="", date_from="", date_to="",
                  sort="newest", page=1, page_size=100, batch="", rating_min=0,
                  published_only=False, art_tag="", lora="", media_type="", source="",
                  collection=""):
    """Return (rows, total) with filtering, sorting and pagination done in SQL."""
    where, params = _build_where(q, model, date_from, date_to, batch, rating_min,
                                 published_only, art_tag, lora, media_type, source,
                                 collection)
    order = _SORT_SQL.get(sort, _DEFAULT_SORT_SQL)
    offset = (max(1, page) - 1) * page_size
    con = _connect(db_path)
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM catalog WHERE {}".format(where), params
        ).fetchone()[0]
        rows = con.execute(
            "SELECT * FROM catalog WHERE {} ORDER BY {} LIMIT ? OFFSET ?".format(where, order),
            params + [page_size, offset],
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        con.close()


def list_media_ids(db_path, q="", model="", date_from="", date_to="", sort="newest",
                   batch="", rating_min=0, published_only=False, art_tag="", lora="",
                   media_type="", source="", collection=""):
    """Return ordered list of media_ids matching the filter (no row data)."""
    where, params = _build_where(q, model, date_from, date_to, batch, rating_min,
                                 published_only, art_tag, lora, media_type, source,
                                 collection)
    order = _SORT_SQL.get(sort, _DEFAULT_SORT_SQL)
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT media_id FROM catalog WHERE {} ORDER BY {}".format(where, order), params
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def unique_models(db_path):
    """Return sorted list of distinct non-empty model names in the catalog."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT model_name FROM catalog WHERE model_name != '' ORDER BY model_name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def catalog_model_options(db_path):
    """Return [(name, model_id)] for distinct models in the catalog, most-used
    first. model_id is the version id used in real generations, so it's a valid,
    guaranteed-working value for --generate's --model -- the basis of the model
    picker dropdown."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT COALESCE(NULLIF(model_name,''), model_id) AS nm, model_id, COUNT(*) c "
            "FROM catalog WHERE COALESCE(model_id,'') != '' AND model_id GLOB '[0-9]*' "
            "GROUP BY model_id ORDER BY c DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        con.close()


def backfill_batches(out_dir, db_path):
    """Scan batches/ on disk and populate the batch column for already-organized images.

    Safe to re-run — only updates rows where batch is currently empty.
    Returns number of rows updated.
    """
    batches_root = Path(out_dir) / "batches"
    if not batches_root.exists():
        return 0
    updates = {}  # media_id -> batch_name
    for batch_dir in batches_root.iterdir():
        if not batch_dir.is_dir():
            continue
        batch_name = batch_dir.name
        for p in batch_dir.rglob("*"):
            if p.suffix.lower() not in _IMAGE_EXTS:
                continue
            mid = p.stem.split("_")[-1]
            updates[mid] = batch_name
    if not updates:
        return 0
    con = _connect(db_path)
    try:
        updated = 0
        for mid, batch_name in updates.items():
            cur = con.execute(
                "UPDATE catalog SET batch=? WHERE media_id=? AND (batch='' OR batch IS NULL)",
                (batch_name, mid),
            )
            updated += cur.rowcount
        con.commit()
        return updated
    finally:
        con.close()


def unique_batches(db_path):
    """Return sorted list of distinct non-empty batch names in the catalog."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT batch FROM catalog WHERE batch != '' ORDER BY batch"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def catalog_years(db_path):
    """Descending list of years (ints) present in catalog created_at, for the
    date-filter dropdowns. Empty if the catalog has no dated rows."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT DISTINCT SUBSTR(created_at,1,4) AS y FROM catalog "
            "WHERE created_at != '' AND y != '' ORDER BY y DESC"
        ).fetchall()
        return [int(r[0]) for r in rows if str(r[0]).isdigit()]
    finally:
        con.close()


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "{:.1f} {}".format(n, unit)
        n /= 1024


def collection_health(out_dir, db_path):
    """Compute at-a-glance metrics for the health dashboard. One disk walk
    (sizes + buckets + Class-A duplicate detection) plus a few catalog queries.

    Returns a dict consumed by the /health route. Cheap (no content hashing) so
    it's safe to render on every page load.
    """
    from collections import defaultdict, Counter
    gallery_dir = out_dir / "gallery"
    quarantine_dir = out_dir / "_duplicates"

    def _under(p, parent):
        try:
            p.relative_to(parent); return True
        except ValueError:
            return False

    per_bucket = Counter()
    total_files = 0
    total_bytes = 0
    on_disk_ids = set()
    on_disk_rels = set()      # relative paths of every media file (incl. videos)
    locs = defaultdict(set)   # media_id -> set of bucket names (Class A dup detection)
    dup_redundant = 0
    dup_bytes = 0
    mid_sizes = defaultdict(list)  # media_id -> [sizes] to estimate reclaimable
    _video_exts = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}

    for p in out_dir.rglob("*"):
        ext = p.suffix.lower()
        is_img = ext in _IMAGE_EXTS
        if (not is_img and ext not in _video_exts) or not p.is_file():
            continue
        if p.name.endswith(".part") or _under(p, gallery_dir) or _under(p, quarantine_dir):
            continue
        rel = p.relative_to(out_dir)
        on_disk_rels.add(str(rel).replace("\\", "/"))
        if not is_img:
            continue          # videos: track the path only; skip image-centric stats
        top = str(rel).replace("\\", "/").split("/")[0]
        if top == "images":
            bucket = "images"
        elif top == "batches":
            bucket = "batches"
        elif top == "unknown-date" or (len(top) == 7 and top[4] == "-" and top[:4].isdigit()):
            bucket = "month"
        else:
            bucket = "other"
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        total_files += 1
        total_bytes += sz
        per_bucket[bucket] += 1
        mid = media_id_of(p)
        on_disk_ids.add(mid)
        locs[mid].add(bucket)
        mid_sizes[mid].append(sz)

    for mid, buckets in locs.items():
        if len(buckets) > 1:
            extra = len(mid_sizes[mid]) - 1
            dup_redundant += extra
            sizes = sorted(mid_sizes[mid])
            dup_bytes += sum(sizes[:-1])  # all but the largest counted as reclaimable

    con = _connect(db_path)
    try:
        def _scalar(sql):
            return con.execute(sql).fetchone()[0]
        total_rows   = _scalar("SELECT COUNT(*) FROM catalog")
        with_image   = _scalar("SELECT COUNT(*) FROM catalog WHERE filename != ''")
        with_full    = _scalar("SELECT COUNT(*) FROM catalog WHERE COALESCE(prompt_full,'') != ''")
        rated        = _scalar("SELECT COUNT(*) FROM catalog "
                               "WHERE COALESCE(NULLIF(rating,''),'0') NOT IN ('0')")
        by_month = con.execute(
            "SELECT SUBSTR(created_at,1,7) AS m, COUNT(*) FROM catalog "
            "WHERE created_at != '' GROUP BY m ORDER BY m"
        ).fetchall()
        top_models = con.execute(
            "SELECT COALESCE(NULLIF(model_name,''), NULLIF(model_id,''), 'unknown') AS mdl, "
            "COUNT(*) AS c FROM catalog WHERE filename != '' GROUP BY mdl ORDER BY c DESC LIMIT 8"
        ).fetchall()
        # Published-artwork analytics (populated by --sync-artworks)
        published = _scalar("SELECT COUNT(*) FROM catalog WHERE is_published = '1'")
        total_likes = _scalar(
            "SELECT COALESCE(SUM(CAST(COALESCE(NULLIF(liked_count,''),'0') AS INTEGER)),0) "
            "FROM catalog WHERE is_published = '1'")
        tag_rows = con.execute(
            "SELECT art_tags FROM catalog WHERE COALESCE(art_tags,'') != ''").fetchall()
        lora_rows = con.execute(
            "SELECT loras FROM catalog WHERE COALESCE(loras,'') != ''").fetchall()
        prompt_rows = con.execute(
            "SELECT prompt_preview FROM catalog WHERE COALESCE(prompt_preview,'') != ''"
        ).fetchall()
        # catalog rows that claim a file but whose media_id isn't on disk
        cat_rows = con.execute(
            "SELECT media_id, filename FROM catalog WHERE filename != ''").fetchall()
    finally:
        con.close()

    tag_counter = Counter()
    for (tags,) in tag_rows:
        for t in (tags or "").split(","):
            t = t.strip()
            if t:
                tag_counter[t] += 1
    top_tags = tag_counter.most_common(10)

    lora_counter = Counter()
    for (loras,) in lora_rows:
        for part in (loras or "").split(","):
            name = part.strip().rsplit(":", 1)[0].strip()  # drop ":weight"
            if name:
                lora_counter[name] += 1
    top_loras = lora_counter.most_common(10)

    # Prompt word-cloud: most common meaningful words across prompt previews.
    import re as _re
    stop = {"the", "and", "a", "an", "of", "with", "in", "on", "at", "to", "for",
            "is", "by", "as", "or", "from", "best", "quality", "masterpiece",
            "highres", "detailed", "very", "high", "score", "up", "1girl", "1boy",
            "solo", "looking", "viewer"}
    word_counter = Counter()
    for (pp,) in prompt_rows:
        for w in _re.findall(r"[a-z][a-z']{2,}", (pp or "").lower()):
            if w not in stop:
                word_counter[w] += 1
    top_words = word_counter.most_common(40)

    # A row is "missing" only if NEITHER its media id is on disk (the PixAI
    # naming path) NOR its filename resolves to a real file (the imported/local
    # path, whose media_id is a synthetic local_<hash> that never matches a file).
    missing = sum(
        1 for mid, fn in cat_rows
        if (not mid or mid not in on_disk_ids)
        and (fn or "").replace("\\", "/") not in on_disk_rels)

    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_size_h": _fmt_size(total_bytes),
        "per_bucket": dict(per_bucket),
        "dup_redundant": dup_redundant,
        "dup_bytes": dup_bytes,
        "dup_bytes_h": _fmt_size(dup_bytes),
        "catalog_rows": total_rows,
        "with_image": with_image,
        "with_full_meta": with_full,
        "full_meta_pct": round(100 * with_full / with_image) if with_image else 0,
        "rated": rated,
        "missing": missing,
        "by_month": [(m, c) for (m, c) in by_month],
        "top_models": [(m, c) for (m, c) in top_models],
        "published": published,
        "total_likes": total_likes,
        "top_tags": top_tags,
        "top_loras": top_loras,
        "top_words": top_words,
    }


def duplicate_groups(out_dir, limit=300):
    """Class-A duplicates for the gallery review browser: media_ids whose file
    exists in more than one folder bucket. Cheap (no hashing). Returns a list of
    {media_id, keeper(rel), copies:[{rel,bucket,size}]} sorted keeper-first."""
    from collections import defaultdict
    gallery_dir = out_dir / "gallery"
    quarantine_dir = out_dir / "_duplicates"
    prio = {"batches": 0, "month": 1, "images": 2, "other": 3}
    locs = defaultdict(list)
    for p in out_dir.rglob("*"):
        if p.suffix.lower() not in _IMAGE_EXTS or not p.is_file():
            continue
        if p.name.endswith(".part") or _is_under(p, gallery_dir) or _is_under(p, quarantine_dir):
            continue
        rel = p.relative_to(out_dir)
        top = str(rel).replace("\\", "/").split("/")[0]
        if top == "images":
            bucket = "images"
        elif top == "batches":
            bucket = "batches"
        elif top == "unknown-date" or (len(top) == 7 and top[4] == "-" and top[:4].isdigit()):
            bucket = "month"
        else:
            bucket = "other"
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        locs[media_id_of(p)].append({"rel": str(rel), "bucket": bucket, "size": sz})

    groups = []
    for mid, items in locs.items():
        if len(items) > 1 and len({it["bucket"] for it in items}) > 1:
            ordered = sorted(items, key=lambda it: (prio.get(it["bucket"], 9), len(it["rel"])))
            groups.append({"media_id": mid, "keeper": ordered[0]["rel"], "copies": ordered})
            if len(groups) >= limit:
                break
    return groups


def media_id_of(path):
    """Canonical media_id extraction (INVARIANT 1): the last underscore-delimited
    chunk of the filename stem. Works for every naming layout the tool produces:
    flat (`prompt_task_<mid>`), batch (`NN_<mid>`), and bare (`<mid>`)."""
    from pathlib import Path
    return Path(path).stem.split("_")[-1]


def _is_under(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def find_files_for_media_id(out_dir, media_id, include_gallery=False):
    """All on-disk image files whose media_id matches, anywhere under out_dir.

    Single source of truth for media-id -> file resolution, shared by resume
    (`already_downloaded`), the gallery (`find_image_file`), and the duplicate
    audit. Matches BOTH naming layouts in one pass:
      * prefixed   `prompt_task_<mid>.ext` / `NN_<mid>.ext`
      * bare       `<mid>.ext`   (single-image --organize month files)

    The exact `media_id_of(p) == mid` check prevents substring collisions (a
    longer id ending in these digits). Skips `.part`, zero-byte files, gallery
    thumbnails (unless include_gallery=True), and quarantined files under
    _duplicates/ (so a quarantined copy never counts as a live "survivor" and
    resume treats it as not-present). Returns a list of Paths.
    """
    mid = str(media_id)
    gallery_dir = out_dir / "gallery"
    quarantine_dir = out_dir / "_duplicates"
    matches = []
    for p in out_dir.rglob("*{}.*".format(mid)):
        if p.suffix.lower() not in _IMAGE_EXTS:
            continue
        if p.name.endswith(".part"):
            continue
        if media_id_of(p) != mid:
            continue
        if not include_gallery and _is_under(p, gallery_dir):
            continue
        if _is_under(p, quarantine_dir):
            continue
        try:
            if not p.is_file() or p.stat().st_size == 0:
                continue
        except OSError:
            continue
        matches.append(p)
    return matches


def find_image_file(out_dir, media_id, filename):
    """Locate an image file: try catalog filename first, then media-id fallback.

    Excludes out_dir/gallery/ so thumbnails are never returned as full-res images.
    """
    gallery_dir = out_dir / "gallery"
    if filename:
        for candidate in out_dir.rglob(filename):
            if candidate.is_file() and not _is_under(candidate, gallery_dir):
                return candidate
    matches = find_files_for_media_id(out_dir, media_id)
    return matches[0] if matches else None


def make_thumbnail(img_path, thumb_path):
    if Image is None:
        return False
    try:
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            im.thumbnail(THUMB_SIZE, Image.LANCZOS)
            im.save(thumb_path, "JPEG", quality=THUMB_QUALITY)
        return True
    except Exception:
        return False


def build_thumbnails(rows, out_dir, thumb_dir, force=False, progress_cb=None, workers=4):
    """Generate JPEG thumbnails for rows that have a file. CPU-bound (Pillow),
    so a thread pool gives a real multi-core speedup (Pillow releases the GIL
    during decode/encode). workers<=1 runs serially. Each worker writes a distinct
    thumb file; progress is reported on the calling thread."""
    if Image is None:
        print("Warning: Pillow not installed -- thumbnails will not be generated.")
        return
    total = 0
    done = 0
    work = []
    for row in rows:
        if not row.get("filename") or row.get("is_video") == "1":
            continue  # videos have no still of their own; poster_media_id covers it
        total += 1
        thumb_path = thumb_dir / "{}.jpg".format(row["media_id"])
        if not force and thumb_path.exists():
            done += 1
            continue
        work.append((row["media_id"], thumb_path, row.get("filename")))

    def _one(item):
        mid, thumb_path, filename = item
        img_path = find_image_file(out_dir, mid, filename)
        return bool(img_path and make_thumbnail(img_path, thumb_path))

    def _tick():
        pct = int(done / total * 100) if total else 100
        if progress_cb:
            progress_cb(done, total, pct)
        else:
            print("\r  Thumbnails: {}/{} ({:d}%)  ".format(done, total, pct),
                  end="", flush=True)

    if work and workers and workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(_one, it) for it in work]):
                try:
                    if fut.result():
                        done += 1
                except Exception:
                    pass
                _tick()
    else:
        for it in work:
            if _one(it):
                done += 1
            _tick()
    if total and not progress_cb:
        print()


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app(out_dir: Path):
    app = Flask(__name__)
    db_path = out_dir / "catalog.db"
    init_db(db_path)
    backfill_batches(out_dir, db_path)
    thumb_dir = out_dir / "gallery" / "thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Template
    # ------------------------------------------------------------------
    BASE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
<meta name="theme-color" content="#0c0a1c">
<title>Moonglade Athenaeum</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23cba6f7'/%3E%3Cpath d='M9 22V10h6a4 4 0 0 1 0 8h-3' stroke='%231e1e2e' stroke-width='2.4' fill='none' stroke-linecap='round'/%3E%3Ccircle cx='23' cy='11' r='2.2' fill='%23d4af37'/%3E%3C/svg%3E">
<script>if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}</script>
<style>
  :root {
    /* Palette sampled from two reference images:
       731004762264180451.webp — teal "magic glow", green gems, rare gold trim.
       s1_06.png              — the deep violet armor (#33236d/#241f5b/#36345a/#643aac)
                                that tints the ground and surfaces below. */
    --base:    #0c0a1c; --mantle:  #0a0818; --surface0:#211f3a;
    --surface1:#3a3460; --overlay0:#6a6088; --text:    #d6d2e2;
    --subtext: #9a93ab; --lavender:#b692e6; --mauve:   #c4a6f0;
    --red:     #f38ba8; --peach:   #fab387; --green:   #46d488;
    --blue:    #47cbc3; --sapphire:#3a8a93;
    /* Moonglade Athenaeum palette: lavender leads, emerald is the "magic"
       highlight (Nelnamara's gems), gold filigree is rare. */
    --accent:  #b692e6; --accent-soft:#4fc99a; --gold: #d4af37; --emerald:#4fc99a;
    --purple-deep: #33236d; --purple-bright: #643aac;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--base); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }

  /* Header */
  header { background: var(--mantle); padding: 12px 20px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--surface0); position: sticky; top: 0; z-index: 100; }
  .brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .brand .mark { width: 26px; height: 26px; border-radius: 7px; background: var(--accent); display: flex; align-items: center; justify-content: center; color: var(--base); font-weight: 700; font-size: 15px; position: relative; }
  .brand .mark::after { content: ''; position: absolute; top: 5px; right: 5px; width: 4px; height: 4px; border-radius: 50%; background: var(--gold); }
  header h1 { font-size: 18px; color: var(--text); flex-shrink: 0; font-weight: 600; border-bottom: 2px solid var(--gold); padding-bottom: 1px; line-height: 1.1; }
  .header-stats { color: var(--subtext); font-size: 12px; }

  /* Filters */
  .filters { background: var(--mantle); padding: 10px 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; border-bottom: 1px solid var(--surface0); }
  .filters input, .filters select { background: var(--surface0); color: var(--text); border: 1px solid var(--surface1); border-radius: 6px; padding: 5px 10px; font-size: 13px; }
  .filters input { width: 280px; }
  .filters input:focus, .filters select:focus { outline: none; border-color: var(--accent-soft); box-shadow: 0 0 0 2px rgba(79,201,154,.25); }
  .filters label { color: var(--subtext); font-size: 12px; }
  .filter-toggle { display: none; }
  /* Mobile: collapse the filter bar behind a toggle so the grid leads. */
  @media (max-width: 680px) {
    header h1 { font-size: 16px; }
    header .back-link { font-size: 12px; }
    .filter-toggle { display: inline-flex; align-items: center; gap: 6px; margin: 8px 12px 0; }
    .filters { display: none; flex-direction: column; align-items: stretch; padding: 10px 12px; }
    .filters.open { display: flex; }
    .filters > div { width: 100%; }
    .filters input, .filters select { width: 100% !important; box-sizing: border-box; }
    .grid { padding: 10px 12px; gap: 8px; }
    .chips { padding: 8px 12px 0; }
    .filters input, .filters select { font-size: 16px; }  /* >=16px stops iOS zoom-on-focus */
  }
  /* Tablet: keep the filter bar visible but let wide text inputs shrink so the
     row wraps tidily instead of running off-screen. */
  @media (min-width: 681px) and (max-width: 1024px) {
    .filters input { width: 180px; }
    .filters { padding: 10px 14px; }
    .grid { padding: 12px 14px; }
  }
  .btn { background: var(--surface0); color: var(--text); border: 1px solid var(--surface1); border-radius: 6px; padding: 5px 14px; cursor: pointer; font-size: 13px; }
  .btn:hover { background: var(--surface1); }
  .btn-danger { background: var(--red); color: var(--base); border-color: var(--red); font-weight: 600; }
  .btn-danger:hover { opacity: 0.85; }
  .btn-primary { background: var(--lavender); color: var(--base); border-color: var(--lavender); font-weight: 600; }
  .btn-primary:hover { opacity: 0.85; }

  /* Active-filter chips */
  .chips { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 20px 0; align-items: center; }
  .chips .chips-label { color: var(--overlay0); font-size: 12px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; background: var(--surface0); border: 1px solid var(--surface1); border-left: 3px solid var(--gold); border-radius: 4px; padding: 3px 8px; font-size: 12px; color: var(--text); }
  .chip .k { color: var(--subtext); }
  .chip a { color: var(--overlay0); text-decoration: none; font-weight: 700; padding-left: 2px; }
  .chip a:hover { color: var(--red); }
  .chips .clear-all { color: var(--accent-soft); font-size: 12px; text-decoration: none; }
  .chips .clear-all:hover { text-decoration: underline; }

  /* Bulk toolbar */
  .bulk-bar { background: var(--surface0); padding: 8px 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--surface1); min-height: 40px; flex-wrap: wrap; position: sticky; top: var(--bulk-top, 52px); z-index: 99; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
  /* Select mode: cards capture the drag (no scroll-hijack mid-card) and never open the lightbox. */
  .select-mode .grid .card { touch-action: none; cursor: copy; }
  .select-mode .grid .card .cover { cursor: copy; }
  #select-mode-btn.active { background: var(--accent); color: #1e1e2e; font-weight: 600; }
  body.select-mode .bulk-bar::after { content: "Select mode: tap to toggle · drag across images to paint"; color: var(--accent); font-size: 12px; flex-basis: 100%; }
  .bulk-bar span { color: var(--subtext); font-size: 13px; }
  #sel-count { color: var(--gold); font-weight: 600; }

  /* Thumbnail loading skeleton */
  @keyframes shimmer { 0% { background-position: -200px 0; } 100% { background-position: 200px 0; } }
  .card img { background-image: linear-gradient(90deg, var(--surface0) 0px, var(--surface1) 100px, var(--surface0) 200px); background-size: 400px 100%; animation: shimmer 1.2s infinite linear; }
  .card img.loaded { animation: none; background: var(--surface0); }

  /* Empty state */
  .empty { text-align: center; padding: 64px 20px; color: var(--subtext); }
  .empty .big { font-size: 40px; margin-bottom: 8px; color: var(--overlay0); }
  .empty a { color: var(--accent-soft); text-decoration: none; }
  .empty a:hover { text-decoration: underline; }

  /* Grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--thumb, 200px), 1fr)); gap: 12px; padding: 16px 20px; }
  .card { background: var(--mantle); border-radius: 8px; overflow: hidden; border: 2px solid transparent; transition: border-color .15s; position: relative; cursor: pointer; }
  .card:hover { border-color: var(--surface1); }
  .card.selected { border-color: var(--purple-bright); box-shadow: 0 0 0 1px var(--purple-bright); }
  .card.kbd-focus { border-color: var(--accent-soft); box-shadow: 0 0 0 2px var(--accent-soft); }

  /* Lightbox */
  .lb { display: none; position: fixed; inset: 0; z-index: 300; background: rgba(8,6,18,.92);
        flex-direction: column; align-items: center; justify-content: center; }
  .lb.open { display: flex; }
  .lb-bar { position: absolute; top: 0; left: 0; right: 0; display: flex; align-items: center;
            justify-content: space-between; gap: 12px; padding: 10px 16px; background: rgba(10,8,24,.6); }
  #lb-caption { color: var(--subtext); font-size: 12px; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; max-width: 60%; }
  .lb-actions { display: flex; gap: 8px; flex-shrink: 0; }
  #lb-img { max-width: 94vw; max-height: 86vh; object-fit: contain; border-radius: 6px; }
  #lb-video { max-width: 94vw; max-height: 86vh; border-radius: 6px; background: #000; }
  .lb-nav { position: absolute; top: 50%; transform: translateY(-50%); background: rgba(10,8,24,.5);
            color: var(--text); border: 1px solid var(--surface1); border-radius: 8px; font-size: 28px;
            line-height: 1; padding: 10px 16px; cursor: pointer; }
  .lb-nav:hover { background: var(--surface0); }
  .lb-prev { left: 14px; } .lb-next { right: 14px; }
  @media (max-width: 680px) { .lb-nav { padding: 8px 12px; font-size: 22px; } #lb-caption { max-width: 40%; } }
  .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: var(--surface0); }
  .card .no-thumb { width: 100%; aspect-ratio: 1; background: var(--surface0); display: flex; align-items: center; justify-content: center; color: var(--overlay0); font-size: 11px; }
  .card .meta { padding: 6px 8px; }
  .card .meta .title { font-size: 12px; color: var(--text); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .meta .model { font-size: 11px; color: var(--mauve); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .meta .date  { font-size: 10px; color: var(--overlay0); }
  /* Privacy blur (opt-in toggle): blur every thumbnail until hover. Useful on
     LAN / mobile / over-the-shoulder. NSFW-flagged cards (data-nsfw="1") blur
     more heavily when the flag is known. */
  body.privacy-blur .card img { filter: blur(16px); transition: filter .12s; }
  body.privacy-blur .card[data-nsfw="1"] img { filter: blur(28px); }
  body.privacy-blur .card:hover img { filter: none; }
  .card .cb-wrap { position: absolute; top: 6px; left: 6px; }
  .card .cb-wrap input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--lavender); cursor: pointer; }
  .card a.cover { position: absolute; inset: 0; z-index: 1; }
  .card .cb-wrap { z-index: 2; }
  .card .vbadge { position: absolute; top: 6px; right: 6px; z-index: 2; background: rgba(0,0,0,.6); color: #fff; font-size: 11px; line-height: 1; padding: 4px 7px; border-radius: 20px; pointer-events: none; }
  .card .sbadge { position: absolute; top: 6px; left: 30px; z-index: 2; font-size: 10px; font-weight: 600; line-height: 1; padding: 3px 6px; border-radius: 4px; pointer-events: none; letter-spacing: .03em; }
  .card .sbadge.gen { background: var(--mauve, #cba6f7); color: #1e1e2e; }
  .card .sbadge.loc { background: var(--teal, #94e2d5); color: #1e1e2e; }

  /* Pagination */
  .pagination { display: flex; justify-content: center; gap: 6px; padding: 20px; flex-wrap: wrap; }
  .pagination a, .pagination span { padding: 6px 12px; border-radius: 6px; font-size: 13px; text-decoration: none; }
  .pagination a { background: var(--surface0); color: var(--text); border: 1px solid var(--surface1); }
  .pagination a:hover { background: var(--surface1); }
  .pagination span.current { background: var(--lavender); color: var(--base); font-weight: 600; border: 1px solid var(--lavender); }
  .pagination span.ellipsis { color: var(--overlay0); }

  /* Detail */
  .detail-wrap { max-width: 1100px; margin: 0 auto; padding: 20px; }
  .detail-img { text-align: center; margin-bottom: 20px; }
  .detail-img img { max-width: 100%; max-height: 70vh; border-radius: 8px; }
  .detail-meta { background: var(--mantle); border-radius: 8px; padding: 16px; display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px; }
  .detail-meta .lbl { color: var(--subtext); font-size: 12px; text-align: right; padding-top: 2px; }
  .detail-meta .val { color: var(--text); font-size: 13px; word-break: break-word; }
  .detail-meta .val.prompt { font-size: 12px; line-height: 1.6; white-space: pre-wrap; }
  .detail-actions { margin-top: 16px; display: flex; gap: 10px; }
  .focus-btn { font-size: 12px; padding: 3px 10px; cursor: pointer; background: var(--surface0); border: 1px solid var(--surface1); border-radius: 4px; color: var(--text); }
  .focus-btn:hover { background: var(--surface1); }
  .focus-mode .detail-meta,
  .focus-mode .detail-stars,
  .focus-mode .detail-actions { display: none; }
  .focus-mode { max-width: 100% !important; padding: 8px !important; display: flex; flex-direction: column; align-items: center; }
  .focus-mode .detail-nav { width: 100%; max-width: 900px; }
  .focus-mode .detail-img { width: 100%; display: flex; justify-content: center; }
  .focus-mode .detail-img img { max-height: 90vh; max-width: 95vw; width: auto; height: auto; }
  .back-link { display: inline-block; color: var(--blue); text-decoration: none; font-size: 13px; }
  .back-link:hover { text-decoration: underline; }
  .detail-nav { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .nav-arrow { color: var(--blue); text-decoration: none; font-size: 13px; padding: 4px 10px; border: 1px solid var(--surface1); border-radius: 4px; }
  .nav-arrow:hover { background: var(--surface1); text-decoration: none; }
  .nav-disabled { color: var(--overlay0); font-size: 13px; padding: 4px 10px; border: 1px solid var(--surface0); border-radius: 4px; cursor: default; }

  /* Modal */
  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 200; align-items: center; justify-content: center; }
  .modal-bg.open { display: flex; }
  .modal { background: var(--mantle); border: 1px solid var(--surface1); border-radius: 10px; padding: 24px; max-width: 400px; width: 90%; }
  .modal h2 { font-size: 16px; margin-bottom: 10px; color: var(--red); }
  .modal p { color: var(--subtext); font-size: 13px; margin-bottom: 18px; line-height: 1.5; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

  /* Empty */
  .empty { text-align: center; padding: 60px 20px; color: var(--overlay0); }

  /* Stars */
  .stars { display: flex; gap: 2px; }
  .stars button { background: none; border: none; cursor: pointer; font-size: 14px; padding: 0; line-height: 1; color: var(--overlay0); }
  .stars button.on { color: #f9e2af; }
  .stars button:hover { color: #f9e2af; opacity: 0.7; }
  .card .stars { padding: 3px 6px 5px; }
  .detail-stars { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
  .detail-stars .stars button { font-size: 22px; }
  .detail-stars .rating-label { color: var(--subtext); font-size: 12px; }
</style>
<script>
function closeModal() { document.getElementById('del-modal').classList.remove('open'); }
function confirmDelete(url, msg) {
  document.getElementById('del-modal-msg').textContent = msg;
  document.getElementById('del-modal-form').action = url;
  document.getElementById('del-modal').classList.add('open');
}
function setRating(mediaId, value, starsEl) {
  fetch('/rate/' + mediaId, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rating: value})
  }).then(r => r.json()).then(data => {
    if (data.ok) updateStars(starsEl, data.rating);
  });
}
function updateStars(el, rating) {
  el.querySelectorAll('button').forEach(function(btn, i) {
    btn.classList.toggle('on', i < rating);
  });
  var lbl = el.parentElement.querySelector('.rating-label');
  if (lbl) lbl.textContent = rating > 0 ? rating + ' / 5' : 'unrated';
}
function buildStars(mediaId, rating, containerEl) {
  for (var i = 1; i <= 5; i++) {
    (function(star) {
      var btn = document.createElement('button');
      btn.textContent = '★';
      if (star <= rating) btn.classList.add('on');
      btn.addEventListener('click', function(e) {
        e.preventDefault(); e.stopPropagation();
        var newVal = (rating === star) ? 0 : star;
        rating = newVal;
        setRating(mediaId, newVal, containerEl);
      });
      containerEl.appendChild(btn);
    })(i);
  }
}
document.addEventListener('DOMContentLoaded', function() {
  var modal = document.getElementById('del-modal');
  if (modal) modal.addEventListener('click', function(e) { if (e.target === this) closeModal(); });
  document.querySelectorAll('.stars[data-mid]').forEach(function(el) {
    buildStars(el.dataset.mid, parseInt(el.dataset.rating) || 0, el);
  });
});
</script>
</head>
<body>
{% block body %}{% endblock %}

<div class="modal-bg" id="del-modal">
  <div class="modal">
    <h2>Confirm Delete</h2>
    <p id="del-modal-msg">Are you sure?</p>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <form id="del-modal-form" method="post" style="display:inline">
        <button type="submit" class="btn btn-danger">Delete</button>
      </form>
    </div>
  </div>
</div>
</body>
</html>
"""

    INDEX_HTML = BASE_HTML.replace("{% block body %}{% endblock %}", """
{% macro date_select(prefix, value, years) %}
  {% set yr = value[:4] %}{% set mo = value[5:7] %}
  <select name="{{ prefix }}_year" style="width:78px">
    <option value="">Year</option>
    {% for y in years %}
    <option value="{{ y }}" {% if value and y|string == yr %}selected{% endif %}>{{ y }}</option>
    {% endfor %}
  </select>
  <select name="{{ prefix }}_month" style="width:64px">
    <option value="">Mon</option>
    {% for mnum in range(1, 13) %}
    {% set mm = '%02d'|format(mnum) %}
    <option value="{{ mm }}" {% if mm == mo %}selected{% endif %}>{{ mm }}</option>
    {% endfor %}
  </select>
{% endmacro %}
<header>
  <div class="brand"><span class="mark">M</span><h1>Moonglade Athenaeum</h1></div>
  <span class="header-stats">{{ '{:,}'.format(total) }} images</span>
  <a class="back-link" href="{{ url_for('health') }}" style="margin-left:auto;">Collection health →</a>
</header>

<button type="button" class="filter-toggle btn" onclick="toggleFilters()"
        aria-expanded="false">Filters &#9662;</button>
<form method="get" action="/" id="filter-form">
<div class="filters">
  <div>
    <label>Prompt</label><br>
    <input type="text" name="q" value="{{ q }}" placeholder="words, night* wildcard…"
           title="Multiple words are ANDed. Use * (any) and ? (one char), e.g. night* elf">
  </div>
  <div>
    <label>Model</label><br>
    <input type="text" name="model" value="{{ model_filter }}" list="models-list"
           placeholder="All models — type to search" autocomplete="off" style="width:200px">
    <datalist id="models-list">
      {% for m in models %}<option value="{{ m }}">{% endfor %}
    </datalist>
  </div>
  {% if batches %}
  <div>
    <label>Batch</label><br>
    <input type="text" name="batch" value="{{ batch_filter }}" list="batches-list"
           placeholder="All batches — type to search" autocomplete="off" style="width:200px">
    <datalist id="batches-list">
      {% for b in batches %}<option value="{{ b }}">{% endfor %}
    </datalist>
  </div>
  {% endif %}
  <div>
    <label>From</label><br>
    {{ date_select('from', date_from, years) }}
  </div>
  <div>
    <label>To</label><br>
    {{ date_select('to', date_to, years) }}
  </div>
  <div>
    <label>Min rating</label><br>
    <select name="rating_min">
      <option value="0" {% if rating_min==0 %}selected{% endif %}>Any</option>
      {% for r in [1,2,3,4,5] %}
      <option value="{{ r }}" {% if rating_min==r %}selected{% endif %}>{{ '★' * r }}+</option>
      {% endfor %}
    </select>
  </div>
  <div>
    <label>Tag / contest</label><br>
    <input type="text" name="tag" value="{{ art_tag }}" placeholder="published tag…" style="width:140px">
  </div>
  <div>
    <label>LoRA</label><br>
    <input type="text" name="lora" value="{{ lora_filter }}" placeholder="lora name…" style="width:140px">
  </div>
  <div>
    <label>Media</label><br>
    <select name="media">
      <option value="" {% if not media_type %}selected{% endif %}>All</option>
      <option value="image" {% if media_type=='image' %}selected{% endif %}>Images</option>
      <option value="video" {% if media_type=='video' %}selected{% endif %}>Videos</option>
    </select>
  </div>
  <div>
    <label>Source</label><br>
    <select name="source">
      <option value="" {% if not source_filter %}selected{% endif %}>All</option>
      <option value="online" {% if source_filter=='online' %}selected{% endif %}>PixAI history</option>
      <option value="api" {% if source_filter=='api' %}selected{% endif %}>Generated</option>
      <option value="local" {% if source_filter=='local' %}selected{% endif %}>Imported</option>
      <option value="deleted" {% if source_filter=='deleted' %}selected{% endif %}>Deleted on PixAI</option>
    </select>
  </div>
  <div>
    <label>Collection</label><br>
    <select name="collection">
      <option value="">All</option>
      {% for c in collections %}
      <option value="{{ c }}" {% if collection==c %}selected{% endif %}>{{ c }}</option>
      {% endfor %}
    </select>
  </div>
  <div>
    <label>&nbsp;</label><br>
    <label style="color:var(--text);font-size:13px;display:inline-flex;align-items:center;gap:6px;">
      <input type="checkbox" name="published" value="1" {% if published_only %}checked{% endif %}
             style="width:auto;"> Published only
    </label>
  </div>
  <div>
    <label>Per page</label><br>
    <select name="per_page">
      {% for n in per_page_opts %}
      <option value="{{ n }}" {% if n == per_page %}selected{% endif %}>{{ n }}</option>
      {% endfor %}
    </select>
  </div>
  <div>
    <label>Sort</label><br>
    <select name="sort">
      <option value="newest"      {% if sort=='newest' %}selected{% endif %}>Newest first</option>
      <option value="oldest"      {% if sort=='oldest' %}selected{% endif %}>Oldest first</option>
      <option value="rating_desc" {% if sort=='rating_desc' %}selected{% endif %}>Rating ↓</option>
      <option value="rating_asc"  {% if sort=='rating_asc' %}selected{% endif %}>Rating ↑</option>
      <option value="model"       {% if sort=='model' %}selected{% endif %}>Model name</option>
      <option value="pixels"      {% if sort=='pixels' %}selected{% endif %}>Resolution ↓</option>
      <option value="aspect"      {% if sort=='aspect' %}selected{% endif %}>Aspect (wide→tall)</option>
      <option value="aes_desc"    {% if sort=='aes_desc' %}selected{% endif %}>Aesthetic score ↓</option>
      <option value="aes_asc"     {% if sort=='aes_asc' %}selected{% endif %}>Aesthetic score ↑</option>
      <option value="likes"       {% if sort=='likes' %}selected{% endif %}>Most liked</option>
      <option value="width"       {% if sort=='width' %}selected{% endif %}>Width ↓</option>
      <option value="height"      {% if sort=='height' %}selected{% endif %}>Height ↓</option>
    </select>
  </div>
  <div>
    <label>Thumb size</label><br>
    <input type="range" id="thumb-size" min="120" max="320" step="20" value="200"
           title="Thumbnail size" style="width:110px;vertical-align:middle">
  </div>
  <div style="align-self:flex-end">
    <button type="submit" class="btn btn-primary">Filter</button>
    <a href="/" class="btn">Reset</a>
  </div>
</div>
</form>

{% if chips %}
<div class="chips">
  <span class="chips-label">Active:</span>
  {% for c in chips %}
  <span class="chip"><span class="k">{{ c.k }}:</span> {{ c.v }}
    <a href="{{ c.url }}" title="Remove this filter">×</a></span>
  {% endfor %}
  <a class="clear-all" href="{{ url_for('index') }}">Clear all</a>
</div>
{% endif %}

{% if request.args.get('replaced') %}
<div style="margin:8px 20px 0;padding:8px 12px;background:var(--mantle);border-left:3px solid var(--green);border-radius:4px;color:var(--text);font-size:13px;">
  Replaced text in {{ request.args.get('replaced') }} prompt(s).</div>
{% endif %}
{% if request.args.get('collected') %}
<div style="margin:8px 20px 0;padding:8px 12px;background:var(--mantle);border-left:3px solid var(--emerald, #4fc99a);border-radius:4px;color:var(--text);font-size:13px;">
  Added {{ request.args.get('collected') }} image(s) to the collection.</div>
{% endif %}
{% if request.args.get('deleted') %}
<div style="margin:8px 20px 0;padding:8px 12px;background:var(--mantle);border-left:3px solid var(--red);border-radius:4px;color:var(--text);font-size:13px;">
  Deleted {{ request.args.get('deleted') }} task(s) from PixAI · {{ request.args.get('removed') }} local file(s) purged{% if request.args.get('failed') and request.args.get('failed') != '0' %} · <span style="color:var(--red);">{{ request.args.get('failed') }} failed</span>{% endif %}.</div>
{% endif %}
{% if request.args.get('delerr') %}
<div style="margin:8px 20px 0;padding:8px 12px;background:var(--mantle);border-left:3px solid var(--red);border-radius:4px;color:var(--text);font-size:13px;">
  Delete error: {{ request.args.get('delerr') }}</div>
{% endif %}

<div class="bulk-bar">
  <button class="btn" onclick="selectAll()">Select All (page)</button>
  <button class="btn" onclick="clearAll()">Clear</button>
  <button class="btn" id="select-mode-btn" onclick="toggleSelectMode()" title="Select mode: tap an image to toggle it, or drag across images to paint a selection. No lightbox opens, so no accidental opens. Great for touch/tablet.">Select</button>
  <span><span id="sel-count">0</span> selected</span>
  <button class="btn" id="bulk-zip-btn" style="display:none" onclick="downloadZip()">Download ZIP</button>
  <button class="btn" id="bulk-replace-btn" style="display:none" onclick="bulkReplacePrompt()" title="Find/replace text in the prompts of selected images">Find/Replace</button>
  <button class="btn btn-primary" id="bulk-collection-btn" onclick="bulkAddCollection()" title="Check some images/videos first, then click to add them to a named collection (files are not moved)">+ Add to Collection</button>
  <button class="btn" id="blur-btn" onclick="toggleBlur()" title="Privacy blur: blur all thumbnails until you hover">Privacy blur</button>
  <select id="preset-select" onchange="loadPreset(this.value)" style="font-size:13px;"
          title="Saved views"><option value="">Saved views…</option></select>
  <button class="btn" onclick="savePreset()" title="Save current filters as a named view">Save view</button>
  <button class="btn btn-danger" id="bulk-del-btn" style="display:none"
    onclick="confirmBulkDelete()" title="Remove from this local catalog only (keeps the cloud task)">Delete (local)</button>
  <button class="btn btn-danger" id="bulk-del-cloud-btn" style="display:none"
    onclick="confirmBulkDeleteCloud()"
    title="Delete the whole TASK from your PixAI account AND locally (irreversible)">Delete from PixAI</button>
  <span style="margin-left:auto;color:var(--overlay0);font-size:12px;">tip: click an image to open the lightbox · arrow keys to browse · F for slideshow</span>
</div>

<form id="bulk-form" method="post" action="/delete-bulk">
  <input type="hidden" name="back" value="{{ request.url }}">
  <div class="grid">
    {% for row in rows %}
    <div class="card" id="card-{{ row.media_id }}" data-mid="{{ row.media_id }}"
         data-prompt="{{ (row.title or row.prompt_preview or '')|e }}"
         {% if row.is_video == '1' %}data-video="1"{% endif %}
         {% if row.is_nsfw == '1' %}data-nsfw="1"{% endif %}>
      <div class="cb-wrap">
        <input type="checkbox" name="media_ids" value="{{ row.media_id }}"
               onchange="onCheck()" onclick="event.stopPropagation()">
      </div>
      <a class="cover" href="{{ url_for('detail', media_id=row.media_id, back=current_url) }}"
         data-idx="{{ loop.index0 }}" onclick="return openLightbox(event, {{ loop.index0 }})"></a>
      {% if row._has_thumb %}
      <img src="{{ url_for('thumb', media_id=row._thumb_mid) }}" loading="lazy"
           decoding="async" onload="this.classList.add('loaded')"
           alt="{{ row.prompt_preview[:60] }}">
      {% else %}
      <div class="no-thumb">no preview</div>
      {% endif %}
      {% if row.is_video == '1' %}<div class="vbadge" title="Video">▶</div>{% endif %}
      {% if row.source == 'api' %}<div class="sbadge gen" title="Generated via PixAI">AI</div>
      {% elif row.source == 'local' %}<div class="sbadge loc" title="Imported local file">local</div>{% endif %}
      <div class="meta">
        {% if row.title %}<div class="title" title="{{ row.title }}">{{ row.title }}</div>{% endif %}
        <div class="model">{{ row.model_name or row.model_id or '—' }}</div>
        <div class="date">{{ row.created_at[:10] if row.created_at else '' }}{% if row.liked_count and row.liked_count not in ('0','') %} · ♥ {{ row.liked_count }}{% endif %}</div>
      </div>
      <div class="stars" id="stars-{{ row.media_id }}"
           data-mid="{{ row.media_id }}" data-rating="{{ row.rating or 0 }}"></div>
    </div>
    {% endfor %}
  </div>
</form>

<div id="lightbox" class="lb" onclick="if(event.target===this)closeLightbox()">
  <div class="lb-bar">
    <span id="lb-caption"></span>
    <span class="lb-actions">
      <a id="lb-details" class="btn" href="#">Details</a>
      <button class="btn" id="lb-play" onclick="toggleSlideshow()">▶ Slideshow</button>
      <button class="btn" onclick="closeLightbox()">✕ Close</button>
    </span>
  </div>
  <button class="lb-nav lb-prev" onclick="lbStep(-1)" aria-label="Previous">&#8249;</button>
  <img id="lb-img" alt="">
  <video id="lb-video" controls loop playsinline style="display:none"></video>
  <button class="lb-nav lb-next" onclick="lbStep(1)" aria-label="Next">&#8250;</button>
</div>

{% if not rows %}
<div class="empty">
  <div class="big">⌕</div>
  <div>No images match your filters.</div>
  {% if chips %}<div style="margin-top:8px;font-size:13px;">Try <a href="{{ url_for('index') }}">clearing all filters</a> or widening the date range.</div>{% endif %}
</div>
{% endif %}

<div class="pagination">
  {% if page > 1 %}
  <a href="{{ page_url(1) }}">« First</a>
  <a id="pg-prev" href="{{ page_url(page - 1) }}">‹ Prev</a>
  {% endif %}
  {% for p in page_range %}
    {% if p == '…' %}
    <span class="ellipsis">…</span>
    {% elif p == page %}
    <span class="current">{{ p }}</span>
    {% else %}
    <a href="{{ page_url(p) }}">{{ p }}</a>
    {% endif %}
  {% endfor %}
  {% if page < total_pages %}
  <a id="pg-next" href="{{ page_url(page + 1) }}">Next ›</a>
  <a href="{{ page_url(total_pages) }}">Last »</a>
  {% endif %}
</div>

<script>
function toggleFilters() {
  var f = document.querySelector('.filters');
  var btn = document.querySelector('.filter-toggle');
  var open = f.classList.toggle('open');
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}
function applyBlur() {
  var on = localStorage.getItem('gallery_privacy_blur') === '1';
  document.body.classList.toggle('privacy-blur', on);
  var b = document.getElementById('blur-btn');
  if (b) b.textContent = on ? 'Unblur' : 'Privacy blur';
}
function toggleBlur() {
  var on = localStorage.getItem('gallery_privacy_blur') === '1';
  localStorage.setItem('gallery_privacy_blur', on ? '' : '1');
  applyBlur();
}
function presetsGet() { try { return JSON.parse(localStorage.getItem('gallery_presets') || '{}'); } catch(e) { return {}; } }
function refreshPresets() {
  var s = document.getElementById('preset-select'); if (!s) return;
  var p = presetsGet();
  s.innerHTML = '<option value="">Saved views…</option>';
  Object.keys(p).forEach(function(n){ var o = document.createElement('option'); o.value = n; o.textContent = n; s.appendChild(o); });
}
function savePreset() {
  var n = prompt('Name this view (the current filters):'); if (!n) return;
  var p = presetsGet(); p[n] = location.search || '?';
  localStorage.setItem('gallery_presets', JSON.stringify(p)); refreshPresets();
}
function loadPreset(n) {
  if (!n) return;
  if (n.charAt(0) === '✕') { // not used; reserved
    return;
  }
  var p = presetsGet();
  if (p[n] !== undefined) location.href = '/' + p[n];
}
(function(){
  // On mobile, auto-open the filter bar if any filter is active so the user sees
  // what's applied; otherwise keep it collapsed to give the grid the screen.
  if (window.matchMedia('(max-width: 680px)').matches &&
      document.querySelector('.chips')) {
    var f = document.querySelector('.filters');
    if (f) f.classList.add('open');
  }
  var grid = document.querySelector('.grid');
  var slider = document.getElementById('thumb-size');
  if (grid && slider) {
    var saved = localStorage.getItem('gallery_thumb');
    if (saved) { slider.value = saved; grid.style.setProperty('--thumb', saved + 'px'); }
    slider.addEventListener('input', function(){
      grid.style.setProperty('--thumb', slider.value + 'px');
      localStorage.setItem('gallery_thumb', slider.value);
    });
  }
})();
/* ---- Cross-page selection (persisted in localStorage) ---- */
function selGet() { try { return new Set(JSON.parse(localStorage.getItem('gallery_sel') || '[]')); } catch(e) { return new Set(); } }
function selSave(s) { localStorage.setItem('gallery_sel', JSON.stringify([...s])); }
function refreshSelUI() {
  var sel = selGet();
  document.querySelectorAll('input[name=media_ids]').forEach(function(cb){
    var on = sel.has(cb.value);
    cb.checked = on;
    cb.closest('.card').classList.toggle('selected', on);
  });
  document.getElementById('sel-count').textContent = sel.size;
  document.getElementById('bulk-del-btn').style.display = sel.size ? 'inline-block' : 'none';
  document.getElementById('bulk-zip-btn').style.display = sel.size ? 'inline-block' : 'none';
  var rb = document.getElementById('bulk-replace-btn');
  if (rb) rb.style.display = sel.size ? 'inline-block' : 'none';
  var cb = document.getElementById('bulk-del-cloud-btn');
  if (cb) cb.style.display = sel.size ? 'inline-block' : 'none';
}
function onCheck() {
  var sel = selGet();
  document.querySelectorAll('input[name=media_ids]').forEach(function(cb){
    if (cb.checked) sel.add(cb.value); else sel.delete(cb.value);
  });
  selSave(sel); refreshSelUI();
}
function selectAll() {
  var sel = selGet();
  document.querySelectorAll('input[name=media_ids]').forEach(function(cb){ sel.add(cb.value); });
  selSave(sel); refreshSelUI();
}
function clearAll() { selSave(new Set()); refreshSelUI(); }
/* ---- Select mode + drag-paint multi-select (mouse + touch via Pointer Events) ---- */
var selectMode = localStorage.getItem('gallery_selmode') === '1';
function applySelectMode() {
  document.body.classList.toggle('select-mode', selectMode);
  var b = document.getElementById('select-mode-btn');
  if (b) { b.classList.toggle('active', selectMode); b.textContent = selectMode ? 'Select: ON' : 'Select'; }
}
function toggleSelectMode() {
  selectMode = !selectMode;
  localStorage.setItem('gallery_selmode', selectMode ? '1' : '');
  applySelectMode();
}
(function() {
  var painting = false, paintVal = true, paintSet = null, lastCard = null;
  function cardAt(x, y) { var el = document.elementFromPoint(x, y); return el ? el.closest('.card') : null; }
  function paint(card) {
    if (!card || card === lastCard || !paintSet) return;
    lastCard = card;
    var mid = card.dataset.mid;
    if (paintVal) paintSet.add(mid); else paintSet.delete(mid);
    card.classList.toggle('selected', paintVal);
    var cb = card.querySelector('input[name=media_ids]'); if (cb) cb.checked = paintVal;
    var c = document.getElementById('sel-count'); if (c) c.textContent = paintSet.size;
  }
  document.addEventListener('pointerdown', function(e) {
    if (!selectMode || !e.target.closest) return;
    var card = e.target.closest('.card');
    if (!card || e.target.closest('.cb-wrap')) return;   // empty space scrolls; checkbox handles itself
    painting = true; lastCard = null;
    paintSet = selGet();
    paintVal = !paintSet.has(card.dataset.mid);           // first card sets paint direction
    paint(card);
    e.preventDefault();
  });
  document.addEventListener('pointermove', function(e) {
    if (!painting) return;
    paint(cardAt(e.clientX, e.clientY));
    e.preventDefault();
  });
  function endPaint() {
    if (!painting) return;
    painting = false;
    if (paintSet) { selSave(paintSet); refreshSelUI(); }
    paintSet = null; lastCard = null;
  }
  document.addEventListener('pointerup', endPaint);
  document.addEventListener('pointercancel', endPaint);
  // Swallow the click so the lightbox / detail link never fires in select mode (images and videos).
  document.addEventListener('click', function(e) {
    if (!selectMode || !e.target.closest) return;
    var card = e.target.closest('.card');
    if (!card || e.target.closest('.cb-wrap')) return;
    e.preventDefault(); e.stopPropagation();
  }, true);
})();
function downloadZip() {
  var sel = [...selGet()];
  if (!sel.length) return;
  var f = document.createElement('form');
  f.method = 'post'; f.action = '/export-zip';
  sel.forEach(function(mid){
    var i = document.createElement('input');
    i.type = 'hidden'; i.name = 'media_ids'; i.value = mid; f.appendChild(i);
  });
  document.body.appendChild(f); f.submit(); f.remove();
}
function bulkReplacePrompt() {
  var sel = [...selGet()];
  if (!sel.length) return;
  var find = prompt('Find this text in the prompts of ' + sel.length + ' selected image(s):');
  if (find === null || find === '') return;
  var repl = prompt('Replace "' + find + '" with: (leave blank to delete it)');
  if (repl === null) return;
  if (!confirm('Replace "' + find + '" with "' + repl + '" across ' + sel.length + ' prompt(s)? This edits catalog.db.')) return;
  var f = document.createElement('form');
  f.method = 'post'; f.action = '/bulk-replace-prompt';
  function add(n, v){ var i=document.createElement('input'); i.type='hidden'; i.name=n; i.value=v; f.appendChild(i); }
  add('back', location.href); add('find', find); add('replace', repl);
  sel.forEach(function(mid){ add('media_ids', mid); });
  document.body.appendChild(f); f.submit();
}
function bulkAddCollection() {
  var sel = [...selGet()];
  if (!sel.length) { alert('Select one or more images/videos first (check the boxes, or "Select All (page)"), then click + Add to Collection.'); return; }
  var name = prompt('Add ' + sel.length + ' image(s) to which collection? (a name; files are NOT moved)');
  if (name === null || !name.trim()) return;
  var f = document.createElement('form');
  f.method = 'post'; f.action = '/collection-add';
  function add(n, v){ var i=document.createElement('input'); i.type='hidden'; i.name=n; i.value=v; f.appendChild(i); }
  add('back', location.href); add('name', name.trim());
  sel.forEach(function(mid){ add('media_ids', mid); });
  document.body.appendChild(f); f.submit();
}
function confirmBulkDelete() {
  var ids = [...selGet()];
  if (!ids.length) return;
  confirmDelete('/delete-bulk', 'Permanently delete ' + ids.length + ' image' + (ids.length !== 1 ? 's' : '') + '? This cannot be undone.');
  document.getElementById('del-modal-form').onsubmit = function() {
    var bf = document.getElementById('bulk-form');
    // ensure all cross-page selections are submitted, not just this page's
    ids.forEach(function(mid){
      if (!bf.querySelector('input[name=media_ids][value="' + mid + '"]')) {
        var i = document.createElement('input');
        i.type = 'hidden'; i.name = 'media_ids'; i.value = mid; bf.appendChild(i);
      }
    });
    localStorage.removeItem('gallery_sel');
    bf.submit(); return false;
  };
  document.getElementById('del-modal-form').action = '#';
}
function confirmBulkDeleteCloud() {
  var ids = [...selGet()];
  if (!ids.length) return;
  if (!confirm('Delete ' + ids.length + ' selected image(s) from your PixAI account AND locally?\\n\\n'
    + '⚠ This deletes the whole TASK for each selection (all images in a batch), '
    + 'from the cloud AND your backup. It is IRREVERSIBLE.')) return;
  var typed = prompt('This permanently deletes from PixAI. Type DELETE to confirm:');
  if (typed !== 'DELETE') { alert('Cancelled.'); return; }
  var f = document.createElement('form');
  f.method = 'post'; f.action = '/delete-tasks-bulk';
  function add(n, v){ var i=document.createElement('input'); i.type='hidden'; i.name=n; i.value=v; f.appendChild(i); }
  add('back', location.href);
  ids.forEach(function(mid){ add('media_ids', mid); });
  localStorage.removeItem('gallery_sel');
  document.body.appendChild(f); f.submit();
}

/* ---- Lightbox + keyboard navigation ---- */
var lbCards = [], lbIdx = -1, lbTimer = null, lbZoom = false;
function lbUrl(mid) { return '/full/' + mid; }
function openLightbox(ev, idx) {
  if (ev) { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return true; ev.preventDefault(); }
  lbCards = Array.from(document.querySelectorAll('.card'));
  if (!lbCards.length) return false;
  lbIdx = idx;
  lbShow();
  document.getElementById('lightbox').classList.add('open');
  return false;
}
function lbShow() {
  var card = lbCards[lbIdx]; if (!card) return;
  var mid = card.dataset.mid;
  var im = document.getElementById('lb-img');
  var vid = document.getElementById('lb-video');
  lbZoom = false; im.style.transform = '';      // reset zoom on navigate
  if (card.dataset.video === '1') {
    im.style.display = 'none'; im.removeAttribute('src');
    vid.style.display = '';
    vid.src = '/video-file/' + mid;
    vid.currentTime = 0;
    vid.play().catch(function(){});            // autoplay may be blocked; controls still work
  } else {
    vid.pause(); vid.removeAttribute('src'); vid.load(); vid.style.display = 'none';
    im.style.display = '';
    im.src = lbUrl(mid);
  }
  document.getElementById('lb-caption').textContent =
    (lbIdx + 1) + ' / ' + lbCards.length + '   ' + (card.dataset.prompt || '');
  document.getElementById('lb-details').href = '/image/' + mid + '?back=' + encodeURIComponent(location.href);
}
function lbNavUrl(href, where) {
  return href + (href.indexOf('?') >= 0 ? '&' : '?') + 'lbopen=' + where;
}
function lbStep(d) {
  if (!lbCards.length) return;
  var ni = lbIdx + d;
  if (ni >= lbCards.length) {           // past the last card -> next page (open at its first)
    var nx = document.getElementById('pg-next');
    if (nx) { saveScrollPos(); location.href = lbNavUrl(nx.href, 'first'); return; }
    ni = 0;                              // last page: wrap within the page
  } else if (ni < 0) {                   // before the first card -> previous page (open at its last)
    var pv = document.getElementById('pg-prev');
    if (pv) { saveScrollPos(); location.href = lbNavUrl(pv.href, 'last'); return; }
    ni = lbCards.length - 1;             // first page: wrap within the page
  }
  lbIdx = ni; lbShow();
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  var vid = document.getElementById('lb-video');
  if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
  stopSlideshow();
}
function toggleSlideshow() { lbTimer ? stopSlideshow() : startSlideshow(); }
function startSlideshow() {
  lbTimer = setInterval(function(){ lbStep(1); }, 3000);
  document.getElementById('lb-play').textContent = '❚❚ Pause';
}
function stopSlideshow() {
  if (lbTimer) { clearInterval(lbTimer); lbTimer = null; }
  var b = document.getElementById('lb-play'); if (b) b.textContent = '▶ Slideshow';
}
var kbdIdx = -1;
function kbdFocus(i) {
  var cards = document.querySelectorAll('.card'); if (!cards.length) return;
  if (kbdIdx >= 0 && cards[kbdIdx]) cards[kbdIdx].classList.remove('kbd-focus');
  kbdIdx = Math.max(0, Math.min(i, cards.length - 1));
  cards[kbdIdx].classList.add('kbd-focus');
  cards[kbdIdx].scrollIntoView({block: 'nearest'});
}
function gridCols() {
  var g = document.querySelector('.grid'); if (!g) return 1;
  return getComputedStyle(g).gridTemplateColumns.split(' ').length;
}
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  var open = document.getElementById('lightbox').classList.contains('open');
  if (open) {
    if (e.key === 'ArrowRight') { lbStep(1); }
    else if (e.key === 'ArrowLeft') { lbStep(-1); }
    else if (e.key === 'Escape') { closeLightbox(); }
    else if (e.key === 'f' || e.key === 'F' || e.key === ' ') { e.preventDefault(); toggleSlideshow(); }
    return;
  }
  var cols = gridCols();
  if (e.key === 'ArrowRight') { kbdFocus(kbdIdx + 1); }
  else if (e.key === 'ArrowLeft') { kbdFocus(kbdIdx - 1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); kbdFocus(kbdIdx < 0 ? 0 : kbdIdx + cols); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); kbdFocus(kbdIdx - cols); }
  else if (e.key === 'Enter' && kbdIdx >= 0) { openLightbox(null, kbdIdx); }
});
/* ---- Preserve scroll + re-sync selection across back / detail navigation ---- */
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
function _scrollKey(){ return 'scroll:' + location.pathname + location.search; }
function saveScrollPos(){ try { sessionStorage.setItem(_scrollKey(), String(window.scrollY)); } catch(e){} }
function restoreScrollPos(){
  try {
    var y = sessionStorage.getItem(_scrollKey());
    if (y === null) return;
    var n = parseInt(y, 10) || 0;
    window.scrollTo(0, n);
    // thumbnails can shift layout as they load; re-apply once everything's in
    window.addEventListener('load', function(){ window.scrollTo(0, n); }, {once:true});
  } catch(e){}
}
window.addEventListener('beforeunload', saveScrollPos);
window.addEventListener('pagehide', saveScrollPos);
// pageshow fires on back/forward-cache restores (DOMContentLoaded does not),
// so this is what actually re-checks the boxes after a browser Back.
window.addEventListener('pageshow', function(){ refreshSelUI(); restoreScrollPos(); });
document.addEventListener('DOMContentLoaded', function(){
  refreshSelUI(); applyBlur(); refreshPresets(); restoreScrollPos(); applySelectMode();
  // Keep the bulk action bar stuck just below the sticky header (header height varies).
  (function() {
    var h = document.querySelector('header');
    function setTop() { if (h) document.documentElement.style.setProperty('--bulk-top', h.offsetHeight + 'px'); }
    setTop(); window.addEventListener('resize', setTop);
  })();
  // Cross-page lightbox: arriving with ?lbopen=first|last auto-opens the overlay so
  // arrow-key browsing rolls over page boundaries seamlessly.
  (function() {
    var m = location.search.match(/[?&]lbopen=(first|last)/);
    if (!m) return;
    var cards = document.querySelectorAll('.card');
    if (!cards.length) return;
    try { var u = new URL(location.href); u.searchParams.delete('lbopen'); history.replaceState(null, '', u); } catch(e) {}
    openLightbox(null, m[1] === 'last' ? cards.length - 1 : 0);
  })();
  // Lightbox touch: swipe left/right to navigate, double-tap to zoom 2x.
  var im = document.getElementById('lb-img');
  if (im) {
    var x0 = null, lastTap = 0;
    im.addEventListener('touchstart', function(e){
      if (e.touches.length === 1) x0 = e.touches[0].clientX;
    }, {passive: true});
    im.addEventListener('touchend', function(e){
      var now = Date.now();
      if (now - lastTap < 300) {            // double-tap zoom
        lbZoom = !lbZoom;
        im.style.transform = lbZoom ? 'scale(2)' : '';
        lastTap = 0; x0 = null; return;
      }
      lastTap = now;
      if (x0 !== null && !lbZoom) {         // horizontal swipe = navigate
        var dx = e.changedTouches[0].clientX - x0;
        if (Math.abs(dx) > 50) lbStep(dx < 0 ? 1 : -1);
      }
      x0 = null;
    }, {passive: true});
  }
});
</script>
""")

    DETAIL_HTML = BASE_HTML.replace("{% block body %}{% endblock %}", """
<script>
function toggleFocus() {
  var wrap = document.querySelector('.detail-wrap');
  var on = wrap.classList.toggle('focus-mode');
  localStorage.setItem('gallery_focus', on ? '1' : '');
  document.getElementById('focus-btn').textContent = on ? 'Details' : 'Focus';
}
document.addEventListener('DOMContentLoaded', function() {
  if (localStorage.getItem('gallery_focus')) {
    document.querySelector('.detail-wrap').classList.add('focus-mode');
    document.getElementById('focus-btn').textContent = 'Details';
  }
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft' || e.keyCode === 37) {
      var el = document.getElementById('nav-prev');
      if (el) window.location.href = el.href;
    } else if (e.key === 'ArrowRight' || e.keyCode === 39) {
      var el = document.getElementById('nav-next');
      if (el) window.location.href = el.href;
    } else if (e.key === 'Escape' || e.keyCode === 27 || e.key === 'ArrowUp' || e.keyCode === 38) {
      e.preventDefault();
      var g = document.getElementById('nav-gallery');
      if (g) window.location.href = g.href;
    } else if (e.key === 'f' || e.key === 'F') {
      toggleFocus();
    }
  });
});
</script>
<div class="detail-wrap">
  <div class="detail-nav">
    {% if prev_id %}
    <a id="nav-prev" class="nav-arrow" href="{{ url_for('detail', media_id=prev_id, back=back) }}" title="Previous (← arrow key)">&#8592; Prev</a>
    {% else %}
    <span class="nav-arrow nav-disabled">&#8592; Prev</span>
    {% endif %}
    <a id="nav-gallery" class="back-link" href="{{ back }}" title="Back to gallery (Esc or ↑ arrow)">↑ Gallery</a>
    <button id="focus-btn" class="focus-btn" onclick="toggleFocus()" title="Toggle focus mode (F key)">Focus</button>
    {% if next_id %}
    <a id="nav-next" class="nav-arrow" href="{{ url_for('detail', media_id=next_id, back=back) }}" title="Next (→ arrow key)">Next &#8594;</a>
    {% else %}
    <span class="nav-arrow nav-disabled">Next &#8594;</span>
    {% endif %}
  </div>

  <div class="detail-img">
    {% if row.is_video == '1' %}
    <video controls autoplay loop playsinline preload="metadata"
           style="max-width:100%;border-radius:8px;background:#000"
           {% if poster_url %}poster="{{ poster_url }}"{% endif %}>
      <source src="{{ url_for('video_file', media_id=row.media_id) }}" type="video/mp4">
      Your browser can't play this video. <a href="{{ url_for('video_file', media_id=row.media_id) }}">Download it</a>.
    </video>
    {% elif img_url %}
    <a href="{{ img_url }}" target="_blank" title="Click to open full resolution">
      <img src="{{ img_url }}" alt="{{ row.prompt_preview }}">
    </a>
    {% else %}
    <div style="color:var(--overlay0);padding:40px">Image file not found on disk.</div>
    {% endif %}
  </div>

  <div class="detail-meta">
    {% if row.prompt_full %}
    <span class="lbl">Full Prompt</span>
    <span class="val prompt">{{ row.prompt_full }}</span>
    {% endif %}
    {% if row.natural_prompt %}
    <span class="lbl">Natural Prompt</span>
    <span class="val prompt">{{ row.natural_prompt }}</span>
    {% endif %}
    {% if row.negative_prompt %}
    <span class="lbl">Negative Prompt</span>
    <span class="val prompt">{{ row.negative_prompt }}</span>
    {% endif %}
    <span class="lbl">Prompt Preview</span>
    <span class="val">{{ row.prompt_preview }}</span>
    <span class="lbl">Model</span>
    <span class="val">{{ row.model_name or row.model_id or '—' }}</span>
    {% if row.loras %}
    <span class="lbl">LoRAs</span>
    <span class="val">{{ row.loras }}</span>
    {% endif %}
    {% if row.title %}
    <span class="lbl">Title</span>
    <span class="val">{{ row.title }}</span>
    {% endif %}
    {% if row.art_tags %}
    <span class="lbl">Tags</span>
    <span class="val">{{ row.art_tags }}</span>
    {% endif %}
    {% if row.collections %}
    <span class="lbl">Collections</span>
    <span class="val">{{ row.collections.replace(',', ', ') }}</span>
    {% endif %}
    <span class="lbl">Seed</span>
    <span class="val">{{ row.seed or '—' }}</span>
    <span class="lbl">Steps</span>
    <span class="val">{{ row.steps or '—' }}</span>
    <span class="lbl">Sampler</span>
    <span class="val">{{ row.sampler or '—' }}</span>
    <span class="lbl">CFG Scale</span>
    <span class="val">{{ row.cfg_scale or '—' }}</span>
    {% if row.clip_skip %}
    <span class="lbl">Clip Skip</span>
    <span class="val">{{ row.clip_skip }}</span>
    {% endif %}
    <span class="lbl">Dimensions</span>
    <span class="val">{{ row.width }}×{{ row.height }}</span>
    <span class="lbl">Date</span>
    <span class="val">{{ row.created_at[:10] if row.created_at else '—' }}</span>
    <span class="lbl">Task ID</span>
    <span class="val" style="font-size:11px;color:var(--overlay0)">{{ row.task_id }}</span>
    <span class="lbl">Media ID</span>
    <span class="val" style="font-size:11px;color:var(--overlay0)">{{ row.media_id }}</span>
    <span class="lbl">Filename</span>
    <span class="val" style="font-size:11px;color:var(--overlay0)">{{ row.filename }}</span>
  </div>

  <div class="detail-stars">
    <div class="stars" id="detail-stars"
         data-mid="{{ row.media_id }}" data-rating="{{ row.rating or 0 }}"></div>
    <span class="rating-label">{{ row.rating + ' / 5' if row.rating else 'unrated' }}</span>
  </div>

  <div class="detail-actions">
    {% if img_url %}
    <a class="btn" href="{{ img_url }}" target="_blank">Open Full Size (local)</a>
    {% endif %}
    {% if row.url %}
    <a class="btn" href="{{ row.url }}" target="_blank">Open on PixAI CDN</a>
    {% endif %}
    {% set _prompt = row.prompt_full or row.prompt_preview or '' %}
    {% if _prompt %}
    <button class="btn" id="copy-prompt-btn"
      data-prompt="{{ _prompt|e }}" onclick="copyPrompt(this)">Copy Prompt</button>
    {% endif %}
    {% if row.model_name %}
    <a class="btn" href="{{ url_for('index', model=row.model_name) }}"
       title="Show all images from this model">Find Similar (model)</a>
    {% endif %}
    {% if row.batch %}
    <a class="btn" href="{{ url_for('index', batch=row.batch) }}"
       title="Show the rest of this batch">View Batch</a>
    {% endif %}
    <button class="btn" id="edit-prompt-btn" onclick="toggleEdit()">Edit Prompt</button>
    <button class="btn btn-danger"
      onclick="confirmDelete('{{ url_for('delete_one', media_id=row.media_id) }}?back={{ back|urlencode }}',
        'Permanently delete this image? This cannot be undone.')">
      Delete
    </button>
  </div>
  <div id="prompt-editor" style="display:none;margin-top:12px;">
    <textarea id="prompt-text" style="width:100%;min-height:120px;background:var(--surface0);color:var(--text);border:1px solid var(--surface1);border-radius:6px;padding:8px;font-size:13px;font-family:var(--font-mono,monospace);">{{ row.prompt_full or row.prompt_preview or '' }}</textarea>
    <div style="margin-top:8px;display:flex;gap:8px;align-items:center;">
      <button class="btn btn-primary" onclick="savePrompt()">Save</button>
      <button class="btn" onclick="toggleEdit()">Cancel</button>
      <span id="save-status" style="color:var(--green);font-size:12px;"></span>
    </div>
  </div>
</div>
<script>
function copyPrompt(btn) {
  var text = btn.getAttribute('data-prompt');
  navigator.clipboard.writeText(text).then(function(){
    var old = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(function(){ btn.textContent = old; }, 1200);
  });
}
function toggleEdit() {
  var e = document.getElementById('prompt-editor');
  e.style.display = e.style.display === 'none' ? 'block' : 'none';
}
function savePrompt() {
  var text = document.getElementById('prompt-text').value;
  fetch('/edit-prompt/{{ row.media_id }}', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({prompt: text})})
   .then(function(r){ return r.json(); })
   .then(function(d){
     var s = document.getElementById('save-status');
     s.textContent = d.ok ? 'Saved ✓' : 'Error';
     var cp = document.getElementById('copy-prompt-btn');
     if (cp) cp.setAttribute('data-prompt', text);
   });
}
</script>
""")

    HEALTH_HTML = BASE_HTML.replace("{% block body %}{% endblock %}", """
{% macro stat(label, value, flag='') %}
<div style="background:var(--mantle);border-radius:8px;padding:14px 16px;">
  <div style="font-size:12px;color:var(--subtext);margin-bottom:6px;">{{ label }}</div>
  <div style="font-size:22px;font-weight:500;color:{{ '#e2a04a' if flag=='warn' else ('#e25555' if flag=='bad' else 'var(--text)') }};">{{ value }}</div>
</div>
{% endmacro %}
<header>
  <div class="brand"><span class="mark">M</span><h1>Collection Health</h1></div>
  <a class="back-link" href="{{ url_for('index') }}" style="margin-left:auto;">↑ Back to gallery</a>
</header>

<div style="padding:8px 20px 24px;max-width:1100px;">
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;">
    {{ stat('Images on disk', '{:,}'.format(h.total_files)) }}
    {{ stat('Storage used', h.total_size_h) }}
    {{ stat('Catalog rows', '{:,}'.format(h.catalog_rows)) }}
    {{ stat('Full-meta', h.full_meta_pct|string + '%') }}
    {{ stat('Rated', '{:,}'.format(h.rated)) }}
    {{ stat('Published', '{:,}'.format(h.published)) }}
    {{ stat('Total likes', '{:,}'.format(h.total_likes)) }}
    {{ stat('Duplicates', '{:,}'.format(h.dup_redundant), 'warn' if h.dup_redundant else '') }}
    {{ stat('Reclaimable', h.dup_bytes_h, 'warn' if h.dup_bytes else '') }}
    {{ stat('Missing files', '{:,}'.format(h.missing), 'bad' if h.missing else '') }}
  </div>

  <h2 style="margin:28px 0 10px;font-size:16px;">Images by month</h2>
  <div style="display:flex;flex-direction:column;gap:4px;">
    {% set maxm = (h.by_month|map(attribute=1)|max) if h.by_month else 1 %}
    {% for m, c in h.by_month %}
    <div style="display:flex;align-items:center;gap:10px;font-size:12px;">
      <span style="width:64px;color:var(--subtext);">{{ m }}</span>
      <div style="flex:1;background:var(--mantle);border-radius:4px;overflow:hidden;height:16px;">
        <div style="height:100%;width:{{ (100*c/maxm)|round(1) }}%;background:var(--accent);"></div>
      </div>
      <span style="width:52px;text-align:right;color:var(--subtext);">{{ '{:,}'.format(c) }}</span>
    </div>
    {% endfor %}
  </div>

  <h2 style="margin:28px 0 10px;font-size:16px;">Top models</h2>
  <div style="display:flex;flex-direction:column;gap:4px;">
    {% set maxt = (h.top_models|map(attribute=1)|max) if h.top_models else 1 %}
    {% for name, c in h.top_models %}
    <div style="display:flex;align-items:center;gap:10px;font-size:12px;">
      <span style="width:180px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
            title="{{ name }}">{{ name }}</span>
      <div style="flex:1;background:var(--mantle);border-radius:4px;overflow:hidden;height:16px;">
        <div style="height:100%;width:{{ (100*c/maxt)|round(1) }}%;background:var(--accent-soft);"></div>
      </div>
      <a style="width:52px;text-align:right;color:var(--subtext);"
         href="{{ url_for('index', model=name) }}">{{ '{:,}'.format(c) }}</a>
    </div>
    {% endfor %}
  </div>

  {% if h.top_tags %}
  <h2 style="margin:28px 0 10px;font-size:16px;">Top tags &amp; contests</h2>
  <div style="display:flex;flex-wrap:wrap;gap:8px;">
    {% for name, c in h.top_tags %}
    <a href="{{ url_for('index', tag=name) }}"
       style="display:inline-flex;align-items:center;gap:6px;background:var(--mantle);border:0.5px solid var(--surface1);border-left:3px solid var(--gold);border-radius:4px;padding:4px 10px;font-size:12px;color:var(--text);text-decoration:none;">
      {{ name }} <span style="color:var(--subtext);">{{ c }}</span></a>
    {% endfor %}
  </div>
  {% endif %}

  {% if h.top_words %}
  <h2 style="margin:28px 0 10px;font-size:16px;">Prompt word cloud</h2>
  {% set wmax = h.top_words[0][1] %}
  <div style="display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;line-height:1.6;">
    {% for word, c in h.top_words %}
    <a href="{{ url_for('index', q=word) }}" title="{{ c }} images"
       style="text-decoration:none;color:var(--lavender);font-size:{{ (12 + (16 * c / wmax))|round|int }}px;">{{ word }}</a>
    {% endfor %}
  </div>
  {% endif %}

  {% if h.top_loras %}
  <h2 style="margin:28px 0 10px;font-size:16px;">Top LoRAs</h2>
  <div style="display:flex;flex-wrap:wrap;gap:8px;">
    {% for name, c in h.top_loras %}
    <a href="{{ url_for('index', lora=name) }}"
       style="display:inline-flex;align-items:center;gap:6px;background:var(--mantle);border:0.5px solid var(--surface1);border-left:3px solid var(--accent-soft);border-radius:4px;padding:4px 10px;font-size:12px;color:var(--text);text-decoration:none;">
      {{ name }} <span style="color:var(--subtext);">{{ c }}</span></a>
    {% endfor %}
  </div>
  {% endif %}

  <h2 style="margin:28px 0 10px;font-size:16px;">Folder breakdown</h2>
  <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--subtext);">
    {% for b, c in h.per_bucket.items() %}
    <span><strong style="color:var(--text);">{{ '{:,}'.format(c) }}</strong> {{ b }}</span>
    {% endfor %}
  </div>

  {% if h.dup_redundant or h.missing %}
  <div style="margin-top:24px;padding:12px 16px;background:var(--mantle);border-radius:8px;font-size:13px;color:var(--subtext);">
    {% if h.dup_redundant %}<div>· {{ '{:,}'.format(h.dup_redundant) }} duplicate copies ({{ h.dup_bytes_h }}). Run <code>--dedup</code> to quarantine.</div>{% endif %}
    {% if h.missing %}<div>· {{ '{:,}'.format(h.missing) }} catalog rows reference a file that's missing on disk. Re-run a download to refetch.</div>{% endif %}
  </div>
  {% endif %}
  {% if h.dup_redundant %}
  <div style="margin-top:14px;"><a class="back-link" href="{{ url_for('duplicates') }}">Review duplicates →</a></div>
  {% endif %}
</div>
""")

    DUPES_HTML = BASE_HTML.replace("{% block body %}{% endblock %}", """
<header>
  <div class="brand"><span class="mark">M</span><h1>Duplicate Review</h1></div>
  <a class="back-link" href="{{ url_for('index') }}" style="margin-left:auto;">↑ Back to gallery</a>
</header>
<div style="padding:10px 20px 28px;max-width:1100px;">
  {% if not groups %}
  <div class="empty"><div class="big">✓</div><div>No duplicate copies found.</div></div>
  {% else %}
  <p style="color:var(--subtext);font-size:13px;">
    {{ groups|length }} media id(s) exist in more than one folder. The
    <span style="color:var(--green);">keeper</span> is the most-organized copy;
    <code>--dedup</code> would quarantine the rest. Review below, then run Dedup from the GUI Utilities tab.
  </p>
  {% for g in groups %}
  <div style="display:flex;gap:14px;align-items:flex-start;background:var(--mantle);border-radius:8px;padding:12px;margin-bottom:10px;">
    <img src="{{ url_for('thumb', media_id=g.media_id) }}" loading="lazy"
         style="width:90px;height:90px;object-fit:cover;border-radius:6px;background:var(--surface0);flex-shrink:0;">
    <div style="flex:1;min-width:0;">
      <div style="font-size:12px;color:var(--subtext);margin-bottom:4px;">media_id {{ g.media_id }}</div>
      {% for c in g.copies %}
      <div style="font-size:12px;font-family:var(--font-mono,monospace);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
                  color:{{ 'var(--green)' if c.rel == g.keeper else 'var(--subtext)' }};">
        {{ 'KEEP  ' if c.rel == g.keeper else 'dupe  ' }}{{ c.rel }}
        <span style="color:var(--overlay0);">({{ c.bucket }})</span>
      </div>
      {% endfor %}
      <a class="back-link" style="font-size:12px;"
         href="{{ url_for('detail', media_id=g.media_id) }}">open →</a>
    </div>
  </div>
  {% endfor %}
  {% endif %}
</div>
""")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _page_range(page, total_pages, window=2):
        pages = []
        for p in range(1, total_pages + 1):
            if p == 1 or p == total_pages or abs(p - page) <= window:
                pages.append(p)
        result = []
        prev = None
        for p in pages:
            if prev and p - prev > 1:
                result.append("…")
            result.append(p)
            prev = p
        return result

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    @app.route("/health")
    def health():
        return render_template_string(
            HEALTH_HTML, h=collection_health(out_dir, db_path))

    @app.route("/duplicates")
    def duplicates():
        return render_template_string(
            DUPES_HTML, groups=duplicate_groups(out_dir))

    @app.route("/")
    def index():
        q            = request.args.get("q", "")
        model_filter = request.args.get("model", "")
        batch_filter = request.args.get("batch", "")
        sort         = request.args.get("sort", "newest")
        page         = int(request.args.get("page", 1))

        # Date filters come from Year+Month dropdowns and assemble into YYYY-MM.
        # A year with no month still filters by year (month defaults to 01/12).
        def _ym(prefix, month_default):
            y = request.args.get(prefix + "_year", "")
            m = request.args.get(prefix + "_month", "")
            if not y:
                return ""
            return "{}-{}".format(y, m or month_default)
        date_from = _ym("from", "01")
        date_to   = _ym("to", "12")

        per_page_opts = [50, 100, 200, 500]
        try:
            per_page = int(request.args.get("per_page", PAGE_SIZE))
        except ValueError:
            per_page = PAGE_SIZE
        if per_page not in per_page_opts:
            per_page = PAGE_SIZE

        try:
            rating_min = max(0, min(5, int(request.args.get("rating_min", 0))))
        except ValueError:
            rating_min = 0
        published_only = request.args.get("published") == "1"
        art_tag = request.args.get("tag", "")
        lora_filter = request.args.get("lora", "")
        media_type = request.args.get("media", "")
        if media_type not in ("image", "video"):
            media_type = ""
        source = request.args.get("source", "")
        if source not in ("online", "api", "local", "deleted"):
            source = ""
        collection = request.args.get("collection", "")

        models  = unique_models(db_path)
        batches = unique_batches(db_path)
        years   = catalog_years(db_path)
        collections = unique_collections(db_path)
        page_rows, total = query_catalog(
            db_path, q, model_filter, date_from, date_to, sort, page, per_page,
            batch=batch_filter, rating_min=rating_min,
            published_only=published_only, art_tag=art_tag, lora=lora_filter,
            media_type=media_type, source=source, collection=collection,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        for r in page_rows:
            mid = r["media_id"]
            tmid = mid
            if r.get("is_video") == "1" and not (thumb_dir / "{}.jpg".format(mid)).exists():
                # fall back to the still-frame poster's thumb if the video's own
                # poster thumb wasn't generated (older sync runs)
                tmid = r.get("poster_media_id") or mid
            r["_thumb_mid"] = tmid
            r["_has_thumb"] = (thumb_dir / "{}.jpg".format(tmid)).exists()

        def page_url(p):
            args = dict(request.args)
            args["page"] = p
            return url_for("index", **args)

        def _without(*keys):
            args = {k: v for k, v in request.args.items() if k not in keys}
            args.pop("page", None)
            return url_for("index", **args)

        # Active-filter chips (label + a URL that removes just that filter).
        chips = []
        if q:
            chips.append({"k": "search", "v": q, "url": _without("q")})
        if model_filter:
            chips.append({"k": "model", "v": model_filter, "url": _without("model")})
        if batch_filter:
            chips.append({"k": "batch", "v": batch_filter, "url": _without("batch")})
        if rating_min:
            chips.append({"k": "rating", "v": "★" * rating_min + "+",
                          "url": _without("rating_min")})
        if date_from:
            chips.append({"k": "from", "v": date_from,
                          "url": _without("from_year", "from_month")})
        if date_to:
            chips.append({"k": "to", "v": date_to,
                          "url": _without("to_year", "to_month")})
        if published_only:
            chips.append({"k": "published", "v": "yes", "url": _without("published")})
        if art_tag:
            chips.append({"k": "tag", "v": art_tag, "url": _without("tag")})
        if lora_filter:
            chips.append({"k": "lora", "v": lora_filter, "url": _without("lora")})
        if media_type:
            chips.append({"k": "media", "v": media_type + "s", "url": _without("media")})
        if source:
            chips.append({"k": "source", "v": source, "url": _without("source")})
        if collection:
            chips.append({"k": "collection", "v": collection, "url": _without("collection")})

        return render_template_string(
            INDEX_HTML,
            chips=chips, published_only=published_only, art_tag=art_tag,
            lora_filter=lora_filter, media_type=media_type, source_filter=source,
            collection=collection, collections=collections,
            rows=page_rows, total=total, page=page,
            total_pages=total_pages, page_range=_page_range(page, total_pages),
            q=q, model_filter=model_filter, batch_filter=batch_filter,
            date_from=date_from,
            date_to=date_to, sort=sort, models=models, batches=batches,
            years=years, per_page=per_page, per_page_opts=per_page_opts,
            rating_min=rating_min,
            page_url=page_url, request=request,
            current_url=request.url,
        )

    @app.route("/image/<media_id>")
    def detail(media_id):
        row = get_row(db_path, media_id)
        if not row:
            return "Image not found.", 404

        img_path = find_image_file(out_dir, media_id, row.get("filename"))
        img_url = None
        if img_path:
            img_url = url_for("serve_image", rel=str(img_path.relative_to(out_dir)).replace("\\", "/"))

        back = request.args.get("back", url_for("index"))

        # Parse filter/sort state from back URL to compute prev/next
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(back)
        qs = parse_qs(parsed.query)
        def _qs1(key, default=""):
            vals = qs.get(key, [])
            return vals[0] if vals else default
        # Reassemble the date filters the same way index() does, so prev/next
        # navigation respects the active Year/Month dropdown filter.
        def _ym(prefix, month_default):
            y = _qs1(prefix + "_year")
            return "{}-{}".format(y, _qs1(prefix + "_month") or month_default) if y else ""
        try:
            _rmin = max(0, min(5, int(_qs1("rating_min", "0"))))
        except ValueError:
            _rmin = 0
        nav_ids = list_media_ids(
            db_path,
            q=_qs1("q"), model=_qs1("model"),
            date_from=_ym("from", "01"), date_to=_ym("to", "12"),
            sort=_qs1("sort", "newest"), batch=_qs1("batch"), rating_min=_rmin,
            published_only=(_qs1("published") == "1"), art_tag=_qs1("tag"),
            lora=_qs1("lora"), media_type=_qs1("media"), source=_qs1("source"),
            collection=_qs1("collection"),
        )
        try:
            idx = nav_ids.index(media_id)
        except ValueError:
            idx = -1
        prev_id = nav_ids[idx - 1] if idx > 0 else None
        next_id = nav_ids[idx + 1] if 0 <= idx < len(nav_ids) - 1 else None

        poster_url = None
        if row.get("is_video") == "1":
            for pmid in (media_id, row.get("poster_media_id")):
                if pmid and (thumb_dir / "{}.jpg".format(pmid)).exists():
                    poster_url = url_for("thumb", media_id=pmid)
                    break

        return render_template_string(
            DETAIL_HTML, row=row, img_url=img_url, back=back,
            prev_id=prev_id, next_id=next_id, poster_url=poster_url,
        )

    @app.route("/delete/<media_id>", methods=["POST"])
    def delete_one(media_id):
        back = request.args.get("back") or url_for("index")
        row = get_row(db_path, media_id)
        if row:
            img_path = find_image_file(out_dir, media_id, row.get("filename"))
            if img_path and img_path.exists():
                img_path.unlink()
            thumb_path = thumb_dir / "{}.jpg".format(media_id)
            if thumb_path.exists():
                thumb_path.unlink()
            delete_from_catalog(db_path, media_id)
        return redirect(back)

    @app.route("/delete-bulk", methods=["POST"])
    def delete_bulk():
        back = request.form.get("back") or url_for("index")
        media_ids = set(request.form.getlist("media_ids"))
        if not media_ids:
            return redirect(back)

        to_delete = {mid: get_row(db_path, mid) for mid in media_ids}
        to_delete = {mid: r for mid, r in to_delete.items() if r}

        for mid, row in to_delete.items():
            img_path = find_image_file(out_dir, mid, row.get("filename"))
            if img_path and img_path.exists():
                img_path.unlink()
            thumb_path = thumb_dir / "{}.jpg".format(mid)
            if thumb_path.exists():
                thumb_path.unlink()
            delete_from_catalog(db_path, mid)

        return redirect(back)

    def _purge_local(media_id, filename):
        """Remove a media's file, thumbnail, and catalog row locally."""
        img = find_image_file(out_dir, media_id, filename)
        if img and img.exists():
            try:
                img.unlink()
            except OSError:
                pass
        tp = thumb_dir / "{}.jpg".format(media_id)
        if tp.exists():
            try:
                tp.unlink()
            except OSError:
                pass
        delete_from_catalog(db_path, media_id)

    @app.route("/delete-tasks-bulk", methods=["POST"])
    def delete_tasks_bulk():
        """Delete the selected images' TASKS from PixAI (irreversible) AND purge
        them locally, so cloud and catalog never drift. Task-level: deleting any
        image deletes its whole task (all batch images), cloud + local. Imports
        with no task id are purged locally only."""
        import urllib.parse
        import pixai_gallery_backup as core   # lazy: avoid import cycle
        back = request.form.get("back") or url_for("index")
        sel = request.form.getlist("media_ids")
        if not sel:
            return redirect(back)

        con = _connect(db_path)
        try:
            sel_rows = [con.execute(
                "SELECT media_id, task_id, filename FROM catalog WHERE media_id=?", (m,)
            ).fetchone() for m in sel]
        finally:
            con.close()
        sel_rows = [dict(r) for r in sel_rows if r]
        task_ids = sorted({(r.get("task_id") or "").strip()
                           for r in sel_rows if (r.get("task_id") or "").strip()})
        local_only = [r for r in sel_rows if not (r.get("task_id") or "").strip()]

        def _err(msg):
            sep = "&" if "?" in back else "?"
            return redirect("{}{}delerr={}".format(back, sep, urllib.parse.quote(msg[:160])))

        deleted = failed = removed = 0
        if task_ids:
            try:
                session = core._make_session(None)
            except core.PixAIError as e:
                return _err(str(e))
            for tid in task_ids:
                try:
                    core.delete_task_gql(session, tid)   # cloud delete (irreversible)
                except Exception:                        # noqa: BLE001
                    failed += 1
                    continue
                deleted += 1
                con = _connect(db_path)
                try:
                    media = con.execute(
                        "SELECT media_id, filename FROM catalog WHERE task_id=?", (tid,)
                    ).fetchall()
                finally:
                    con.close()
                for m in media:
                    _purge_local(m[0], m[1])
                    removed += 1
        for r in local_only:
            _purge_local(r["media_id"], r.get("filename"))
            removed += 1

        sep = "&" if "?" in back else "?"
        return redirect("{}{}deleted={}&failed={}&removed={}".format(
            back, sep, deleted, failed, removed))

    @app.route("/rate/<media_id>", methods=["POST"])
    def rate(media_id):
        data = request.get_json(silent=True) or {}
        try:
            value = max(0, min(5, int(data.get("rating", 0))))
        except (TypeError, ValueError):
            return json.dumps({"ok": False}), 400, {"Content-Type": "application/json"}
        update_rating(db_path, media_id, value)
        return json.dumps({"ok": True, "rating": value}), 200, {"Content-Type": "application/json"}

    @app.route("/edit-prompt/<media_id>", methods=["POST"])
    def edit_prompt(media_id):
        data = request.get_json(silent=True) or {}
        update_prompt_full(db_path, media_id, data.get("prompt", ""))
        return json.dumps({"ok": True}), 200, {"Content-Type": "application/json"}

    @app.route("/collection-add", methods=["POST"])
    def collection_add():
        back = request.form.get("back") or url_for("index")
        ids = request.form.getlist("media_ids")
        name = request.form.get("name", "")
        n = add_to_collection(db_path, ids, name)
        sep = "&" if "?" in back else "?"
        return redirect("{}{}collected={}".format(back, sep, n))

    @app.route("/collection-remove", methods=["POST"])
    def collection_remove():
        back = request.form.get("back") or url_for("index")
        ids = request.form.getlist("media_ids")
        name = request.form.get("name", "")
        remove_from_collection(db_path, ids, name)
        return redirect(back)

    @app.route("/bulk-replace-prompt", methods=["POST"])
    def bulk_replace():
        back = request.form.get("back") or url_for("index")
        ids = request.form.getlist("media_ids")
        find = request.form.get("find", "")
        replace = request.form.get("replace", "")
        n = bulk_replace_prompt(db_path, ids, find, replace)
        # stash a one-shot result in the query string for a small banner
        sep = "&" if "?" in back else "?"
        return redirect("{}{}replaced={}".format(back, sep, n))

    # Thumbnails and full images are content-addressed (keyed by media_id /
    # filename) and never change once written, so we can cache them in the browser
    # essentially forever. This makes pagination, back-navigation, and re-visits
    # instant with zero re-download -- the single biggest win on mobile / LAN.
    _IMMUTABLE = "public, max-age=31536000, immutable"

    @app.route("/thumbs/<media_id>.jpg")
    def thumb(media_id):
        resp = send_from_directory(str(thumb_dir), "{}.jpg".format(media_id),
                                   max_age=31536000)
        resp.headers["Cache-Control"] = _IMMUTABLE
        return resp

    @app.route("/img/<path:rel>")
    def serve_image(rel):
        resp = send_from_directory(str(out_dir), rel, max_age=31536000)
        resp.headers["Cache-Control"] = _IMMUTABLE
        return resp

    @app.route("/video-file/<media_id>")
    def video_file(media_id):
        row = get_row(db_path, media_id)
        if not row or row.get("is_video") != "1" or not row.get("filename"):
            return "Video not found.", 404
        # send_from_directory supports HTTP Range, so the <video> can seek
        resp = send_from_directory(str(out_dir), row["filename"], max_age=31536000)
        resp.headers["Cache-Control"] = _IMMUTABLE
        return resp

    @app.route("/manifest.webmanifest")
    def manifest():
        icon = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                "viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23cba6f7'/%3E"
                "%3Cpath d='M9 22V10h6a4 4 0 0 1 0 8h-3' stroke='%231e1e2e' stroke-width='2.4' "
                "fill='none' stroke-linecap='round'/%3E%3Ccircle cx='23' cy='11' r='2.2' "
                "fill='%23d4af37'/%3E%3C/svg%3E")
        return app.response_class(
            json.dumps({
                "name": "Moonglade Athenaeum", "short_name": "Moonglade",
                "start_url": "/", "display": "standalone",
                "background_color": "#0c0a1c", "theme_color": "#0c0a1c",
                "icons": [{"src": icon, "sizes": "any", "type": "image/svg+xml"}],
            }),
            mimetype="application/manifest+json")

    @app.route("/sw.js")
    def service_worker():
        # Cache-first for immutable thumbnails/images; network for everything else.
        sw = (
            "const C='pixai-img-v1';\n"
            "self.addEventListener('install',e=>self.skipWaiting());\n"
            "self.addEventListener('activate',e=>self.clients.claim());\n"
            "self.addEventListener('fetch',e=>{\n"
            " const u=new URL(e.request.url);\n"
            " if(e.request.method==='GET' && (u.pathname.startsWith('/thumbs/')||u.pathname.startsWith('/img/')||u.pathname.startsWith('/full/'))){\n"
            "  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request).then(resp=>{c.put(e.request,resp.clone());return resp;}))));\n"
            " }\n"
            "});\n")
        return app.response_class(sw, mimetype="application/javascript")

    @app.route("/full/<media_id>")
    def full_image(media_id):
        # Resolve a media_id to its full-res file on the fly (used by the
        # lightbox so the index page doesn't precompute 250 image paths).
        row = get_row(db_path, media_id)
        p = find_image_file(out_dir, media_id, row.get("filename") if row else "")
        if not p or not p.exists():
            return "Not found", 404
        resp = send_from_directory(str(p.parent), p.name, max_age=31536000)
        resp.headers["Cache-Control"] = _IMMUTABLE
        return resp

    @app.route("/export-zip", methods=["POST"])
    def export_zip():
        # Stream a ZIP of the selected images' full-res files. Stored (no
        # recompression) since images are already compressed.
        import io
        import zipfile
        ids = request.form.getlist("media_ids")
        mem = io.BytesIO()
        n = 0
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_STORED) as z:
            seen_names = set()
            for mid in ids[:2000]:  # safety cap
                row = get_row(db_path, mid)
                if not row:
                    continue
                p = find_image_file(out_dir, mid, row.get("filename"))
                if not p or not p.exists():
                    continue
                name = p.name
                if name in seen_names:
                    name = "{}_{}".format(mid, p.name)
                seen_names.add(name)
                z.write(p, arcname=name)
                n += 1
        if not n:
            return "No matching images found.", 404
        mem.seek(0)
        return send_file(mem, mimetype="application/zip", as_attachment=True,
                         download_name="pixai_selection_{}.zip".format(n))

    @app.after_request
    def _gzip_html(resp):
        # Compress only HTML pages (the big card grids). File responses are
        # direct_passthrough streams and are left untouched.
        try:
            if (resp.status_code == 200 and not resp.direct_passthrough
                    and resp.content_type and resp.content_type.startswith("text/html")
                    and "gzip" in request.headers.get("Accept-Encoding", "")):
                data = resp.get_data()
                if len(data) > 1024:
                    import gzip as _gzip
                    packed = _gzip.compress(data, 6)
                    resp.set_data(packed)
                    resp.headers["Content-Encoding"] = "gzip"
                    resp.headers["Content-Length"] = str(len(packed))
                    resp.headers["Vary"] = "Accept-Encoding"
        except Exception:
            pass
        return resp

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Local PixAI gallery server.")
    ap.add_argument("--out", default="pixai_backup",
                    help="backup folder containing catalog.csv (default: pixai_backup)")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; use 0.0.0.0 for LAN)")
    ap.add_argument("--https", action="store_true",
                    help="serve over self-signed HTTPS (needed for PWA install / service "
                         "worker on a phone over LAN; requires the 'cryptography' package; "
                         "browsers show a one-time certificate warning)")
    ap.add_argument("--rebuild-thumbs", action="store_true",
                    help="regenerate all thumbnails even if they already exist")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.exists():
        sys.exit("Output folder not found: {}".format(out_dir))

    db_path  = out_dir / "catalog.db"
    csv_path = out_dir / "catalog.csv"

    # Auto-migrate existing catalog.csv when db is missing or empty
    if _db_is_empty(db_path) and csv_path.exists():
        print("Migrating catalog.csv → catalog.db ...")
        n = migrate_csv_to_db(csv_path, db_path)
        print("Migrated {:,} rows.".format(n))
    elif _db_is_empty(db_path):
        sys.exit("No catalog found in {}. Run a download first.".format(out_dir))

    thumb_dir = out_dir / "gallery" / "thumbs"
    print("Loading catalog...")
    rows = load_catalog(db_path)
    print("Building thumbnails (new only — use --rebuild-thumbs to force all)...")
    build_thumbnails(rows, out_dir, thumb_dir, force=args.rebuild_thumbs)

    ssl_context = None
    scheme = "http"
    if getattr(args, "https", False):
        try:
            import cryptography  # noqa: F401  (werkzeug 'adhoc' needs it)
            ssl_context = "adhoc"
            scheme = "https"
        except ImportError:
            print("--https needs the 'cryptography' package:  pip install cryptography\n"
                  "Falling back to HTTP.")

    app = create_app(out_dir)
    print("\nGallery ready ->  {}://{}:{}/".format(
        scheme, "localhost" if args.host == "127.0.0.1" else args.host, args.port))
    if ssl_context:
        print("(self-signed HTTPS: your browser/phone will show a one-time 'proceed anyway' warning)")
    print("Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=False, threaded=True, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
