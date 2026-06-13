#!/usr/bin/env python3
"""
pixai_gallery.py
================
Local Flask web gallery for your PixAI backup collection.

Reads catalog.csv and serves a browseable, filterable, paginated image gallery
at http://localhost:5000 . Supports single and bulk delete (removes image file,
thumbnail, and catalog row).

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
    "task_id", "media_id", "filename", "url", "width", "height",
    "prompt_preview", "status", "created_at",
    "prompt_full", "natural_prompt", "seed", "steps",
    "sampler", "cfg_scale", "model_id", "model_name",
]

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"})
THUMB_SIZE = (256, 256)
THUMB_QUALITY = 85
PAGE_SIZE = 100


def load_catalog(csv_path):
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_catalog(csv_path, rows):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({field: r.get(field, "") for field in CATALOG_FIELDS})


def find_image_file(out_dir, media_id, filename):
    """Locate an image file: try catalog filename first, then rglob fallback."""
    if filename:
        for candidate in out_dir.rglob(filename):
            if candidate.is_file():
                return candidate
    mid = str(media_id)
    for p in out_dir.rglob("*_{}.*".format(mid)):
        if p.suffix.lower() in _IMAGE_EXTS and not p.name.endswith(".part"):
            return p
    return None


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
    csv_path = out_dir / "catalog.csv"
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
  .back-link { display: inline-block; margin-bottom: 14px; color: var(--blue); text-decoration: none; font-size: 13px; }
  .back-link:hover { text-decoration: underline; }

  /* Modal */
  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 200; align-items: center; justify-content: center; }
  .modal-bg.open { display: flex; }
  .modal { background: var(--mantle); border: 1px solid var(--surface1); border-radius: 10px; padding: 24px; max-width: 400px; width: 90%; }
  .modal h2 { font-size: 16px; margin-bottom: 10px; color: var(--red); }
  .modal p { color: var(--subtext); font-size: 13px; margin-bottom: 18px; line-height: 1.5; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

  /* Empty */
  .empty { text-align: center; padding: 60px 20px; color: var(--overlay0); }
</style>
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

<script>
function closeModal() { document.getElementById('del-modal').classList.remove('open'); }
function confirmDelete(url, msg) {
  document.getElementById('del-modal-msg').textContent = msg;
  document.getElementById('del-modal-form').action = url;
  document.getElementById('del-modal').classList.add('open');
}
document.getElementById('del-modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});
</script>
</body>
</html>
"""

    INDEX_HTML = BASE_HTML.replace("{% block body %}{% endblock %}", """
<header>
  <h1>PixAI Gallery</h1>
  <span class="header-stats">{{ total }} images</span>
</header>

<form method="get" action="/" id="filter-form">
<div class="filters">
  <div>
    <label>Prompt</label><br>
    <input type="text" name="q" value="{{ q }}" placeholder="search prompt...">
  </div>
  <div>
    <label>Model</label><br>
    <select name="model">
      <option value="">All models</option>
      {% for m in models %}
      <option value="{{ m }}" {% if m == model_filter %}selected{% endif %}>{{ m }}</option>
      {% endfor %}
    </select>
  </div>
  <div>
    <label>From</label><br>
    <input type="month" name="date_from" value="{{ date_from }}" style="width:140px">
  </div>
  <div>
    <label>To</label><br>
    <input type="month" name="date_to" value="{{ date_to }}" style="width:140px">
  </div>
  <div>
    <label>Sort</label><br>
    <select name="sort">
      <option value="newest" {% if sort=='newest' %}selected{% endif %}>Newest first</option>
      <option value="oldest" {% if sort=='oldest' %}selected{% endif %}>Oldest first</option>
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
      <a class="cover" href="{{ url_for('detail', media_id=row.media_id) }}"></a>
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
<div class="detail-wrap">
  <a class="back-link" href="{{ back }}">← Back to gallery</a>

  <div class="detail-img">
    {% if img_url %}
    <img src="{{ img_url }}" alt="{{ row.prompt_preview }}">
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

  <div class="detail-actions">
    {% if img_url %}
    <a class="btn" href="{{ img_url }}" target="_blank">Open Full Size</a>
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
    def _filter_rows(rows, q, model_filter, date_from, date_to):
        q = q.strip().lower()
        result = []
        for r in rows:
            if not r.get("filename"):
                continue
            if q:
                haystack = (r.get("prompt_full") or r.get("prompt_preview") or "").lower()
                if q not in haystack:
                    continue
            if model_filter and r.get("model_name") != model_filter:
                continue
            dt = (r.get("created_at") or "")[:7]
            if date_from and dt and dt < date_from:
                continue
            if date_to and dt and dt > date_to:
                continue
            result.append(r)
        return result

    def _paginate(rows, page, page_size):
        total = len(rows)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        return rows[start:start + page_size], page, total_pages

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

    def _unique_models(rows):
        seen = []
        for r in rows:
            m = r.get("model_name") or ""
            if m and m not in seen:
                seen.append(m)
        return sorted(seen)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        rows = load_catalog(csv_path)
        q            = request.args.get("q", "")
        model_filter = request.args.get("model", "")
        date_from    = request.args.get("date_from", "")
        date_to      = request.args.get("date_to", "")
        sort         = request.args.get("sort", "newest")
        page         = int(request.args.get("page", 1))

        models = _unique_models(rows)
        filtered = _filter_rows(rows, q, model_filter, date_from, date_to)
        if sort == "oldest":
            filtered.sort(key=lambda r: r.get("created_at") or "")
        else:
            filtered.sort(key=lambda r: r.get("created_at") or "", reverse=True)

        total = len(filtered)
        page_rows, page, total_pages = _paginate(filtered, page, PAGE_SIZE)

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
            q=q, model_filter=model_filter, date_from=date_from,
            date_to=date_to, sort=sort, models=models,
            page_url=page_url, request=request,
        )

    @app.route("/image/<media_id>")
    def detail(media_id):
        rows = load_catalog(csv_path)
        row = next((r for r in rows if r["media_id"] == media_id), None)
        if not row:
            return "Image not found.", 404

        img_path = find_image_file(out_dir, media_id, row.get("filename"))
        img_url = None
        if img_path:
            img_url = url_for("serve_image", rel=str(img_path.relative_to(out_dir)).replace("\\", "/"))

        back = request.args.get("back", url_for("index"))
        return render_template_string(DETAIL_HTML, row=row, img_url=img_url, back=back)

    @app.route("/delete/<media_id>", methods=["POST"])
    def delete_one(media_id):
        back = request.args.get("back") or url_for("index")
        rows = load_catalog(csv_path)
        row = next((r for r in rows if r["media_id"] == media_id), None)
        if row:
            img_path = find_image_file(out_dir, media_id, row.get("filename"))
            if img_path and img_path.exists():
                img_path.unlink()
            thumb_path = thumb_dir / "{}.jpg".format(media_id)
            if thumb_path.exists():
                thumb_path.unlink()
            rows = [r for r in rows if r["media_id"] != media_id]
            save_catalog(csv_path, rows)
        return redirect(back)

    @app.route("/delete-bulk", methods=["POST"])
    def delete_bulk():
        back = request.form.get("back") or url_for("index")
        media_ids = set(request.form.getlist("media_ids"))
        if not media_ids:
            return redirect(back)

        rows = load_catalog(csv_path)
        to_delete = {r["media_id"]: r for r in rows if r["media_id"] in media_ids}

        for mid, row in to_delete.items():
            img_path = find_image_file(out_dir, mid, row.get("filename"))
            if img_path and img_path.exists():
                img_path.unlink()
            thumb_path = thumb_dir / "{}.jpg".format(mid)
            if thumb_path.exists():
                thumb_path.unlink()

        rows = [r for r in rows if r["media_id"] not in media_ids]
        save_catalog(csv_path, rows)
        return redirect(back)

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

    csv_path = out_dir / "catalog.csv"
    if not csv_path.exists():
        sys.exit("No catalog.csv found at {}. Run a download first.".format(csv_path))

    thumb_dir = out_dir / "gallery" / "thumbs"
    print("Loading catalog...")
    rows = load_catalog(csv_path)
    print("Building thumbnails (new only — use --rebuild-thumbs to force all)...")
    build_thumbnails(rows, out_dir, thumb_dir, force=args.rebuild_thumbs)

    app = create_app(out_dir)
    print("\nGallery ready →  http://{}:{}/".format(
        "localhost" if args.host == "127.0.0.1" else args.host, args.port))
    print("Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
