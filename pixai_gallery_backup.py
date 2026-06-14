#!/usr/bin/env python3
"""
pixai_gallery_backup.py  (v4 - media resolution)
================================================
Bulk-download YOUR OWN PixAI.art generated images. Replays PixAI's persisted
GraphQL query (listUserTaskSummaries) to page backward through your entire
generation history, turns each task's mediaId / batchMediaIds into full-resolution
image URLs, downloads them (with resume), paces itself, and writes a catalog with
the prompt preview next to each image.

You own the copyright to images you generate on PixAI. Keep the rate modest.

--------------------------------------------------------------------------------
HOW IMAGES ARE FETCHED
--------------------------------------------------------------------------------
Task summaries don't contain image URLs -- they contain media IDs. PixAI serves
media at:   https://api.pixai.art/v1/media/<mediaId>/<variant>
where <variant> is e.g. "thumbnail" (small) or the full-resolution one. This
script auto-detects the full-res variant by testing a real media ID once, then
reuses it. Run --probe to see the detection result before committing.

--------------------------------------------------------------------------------
SECURITY MODEL (unchanged)
--------------------------------------------------------------------------------
* No password handling. Bearer token from PIXAI_TOKEN env var or token.txt only.
* HTTPS verification always ON. On 401, refresh the token and re-run (resumes).

--------------------------------------------------------------------------------
QUICK START
--------------------------------------------------------------------------------
  pip install requests truststore
  set PIXAI_TOKEN ...   (your OS's way)
  python pixai_gallery_backup.py --probe     # detect full-res variant, sanity-check
  python pixai_gallery_backup.py             # download everything (backward)
  python pixai_gallery_backup.py --max 40    # small test first
  python pixai_gallery_backup.py --variant original   # force a variant if you know it
"""

__version__ = "1.1.0-dev"

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from pixai_gallery import (CATALOG_FIELDS, _IMAGE_EXTS, init_db, load_catalog,
                            save_catalog, migrate_csv_to_db, export_csv, _db_is_empty)


def _ensure_db(out):
    """Return db_path after auto-migrating catalog.csv if the db is missing/empty.

    Raises PixAIError if neither db nor csv exists.
    """
    out = Path(out)
    db_path  = out / "catalog.db"
    csv_path = out / "catalog.csv"
    if _db_is_empty(db_path):
        if csv_path.exists():
            print("Migrating catalog.csv → catalog.db ...")
            n = migrate_csv_to_db(csv_path, db_path)
            print("Migrated {:,} rows.".format(n))
        else:
            raise PixAIError(
                "No catalog found in {}. Run a download (or --collect-only) first.".format(out))
    return db_path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

_TRUSTSTORE_ACTIVE = False
try:
    import truststore
    truststore.inject_into_ssl()
    _TRUSTSTORE_ACTIVE = True
except Exception:
    pass


class PixAIError(Exception):
    """Raised instead of sys.exit() so the GUI and tests can catch errors cleanly."""


API_URL = "https://api.pixai.art/graphql"

# ===========================================================================
# CAPTURED FROM YOUR BROWSER -- loaded from config.json (see config.example.json)
# Update config.json when the site changes (see RECAPTURE at the bottom).
# ===========================================================================
OPERATION_NAME = "listUserTaskSummaries"
CLIENT_LIBRARY = {"name": "@apollo/client", "version": "4.1.4"}


def _load_config():
    """Read config.json. Returns {} quietly if absent so --help and offline modes
    (--organize, --catalog-stats) work without it; main() validates before API calls.
    Looks next to the script file first, then the current working directory."""
    for cfg_path in (Path(__file__).resolve().parent / "config.json", Path("config.json")):
        if cfg_path.exists():
            break
    else:
        return {}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        print("Warning: could not read config.json: {}".format(e))
        return {}


_cfg = _load_config()
PERSISTED_QUERY_HASH = _cfg.get("PERSISTED_QUERY_HASH", "")
U3T = _cfg.get("U3T", "")
USER_ID = _cfg.get("USER_ID", "")
TASK_DETAIL_HASH = _cfg.get("TASK_DETAIL_HASH", "")
MODEL_DETAIL_HASH = _cfg.get("MODEL_DETAIL_HASH", "")
# ===========================================================================

# Media URL: https://api.pixai.art/v1/media/<id>/<variant>
MEDIA_TMPL = "https://api.pixai.art/v1/media/{id}/{variant}"
MEDIA_BASE = "https://api.pixai.art/v1/media/{id}"
# Tried in order; first one returning a real image (and not the thumbnail) wins.
VARIANT_CANDIDATES = ["original", "orig", "full", "hd", "public", "raw", "thumbnail"]
# ===========================================================================


def load_token(cli_token=None):
    if cli_token:
        return cli_token.strip()
    env = os.environ.get("PIXAI_TOKEN")
    if env:
        return env.strip()
    for f in (Path(__file__).resolve().parent / "token.txt", Path("token.txt")):
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    raise PixAIError("No token found. Set PIXAI_TOKEN, pass --token, or create token.txt.")


def _ssl_help():
    return ("\nSSL verification failed (antivirus/proxy intercepting HTTPS).\n"
            "Fix safely:  pip install truststore   then re-run.\n"
            "(truststore active this run: {})\n".format(_TRUSTSTORE_ACTIVE))


def _format_size(num_bytes):
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return "{:.1f} {}".format(num_bytes, unit)
        num_bytes /= 1024
    return "{:.1f} PB".format(num_bytes)


def _progress_line(done, total, new=0, width=40):
    """Return a \r-overwriting progress line for terminal output."""
    new_str = "  +{} new".format(new) if new else ""
    if total:
        pct = min(done / total, 1.0)
        filled = int(width * pct)
        bar = ("=" * filled + ">" + " " * (width - filled - 1)
               if filled < width else "=" * width)
        return "\r  [{bar}] {done}/{total} checked ({pct:.1f}%){new}  ".format(
            bar=bar, done=done, total=total, pct=pct * 100, new=new_str)
    return "\r  Checking: {done} images...{new}  ".format(done=done, new=new_str)


def _quick_count(session, page_size=500):
    """Paginate through the library to count total images for the progress meter.
    Uses a conservative page size (default 500) to avoid server-side Prisma
    errors that occur at large page sizes. Returns 0 on any API error so the
    download still proceeds — the progress bar degrades to a running total."""
    print("Counting library size for progress meter...")
    try:
        before = None
        total = 0
        while True:
            conn = find_connection(gql(session, page_variables(page_size, before)))
            if not conn:
                break
            for edge in conn.get("edges", []):
                node = edge.get("node", edge)
                total += len(media_ids_for(node))
            pi = conn.get("pageInfo", {})
            if not pi.get("hasPreviousPage"):
                break
            before = pi.get("startCursor")
        print("Library total: {} images\n".format(total))
        return total
    except PixAIError as e:
        print("  (count failed: {}) -- progress bar will show running total only\n".format(e))
        return 0


