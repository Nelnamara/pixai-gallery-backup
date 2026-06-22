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

__version__ = "1.2.0"

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict, Counter
from pathlib import Path

from pixai_gallery import (CATALOG_FIELDS, _IMAGE_EXTS, init_db, load_catalog,
                            save_catalog, migrate_csv_to_db, export_csv, _db_is_empty,
                            media_id_of, find_files_for_media_id)


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
# Default to the captured getGenerationModelByVersionId hash so model-name
# resolution works out of the box (override in config.json if it rotates).
MODEL_DETAIL_HASH = _cfg.get("MODEL_DETAIL_HASH", "") or \
    "0d2ab28b2991e3fd74672ffec0adf8947e599d79e0039348a7d2642e0bf8c9bc"
# Published-artwork ops (for --sync-artworks). These are public persisted-query
# identifiers, not secrets; captured 2026-06-22. Override in config.json if a
# PixAI frontend update rotates them.
ARTWORK_LIST_HASH = _cfg.get("ARTWORK_LIST_HASH", "") or \
    "ce6f4a6e63fe210c7f77b29c7b8bdce8b7ede4d4520c01de1d36e01b224918a5"
ARTWORK_DETAIL_HASH = _cfg.get("ARTWORK_DETAIL_HASH", "") or \
    "ac39a87c58451559f9dcbf2c04862c1ee3260f9645ed60fdfb574e41689a6766"
CLIENT_LIBRARY_ARTWORK = {"name": "@apollo/client", "version": "4.1.4"}
# ===========================================================================

# Media URL: https://api.pixai.art/v1/media/<id>/<variant>
MEDIA_TMPL = "https://api.pixai.art/v1/media/{id}/{variant}"
MEDIA_BASE = "https://api.pixai.art/v1/media/{id}"
# Tried in order; first one returning a real image (and not the thumbnail) wins.
VARIANT_CANDIDATES = ["original", "orig", "full", "hd", "public", "raw", "thumbnail"]
# ===========================================================================


def load_token(cli_token=None):
    # Priority: explicit --token > PIXAI_API_KEY (config) > PIXAI_TOKEN env > token.txt.
    # The official API key is preferred because it's long-lived (up to ~2 years) and
    # authenticates the same Bearer endpoint -- no expiring browser JWT to recapture.
    if cli_token:
        return cli_token.strip()
    api_key = (_cfg.get("PIXAI_API_KEY", "") or "").strip()
    if not api_key:
        fresh = _load_config()
        api_key = (fresh.get("PIXAI_API_KEY", "") if fresh else "").strip()
    if api_key:
        return api_key
    env = os.environ.get("PIXAI_TOKEN")
    if env:
        return env.strip()
    for f in (Path(__file__).resolve().parent / "token.txt", Path("token.txt")):
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    raise PixAIError("No credential found. Add PIXAI_API_KEY to config.json (preferred), "
                     "set PIXAI_TOKEN, pass --token, or create token.txt.")


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

    Uses the shared `find_files_for_media_id` matcher so resume recognizes BOTH
    naming layouts — prefixed `*_<mid>.*` AND bare `<mid>.*` (the single-image
    --organize month layout). Before this was aligned, bare month files were
    invisible to resume, so every re-download re-fetched them as flat files and
    organize left the flat copy orphaned -> the images/+month duplication."""
    matches = find_files_for_media_id(root, media_id)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Content hashing (shared by --audit content dedup and organize's same-bytes check)
# ---------------------------------------------------------------------------
def _file_sha(path, _chunk=1 << 20):
    """Streamed blake2b digest of a file. Returns hex str, or None on read error."""
    import hashlib
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(_chunk), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def _same_bytes(a, b):
    """True if two files are byte-identical. Cheap size check first, then hash."""
    try:
        sa, sb = a.stat().st_size, b.stat().st_size
    except OSError:
        return False
    if sa != sb:
        return False
    ha = _file_sha(a)
    return ha is not None and ha == _file_sha(b)


def _same_pixels(a, b):
    """True if two images have identical pixel content, ignoring container/metadata
    differences (e.g. a PNG with embedded prompt text vs the same image without).
    Returns None if Pillow is unavailable or either file can't be decoded."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    try:
        with Image.open(a) as ia, Image.open(b) as ib:
            if ia.size != ib.size:
                return False
            ra, rb = ia.convert("RGBA"), ib.convert("RGBA")
            return ImageChops.difference(ra, rb).getbbox() is None
    except Exception:
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
    if "avif" in ct:
        return ".avif"
    # Animated artworks resolve to video files
    if "mp4" in ct:
        return ".mp4"
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
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
    "sampler", "cfg_scale", "model_id", "model_name", "loras",
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
    """Pull the extended fields out of a getTaskById task dict."""
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
        "loras":          "",  # filled in by caller via resolve_loras()
    }


def resolve_loras(session, task):
    """Read parameters.lora ({loraVersionId: weight}) from a getTaskById task and
    return a readable "Name:0.7, Name2:0.5" string, resolving each LoRA id to a
    name via getGenerationModelByVersionId (cached). Unresolvable ids keep the
    number. Empty string if the task used no LoRAs."""
    params = (task or {}).get("parameters") or {}
    lora = params.get("lora") or {}
    if not isinstance(lora, dict) or not lora:
        return ""
    parts = []
    for vid, weight in lora.items():
        name = model_name_gql(session, vid)
        if not name or str(name) == str(vid) or str(name).isdigit():
            name = "lora {}".format(vid)
        try:
            w = "{:g}".format(float(weight))
        except (TypeError, ValueError):
            w = str(weight)
        parts.append("{}:{}".format(name, w))
    return ", ".join(parts)


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


