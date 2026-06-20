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
                       send_from_directory, url_for)
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
]

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"})
THUMB_SIZE = (256, 256)
THUMB_QUALITY = 85
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
    rating          TEXT
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


def _build_where(q, model, date_from, date_to, batch=""):
    """Return (where_clause, params) for the common filter set."""
    clauses = ["filename != ''"]
    params  = []
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


def query_catalog(db_path, q="", model="", date_from="", date_to="",
                  sort="newest", page=1, page_size=100, batch=""):
    """Return (rows, total) with filtering, sorting and pagination done in SQL."""
    where, params = _build_where(q, model, date_from, date_to, batch)
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


def list_media_ids(db_path, q="", model="", date_from="", date_to="", sort="newest", batch=""):
    """Return ordered list of media_ids matching the filter (no row data)."""
    where, params = _build_where(q, model, date_from, date_to, batch)
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


def build_thumbnails(rows, out_dir, thumb_dir, force=False, progress_cb=None):
    if Image is None:
        print("Warning: Pillow not installed -- thumbnails will not be generated.")
        return
    total = len([r for r in rows if r.get("filename")])
    done = 0
    for row in rows:
        if not row.get("filename"):
            continue
        mid = row["media_id"]
        thumb_path = thumb_dir / "{}.jpg".format(mid)
        if not force and thumb_path.exists():
            done += 1
            continue
        img_path = find_image_file(out_dir, mid, row.get("filename"))
        if img_path and make_thumbnail(img_path, thumb_path):
            done += 1
        pct = int(done / total * 100) if total else 100
        if progress_cb:
            progress_cb(done, total, pct)
        else:
            print("\r  Thumbnails: {}/{} ({:d}%)  ".format(done, total, pct),
                  end="", flush=True)
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PixAI Gallery</title>
<style>
  :root {
    --base:    #1e1e2e; --mantle:  #181825; --surface0:#313244;
    --surface1:#45475a; --overlay0:#6c7086; --text:    #cdd6f4;
    --subtext: #a6adc8; --lavender:#b4befe; --mauve:   #cba6f7;
    --red:     #f38ba8; --peach:   #fab387; --green:   #a6e3a1;
    --blue:    #89b4fa; --sapphire:#74c7ec;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--base); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }

  /* Header */
  header { background: var(--mantle); padding: 12px 20px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--surface0); position: sticky; top: 0; z-index: 100; }
  header h1 { font-size: 18px; color: var(--lavender); flex-shrink: 0; }
  .header-stats { color: var(--subtext); font-size: 12px; }

  /* Filters */
  .filters { background: var(--mantle); padding: 10px 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; border-bottom: 1px solid var(--surface0); }
  .filters input, .filters select { background: var(--surface0); color: var(--text); border: 1px solid var(--surface1); border-radius: 6px; padding: 5px 10px; font-size: 13px; }
  .filters input { width: 280px; }
  .filters input:focus, .filters select:focus { outline: none; border-color: var(--lavender); }
  .filters label { color: var(--subtext); font-size: 12px; }
  .btn { background: var(--surface0); color: var(--text); border: 1px solid var(--surface1); border-radius: 6px; padding: 5px 14px; cursor: pointer; font-size: 13px; }
  .btn:hover { background: var(--surface1); }
  .btn-danger { background: var(--red); color: var(--base); border-color: var(--red); font-weight: 600; }
  .btn-danger:hover { opacity: 0.85; }
  .btn-primary { background: var(--lavender); color: var(--base); border-color: var(--lavender); font-weight: 600; }
  .btn-primary:hover { opacity: 0.85; }

  /* Bulk toolbar */
  .bulk-bar { background: var(--surface0); padding: 8px 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--surface1); min-height: 40px; }
  .bulk-bar span { color: var(--subtext); font-size: 13px; }
  #sel-count { color: var(--peach); font-weight: 600; }

  /* Grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; padding: 16px 20px; }
  .card { background: var(--mantle); border-radius: 8px; overflow: hidden; border: 2px solid transparent; transition: border-color .15s; position: relative; cursor: pointer; }
  .card:hover { border-color: var(--surface1); }
  .card.selected { border-color: var(--lavender); }
  .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: var(--surface0); }
  .card .no-thumb { width: 100%; aspect-ratio: 1; background: var(--surface0); display: flex; align-items: center; justify-content: center; color: var(--overlay0); font-size: 11px; }
  .card .meta { padding: 6px 8px; }
  .card .meta .model { font-size: 11px; color: var(--mauve); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .meta .date  { font-size: 10px; color: var(--overlay0); }
  .card .cb-wrap { position: absolute; top: 6px; left: 6px; }
  .card .cb-wrap input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--lavender); cursor: pointer; }
  .card a.cover { position: absolute; inset: 0; z-index: 1; }
  .card .cb-wrap { z-index: 2; }

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
  <h1>PixAI Gallery</h1>
  <span class="header-stats">{{ total }} images</span>
</header>

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
      <option value="width"       {% if sort=='width' %}selected{% endif %}>Width ↓</option>
      <option value="height"      {% if sort=='height' %}selected{% endif %}>Height ↓</option>
    </select>
  </div>
  <div style="align-self:flex-end">
    <button type="submit" class="btn btn-primary">Filter</button>
    <a href="/" class="btn">Reset</a>
  </div>
</div>
</form>

<div class="bulk-bar">
  <button class="btn" onclick="selectAll()">Select All</button>
  <button class="btn" onclick="clearAll()">Clear</button>
  <span><span id="sel-count">0</span> selected</span>
  <button class="btn btn-danger" id="bulk-del-btn" style="display:none"
    onclick="confirmBulkDelete()">Delete Selected</button>
</div>

<form id="bulk-form" method="post" action="/delete-bulk">
  <input type="hidden" name="back" value="{{ request.url }}">
  <div class="grid">
    {% for row in rows %}
    <div class="card" id="card-{{ row.media_id }}">
      <div class="cb-wrap">
        <input type="checkbox" name="media_ids" value="{{ row.media_id }}"
               onchange="onCheck()" onclick="event.stopPropagation()">
      </div>
      <a class="cover" href="{{ url_for('detail', media_id=row.media_id, back=current_url) }}"></a>
      {% if row._has_thumb %}
      <img src="{{ url_for('thumb', media_id=row.media_id) }}" loading="lazy"
           alt="{{ row.prompt_preview[:60] }}">
      {% else %}
      <div class="no-thumb">no preview</div>
      {% endif %}
      <div class="meta">
        <div class="model">{{ row.model_name or row.model_id or '—' }}</div>
        <div class="date">{{ row.created_at[:10] if row.created_at else '' }}</div>
      </div>
      <div class="stars" id="stars-{{ row.media_id }}"
           data-mid="{{ row.media_id }}" data-rating="{{ row.rating or 0 }}"></div>
    </div>
    {% endfor %}
  </div>
</form>

{% if not rows %}
<div class="empty">No images match your filters.</div>
{% endif %}

<div class="pagination">
  {% if page > 1 %}
  <a href="{{ page_url(1) }}">« First</a>
  <a href="{{ page_url(page - 1) }}">‹ Prev</a>
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
  <a href="{{ page_url(page + 1) }}">Next ›</a>
  <a href="{{ page_url(total_pages) }}">Last »</a>
  {% endif %}
</div>

<script>
function onCheck() {
  const checked = document.querySelectorAll('input[name=media_ids]:checked');
  document.getElementById('sel-count').textContent = checked.length;
  document.getElementById('bulk-del-btn').style.display = checked.length ? 'inline-block' : 'none';
  checked.forEach(cb => cb.closest('.card').classList.add('selected'));
  document.querySelectorAll('input[name=media_ids]:not(:checked)').forEach(cb => {
    cb.closest('.card').classList.remove('selected');
  });
}
function selectAll() {
  document.querySelectorAll('input[name=media_ids]').forEach(cb => { cb.checked = true; });
  onCheck();
}
function clearAll() {
  document.querySelectorAll('input[name=media_ids]').forEach(cb => { cb.checked = false; });
  onCheck();
}
function confirmBulkDelete() {
  const n = document.querySelectorAll('input[name=media_ids]:checked').length;
  confirmDelete('/delete-bulk', 'Permanently delete ' + n + ' image' + (n !== 1 ? 's' : '') + '? This cannot be undone.');
  document.getElementById('del-modal-form').onsubmit = function() {
    document.getElementById('bulk-form').submit();
    return false;
  };
  document.getElementById('del-modal-form').action = '#';
}
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
    <a class="back-link" href="{{ back }}">↑ Gallery</a>
    <button id="focus-btn" class="focus-btn" onclick="toggleFocus()" title="Toggle focus mode (F key)">Focus</button>
    {% if next_id %}
    <a id="nav-next" class="nav-arrow" href="{{ url_for('detail', media_id=next_id, back=back) }}" title="Next (→ arrow key)">Next &#8594;</a>
    {% else %}
    <span class="nav-arrow nav-disabled">Next &#8594;</span>
    {% endif %}
  </div>

  <div class="detail-img">
    {% if img_url %}
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
    <span class="lbl">Prompt Preview</span>
    <span class="val">{{ row.prompt_preview }}</span>
    <span class="lbl">Model</span>
    <span class="val">{{ row.model_name or row.model_id or '—' }}</span>
    <span class="lbl">Seed</span>
    <span class="val">{{ row.seed or '—' }}</span>
    <span class="lbl">Steps</span>
    <span class="val">{{ row.steps or '—' }}</span>
    <span class="lbl">Sampler</span>
    <span class="val">{{ row.sampler or '—' }}</span>
    <span class="lbl">CFG Scale</span>
    <span class="val">{{ row.cfg_scale or '—' }}</span>
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
    <button class="btn btn-danger"
      onclick="confirmDelete('{{ url_for('delete_one', media_id=row.media_id) }}?back={{ back|urlencode }}',
        'Permanently delete this image? This cannot be undone.')">
      Delete
    </button>
  </div>
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

        models  = unique_models(db_path)
        batches = unique_batches(db_path)
        years   = catalog_years(db_path)
        page_rows, total = query_catalog(
            db_path, q, model_filter, date_from, date_to, sort, page, per_page,
            batch=batch_filter,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        for r in page_rows:
            r["_has_thumb"] = (thumb_dir / "{}.jpg".format(r["media_id"])).exists()

        def page_url(p):
            args = dict(request.args)
            args["page"] = p
            return url_for("index", **args)

        return render_template_string(
            INDEX_HTML,
            rows=page_rows, total=total, page=page,
            total_pages=total_pages, page_range=_page_range(page, total_pages),
            q=q, model_filter=model_filter, batch_filter=batch_filter,
            date_from=date_from,
            date_to=date_to, sort=sort, models=models, batches=batches,
            years=years, per_page=per_page, per_page_opts=per_page_opts,
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
        nav_ids = list_media_ids(
            db_path,
            q=_qs1("q"), model=_qs1("model"),
            date_from=_ym("from", "01"), date_to=_ym("to", "12"),
            sort=_qs1("sort", "newest"), batch=_qs1("batch"),
        )
        try:
            idx = nav_ids.index(media_id)
        except ValueError:
            idx = -1
        prev_id = nav_ids[idx - 1] if idx > 0 else None
        next_id = nav_ids[idx + 1] if 0 <= idx < len(nav_ids) - 1 else None

        return render_template_string(
            DETAIL_HTML, row=row, img_url=img_url, back=back,
            prev_id=prev_id, next_id=next_id,
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

    @app.route("/rate/<media_id>", methods=["POST"])
    def rate(media_id):
        data = request.get_json(silent=True) or {}
        try:
            value = max(0, min(5, int(data.get("rating", 0))))
        except (TypeError, ValueError):
            return json.dumps({"ok": False}), 400, {"Content-Type": "application/json"}
        update_rating(db_path, media_id, value)
        return json.dumps({"ok": True, "rating": value}), 200, {"Content-Type": "application/json"}

    @app.route("/thumbs/<media_id>.jpg")
    def thumb(media_id):
        return send_from_directory(str(thumb_dir), "{}.jpg".format(media_id))

    @app.route("/img/<path:rel>")
    def serve_image(rel):
        return send_from_directory(str(out_dir), rel)

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

    app = create_app(out_dir)
    print("\nGallery ready ->  http://{}:{}/".format(
        "localhost" if args.host == "127.0.0.1" else args.host, args.port))
    print("Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