# ---------------------------------------------------------------------------
# Persisted GraphQL GET (with Apollo CSRF headers)
# ---------------------------------------------------------------------------
def gql(session, variables, retries=4):
    params = {
        "operation": OPERATION_NAME,
        "u3t": U3T,
        "operationName": OPERATION_NAME,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"clientLibrary": CLIENT_LIBRARY,
             "persistedQuery": {"version": 1, "sha256Hash": PERSISTED_QUERY_HASH}},
            separators=(",", ":")),
    }
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            r = session.get(API_URL, params=params, timeout=60)
        except requests.exceptions.SSLError:
            raise PixAIError(_ssl_help())
        except requests.RequestException as e:
            if attempt == retries:
                raise
            print("  network error ({}); retrying in {:.0f}s".format(e, delay))
            time.sleep(delay); delay *= 2; continue

        if r.status_code == 401:
            raise PixAIError("401 Unauthorized -- token missing/expired. Refresh and re-run.")
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                r.raise_for_status()
            print("  HTTP {}; backing off {:.0f}s".format(r.status_code, delay))
            time.sleep(delay); delay *= 2; continue

        try:
            data = r.json()
        except ValueError:
            raise PixAIError("HTTP {} non-JSON response:\n{}".format(
                r.status_code, r.text[:800]))
        if data.get("errors"):
            if "PersistedQueryNotFound" in json.dumps(data["errors"]):
                raise PixAIError("Persisted-query hash not recognized. Recapture the hash "
                                 "(see RECAPTURE at the bottom of this file).")
            print("\n=== GraphQL error (HTTP {}) ===".format(r.status_code))
            print(json.dumps(data["errors"], indent=2)[:3000])
            raise PixAIError("GraphQL error (see log above).")
        if r.status_code >= 400:
            print("\nHTTP {}:\n{}".format(r.status_code, json.dumps(data, indent=2)[:1500]))
            raise PixAIError("HTTP {} error (see log above).".format(r.status_code))
        return data["data"]
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def find_connection(data):
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "edges" in cur and "pageInfo" in cur:
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def slug_from_prompt(prompt, max_len, sep="_"):
    """Make a filesystem-safe slug from a prompt preview.

    Removes characters Windows forbids (\\ / : * ? " < > |), collapses runs of
    punctuation/whitespace (commas etc.) into the separator, trims to max_len, and
    strips trailing dots/spaces/separators (which Windows dislikes).
    """
    if not prompt:
        return ""
    s = prompt.strip()
    # Drop anything that's not a word char, space, or hyphen; this removes the
    # forbidden set plus commas, quotes, parentheses, colons, etc.
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    # Collapse whitespace/hyphen runs into the chosen separator.
    s = re.sub(r"[\s-]+", sep, s).strip(sep + ". ")
    if len(s) > max_len:
        s = s[:max_len].rstrip(sep + ". ")
    return s


def build_stem_name(prompt_preview, task_id, media_id, max_len, sep="_"):
    """<clean_prompt>_<task_id>_<media_id>, falling back gracefully if no prompt.

    The media_id is always last so resume can match on `_<media_id>` no matter
    what readable text precedes it. The task_id is the stable per-task anchor.
    """
    slug = slug_from_prompt(prompt_preview, max_len, sep)
    tid = str(task_id or "task")
    mid = str(media_id)
    parts = [p for p in (slug, tid, mid) if p]
    return sep.join(parts)


def already_downloaded(root, media_id):
    """Return an existing image file for this media_id anywhere under root,
    regardless of its prompt prefix, task id, or which subfolder it's in.
    Matches `*_<media_id>.<ext>`. Searching recursively is what lets resume keep
    working after files have been moved into batch/month subfolders."""
    mid = str(media_id)
    for p in root.rglob("*_{}.*".format(mid)):
        if not p.name.endswith(".part") and p.stat().st_size > 0:
            return p
    return None


def media_ids_for(node):
    ids = []
    if node.get("mediaId"):
        ids.append(str(node["mediaId"]))
    for b in (node.get("batchMediaIds") or []):
        if b:
            ids.append(str(b))
    return list(dict.fromkeys(ids))


def extract_meta(node):
    return {
        "task_id": node.get("id", ""),
        "created_at": node.get("createdAt", ""),
        "prompt_preview": node.get("promptsPreview", "") or "",
        "status": node.get("status", ""),
    }


# ---------------------------------------------------------------------------
# Media URL + variant detection + download
# ---------------------------------------------------------------------------
# Preference order of variant labels inside the media object's "urls" list.
URL_VARIANT_PREFERENCE = ["PUBLIC", "ORIGINAL", "ORIG", "FULL", "THUMBNAIL", "STILL_THUMBNAIL"]


def media_url(mid, variant):
    if variant in ("", None):
        return MEDIA_BASE.format(id=mid)
    return MEDIA_TMPL.format(id=mid, variant=variant)


def resolve_media(session, mid):
    """Fetch the media object and return (best_full_res_url, info_dict).

    Reads the object's `urls` list and picks the highest-quality variant
    (PUBLIC = full-resolution original on PixAI). Returns (None, {}) on failure.
    """
    try:
        r = session.get(MEDIA_BASE.format(id=mid), timeout=30)
        r.raise_for_status()
        obj = r.json()
    except (requests.RequestException, ValueError):
        return None, {}
    urls = obj.get("urls") or []
    by_variant = {}
    for u in urls:
        if isinstance(u, dict) and u.get("url"):
            by_variant[str(u.get("variant", "")).upper()] = u["url"]
    chosen = None
    for pref in URL_VARIANT_PREFERENCE:
        if pref in by_variant:
            chosen = by_variant[pref]
            break
    if not chosen and by_variant:
        chosen = next(iter(by_variant.values()))
    info = {"width": obj.get("width"), "height": obj.get("height"),
            "type": obj.get("type", "")}
    return chosen, info


def test_variant(session, mid, variant):
    """Return (status_code, content_type, size_str, is_image)."""
    try:
        r = session.get(media_url(mid, variant), stream=True, timeout=30)
        ct = r.headers.get("Content-Type", "")
        size = r.headers.get("Content-Length", "?")
        is_img = (r.status_code == 200 and ct.lower().startswith("image"))
        r.close()
        return (r.status_code, ct, size, is_img)
    except requests.RequestException as e:
        return ("ERR", str(e)[:60], "", False)


def detect_variant(session, mid, verbose=False):
    """Pick the first candidate that returns a real image, preferring non-thumbnail."""
    best = None
    for v in VARIANT_CANDIDATES + [""]:
        code, ct, size, is_img = test_variant(session, mid, v)
        label = v or "(base)"
        if verbose:
            print("  {:<10} -> {} {} {}".format(
                label, code, ct, ("" if size == "?" else size + " bytes")))
        if is_img and best is None and v != "thumbnail":
            best = v
    if best is None:
        # fall back to thumbnail if it's the only thing that worked
        code, ct, size, is_img = test_variant(session, mid, "thumbnail")
        if is_img:
            best = "thumbnail"
    return best


def ext_from_ct(ct):
    ct = (ct or "").lower()
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".png"