# ---------------------------------------------------------------------------
# Duplicate audit + dedup (filesystem-truth; independent of catalog.db)
# ---------------------------------------------------------------------------
# Keeper priority when the same image lives in several buckets: lower wins
# (i.e. we KEEP the most-organized copy and remove the rest). This reinforces
# --organize's layout instead of fighting it.
_BUCKET_PRIORITY = {"batches": 0, "month": 1, "images": 2, "other": 3}


def _bucket_of(rel_path):
    """Classify a path (relative to out_dir) into a top-level bucket name."""
    top = str(rel_path).replace("\\", "/").split("/")[0]
    if top == "images":
        return "images"
    if top == "batches":
        return "batches"
    if top == "unknown-date":
        return "month"
    if len(top) == 7 and top[4] == "-" and top[:4].isdigit():
        return "month"
    return "other"


def _scan_media_files(out_dir):
    """One walk of the tree. Yields (path, rel, bucket, media_id) for every image
    file outside gallery/ and _duplicates/. Single source of truth for the audit."""
    gallery_dir = out_dir / "gallery"
    quarantine_dir = out_dir / "_duplicates"
    for p in out_dir.rglob("*"):
        if p.suffix.lower() not in _IMAGE_EXTS or not p.is_file():
            continue
        if p.name.endswith(".part"):
            continue
        if _is_under_dir(p, gallery_dir) or _is_under_dir(p, quarantine_dir):
            continue
        rel = p.relative_to(out_dir)
        yield p, rel, _bucket_of(rel), media_id_of(p)