def embed_metadata(path, fields):
    """Embed prompt/IDs/date into the image file itself.

    PNG -> text chunks (lossless re-save). JPEG -> EXIF ImageDescription with
    quality='keep' (no recompression). WebP and others -> skipped ('unsupported').
    Returns a short status note. Never raises.
    """
    try:
        from PIL import Image, PngImagePlugin
    except ImportError:
        return "pillow-missing"
    ext = path.suffix.lower()
    pairs = [(str(k), str(v)) for k, v in fields.items() if v not in (None, "")]
    try:
        if ext == ".png":
            with Image.open(path) as im:
                im.load()
                meta = PngImagePlugin.PngInfo()
                for k, v in pairs:
                    meta.add_text(k, v)
                im.save(path, "PNG", pnginfo=meta, optimize=True)
            return "ok"
        if ext in (".jpg", ".jpeg"):
            with Image.open(path) as im:
                im.load()
                exif = im.getexif()
                desc = "; ".join("{}={}".format(k, v) for k, v in pairs)
                exif[0x010E] = desc[:1500]  # ImageDescription
                im.save(path, "JPEG", quality="keep", exif=exif)
            return "ok"
        return "unsupported"
    except Exception as e:
        return "error: {}".format(str(e)[:60])


def convert_image(path, target, jpeg_quality=92, jpeg_bg="white", keep_original=False):
    """Convert an image file to target format ('png' or 'jpeg').

    Returns (final_path, note). Requires Pillow. On any failure, leaves the
    original untouched and returns it with an explanatory note.
    """
    try:
        from PIL import Image
    except ImportError:
        return path, "pillow-missing"
    target = target.lower()
    out_ext = ".jpg" if target in ("jpg", "jpeg") else ".png"
    if path.suffix.lower() == out_ext:
        return path, "already"
    out_path = path.with_suffix(out_ext)
    try:
        with Image.open(path) as im:
            if target in ("jpg", "jpeg"):
                # JPEG has no alpha: flatten onto a background.
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size,
                                   (0, 0, 0) if jpeg_bg == "black" else (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
                im.save(out_path, "JPEG", quality=jpeg_quality, optimize=True)
            else:
                im.save(out_path, "PNG", optimize=True)
    except Exception as e:
        # Clean up a partial output; keep the original.
        try:
            if out_path.exists() and out_path != path:
                out_path.unlink()
        except OSError:
            pass
        return path, "convert-error: {}".format(str(e)[:80])
    if not keep_original and out_path != path:
        try:
            path.unlink()
        except OSError:
            pass
    return out_path, "ok"


def download(session, url, stem, retries=3, convert=None,
             jpeg_quality=92, jpeg_bg="white", keep_webp=False):
    """stem is a Path WITHOUT extension. Returns (status, final_path_or_None)."""
    existing = [p for p in stem.parent.glob(stem.name + ".*")
                if not p.name.endswith(".part") and p.stat().st_size > 0]
    if existing:
        return ("skip", existing[0])
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    return ("missing", None)
                r.raise_for_status()
                ext = ext_from_ct(r.headers.get("Content-Type"))
                dest = stem.with_name(stem.name + ext)
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=65536):
                        fh.write(chunk)
                tmp.replace(dest)
            if convert:
                dest, note = convert_image(dest, convert, jpeg_quality,
                                           jpeg_bg, keep_original=keep_webp)
                if note == "pillow-missing":
                    raise PixAIError("--convert needs Pillow. Run:  pip install pillow\n"
                                     "(The image downloaded fine; just install Pillow and "
                                     "re-run -- finished files are skipped.)")
                if note.startswith("convert-error"):
                    print("    convert warning for {}: {}".format(dest.name, note))
            return ("ok", dest)
        except requests.exceptions.SSLError:
            raise PixAIError(_ssl_help())
        except requests.RequestException as e:
            if attempt == retries:
                print("    FAILED {} ({})".format(url, e))
                return ("fail", None)
            time.sleep(delay); delay *= 2


def page_variables(page_size, before=None):
    v = {"last": page_size, "userId": USER_ID}
    if before:
        v["before"] = before
    return v


# ---------------------------------------------------------------------------
# Full-meta API (task detail + model name)
# ---------------------------------------------------------------------------
_FULL_META_FIELDS = (
    "prompt_full", "natural_prompt", "seed", "steps",
    "sampler", "cfg_scale", "model_id", "model_name",
)


def task_detail_gql(session, task_id):
    """GET getTaskById for one task. Returns the task dict or None on failure."""
    if not TASK_DETAIL_HASH:
        raise PixAIError(
            "TASK_DETAIL_HASH missing from config.json. "
            "Capture it from DevTools (see README -> Full Meta) and add it.")
    params = {
        "operation": "getTaskById",
        "u3t": U3T,
        "operationName": "getTaskById",
        "variables": json.dumps({"id": str(task_id)}, separators=(",", ":")),
        "extensions": json.dumps(
            {"clientLibrary": CLIENT_LIBRARY,
             "persistedQuery": {"version": 1, "sha256Hash": TASK_DETAIL_HASH}},
            separators=(",", ":")),
    }
    try:
        r = session.get(API_URL, params=params, timeout=60)
        if r.status_code != 200:
            return None
        data = r.json()
        return (data.get("data") or {}).get("task")
    except (requests.RequestException, ValueError):
        return None


def model_name_gql(session, model_version_id, _cache={}):
    """GET getGenerationModelByVersionId; result cached by ID (few unique models)."""
    if not model_version_id:
        return ""
    mid = str(model_version_id)
    if mid in _cache:
        return _cache[mid]
    if not MODEL_DETAIL_HASH:
        _cache[mid] = mid
        return mid
    params = {
        "operation": "getGenerationModelByVersionId",
        "u3t": U3T,
        "operationName": "getGenerationModelByVersionId",
        "variables": json.dumps({"id": mid}, separators=(",", ":")),
        "extensions": json.dumps(
            {"clientLibrary": CLIENT_LIBRARY,
             "persistedQuery": {"version": 1, "sha256Hash": MODEL_DETAIL_HASH}},
            separators=(",", ":")),
    }
    try:
        r = session.get(API_URL, params=params, timeout=60)
        r.raise_for_status()
        mv = (r.json().get("data") or {}).get("generationModelVersion") or {}
        title = (mv.get("model") or {}).get("title", "")
        version = mv.get("name", "")
        name = "{} {}".format(title, version).strip() if title else mid
    except Exception:
        name = mid
    _cache[mid] = name
    return name


def extract_full_meta(task):
    """Pull the 8 extended fields out of a getTaskById task dict."""
    if not task:
        return {}
    params = task.get("parameters") or {}
    outputs = task.get("outputs") or {}
    detail = outputs.get("detailParameters") or {}
    return {
        "prompt_full":    params.get("prompts", ""),
        "natural_prompt": (params.get("extra") or {}).get("naturalPrompts", ""),
        "seed":           str(outputs.get("seed") or ""),
        "steps":          str(detail.get("steps") or ""),
        "sampler":        detail.get("sampler", ""),
        "cfg_scale":      str(detail.get("cfg_scale") or ""),
        "model_id":       str(params.get("modelId") or ""),
        "model_name":     "",  # filled in by caller after model_name_gql
    }


def _merge_full(fm, kr):
    """Merge full-meta fields: prefer fresh fm, fall back to known-row kr."""
    return {f: (fm.get(f) or kr.get(f, "")) for f in _FULL_META_FIELDS}


def cmd_convert_existing(args, out):
    """Convert all .webp files in the backup tree to the target format in-place."""
    target = (args.convert or "png").lower()
    out_ext = ".jpg" if target in ("jpg", "jpeg") else ".png"

    webp_files = sorted(p for p in out.rglob("*.webp")
                        if not p.name.endswith(".part") and p.stat().st_size > 0)
    if not webp_files:
        print("No .webp files found under {}.".format(out))
        return

    print("Found {} .webp file(s); converting to {}.".format(len(webp_files), target))
    if args.keep_webp:
        print("--keep-webp: originals kept alongside converted files.")

    if args.dry_run:
        for p in webp_files[:10]:
            print("  {} -> {}".format(p.name, p.with_suffix(out_ext).name))
        if len(webp_files) > 10:
            print("  ... and {} more".format(len(webp_files) - 10))
        print("\nDry run -- nothing converted. Re-run without --dry-run to apply.")
        return

    ok = failed = 0
    total = len(webp_files)
    _prog = getattr(args, "progress", None)
    for i, p in enumerate(webp_files):
        _, note = convert_image(p, target, args.jpeg_quality, args.jpeg_bg,
                                keep_original=args.keep_webp)
        if note == "pillow-missing":
            raise PixAIError("--convert-existing needs Pillow:  pip install pillow")
        if note == "ok":
            ok += 1
        else:
            print("  FAILED {}: {}".format(p.name, note))
            failed += 1
        if _prog:
            _prog(i + 1, total, 0)
        else:
            sys.stdout.write("\r  {:,}/{:,}  ok {:,}  failed {:,}  ".format(
                i + 1, total, ok, failed))
            sys.stdout.flush()

    print("\nConverted: {}, failed: {}.".format(ok, failed))
    if failed:
        print("Failed files left as .webp -- re-run to retry.")


def cmd_organize(args, out, img_dir, db_path):
    """Reorganize already-downloaded files into batch/ and YYYY-MM/ folders using
    the catalog. Embeds prompt metadata, optionally converts, writes per-folder
    info files. Idempotent (only touches flat files in images/) and dry-runnable."""
    db_path = _ensure_db(out)
    if not img_dir.exists():
        raise PixAIError("No images folder at {}.".format(img_dir))

    # catalog: media_id -> row, and task_id -> [media_ids]
    meta_by_mid, mids_by_task = {}, defaultdict(list)
    for row in load_catalog(db_path):
        mid = row.get("media_id")
        if not mid:
            continue
        meta_by_mid[mid] = row
        mids_by_task[row.get("task_id", "")].append(mid)

    # Sources = flat files in images/ only, so re-runs are idempotent (already
    # organized files live in sibling folders and are left alone).
    src_by_mid = {}
    for p in sorted(img_dir.glob("*.*")):
        if p.name.endswith(".part") or p.name.startswith("_"):
            continue
        src_by_mid.setdefault(p.stem.split("_")[-1], p)

    batches_root = out / "batches"
    moved = converted = embedded = skipped = missing = 0
    month_index = defaultdict(list)   # "YYYY-MM" -> list of row dicts for index csv
    batch_txt = {}                    # folder -> info text

    plan = []  # (src, dst, is_batch, mid)
    for tid, mids in mids_by_task.items():
        distinct = list(dict.fromkeys(mids))
        is_batch = len(distinct) > 1
        for idx, mid in enumerate(sorted(distinct)):
            src = src_by_mid.get(mid)
            if not src:
                continue  # not on disk as a flat file (already organized or never got)
            row = meta_by_mid.get(mid, {})
            ext = src.suffix.lower()
            if is_batch:
                folder = batches_root / build_stem_name(
                    row.get("prompt_preview", ""), tid, "", args.name_length, args.name_sep)
                dst = folder / "{:02d}_{}{}".format(idx + 1, mid, ext)
            else:
                month = (row.get("created_at") or "")[:7] or "unknown-date"
                folder = out / month
                dst = folder / (mid + ext)
            plan.append((src, dst, is_batch, mid, row))

    print("Organize plan: {} flat files to sort ({} not found on disk are skipped)."
          .format(len(plan), sum(1 for m in meta_by_mid if m not in src_by_mid)))
    for src, dst, is_b, mid, row in plan[:6]:
        print("  {}  ->  {}".format(src.name, dst.relative_to(out)))
    if len(plan) > 6:
        print("  ... and {} more".format(len(plan) - 6))
    if args.convert:
        print("Will also convert to {} ({}).".format(
            args.convert, "keeping .webp" if args.keep_webp else "replacing .webp"))
    print("Will embed prompt metadata into PNG/JPEG files (WebP skipped).")

    if args.dry_run:
        print("\nDry run -- nothing moved. Re-run without --dry-run to apply.")
        return

    catalog_updates = {}  # media_id -> (new filename, batch name)
    _prog = getattr(args, "progress", None)

    for n, (src, dst, is_batch, mid, row) in enumerate(plan):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.resolve() != src.resolve():
            skipped += 1
            final = dst
        else:
            try:
                src.replace(dst)
                moved += 1
                final = dst
                batch_name = final.parent.parent.name if is_batch else ""
                catalog_updates[mid] = (final.name, batch_name)
            except OSError as e:
                print("  move failed {} ({})".format(src.name, e))
                continue
        # optional convert (replaces extension; updates final path)
        if args.convert:
            final, note = convert_image(final, args.convert, args.jpeg_quality,
                                        args.jpeg_bg, keep_original=args.keep_webp)
            if note == "pillow-missing":
                raise PixAIError("--convert needs Pillow:  pip install pillow")
            if note == "ok":
                converted += 1
        # embed metadata
        fields = {
            "prompt": row.get("prompt_preview", ""),
            "task_id": row.get("task_id", ""),
            "media_id": mid,
            "width": row.get("width", ""),
            "height": row.get("height", ""),
            "created_at": row.get("created_at", ""),
            "status": row.get("status", ""),
            "source": "PixAI",
        }
        note = embed_metadata(final, fields)
        if note == "ok":
            embedded += 1
        elif note == "pillow-missing":
            print("  (install Pillow to embed metadata:  pip install pillow)")

        if is_batch:
            folder = final.parent
            if folder not in batch_txt:
                batch_txt[folder] = [
                    "Prompt (preview): {}".format(row.get("prompt_preview", "")),
                    "Task ID         : {}".format(row.get("task_id", "")),
                    "Created         : {}".format(row.get("created_at", "")),
                    "Status          : {}".format(row.get("status", "")),
                    "Source          : PixAI",
                    "", "Images in this batch:",
                ]
            batch_txt[folder].append("  {}  ({}x{})".format(
                final.name, row.get("width", "?"), row.get("height", "?")))
        else:
            month = final.parent.name
            month_index[month].append({
                "filename": final.name, "media_id": mid,
                "task_id": row.get("task_id", ""),
                "prompt_preview": row.get("prompt_preview", ""),
                "width": row.get("width", ""), "height": row.get("height", ""),
                "created_at": row.get("created_at", ""),
                "status": row.get("status", ""),
            })

        if _prog:
            _prog(n + 1, len(plan), 0)
        else:
            sys.stdout.write("\r  {:,}/{:,}  moved {:,}  skip {:,}  ".format(
                n + 1, len(plan), moved, skipped))
            sys.stdout.flush()

    if not _prog:
        print()  # newline after \r output

    # write per-batch _prompt.txt
    for folder, lines in batch_txt.items():
        (folder / "_prompt.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # write/append per-month _index.csv
    for month, entries in month_index.items():
        idx_path = out / month / "_index.csv"
        new = not idx_path.exists()
        with open(idx_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["filename", "media_id", "task_id",
                                              "prompt_preview", "width", "height",
                                              "created_at", "status"])
            if new:
                w.writeheader()
            for e in entries:
                w.writerow(e)

    # Update catalog filenames to reflect new locations
    if catalog_updates:
        rows = load_catalog(db_path)
        for r in rows:
            if r["media_id"] in catalog_updates:
                r["filename"], r["batch"] = catalog_updates[r["media_id"]]
        save_catalog(db_path, rows)
        print("Updated {:,} catalog filename/batch entries.".format(len(catalog_updates)))

    print("\nOrganized: moved {}, already-in-place {}.".format(moved, skipped))
    if args.convert:
        print("Converted to {}: {}.".format(args.convert, converted))
    print("Embedded metadata into {} images.".format(embedded))
    print("Batch folders: {}   Month folders written with _index.csv.".format(
        len(batch_txt)))


# ---------------------------------------------------------------------------
# Callable API (used by the GUI; also called by main() for the CLI)
# ---------------------------------------------------------------------------
def _make_session(token_val):
    """Validate config, load token, return a configured requests.Session.
    Re-reads config.json at call time so the GUI works even when the module
    was imported before the working directory was set correctly."""
    global PERSISTED_QUERY_HASH, U3T, USER_ID, TASK_DETAIL_HASH, MODEL_DETAIL_HASH
    fresh = _load_config()
    if fresh:
        PERSISTED_QUERY_HASH = fresh.get("PERSISTED_QUERY_HASH", "") or PERSISTED_QUERY_HASH
        U3T = fresh.get("U3T", "") or U3T
        USER_ID = fresh.get("USER_ID", "") or USER_ID
        TASK_DETAIL_HASH = fresh.get("TASK_DETAIL_HASH", "") or TASK_DETAIL_HASH
        MODEL_DETAIL_HASH = fresh.get("MODEL_DETAIL_HASH", "") or MODEL_DETAIL_HASH
    if not all([PERSISTED_QUERY_HASH, U3T, USER_ID]):
        raise PixAIError(
            "config.json is missing or incomplete "
            "(need PERSISTED_QUERY_HASH, U3T, USER_ID).\n"
            "Copy config.example.json to config.json and fill in your captured values.\n"
            "See the README -> Configuration for instructions."
        )
    token = load_token(token_val)
    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/json",
        "User-Agent": "pixai-personal-backup/1.0",
        "apollo-require-preflight": "true",
        "x-apollo-operation-name": OPERATION_NAME,
    })
    return session