def _is_under_dir(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def audit_collection(out_dir, content=True, progress=None):
    """Filesystem-truth duplicate audit. Returns a dict:
        per_bucket       : {bucket: count}
        class_a          : [ {media_id, files:[(rel,bucket,size)], keeper, losers} ]
        class_b          : [ {sha, files:[(rel,bucket,size,media_id)], keeper, losers} ]
        totals           : counts + reclaimable bytes
    Class A = same media_id in >1 location (no hashing needed).
    Class B = byte-identical content under DIFFERENT media_ids (size-bucketed hash).
    """
    by_mid = defaultdict(list)      # mid -> [(path, rel, bucket, size)]
    by_size = defaultdict(list)     # size -> [(path, rel, bucket, mid)]
    per_bucket = Counter()
    all_files = list(_scan_media_files(out_dir))
    total = len(all_files)
    for i, (p, rel, bucket, mid) in enumerate(all_files):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        per_bucket[bucket] += 1
        by_mid[mid].append((p, rel, bucket, size))
        by_size[size].append((p, rel, bucket, mid))
        if progress and (i % 500 == 0 or i + 1 == total):
            progress(i + 1, total, 0)

    def _keeper(items, key_bucket):
        # items: list of tuples; key_bucket(item) -> bucket name. Prefer organized,
        # then shortest path (stable), so the canonical copy is deterministic.
        return min(items, key=lambda it: (_BUCKET_PRIORITY.get(key_bucket(it), 9),
                                          len(str(it[1]))))

    # ---- Class A: same media_id across >1 distinct bucket -------------------
    class_a = []
    for mid, items in by_mid.items():
        buckets = {b for (_, _, b, _) in items}
        if len(items) > 1 and len(buckets) > 1:
            keeper = _keeper(items, lambda it: it[2])
            losers = [it for it in items if it[0] != keeper[0]]
            class_a.append({"media_id": mid, "files": items,
                            "keeper": keeper, "losers": losers})

    # ---- Class B: identical bytes, different media_id -----------------------
    class_b = []
    if content:
        # Only hash within same-size groups that span >1 distinct media_id.
        candidates = [(s, grp) for s, grp in by_size.items()
                      if len({m for (_, _, _, m) in grp}) > 1]
        hashed = 0
        n_to_hash = sum(len(grp) for _, grp in candidates)
        by_sha = defaultdict(list)
        for s, grp in candidates:
            for (p, rel, bucket, mid) in grp:
                sha = _file_sha(p)
                hashed += 1
                if sha:
                    by_sha[sha].append((p, rel, bucket, s, mid))
                if progress and (hashed % 200 == 0 or hashed == n_to_hash):
                    progress(hashed, max(n_to_hash, 1), 1)
        for sha, items in by_sha.items():
            mids = {m for (_, _, _, _, m) in items}
            if len(items) > 1 and len(mids) > 1:
                keeper = _keeper(items, lambda it: it[2])
                losers = [it for it in items if it[0] != keeper[0]]
                class_b.append({"sha": sha, "files": items,
                                "keeper": keeper, "losers": losers})

    reclaim_a = sum(sz for g in class_a for (_, _, _, sz) in g["losers"])
    reclaim_b = sum(it[3] for g in class_b for it in g["losers"])
    return {
        "per_bucket": dict(per_bucket),
        "class_a": class_a,
        "class_b": class_b,
        "totals": {
            "files": total,
            "class_a_groups": len(class_a),
            "class_a_redundant": sum(len(g["losers"]) for g in class_a),
            "class_b_groups": len(class_b),
            "class_b_redundant": sum(len(g["losers"]) for g in class_b),
            "reclaimable_bytes": reclaim_a + reclaim_b,
        },
    }


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "{:.1f} {}".format(n, unit)
        n /= 1024


def cmd_audit(args, out):
    """Read-only duplicate audit. Prints a summary and writes audit_report.csv.
    Touches nothing on disk. Independent of catalog.db."""
    content = not getattr(args, "no_content", False)
    print("Auditing {} (content hashing: {})...".format(
        out, "on" if content else "off"))
    _prog = getattr(args, "progress", None)
    rep = audit_collection(out, content=content, progress=_prog)
    t = rep["totals"]

    print("\nFiles per bucket:")
    for b, c in sorted(rep["per_bucket"].items(), key=lambda kv: -kv[1]):
        print("  {:<10} {:,}".format(b, c))

    print("\nClass A  - same media_id in >1 folder : {:,} groups, {:,} redundant files"
          .format(t["class_a_groups"], t["class_a_redundant"]))
    print("Class B  - identical bytes, diff id   : {:,} groups, {:,} redundant files"
          .format(t["class_b_groups"], t["class_b_redundant"]))
    print("Reclaimable if deduped                : {}".format(
        _fmt_bytes(t["reclaimable_bytes"])))

    # Write detailed CSV
    report_path = out / "audit_report.csv"
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class", "group_key", "role", "bucket", "media_id", "size", "path"])
        for g in rep["class_a"]:
            kp, kr, kb, ksz = g["keeper"]
            w.writerow(["A", g["media_id"], "keep", kb, g["media_id"], ksz, str(kr)])
            for (_, rel, b, sz) in g["losers"]:
                w.writerow(["A", g["media_id"], "remove", b, g["media_id"], sz, str(rel)])
        for g in rep["class_b"]:
            kp, kr, kb, ksz, kmid = g["keeper"]
            w.writerow(["B", g["sha"][:12], "keep", kb, kmid, ksz, str(kr)])
            for (_, rel, b, sz, mid) in g["losers"]:
                w.writerow(["B", g["sha"][:12], "remove", b, mid, sz, str(rel)])
    print("\nDetailed report -> {}".format(report_path.relative_to(out.parent)
                                            if out.parent else report_path))
    print("Run --dedup to act on this (quarantine by default; nothing deleted yet).")
    return rep


def cmd_dedup(args, out, db_path):
    """Act on the audit: move redundant copies to _duplicates/ (default) or delete
    them (--dedup-delete). Keeps the most-organized copy. Dry-run by default.
    Reconciles catalog.db with what's left on disk afterward."""
    # Dedup is filesystem-truth: it does not need a catalog to run. Reconcile is
    # a bonus, applied only if a catalog exists.
    try:
        db_path = _ensure_db(out)
        have_catalog = True
    except PixAIError:
        have_catalog = False
    content = not getattr(args, "no_content", False)
    delete = getattr(args, "dedup_delete", False)
    apply = getattr(args, "apply", False)  # default is dry-run unless --apply

    rep = audit_collection(out, content=content, progress=getattr(args, "progress", None))
    losers = []  # (rel_path, abs_path)
    for g in rep["class_a"]:
        for (p, rel, b, sz) in g["losers"]:
            losers.append((rel, p))
    for g in rep["class_b"]:
        for (p, rel, b, sz, mid) in g["losers"]:
            losers.append((rel, p))

    action = "DELETE" if delete else "quarantine to _duplicates/"
    print("\nDedup plan: {:,} redundant files to {} ({})".format(
        len(losers), action, _fmt_bytes(rep["totals"]["reclaimable_bytes"])))
    for rel, _ in losers[:8]:
        print("  {}".format(rel))
    if len(losers) > 8:
        print("  ... and {:,} more".format(len(losers) - 8))

    if not apply:
        print("\nDry run -- nothing changed. Re-run with --apply to perform it.")
        return rep

    quarantine_root = out / "_duplicates"
    moved = removed = failed = 0
    _prog = getattr(args, "progress", None)
    for i, (rel, p) in enumerate(losers):
        try:
            if delete:
                p.unlink()
                removed += 1
            else:
                dest = quarantine_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest = dest.with_name(dest.stem + "_dup" + dest.suffix)
                p.replace(dest)
                moved += 1
        except OSError as e:
            print("  failed {} ({})".format(rel, e))
            failed += 1
        if _prog:
            _prog(i + 1, len(losers), 0)

    if delete:
        print("\nDeleted {:,} files, {:,} failed.".format(removed, failed))
    else:
        print("\nQuarantined {:,} files to {}, {:,} failed.".format(
            moved, quarantine_root.relative_to(out.parent) if out.parent else quarantine_root,
            failed))

    if have_catalog:
        n = reconcile_catalog_with_disk(out, db_path)
        print("Reconciled catalog: updated {:,} filename/batch entries to match disk.".format(n))

    # Auto-verify after quarantining. Dedup chose losers by media_id WITHOUT
    # comparing bytes, so this is the only step that confirms each quarantined
    # file truly matches a surviving keeper. Never auto-deletes -- the human does.
    if not delete and moved:
        print("\n--- Verifying the quarantine (confirming every moved file is "
              "redundant) ---")
        vr = verify_quarantine(out, progress=getattr(args, "progress", None))
        ok = vr["safe"] + vr["meta_only"]
        print("Verify: {:,} confirmed safe ({:,} byte-identical + {:,} metadata-only), "
              "{:,} differ, {:,} orphan.".format(
                  ok, vr["safe"], vr["meta_only"], len(vr["differs"]), len(vr["orphan"])))
        if vr["differs"] or vr["orphan"]:
            print("REVIEW NEEDED before deleting _duplicates/ -- run --verify-dupes "
                  "to write verify_report.csv with the flagged items.")
        else:
            print("All quarantined files confirmed redundant -- _duplicates/ is safe "
                  "to delete to reclaim the space.")
    return rep


def verify_quarantine(out_dir, restore_orphans=False, progress=None):
    """Final-pass safety check on _duplicates/ BEFORE you delete it.

    For every quarantined file, find the surviving keeper with the same media_id
    (outside _duplicates/) and compare bytes. Classifies each as:
      * safe    - a keeper exists AND bytes are identical -> truly redundant
      * differs - a keeper exists but bytes DIFFER -> same media_id, different
                  content (a naming collision the sort/backfill missed) -> REVIEW
      * orphan  - no surviving keeper at all -> quarantining it lost the only copy
    Optionally restores orphans back to images/. Returns a result dict.
    """
    quarantine_root = out_dir / "_duplicates"
    if not quarantine_root.exists():
        return {"safe": 0, "differs": [], "orphan": [], "total": 0}

    files = [p for p in quarantine_root.rglob("*")
             if p.is_file() and p.suffix.lower() in _IMAGE_EXTS]
    # Index surviving keepers (everything outside _duplicates/ and gallery/) once,
    # in a single walk, so we don't rglob the whole tree per quarantined file.
    survivors = defaultdict(list)
    for p, rel, bucket, mid in _scan_media_files(out_dir):
        survivors[mid].append(p)

    safe = 0
    meta_only = 0  # bytes differ but pixels identical (e.g. embedded PNG metadata)
    differs = []   # (quarantined_path, keeper_path) - genuinely different pixels
    orphan = []    # quarantined_path
    total = len(files)
    for i, q in enumerate(files):
        keepers = survivors.get(media_id_of(q), [])
        if not keepers:
            orphan.append(q)
        elif _same_bytes(q, keepers[0]):
            safe += 1
        else:
            # Bytes differ. Fall back to a pixel compare: identical pixels mean the
            # difference is just container/metadata (the keeper has prompt text
            # embedded), so it's still safe to delete the quarantined copy.
            px = _same_pixels(q, keepers[0])
            if px is True:
                meta_only += 1
            else:
                differs.append((q, keepers[0]))
        if progress and (i % 200 == 0 or i + 1 == total):
            progress(i + 1, total, 0)

    restored = 0
    if restore_orphans and orphan:
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        for q in orphan:
            dest = images_dir / q.name
            try:
                q.replace(dest)
                restored += 1
            except OSError as e:
                print("  restore failed {} ({})".format(q.name, e))

    return {"safe": safe, "meta_only": meta_only, "differs": differs,
            "orphan": orphan, "total": total, "restored": restored}


def cmd_verify_dupes(args, out):
    """Verify the _duplicates/ quarantine is safe to delete. Read-only unless
    --restore-orphans is passed."""
    restore = getattr(args, "restore_orphans", False)
    print("Verifying quarantine in {}/_duplicates ...".format(out))
    res = verify_quarantine(out, restore_orphans=restore,
                            progress=getattr(args, "progress", None))
    if res["total"] == 0:
        print("No _duplicates/ folder (nothing quarantined yet).")
        return res

    print("\nQuarantined files checked : {:,}".format(res["total"]))
    print("  safe - byte-identical keeper exists       : {:,}".format(res["safe"]))
    print("  safe - pixels identical (metadata-only)   : {:,}".format(res["meta_only"]))
    print("  DIFFERS - same id, DIFFERENT pixels       : {:,}".format(len(res["differs"])))
    print("  ORPHAN  - no surviving keeper             : {:,}".format(len(res["orphan"])))

    if res["differs"] or res["orphan"]:
        report = out / "verify_report.csv"
        with open(report, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["status", "quarantined_file", "surviving_keeper"])
            for q, k in res["differs"]:
                w.writerow(["differs", str(q.relative_to(out)), str(k.relative_to(out))])
            for q in res["orphan"]:
                w.writerow(["orphan", str(q.relative_to(out)), ""])
        print("\nFlagged items written to {}".format(report.relative_to(out.parent)
                                                     if out.parent else report))

    if res.get("restored"):
        print("Restored {:,} orphaned files to images/.".format(res["restored"]))

    if not res["differs"] and not res["orphan"]:
        print("\nAll clear: every quarantined file is byte-identical to a surviving "
              "copy. Safe to delete _duplicates/.")
    else:
        print("\nDo NOT blanket-delete yet -- review the flagged items above first.")
        if res["orphan"] and not restore:
            print("Re-run with --restore-orphans to move orphans back to images/.")
    return res


def run_verify_dupes(args):
    """GUI/CLI wrapper for quarantine verification."""
    cmd_verify_dupes(args, Path(args.out))


def reconcile_catalog_with_disk(out_dir, db_path):
    """After files move/disappear, point each catalog row's filename+batch at the
    surviving on-disk file for that media_id. Rows whose file is gone keep their
    last-known filename but are left intact (the image may be re-downloadable)."""
    rows = load_catalog(db_path)
    updated = 0
    for r in rows:
        mid = r.get("media_id")
        if not mid:
            continue
        matches = find_files_for_media_id(out_dir, mid)
        if not matches:
            continue
        survivor = matches[0]
        rel = survivor.relative_to(out_dir)
        bucket = _bucket_of(rel)
        new_batch = rel.parts[1] if bucket == "batches" and len(rel.parts) > 2 else (
            "" if bucket != "batches" else r.get("batch", ""))
        if r.get("filename") != survivor.name or r.get("batch", "") != new_batch:
            r["filename"] = survivor.name
            r["batch"] = new_batch
            updated += 1
    if updated:
        save_catalog(db_path, rows)
    return updated


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
    deduped = 0  # redundant flat sources removed because dst already holds the same bytes

    for n, (src, dst, is_batch, mid, row) in enumerate(plan):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.resolve() != src.resolve():
            # The organized copy already exists. Historically we left `src` in
            # images/ here, which is exactly what produced the images/+month
            # duplication. If src is byte-identical to the existing dst, the flat
            # copy is pure redundancy -> remove it. If they differ, leave both
            # untouched and report so nothing is lost.
            if _same_bytes(src, dst):
                try:
                    src.unlink()
                    deduped += 1
                except OSError as e:
                    print("  could not remove redundant flat copy {} ({})".format(src.name, e))
            else:
                print("  KEPT both (differ): {} vs existing {}".format(
                    src.name, dst.relative_to(out)))
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
    if deduped:
        print("Removed {:,} redundant flat copies (byte-identical to the "
              "organized version).".format(deduped))
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
    # With an API key (sent as Bearer) the per-session u3t is not required; the
    # browser-JWT path still wants it. Always need the persisted hash + USER_ID.
    have_api_key = bool((fresh or {}).get("PIXAI_API_KEY") or _cfg.get("PIXAI_API_KEY"))
    required = [PERSISTED_QUERY_HASH, USER_ID] if have_api_key else [PERSISTED_QUERY_HASH, U3T, USER_ID]
    if not all(required):
        raise PixAIError(
            "config.json is missing or incomplete (need PERSISTED_QUERY_HASH, USER_ID"
            "{}).\nCopy config.example.json to config.json and fill in your values.\n"
            "See the README -> Configuration for instructions.".format(
                "" if have_api_key else ", U3T"))
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


def run_audit(args):
    """GUI/CLI wrapper: read-only duplicate audit."""
    cmd_audit(args, Path(args.out))


def run_dedup(args):
    """GUI/CLI wrapper: dedup (quarantine/delete + catalog reconcile)."""
    out = Path(args.out)
    cmd_dedup(args, out, out / "catalog.db")


def artwork_list_gql(session, before=None, last=50):
    """GET listArtworks for the owner's own authorId. Returns the Relay
    connection dict (edges + pageInfo) or None on failure."""
    variables = {"authorId": str(USER_ID), "last": last, "tackLanguage": "en"}
    if before:
        variables["before"] = before
    params = {
        "operation": "listArtworks",
        "u3t": U3T,
        "operationName": "listArtworks",
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"clientLibrary": CLIENT_LIBRARY_ARTWORK,
             "persistedQuery": {"version": 1, "sha256Hash": ARTWORK_LIST_HASH}},
            separators=(",", ":")),
    }
    try:
        r = session.get(API_URL, params=params, timeout=60,
                        headers={"x-apollo-operation-name": "listArtworks"})
        if r.status_code != 200:
            return None
        return find_connection(r.json().get("data") or {})
    except (requests.RequestException, ValueError):
        return None