def run_probe(args):
    """Test API connection and resolve full-res media URL for the newest task."""
    session = _make_session(getattr(args, "token", None))
    print("SSL trust store via truststore: {}".format(
        "on" if _TRUSTSTORE_ACTIVE else "off (requests default)"))
    print("Fetching newest page...\n")
    conn = find_connection(gql(session, page_variables(args.page_size)))
    if not conn:
        print("No connection found.")
        return
    edges = conn.get("edges", [])
    pi = conn.get("pageInfo", {})
    print("OK -- {} items. hasPreviousPage={}".format(
        len(edges), pi.get("hasPreviousPage")))
    node = edges[0].get("node", edges[0]) if edges else {}
    meta = extract_meta(node)
    mids = media_ids_for(node)
    print("First task: id={} media_ids={}".format(meta["task_id"], mids))
    print("Prompt preview:", meta["prompt_preview"][:80])
    if mids:
        url, info = resolve_media(session, mids[0])
        print("\nResolved full-res URL:", url or "(none!)")
        print("Dimensions: {}x{}".format(info.get("width"), info.get("height")))
        if url:
            print("\nLooks right? Run a download to back up everything.")
        else:
            print("\nCouldn't find a URL in the media object -- paste this back.")


def run_count(args):
    """Tally total tasks and images in the library without downloading."""
    session = _make_session(getattr(args, "token", None))
    count_size = getattr(args, "count_page_size", 5000)
    print("Counting your whole library (page size {})...".format(count_size))
    before = None
    tasks = images = page = 0
    batched_tasks = 0
    while True:
        page += 1
        conn = find_connection(gql(session, page_variables(count_size, before)))
        if not conn:
            break
        edges = conn.get("edges", [])
        if not edges:
            break
        for edge in edges:
            node = edge.get("node", edge)
            tasks += 1
            n = len(media_ids_for(node))
            images += n
            if n > 1:
                batched_tasks += 1
        pi = conn.get("pageInfo", {})
        more = pi.get("hasPreviousPage")
        print("  page {}: {} tasks so far, {} images so far{}".format(
            page, tasks, images, "" if more else "  (reached the end)"))
        if not more:
            break
        before = pi.get("startCursor")
        time.sleep(args.delay)
    print("\n================ LIBRARY TOTALS ================")
    print("Total tasks (generations) : {}".format(tasks))
    print("Total images              : {}  (mediaId + batchMediaIds)".format(images))
    print("Tasks that are batches    : {}  (>1 image each)".format(batched_tasks))
    print("Fetched in {} request(s).".format(page))
    out = Path(args.out)
    disk_count = disk_bytes = 0
    if out.exists():
        for p in out.rglob("*"):
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS and not p.name.endswith(".part"):
                disk_count += 1
                disk_bytes += p.stat().st_size
    print("\n--- On disk ({}) ---".format(args.out))
    print("Image files on disk       : {}".format(disk_count))
    print("Total collection size     : {}".format(
        _format_size(disk_bytes) if disk_bytes else "0 B (folder empty or not found)"))
    if images > tasks:
        print("\nNote: image count exceeds task count because some older tasks\n"
              "produced batches of several images -- all of them get downloaded.")


def run_catalog_stats(args):
    """Summarize the existing catalog (no network needed)."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    img_dir = out / "images"
    total = downloaded = missing = pending = 0
    for row in load_catalog(db_path):
        total += 1
        if row.get("filename"):
            downloaded += 1
        elif not row.get("url"):
            missing += 1
        else:
            pending += 1
    print("Catalog: {}".format(db_path))
    print("Total image entries : {}".format(total))
    print("  downloaded files  : {}".format(downloaded))
    print("  resolved, pending : {}".format(pending))
    print("  no URL (missing)  : {}".format(missing))
    disk_count = disk_bytes = 0
    for p in out.rglob("*"):
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS and not p.name.endswith(".part"):
            disk_count += 1
            disk_bytes += p.stat().st_size
    if disk_count:
        print("Image files on disk : {}  ({})".format(disk_count, _format_size(disk_bytes)))


def run_backfill_meta(args):
    """Fill in missing url/width/height for catalog rows via resolve_media().
    Safe to re-run -- skips rows that already have all three fields."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    session = _make_session(getattr(args, "token", None))
    rows = load_catalog(db_path)

    to_fill = [r for r in rows if not (r.get("url") and r.get("width") and r.get("height"))]
    print("Found {:,} rows missing url/width/height (out of {:,} total).".format(
        len(to_fill), len(rows)))
    if not to_fill:
        print("Nothing to backfill.")
        return

    updated = failed = 0
    _prog = getattr(args, "progress", None)
    for i, row in enumerate(to_fill):
        url, info = resolve_media(session, row["media_id"])
        if url:
            row["url"] = url
            row["width"] = str(info.get("width") or "")
            row["height"] = str(info.get("height") or "")
            updated += 1
        else:
            failed += 1
        if _prog:
            _prog(i + 1, len(to_fill), 0)
        else:
            sys.stdout.write("\r  {:,}/{:,}  updated {:,}  failed {:,}  ".format(
                i + 1, len(to_fill), updated, failed))
            sys.stdout.flush()
        time.sleep(args.delay)

    print("\nWriting catalog...")
    save_catalog(db_path, to_fill)
    print("Done. Updated {:,} rows, {:,} still missing.".format(updated, failed))


def run_backfill_full_meta(args):
    """Fill in prompt_full/natural_prompt/seed/steps/sampler/cfg_scale/model_id/model_name
    for catalog rows missing them, using getTaskById + getGenerationModelByVersionId.
    Also fills url/width/height from the task's media object as a free side effect.
    Safe to re-run -- skips rows that already have prompt_full."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    session = _make_session(getattr(args, "token", None))

    if not TASK_DETAIL_HASH:
        raise PixAIError(
            "--backfill-full-meta requires TASK_DETAIL_HASH in config.json. "
            "See README -> Full Meta for capture instructions.")

    rows = load_catalog(db_path)

    # Work per unique task_id (one API call covers all media in that task)
    needs_fill = [r for r in rows if not r.get("prompt_full")]
    task_ids = list(dict.fromkeys(r["task_id"] for r in needs_fill if r.get("task_id")))
    print("Found {:,} rows missing full meta across {:,} unique tasks.".format(
        len(needs_fill), len(task_ids)))
    if not task_ids:
        print("Nothing to backfill.")
        return

    # Fetch and cache full meta per task_id
    task_cache = {}  # task_id -> full meta dict
    fetched = failed = 0
    _prog = getattr(args, "progress", None)
    for i, tid in enumerate(task_ids):
        task_data = task_detail_gql(session, tid)
        fm = extract_full_meta(task_data)
        if fm.get("model_id"):
            fm["model_name"] = model_name_gql(session, fm["model_id"])

        # Also grab media URL/dimensions from the task's media object if present
        media_obj = (task_data or {}).get("media") or {}
        if media_obj:
            media_urls = media_obj.get("urls") or []
            by_v = {str(u.get("variant", "")).upper(): u["url"]
                    for u in media_urls if isinstance(u, dict) and u.get("url")}
            for pref in ("PUBLIC", "ORIGINAL", "ORIG", "FULL", "THUMBNAIL"):
                if pref in by_v:
                    fm["_media_url"] = by_v[pref]
                    break
            fm["_media_width"] = str(media_obj.get("width") or "")
            fm["_media_height"] = str(media_obj.get("height") or "")

        task_cache[tid] = fm
        if fm.get("prompt_full"):
            fetched += 1
        else:
            failed += 1
        if _prog:
            _prog(i + 1, len(task_ids), 0)
        else:
            sys.stdout.write("\r  Tasks {:,}/{:,}  fetched {:,}  failed {:,}  ".format(
                i + 1, len(task_ids), fetched, failed))
            sys.stdout.flush()
        time.sleep(args.delay)

    print("\nApplying to {:,} catalog rows...".format(len(rows)))
    for row in rows:
        fm = task_cache.get(row.get("task_id"), {})
        if not fm:
            continue
        for f in _FULL_META_FIELDS:
            if not row.get(f) and fm.get(f):
                row[f] = fm[f]
        # Backfill url/width/height from task media as bonus
        if not row.get("url") and fm.get("_media_url"):
            row["url"] = fm["_media_url"]
        if not row.get("width") and fm.get("_media_width"):
            row["width"] = fm["_media_width"]
        if not row.get("height") and fm.get("_media_height"):
            row["height"] = fm["_media_height"]

    save_catalog(db_path, rows)
    print("Done. Fetched {:,} tasks, {:,} failed, catalog updated.".format(fetched, failed))


def cmd_rename(args, out, img_dir, db_path):
    """Rename already-downloaded files to the prompt_taskid_mediaid scheme."""
    db_path = _ensure_db(out)
    if not img_dir.exists():
        raise PixAIError("No images folder at {}.".format(img_dir))
    info_by_mid = {}
    for row in load_catalog(db_path):
        mid = row.get("media_id")
        if mid:
            info_by_mid[mid] = (row.get("task_id", ""),
                                row.get("prompt_preview", ""))
    renamed = skipped = unmatched = clash = 0
    planned = []
    used_names = set()
    for p in sorted(img_dir.glob("*.*")):
        if p.name.endswith(".part"):
            continue
        stem, ext = p.stem, p.suffix
        mid = stem.split("_")[-1]
        if mid not in info_by_mid:
            unmatched += 1
            continue
        task_id, prompt = info_by_mid[mid]
        new_stem = build_stem_name(prompt, task_id, mid,
                                   args.name_length, args.name_sep)
        new_name = new_stem + ext
        if new_name == p.name:
            skipped += 1
            continue
        target = img_dir / new_name
        if new_name in used_names or (target.exists() and target != p):
            clash += 1
            continue
        used_names.add(new_name)
        planned.append((p, target))

    print("Rename plan: {} to rename, {} already correct, {} unmatched in "
          "catalog, {} skipped (name clash).".format(
              len(planned), skipped, unmatched, clash))
    for src, dst in planned[:8]:
        print("  {}  ->  {}".format(src.name, dst.name))
    if len(planned) > 8:
        print("  ... and {} more".format(len(planned) - 8))

    if getattr(args, "dry_run", False):
        print("\nDry run -- nothing changed. Re-run without --dry-run to apply.")
        return
    for src, dst in planned:
        try:
            src.rename(dst)
            renamed += 1
        except OSError as e:
            print("  FAILED {} ({})".format(src.name, e))
    print("\nRenamed {} files.".format(renamed))


def run_download(args, progress=None):
    """Run the full paginated download + catalog loop.

    progress: optional callable(done: int, total: int) invoked after each
    image is processed (downloaded or skipped). Used by the GUI progress bar.
    When stdout is a real terminal and no progress callback is provided, a
    \r-overwriting ASCII progress bar is printed instead.
    """
    out = Path(args.out)
    img_dir = out / "images"
    raw_path = out / "raw_tasks.jsonl"
    db_path  = out / "catalog.db"

    # Ensure db exists and is populated (auto-migrates catalog.csv if needed)
    try:
        db_path = _ensure_db(out)
    except PixAIError:
        # Fresh install with no prior catalog — create empty db
        init_db(db_path)

    # Load existing catalog so prior-session rows are never lost
    known = {r["media_id"]: r for r in load_catalog(db_path) if r.get("media_id")}
    if known:
        print("Loaded {} existing catalog entries.\n".format(len(known)))

    session = _make_session(getattr(args, "token", None))
    print("SSL trust store via truststore: {}".format(
        "on" if _TRUSTSTORE_ACTIVE else "off (requests default)"))

    if use_full_meta and not TASK_DETAIL_HASH:
        raise PixAIError(
            "--full-meta requires TASK_DETAIL_HASH in config.json. "
            "See README -> Full Meta for capture instructions.")

    if not getattr(args, "organize_adv_live", False):
        img_dir.mkdir(parents=True, exist_ok=True)

    total_images = _quick_count(session)

    # Seed progress by counting image files already on disk. Works for flat,
    # --organize-adv, and --organize-adv-live since rglob finds files in
    # batches/ and YYYY-MM/ equally.
    already_done = 0
    disk_bytes = 0
    if out.exists():
        for p in out.rglob("*"):
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS and not p.name.endswith(".part"):
                already_done += 1
                disk_bytes += p.stat().st_size
    processed = already_done

    if already_done:
        print("Resuming: {} image files already on disk ({}).\n".format(
            already_done, _format_size(disk_bytes)))

    def _tick():
        nonlocal processed
        processed += 1
        if progress:
            progress(processed, total_images, dl["ok"])
        elif sys.stdout.isatty():
            sys.stdout.write(_progress_line(processed, total_images, dl["ok"]))
            sys.stdout.flush()

    if progress:
        progress(processed, total_images, 0)

    print("Walking your generation history (newest -> oldest)...")
    raw_f = open(raw_path, "w", encoding="utf-8")

    use_full_meta = getattr(args, "full_meta", False)
    _full_meta_cache = {}  # task_id -> full meta dict

    before = None
    seen = 0
    written = set()   # media_ids written this session
    dl = {"ok": 0, "skip": 0, "missing": 0, "fail": 0}
    page = 0
    try:
        while True:
            page += 1
            conn = find_connection(gql(session, page_variables(args.page_size, before)))
            if not conn:
                print("No connection; stopping.")
                break
            edges = conn.get("edges", [])
            if not edges:
                break
            print("Page {}: {} tasks (total {})".format(page, len(edges), seen + len(edges)))

            page_rows = []  # rows accumulated this page; upserted after each page

            for edge in edges:
                node = edge.get("node", edge)
                raw_f.write(json.dumps(node, ensure_ascii=False) + "\n")
                meta = extract_meta(node)
                all_mids = media_ids_for(node)
                is_batch = len(all_mids) > 1

                # Fetch full task detail once per task_id (cached; batches cost 1 call)
                full_meta = {}
                if use_full_meta:
                    tid = meta["task_id"]
                    if tid not in _full_meta_cache:
                        task_data = task_detail_gql(session, tid)
                        fm = extract_full_meta(task_data)
                        if fm.get("model_id"):
                            fm["model_name"] = model_name_gql(session, fm["model_id"])
                        _full_meta_cache[tid] = fm
                        time.sleep(args.delay)
                    full_meta = _full_meta_cache.get(meta["task_id"], {})

                if getattr(args, "organize_adv_live", False):
                    if is_batch:
                        folder_name = build_stem_name(
                            meta["prompt_preview"], meta["task_id"], "",
                            args.name_length, args.name_sep)
                        task_folder = out / "batches" / folder_name
                    else:
                        month = (meta.get("created_at") or "")[:7] or "unknown-date"
                        task_folder = out / month
                    if not getattr(args, "collect_only", False):
                        task_folder.mkdir(parents=True, exist_ok=True)
                else:
                    task_folder = img_dir

                batch_results = []
                for idx, mid in enumerate(all_mids):
                    existing = (None if getattr(args, "collect_only", False)
                                else already_downloaded(out, mid))
                    if existing:
                        dl["skip"] += 1
                        k = known.get(mid, {})
                        page_rows.append({
                            "task_id":        k.get("task_id") or meta["task_id"],
                            "media_id":       mid,
                            "filename":       existing.name,
                            "url":            k.get("url", ""),
                            "width":          k.get("width", ""),
                            "height":         k.get("height", ""),
                            "prompt_preview": k.get("prompt_preview") or meta["prompt_preview"],
                            "status":         k.get("status") or meta["status"],
                            "created_at":     k.get("created_at") or meta["created_at"],
                            **_merge_full(full_meta, k),
                        })
                        written.add(mid)
                        _tick()
                        continue
                    if getattr(args, "organize_adv_live", False) and is_batch:
                        stem_name = "{:02d}_{}".format(idx + 1, mid)
                    else:
                        stem_name = build_stem_name(
                            meta["prompt_preview"], meta["task_id"], mid,
                            args.name_length, args.name_sep)
                    stem = task_folder / stem_name
                    url, info = resolve_media(session, mid)
                    w, h = info.get("width", ""), info.get("height", "")
                    if not url:
                        dl["missing"] += 1
                        page_rows.append({
                            "task_id": meta["task_id"], "media_id": mid,
                            "filename": "", "url": "", "width": w, "height": h,
                            "prompt_preview": meta["prompt_preview"],
                            "status": meta["status"], "created_at": meta["created_at"],
                            **_merge_full(full_meta, known.get(mid, {})),
                        })
                        written.add(mid)
                        _tick()
                        continue
                    if getattr(args, "collect_only", False):
                        page_rows.append({
                            "task_id": meta["task_id"], "media_id": mid,
                            "filename": "", "url": url, "width": w, "height": h,
                            "prompt_preview": meta["prompt_preview"],
                            "status": meta["status"], "created_at": meta["created_at"],
                            **_merge_full(full_meta, known.get(mid, {})),
                        })
                        written.add(mid)
                        _tick()
                        continue
                    status, path = download(
                        session, url, stem,
                        convert=getattr(args, "convert", None),
                        jpeg_quality=getattr(args, "jpeg_quality", 92),
                        jpeg_bg=getattr(args, "jpeg_bg", "white"),
                        keep_webp=getattr(args, "keep_webp", False))
                    dl[status] += 1
                    _tick()
                    page_rows.append({
                        "task_id": meta["task_id"], "media_id": mid,
                        "filename": path.name if path else "",
                        "url": url, "width": w, "height": h,
                        "prompt_preview": meta["prompt_preview"],
                        "status": meta["status"], "created_at": meta["created_at"],
                        **_merge_full(full_meta, known.get(mid, {})),
                    })
                    written.add(mid)
                    if path and status in ("ok", "skip"):
                        batch_results.append((idx, mid, path, info))
                    if status == "ok":
                        time.sleep(args.delay)

            # Upsert this page's rows so progress is durable even on interrupt
            if page_rows:
                save_catalog(db_path, page_rows)

                if getattr(args, "organize_adv_live", False) and batch_results:
                    if is_batch:
                        prompt_txt = task_folder / "_prompt.txt"
                        if not prompt_txt.exists():
                            lines = [
                                "Prompt (preview): {}".format(meta["prompt_preview"]),
                                "Task ID         : {}".format(meta["task_id"]),
                                "Created         : {}".format(meta["created_at"]),
                                "Status          : {}".format(meta["status"]),
                                "Source          : PixAI",
                                "", "Images in this batch:",
                            ]
                            for _, _, bp, bi in batch_results:
                                lines.append("  {}  ({}x{})".format(
                                    bp.name, bi.get("width", "?"), bi.get("height", "?")))
                            prompt_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    else:
                        idx_path = task_folder / "_index.csv"
                        new_file = not idx_path.exists()
                        with open(idx_path, "a", newline="", encoding="utf-8") as f_idx:
                            w_idx = csv.DictWriter(f_idx, fieldnames=[
                                "filename", "media_id", "task_id", "prompt_preview",
                                "width", "height", "created_at", "status"])
                            if new_file:
                                w_idx.writeheader()
                            for _, bi_mid, bi_path, bi_info in batch_results:
                                w_idx.writerow({
                                    "filename": bi_path.name,
                                    "media_id": bi_mid,
                                    "task_id": meta["task_id"],
                                    "prompt_preview": meta["prompt_preview"],
                                    "width": bi_info.get("width", ""),
                                    "height": bi_info.get("height", ""),
                                    "created_at": meta["created_at"],
                                    "status": meta["status"],
                                })

                seen += 1
                if args.max and seen >= args.max:
                    break

            raw_f.flush()
            if args.max and seen >= args.max:
                print("Reached --max limit.")
                break
            pi = conn.get("pageInfo", {})
            if not pi.get("hasPreviousPage"):
                break
            before = pi.get("startCursor")
            time.sleep(args.delay)

    finally:
        raw_f.close()

    if not progress and sys.stdout.isatty() and processed:
        print()  # move past the \r progress bar line

    print("\nDone. Tasks seen: {}".format(seen))
    print("Images -> downloaded {}, skipped {}, missing {}, failed {}".format(
        dl["ok"], dl["skip"], dl["missing"], dl["fail"]))
    print("Catalog: {}\nRaw: {}\nImages: {}".format(db_path, raw_path, img_dir))
    if dl["fail"]:
        print("Some failed -- just re-run; finished files are skipped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Back up your own PixAI gallery.")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    ap.add_argument("--token",
                    help="Bearer token for PixAI API auth (overrides PIXAI_TOKEN env var "
                         "and token.txt)")
    ap.add_argument("--out", default="pixai_backup",
                    help="output folder for images and catalog (default: pixai_backup)")
    ap.add_argument("--page-size", type=int, default=20, help="items per page (try 50)")
    ap.add_argument("--max", type=int, default=0, help="stop after N tasks (0=all)")
    ap.add_argument("--delay", type=float, default=0.4,
                    help="seconds to wait between API requests (default: 0.4)")
    ap.add_argument("--variant", default=None,
                    help="force a media variant (skip auto-detect), e.g. original")
    ap.add_argument("--probe", action="store_true",
                    help="show first page + auto-detect the full-res variant, then exit")
    ap.add_argument("--count", action="store_true",
                    help="tally total tasks + images via the API (no downloads), then exit")
    ap.add_argument("--count-page-size", type=int, default=5000,
                    help="page size used by --count (bigger = fewer requests; "
                         "server errors above ~10000 so default is 5000)")
    ap.add_argument("--catalog-stats", action="store_true",
                    help="summarize the existing catalog.db (counts only), then exit")
    ap.add_argument("--collect-only", action="store_true",
                    help="scan and catalog images without downloading files")
    ap.add_argument("--name-length", type=int, default=60,
                    help="max characters of the prompt used in filenames (default 60)")
    ap.add_argument("--name-sep", default="_", choices=["_", "-"],
                    help="word separator in filenames (default _)")
    ap.add_argument("--convert", default=None, choices=["png", "jpeg", "jpg"],
                    help="convert each downloaded webp to png or jpeg (needs Pillow). "
                         "Replaces the .webp unless --keep-webp is set.")
    ap.add_argument("--jpeg-quality", type=int, default=92,
                    help="JPEG quality 1-100 when --convert jpeg (default 92)")
    ap.add_argument("--jpeg-bg", default="white", choices=["white", "black"],
                    help="background to flatten transparency onto for JPEG")
    ap.add_argument("--keep-webp", action="store_true",
                    help="keep the original .webp after converting")
    ap.add_argument("--convert-existing", action="store_true",
                    help="convert all already-downloaded .webp files to --convert format "
                         "(default png). No token needed. Supports --dry-run and --keep-webp.")
    ap.add_argument("--organize", action="store_true",
                    help="rename already-downloaded files to the prompt_taskid_mediaid "
                         "scheme using catalog.db, then exit")
    ap.add_argument("--organize-live", action="store_true",
                    help="apply prompt_taskid_mediaid naming to files as they download "
                         "(same as default naming; flag makes intent explicit)")
    ap.add_argument("--organize-adv", action="store_true",
                    help="sort already-downloaded files into batches/ and YYYY-MM/ "
                         "folders with info files + embedded metadata, then exit")
    ap.add_argument("--organize-adv-live", action="store_true",
                    help="sort files into batches/ and YYYY-MM/ folders live as they "
                         "download, writing _prompt.txt and _index.csv per folder")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --organize or --organize-adv, show plan without making changes")
    ap.add_argument("--full-meta", action="store_true",
                    help="fetch full prompt, seed, steps, sampler, CFG, and model name for each "
                         "task via a second API call (requires TASK_DETAIL_HASH + MODEL_DETAIL_HASH "
                         "in config.json). One extra call per unique task; batch images share one call.")
    ap.add_argument("--backfill-meta", action="store_true",
                    help="fill in missing url/width/height in catalog.db via resolve_media "
                         "for rows that lack them, then exit")
    ap.add_argument("--backfill-full-meta", action="store_true",
                    help="fill in prompt_full/seed/model/etc in catalog.db via getTaskById "
                         "for rows that lack them; also backfills url/width/height as a bonus, then exit")
    ap.add_argument("--export-csv", action="store_true",
                    help="export catalog.db to catalog.csv for interop/backup, then exit")
    args = ap.parse_args()

    if args.probe and args.count:
        print("Note: --probe exits before --count runs. Run them separately:\n"
              "  python pixai_gallery_backup.py --count\n"
              "Continuing with --probe only.\n")

    out = Path(args.out)
    img_dir = out / "images"
    db_path  = out / "catalog.db"
    csv_path = out / "catalog.csv"

    try:
        if args.catalog_stats:
            run_catalog_stats(args)
            return
        if args.export_csv:
            if not db_path.exists():
                sys.exit("No catalog.db found at {}.".format(db_path))
            export_csv(db_path, csv_path)
            print("Exported {:,} rows to {}.".format(
                len(load_catalog(db_path)), csv_path))
            return
        if args.backfill_meta:
            run_backfill_meta(args)
            return
        if args.backfill_full_meta:
            run_backfill_full_meta(args)
            return
        if args.convert_existing:
            cmd_convert_existing(args, out)
            return
        if args.organize_adv:
            cmd_organize(args, out, img_dir, db_path)
            return
        if args.organize:
            cmd_rename(args, out, img_dir, db_path)
            return
        if args.probe:
            run_probe(args)
            return
        if args.count:
            run_count(args)
            return
        run_download(args)
    except PixAIError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

# ===========================================================================
# RECAPTURE (only if the site changes): re-grab the persisted sha256Hash, U3T,
# and USER_ID from Network tab -> graphql row -> Payload, and update config.json.
# Keep your token private.
# ===========================================================================