def extract_artwork_meta(node):
    """Pull the published-artwork fields we store from a listArtworks node.
    Keyed by media_id so it merges onto the existing catalog row."""
    tacks = node.get("tacks") or []
    tags = [t.get("displayName") or t.get("codeName") for t in tacks
            if (t.get("displayName") or t.get("codeName"))]
    return {
        "media_id":      str(node.get("mediaId") or ""),
        "artwork_id":    str(node.get("id") or ""),
        "title":         node.get("title") or "",
        "is_published":  "1" if (node.get("visibility") == "PUBLIC") else "0",
        "is_nsfw":       "1" if node.get("isNsfw") else "0",
        "liked_count":   str(node.get("likedCount") or 0),
        "comment_count": str(node.get("commentCount") or 0),
        "aes_score":     str(node.get("aesScore") or ""),
        "art_tags":      ", ".join(tags),
    }


def run_sync_artworks(args):
    """Page the owner's published artworks (listArtworks) and merge their
    metadata (title, published flag, NSFW flag, likes, comments, aes score, tags)
    onto matching catalog rows by media_id. Published artworks are a subset of
    generations, so unmatched/undownloaded ones are simply skipped."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    if not USER_ID:
        raise PixAIError("USER_ID missing from config.json.")
    session = _make_session(getattr(args, "token", None))

    by_mid = {}                      # media_id -> artwork fields
    videos = []                      # (video_media_id, title) for animated artworks
    with_videos = getattr(args, "with_videos", False)
    artworks = 0
    before = None
    page = 0
    _prog = getattr(args, "progress", None)
    print("Syncing published artworks (listArtworks)...")
    while True:
        page += 1
        conn = artwork_list_gql(session, before=before, last=50)
        if not conn:
            if page == 1:
                raise PixAIError(
                    "listArtworks returned no data. The ARTWORK_LIST_HASH may have "
                    "rotated after a PixAI update -- recapture it into config.json.")
            break
        edges = conn.get("edges", [])
        if not edges:
            break
        for edge in edges:
            node = edge.get("node", edge)
            meta = extract_artwork_meta(node)
            if meta["media_id"]:
                by_mid[meta["media_id"]] = meta
                artworks += 1
            vmid = node.get("videoMediaId")
            if vmid:
                videos.append((str(vmid), meta.get("title") or node.get("id")))
        print("  page {}: {} artworks (total {})".format(page, len(edges), artworks))
        if _prog:
            _prog(artworks, artworks, 0)
        pi = conn.get("pageInfo", {})
        if not pi.get("hasPreviousPage"):
            break
        before = pi.get("startCursor")
        time.sleep(getattr(args, "delay", 0.4))

    # Merge onto existing catalog rows by media_id.
    rows = load_catalog(db_path)
    matched = 0
    for r in rows:
        m = by_mid.get(r.get("media_id"))
        if not m:
            continue
        for k, v in m.items():
            if k != "media_id":
                r[k] = v
        matched += 1
    if matched:
        save_catalog(db_path, rows)
    print("\nArtworks fetched: {}.  Matched to catalog rows: {}.  "
          "(Unmatched artworks have no downloaded image.)".format(artworks, matched))

    # Optionally download animated-artwork video files (videoMediaId) into videos/.
    vids_ok = 0
    if with_videos and videos:
        vdir = out / "videos"
        vdir.mkdir(parents=True, exist_ok=True)
        print("\nDownloading {} animated artwork video(s) -> videos/ ...".format(len(videos)))
        for i, (vmid, title) in enumerate(videos):
            if already_downloaded(out, vmid):
                vids_ok += 1
                continue
            url, info = resolve_media(session, vmid)
            if not url:
                print("  no media url for video {} ({})".format(vmid, title))
                continue
            stem = vdir / build_stem_name(title or "", "", vmid,
                                          getattr(args, "name_length", 60),
                                          getattr(args, "name_sep", "_"))
            status, path = download(session, url, stem)
            if status in ("ok", "skip"):
                vids_ok += 1
            if _prog:
                _prog(i + 1, len(videos), 0)
            time.sleep(getattr(args, "delay", 0.4))
        print("Videos saved/present: {} of {}.".format(vids_ok, len(videos)))
    elif videos and not with_videos:
        print("({} animated artworks have video; re-run with --with-videos to download them.)"
              .format(len(videos)))

    return {"artworks": artworks, "matched": matched, "videos": vids_ok}


def _needs_model_fix(row):
    """Return the model version-id to resolve if this row's model_name is missing
    or still a raw numeric id; else ''. Handles the case where model_name was
    set to the numeric id (MODEL_DETAIL_HASH was absent on an earlier run)."""
    mid = (row.get("model_id") or "").strip()
    name = (row.get("model_name") or "").strip()
    if not mid and name.isdigit():
        mid = name  # model_name itself is the numeric id
    if not mid:
        return ""
    if not name or name == mid or name.isdigit():
        return mid
    return ""


def run_fix_models(args):
    """Re-resolve human-readable model names for catalog rows whose model_name is
    blank or still a numeric version-id (e.g. saved before MODEL_DETAIL_HASH was
    configured). One API call per distinct model id (cached)."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    session = _make_session(getattr(args, "token", None))
    rows = load_catalog(db_path)

    to_resolve = {}   # version_id -> rows needing it
    for r in rows:
        vid = _needs_model_fix(r)
        if vid:
            to_resolve.setdefault(vid, []).append(r)

    if not to_resolve:
        print("No model names need fixing -- catalog already has readable names.")
        return {"fixed": 0, "models": 0, "unresolved": 0}

    relabel = getattr(args, "relabel_removed", False)
    removed_label = "Unknown or removed model"
    print("Resolving {} distinct model id(s) across {} rows...".format(
        len(to_resolve), sum(len(v) for v in to_resolve.values())))
    _prog = getattr(args, "progress", None)
    fixed = relabeled = unresolved = 0
    for i, vid in enumerate(sorted(to_resolve)):
        name = model_name_gql(session, vid)
        if name and name != vid and not str(name).isdigit():
            for r in to_resolve[vid]:
                r["model_name"] = name
                fixed += 1
        else:
            unresolved += 1
            if relabel:
                for r in to_resolve[vid]:
                    r["model_name"] = removed_label  # model_id kept for reference
                    relabeled += 1
                print("  {} unresolved -> '{}'".format(vid, removed_label))
            else:
                print("  could not resolve model {} (left as-is)".format(vid))
        if _prog:
            _prog(i + 1, len(to_resolve), 0)
        time.sleep(getattr(args, "delay", 0.4))

    if fixed or relabeled:
        save_catalog(db_path, rows)
    print("\nFixed {} row(s) across {} model(s); {} id(s) unresolved{}.".format(
        fixed, len(to_resolve) - unresolved, unresolved,
        " (relabeled {} rows to '{}')".format(relabeled, removed_label) if relabeled else ""))
    return {"fixed": fixed, "relabeled": relabeled, "models": len(to_resolve) - unresolved,
            "unresolved": unresolved}


def _simple_gql(session, op, sha256, variables=None):
    """Replay a no-arg (or simple) persisted GET op; return data dict or {}."""
    params = {
        "operation": op, "u3t": U3T, "operationName": op,
        "variables": json.dumps(variables or {}, separators=(",", ":")),
        "extensions": json.dumps(
            {"clientLibrary": CLIENT_LIBRARY_ARTWORK,
             "persistedQuery": {"version": 1, "sha256Hash": sha256}},
            separators=(",", ":")),
    }
    try:
        r = session.get(API_URL, params=params, timeout=60,
                        headers={"x-apollo-operation-name": op})
        if r.status_code != 200:
            return {}
        return r.json().get("data") or {}
    except (requests.RequestException, ValueError):
        return {}


_QUOTA_HASH = "9356b42a4ff6e987347a1f1ee3de7aba4bd103b1cdbfbbc4c5c5fcf52767ad66"
_MEMBERSHIP_HASH = "53dbad3c972e775222a4a6344727b3d2809fc3f08f6787f56500abb8245f9e88"


def run_account_info(args):
    """Print account quota (credits) and membership/plan info."""
    session = _make_session(getattr(args, "token", None))
    quota = _simple_gql(session, "getMyQuota", _QUOTA_HASH)
    me = quota.get("me") or {}
    print("Account: {}".format(me.get("id") or USER_ID))
    print("Quota / credits: {}".format(me.get("quotaAmount", "unknown")))
    member = _simple_gql(session, "getMyMembership", _MEMBERSHIP_HASH)
    m = member.get("me") or member.get("membership") or member
    if m:
        print("Membership: {}".format(json.dumps(m)[:300]))
    return {"quota": me.get("quotaAmount")}


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
    with_loras = getattr(args, "with_loras", False)

    # Work per unique task_id (one API call covers all media in that task).
    # --with-loras also re-processes rows that have full meta but a blank `loras`
    # column (e.g. backfilled before LoRA tracking existed). It re-fetches their
    # getTaskById to extract parameters.lora.
    def _needs(r):
        if not r.get("prompt_full"):
            return True
        if with_loras and r.get("task_id") and not r.get("loras"):
            return True
        return False
    needs_fill = [r for r in rows if _needs(r)]
    task_ids = list(dict.fromkeys(r["task_id"] for r in needs_fill if r.get("task_id")))
    print("Found {:,} rows to fill across {:,} unique tasks{}.".format(
        len(needs_fill), len(task_ids), " (incl. LoRAs)" if with_loras else ""))
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
        fm["loras"] = resolve_loras(session, task_data)

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

    use_full_meta = getattr(args, "full_meta", False)

    session = _make_session(getattr(args, "token", None))
    print("SSL trust store via truststore: {}".format(
        "on" if _TRUSTSTORE_ACTIVE else "off (requests default)"))

    if use_full_meta and not TASK_DETAIL_HASH:
        raise PixAIError(
            "--full-meta requires TASK_DETAIL_HASH in config.json. "
            "See README -> Full Meta for capture instructions.")

    if not getattr(args, "organize_adv_live", False):
        img_dir.mkdir(parents=True, exist_ok=True)

    # ONE fast tree walk at startup (os.scandir, ~free stat() on Windows): seed
    # the progress count AND build the on-disk media_id index. Resume is then an
    # O(1) dict lookup instead of an O(whole-tree) rglob per media_id -- the
    # latter made follow-up runs scale quadratically with collection size.
    # Prunes gallery/ thumbnails and _duplicates/ quarantine.
    already_done = 0
    disk_bytes = 0
    on_disk_by_mid = {}   # media_id -> Path of an existing full-res image

    def _iter_image_entries(root):
        skip_dirs = {"gallery", "_duplicates"}
        stack = [str(root)]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in skip_dirs:
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            yield e
            except OSError:
                continue

    if out.exists():
        for e in _iter_image_entries(out):
            name = e.name
            if name.endswith(".part") or os.path.splitext(name)[1].lower() not in _IMAGE_EXTS:
                continue
            already_done += 1
            try:
                disk_bytes += e.stat().st_size
            except OSError:
                pass
            on_disk_by_mid.setdefault(media_id_of(name), Path(e.path))
    # Progress counts items as the walk visits them (skips included), starting at
    # zero -- it must NOT be seeded with already_done, or the on-disk images get
    # counted twice (seed + re-check) and the bar overshoots past 100%.
    processed = 0

    if already_done:
        print("Resuming: {} image files already on disk ({}).\n".format(
            already_done, _format_size(disk_bytes)))

    # Progress denominator: avoid a full-history NETWORK pre-count on every run.
    # For a populated library the catalog size is an instant, good-enough estimate
    # (the progress bar already tolerates over/under). Only walk the API to count
    # on a fresh library (empty catalog) or when the user asks for --accurate-count.
    if getattr(args, "accurate_count", False) or not known:
        total_images = _quick_count(session)
    else:
        total_images = max(already_done, len(known))
        print("Library size (catalog estimate): ~{} images "
              "(use --accurate-count for an exact API count)\n".format(total_images))

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

    _full_meta_cache = {}  # task_id -> full meta dict

    before = None
    seen = 0
    written = set()   # media_ids written this session
    dl = {"ok": 0, "skip": 0, "missing": 0, "fail": 0}
    page = 0
    update_mode = getattr(args, "update", False)
    update_grace = getattr(args, "update_grace", 2)
    consecutive_known_pages = 0

    # Parallel downloads: only for the common flat-download case. collect_only does
    # no downloads, and organize-adv-live has per-folder side effects that assume
    # serial ordering -- both fall back to the serial path.
    workers = max(1, getattr(args, "workers", 1) or 1)
    parallel = (workers > 1
                and not getattr(args, "collect_only", False)
                and not getattr(args, "organize_adv_live", False))
    if parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print("Parallel downloads: {} workers.\n".format(workers))

    def _row_for(meta, mid, full_meta, filename="", url="", w="", h=""):
        return {
            "task_id": meta["task_id"], "media_id": mid,
            "filename": filename, "url": url, "width": w, "height": h,
            "prompt_preview": meta["prompt_preview"],
            "status": meta["status"], "created_at": meta["created_at"],
            **_merge_full(full_meta, known.get(mid, {})),
        }

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
            page_new = 0    # media_ids on this page NOT already on disk (for --update)

            if parallel:
                # Pass 1 (serial, local): emit raw json, handle on-disk skips, and
                # build a worklist of media_ids that actually need fetching.
                worklist = []
                for edge in edges:
                    node = edge.get("node", edge)
                    raw_f.write(json.dumps(node, ensure_ascii=False) + "\n")
                    meta = extract_meta(node)
                    all_mids = media_ids_for(node)
                    full_meta = {}
                    if use_full_meta:
                        tid = meta["task_id"]
                        if tid not in _full_meta_cache:
                            task_data = task_detail_gql(session, tid)
                            fm = extract_full_meta(task_data)
                            if fm.get("model_id"):
                                fm["model_name"] = model_name_gql(session, fm["model_id"])
                            fm["loras"] = resolve_loras(session, task_data)
                            _full_meta_cache[tid] = fm
                            time.sleep(args.delay)
                        full_meta = _full_meta_cache.get(tid, {})
                    for mid in all_mids:
                        existing = on_disk_by_mid.get(mid)
                        if existing:
                            dl["skip"] += 1
                            k = known.get(mid, {})
                            row = _row_for(meta, mid, full_meta,
                                           filename=existing.name, url=k.get("url", ""),
                                           w=k.get("width", ""), h=k.get("height", ""))
                            row["prompt_preview"] = k.get("prompt_preview") or meta["prompt_preview"]
                            row["status"] = k.get("status") or meta["status"]
                            row["created_at"] = k.get("created_at") or meta["created_at"]
                            page_rows.append(row)
                            written.add(mid)
                            _tick()
                            continue
                        page_new += 1
                        stem = img_dir / build_stem_name(
                            meta["prompt_preview"], meta["task_id"], mid,
                            args.name_length, args.name_sep)
                        worklist.append({"meta": meta, "mid": mid, "stem": stem,
                                         "full_meta": full_meta})

                # Pass 2 (parallel): resolve + download. Only the per-item network
                # and file write run in threads; all shared state is mutated here
                # in the main thread as futures complete.
                def _work(item):
                    url, info = resolve_media(session, item["mid"])
                    if not url:
                        return item, "missing", "", info, None
                    status, path = download(
                        session, url, item["stem"],
                        convert=getattr(args, "convert", None),
                        jpeg_quality=getattr(args, "jpeg_quality", 92),
                        jpeg_bg=getattr(args, "jpeg_bg", "white"),
                        keep_webp=getattr(args, "keep_webp", False))
                    return item, status, url, info, path

                if worklist:
                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        for fut in as_completed([ex.submit(_work, it) for it in worklist]):
                            item, status, url, info, path = fut.result()
                            meta, mid, full_meta = item["meta"], item["mid"], item["full_meta"]
                            w, h = info.get("width", ""), info.get("height", "")
                            if status == "missing":
                                dl["missing"] += 1
                                page_rows.append(_row_for(meta, mid, full_meta, w=w, h=h))
                            else:
                                dl[status] += 1
                                page_rows.append(_row_for(
                                    meta, mid, full_meta,
                                    filename=path.name if path else "", url=url, w=w, h=h))
                                if path and status in ("ok", "skip"):
                                    on_disk_by_mid[mid] = path
                            written.add(mid)
                            _tick()

                if page_rows:
                    save_catalog(db_path, page_rows)
                seen += len(edges)
                if args.max and seen >= args.max:
                    print("Reached --max limit.")
                    break
                if update_mode:
                    if page_new == 0:
                        consecutive_known_pages += 1
                        if consecutive_known_pages >= update_grace:
                            print("\n--update: {} consecutive pages already on disk; "
                                  "stopping (older items are already downloaded)."
                                  .format(consecutive_known_pages))
                            break
                    else:
                        consecutive_known_pages = 0
                raw_f.flush()
                pi = conn.get("pageInfo", {})
                if not pi.get("hasPreviousPage"):
                    break
                before = pi.get("startCursor")
                time.sleep(args.delay)
                continue

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
                        fm["loras"] = resolve_loras(session, task_data)
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
                                else on_disk_by_mid.get(mid))
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
                    page_new += 1  # this media_id is not yet on disk
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
                        on_disk_by_mid[mid] = path  # keep index current within the run
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

            # Incremental --update: pages come newest -> oldest, so once we hit
            # a run of pages where everything is already on disk, the rest of the
            # history is older and already downloaded -> stop early. The grace
            # window tolerates occasional gaps (a few missing/failed items).
            if update_mode:
                if page_new == 0:
                    consecutive_known_pages += 1
                    if consecutive_known_pages >= update_grace:
                        print("\n--update: {} consecutive pages already on disk; "
                              "stopping (older items are already downloaded)."
                              .format(consecutive_known_pages))
                        break
                else:
                    consecutive_known_pages = 0

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
    ap.add_argument("--page-size", type=int, default=250,
                    help="tasks per API page (default 250; fewer round-trips. Keep <~8000)")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel download workers (default 4). 1 = serial/polite. "
                         "Higher saturates bandwidth on bulk first-time pulls; ignored for "
                         "--collect-only and --organize-adv-live.")
    ap.add_argument("--max", type=int, default=0, help="stop after N tasks (0=all)")
    ap.add_argument("--update", action="store_true",
                    help="incremental follow-up run: stop paging once a run of pages is "
                         "already fully on disk (newest-first, so older items are already "
                         "downloaded). Much faster than re-walking the whole history.")
    ap.add_argument("--update-grace", type=int, default=2,
                    help="with --update, number of consecutive all-on-disk pages before "
                         "stopping (default 2; raise if your history has gaps)")
    ap.add_argument("--accurate-count", action="store_true",
                    help="walk the whole API to count library size for the progress bar "
                         "(slow). Default uses the catalog size as a fast estimate.")
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
    ap.add_argument("--with-loras", action="store_true",
                    help="with --backfill-full-meta, ALSO re-fetch rows that have full meta but "
                         "no LoRA data yet (populates the loras column for older images; long run)")
    ap.add_argument("--export-csv", action="store_true",
                    help="export catalog.db to catalog.csv for interop/backup, then exit")
    ap.add_argument("--sync-artworks", action="store_true",
                    help="fetch your published-artwork metadata (title, NSFW flag, likes, "
                         "comments, aes score, tags) via listArtworks and merge it onto "
                         "matching catalog rows by media_id, then exit")
    ap.add_argument("--with-videos", action="store_true",
                    help="with --sync-artworks, also download animated-artwork video files "
                         "(videoMediaId) into a videos/ folder")
    ap.add_argument("--fix-model-names", action="store_true",
                    help="re-resolve readable model names for catalog rows whose model_name "
                         "is blank or a raw numeric id (one API call per distinct model), then exit")
    ap.add_argument("--relabel-removed", action="store_true",
                    help="with --fix-model-names, relabel ids that no longer resolve (deleted "
                         "models) to 'Unknown or removed model' instead of leaving the raw number")
    ap.add_argument("--audit", action="store_true",
                    help="read-only duplicate audit of the whole backup folder; writes "
                         "audit_report.csv and prints a summary, then exit. Independent of catalog.db.")
    ap.add_argument("--dedup", action="store_true",
                    help="act on the audit: move redundant copies to _duplicates/ (keeping the "
                         "most-organized copy), then reconcile catalog.db. Dry-run unless --apply.")
    ap.add_argument("--apply", action="store_true",
                    help="with --dedup, actually perform the moves/deletes (default is dry-run)")
    ap.add_argument("--dedup-delete", action="store_true",
                    help="with --dedup --apply, delete redundant copies instead of quarantining them")
    ap.add_argument("--no-content", action="store_true",
                    help="with --audit/--dedup, skip content hashing (Class B); only do the fast "
                         "same-media_id location dedup (Class A)")
    ap.add_argument("--verify-dupes", action="store_true",
                    help="final-pass safety check on _duplicates/: confirm every quarantined file "
                         "is byte-identical to a surviving keeper before you delete. Flags orphans "
                         "and same-id-different-bytes mismatches. Read-only unless --restore-orphans.")
    ap.add_argument("--restore-orphans", action="store_true",
                    help="with --verify-dupes, move any orphaned quarantined files (no surviving "
                         "keeper) back to images/")
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
        if args.sync_artworks:
            run_sync_artworks(args)
            return
        if args.fix_model_names:
            run_fix_models(args)
            return
        if args.audit:
            cmd_audit(args, out)
            return
        if args.dedup:
            cmd_dedup(args, out, db_path)
            return
        if args.verify_dupes:
            cmd_verify_dupes(args, out)
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
