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

__version__ = "1.6.0"

import argparse
import csv
import json
import mimetypes
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


# ---------------------------------------------------------------------------
# Verbose diagnostics
# ---------------------------------------------------------------------------
# A single module-level switch shared by the CLI (--verbose) and the GUI
# (Verbose logging checkbox). vlog() is a no-op until set_verbose(True) is
# called, so normal runs and the test suite are completely unaffected.
_VERBOSE = False
_VERBOSE_T0 = None


def set_verbose(on):
    """Enable/disable timestamped diagnostic logging. Resets the elapsed clock
    each time it is enabled so timings read from the start of the operation."""
    global _VERBOSE, _VERBOSE_T0
    _VERBOSE = bool(on)
    if _VERBOSE:
        _VERBOSE_T0 = time.monotonic()


def vlog(msg):
    """Print a diagnostic line prefixed with seconds-since-enabled, but only in
    verbose mode. Writes to stdout so the GUI log pane captures it too."""
    if not _VERBOSE:
        return
    t0 = _VERBOSE_T0 if _VERBOSE_T0 is not None else time.monotonic()
    print("  [v +{:6.1f}s] {}".format(time.monotonic() - t0, msg), flush=True)


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
# Persisted-query hashes are PUBLIC, non-secret identifiers of PixAI's own frontend
# GraphQL operations (the same for every user, embedded in their JS bundle). The
# history feed / task detail / delete operations are NOT exposed on the public API
# the API key talks to, so these hashes are the only way to reach them. They change
# only when PixAI overhauls their frontend -- captured 2026-06-28. Override any in
# config.json if one rotates (you'll get a clear "recapture" error if it does).
PERSISTED_QUERY_HASH = _cfg.get("PERSISTED_QUERY_HASH", "") or \
    "d30424c72dc7d75d14c09d9fe447e1ac3dea8e767668092e2113efb8c817573e"
U3T = _cfg.get("U3T", "")
USER_ID = _cfg.get("USER_ID", "")  # auto-resolved from the API key (me{id}) if blank
TASK_DETAIL_HASH = _cfg.get("TASK_DETAIL_HASH", "") or \
    "2526f64c73c59fcfeff938b0f4a8b3b610f2294bc6eb6b6b281aa671ac81a08e"
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
# Deletion mutation (deleteGenerationTask). Also a public persisted hash. It only
# ever touches YOUR OWN tasks, and the destructive paths are independently gated by
# explicit confirmation (typed "DELETE" in the gallery, --confirm on the CLI), so the
# default is safe; override in config.json if it rotates.
DELETE_TASK_HASH = _cfg.get("DELETE_TASK_HASH", "") or \
    "9f0c8dd3edfe712a4479d700df0b33faebbbc28c7d2310589ea192e1a35d6ee4"
DELETE_OPERATION = "deleteGenerationTask"
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
            _t = time.monotonic()
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
        vlog("{} page -> HTTP {} ({:,} bytes) in {:.2f}s".format(
            OPERATION_NAME, r.status_code, len(r.content), time.monotonic() - _t))
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
    _t = time.monotonic()
    try:
        r = session.get(MEDIA_BASE.format(id=mid), timeout=30)
        r.raise_for_status()
        obj = r.json()
    except (requests.RequestException, ValueError) as e:
        vlog("resolve_media {} FAILED in {:.2f}s ({})".format(
            mid, time.monotonic() - _t, e))
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
    vlog("resolve_media {} -> {} {}x{} in {:.2f}s".format(
        mid, "url" if chosen else "NO-URL",
        info.get("width"), info.get("height"), time.monotonic() - _t))
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
    _t = time.monotonic()
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    vlog("download {} -> missing (404) in {:.2f}s".format(
                        stem.name, time.monotonic() - _t))
                    return ("missing", None)
                r.raise_for_status()
                ext = ext_from_ct(r.headers.get("Content-Type"))
                dest = stem.with_name(stem.name + ext)
                tmp = dest.with_suffix(dest.suffix + ".part")
                nbytes = 0
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=65536):
                        fh.write(chunk)
                        nbytes += len(chunk)
                tmp.replace(dest)
                vlog("download {} -> {:,} bytes in {:.2f}s".format(
                    dest.name, nbytes, time.monotonic() - _t))
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
    "negative_prompt", "clip_skip",
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


def delete_task_gql(session, task_id):
    """Replay the deleteGenerationTask persisted mutation for ONE task id.

    DELETES the generation from your PixAI account -- irreversible. This is a void
    mutation: on SUCCESS the server returns null (data.deleteGenerationTask == None),
    so the meaningful signal is the ABSENCE of an error, not the payload. Raises
    PixAIError with a clear message on any failure. Deliberately single-attempt (NO
    retry/backoff loop) so a flaky network can never cause a delete to fire twice.
    """
    if not DELETE_TASK_HASH:
        raise PixAIError(
            "DELETE_TASK_HASH missing from config.json. Capture deleteGenerationTask's "
            "sha256Hash from DevTools (Network -> graphql -> a delete request -> Payload "
            "-> extensions.persistedQuery.sha256Hash) and add it. This is required on "
            "purpose so deletion can't run without an explicit setup step.")
    # Mutations are POST (Apollo blocks them over GET). Mirror the site's params.
    params = {"operation": DELETE_OPERATION, "u3t": U3T}
    body = {
        "operationName": DELETE_OPERATION,
        "variables": {"taskId": str(task_id)},
        "extensions": {"clientLibrary": CLIENT_LIBRARY,
                       "persistedQuery": {"version": 1, "sha256Hash": DELETE_TASK_HASH}},
    }
    _t = time.monotonic()
    try:
        r = session.post(API_URL, params=params, json=body, timeout=60)
    except requests.exceptions.SSLError:
        raise PixAIError(_ssl_help())
    except requests.RequestException as e:
        raise PixAIError("network error deleting task {}: {}".format(task_id, e))

    if r.status_code == 401:
        raise PixAIError("401 Unauthorized -- token missing/expired. Refresh and re-run.")
    try:
        data = r.json()
    except ValueError:
        raise PixAIError("HTTP {} non-JSON response deleting task {}:\n{}".format(
            r.status_code, task_id, r.text[:500]))
    if data.get("errors"):
        msg = json.dumps(data["errors"])
        if "PersistedQueryNotFound" in msg:
            raise PixAIError("deleteGenerationTask hash not recognized -- recapture "
                             "DELETE_TASK_HASH into config.json (see RECAPTURE).")
        raise PixAIError("GraphQL error deleting task {}: {}".format(task_id, msg[:600]))
    if r.status_code >= 400:
        raise PixAIError("HTTP {} deleting task {}:\n{}".format(
            r.status_code, task_id, json.dumps(data)[:600]))
    result = (data.get("data") or {}).get(DELETE_OPERATION)
    vlog("deleteGenerationTask {} -> {} in {:.2f}s".format(
        task_id, result, time.monotonic() - _t))
    return result


def gql_adhoc(session, query, variables=None, retries=3):
    """Run an ad-hoc (non-persisted) GraphQL operation by POSTing the full query
    document. PixAI's endpoint accepts these under Bearer auth (the API key has
    read+write scope), so NO persisted sha256Hash capture is needed -- this is the
    generic foundation for every read/write op beyond the reverse-engineered
    listing path. Returns the `data` dict; raises PixAIError on GraphQL/HTTP error.

    Mutations must be POST (Apollo blocks them over GET); this always POSTs, so it
    works for queries and mutations alike."""
    body = {"query": query, "variables": variables or {}}
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            r = session.post(API_URL, json=body, timeout=120)
        except requests.exceptions.SSLError:
            raise PixAIError(_ssl_help())
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(delay); delay *= 2; continue
        if r.status_code == 401:
            raise PixAIError("401 Unauthorized -- API key missing/expired.")
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                r.raise_for_status()
            time.sleep(delay); delay *= 2; continue
        try:
            data = r.json()
        except ValueError:
            raise PixAIError("HTTP {} non-JSON response:\n{}".format(r.status_code, r.text[:400]))
        if data.get("errors"):
            raise PixAIError("GraphQL error: " + json.dumps(data["errors"])[:500])
        return data.get("data") or {}
    raise RuntimeError("unreachable")


def resolve_user_id(session):
    """Resolve the authenticated account's user id from the API key, via the public
    `me` query (the one account-scoped query the ad-hoc API surface exposes). Lets
    setup work with just PIXAI_API_KEY -- no manual USER_ID needed."""
    data = gql_adhoc(session, "query{ me{ id } }")
    uid = ((data or {}).get("me") or {}).get("id", "")
    if not uid:
        raise PixAIError("the `me` query returned no id")
    return str(uid)


def media_file_gql(session, media_id):
    """Resolve a VIDEO media's actual file URL. The REST /v1/media endpoint
    returns an empty urls[] for videos; the GraphQL `media` object carries the
    real mp4 in `fileUrl`. Returns {'fileUrl','type','duration'} or {}."""
    query = ("query($id:String!){ media(id:$id){ id type duration fileUrl "
             "hlsUrl size } }")
    try:
        return (gql_adhoc(session, query, {"id": str(media_id)}) or {}).get("media") or {}
    except PixAIError:
        return {}


def video_outputs(task):
    """Extract image-to-video outputs from a getTaskById result. Returns a list of
    {video_media_id, poster_media_id, seed} plus the shared prompt/duration."""
    if not task:
        return [], {}
    params = task.get("parameters") or {}
    rv = params.get("referenceVideo") or {}
    shared = {
        "prompt": rv.get("prompt", ""),
        "duration": rv.get("duration", ""),
        "i2v_model": rv.get("model", ""),
    }
    outs = []
    for v in ((task.get("outputs") or {}).get("videos") or []):
        vmid = v.get("mediaId")
        if vmid:
            outs.append({
                "video_media_id": str(vmid),
                "poster_media_id": str(v.get("thumbnailMediaId") or ""),
                "seed": str(v.get("seed") or ""),
            })
    return outs, shared


def model_search_gql(session, keyword="", limit=15, base_only=False, lora_only=False):
    """Search PixAI generation models by keyword via the `generationModels`
    connection. Returns a list of {title, type, model_id, version_id}.

    IMPORTANT: createGenerationTask's `modelId` wants the *version* id, not the
    model id. The search node's `id` is the MODEL id (which generation rejects);
    `latestVersion.id` is the generatable version id. So we surface version_id as
    the value to feed into --generate.

    base_only=True drops LoRA / video types -- a LoRA can't be the BASE model
    (generation fails), so the base-model picker filters them out. LoRAs belong in
    the separate LoRA picker."""
    q = ("query($k:String,$n:Int){ generationModels(keyword:$k, first:$n){ "
         "edges { node { id title type isNsfw likedCount latestVersion { id } "
         "media { id urls { url } } } } } }")
    data = gql_adhoc(session, q, {"k": keyword, "n": limit})
    out = []
    for e in (data.get("generationModels") or {}).get("edges") or []:
        n = e.get("node") or {}
        mtype = (n.get("type") or "").upper()
        if base_only and ("LORA" in mtype or "VIDEO" in mtype):
            continue
        if lora_only and "LORA" not in mtype:
            continue
        out.append({
            "title": n.get("title") or "",
            "type": n.get("type") or "",
            "is_nsfw": bool(n.get("isNsfw")),
            "liked_count": int(n.get("likedCount") or 0),
            "model_id": str(n.get("id") or ""),
            "version_id": str((n.get("latestVersion") or {}).get("id") or ""),
            "preview_url": _model_preview_url(n.get("media")),
        })
    return out


def _model_preview_url(media):
    """Pick a directly-displayable cover thumbnail from a generationModels node's
    `media.urls`. The CDN list is [orig, thumb, stillThumb]; the `thumb` variant is
    the right size for a picker and needs no auth. Falls back to the first url."""
    urls = [u.get("url") for u in ((media or {}).get("urls") or []) if u.get("url")]
    return next((u for u in urls if "/thumb/" in u), urls[0] if urls else "")


def is_lora_type(model_type):
    """True if a model type is a LoRA (can't be used as a base model)."""
    return "LORA" in (model_type or "").upper()


def run_list_models(args):
    """CLI: search PixAI models and print name / type / generatable version id."""
    session = _make_session(getattr(args, "token", None))
    kw = getattr(args, "list_models", "") or ""
    results = model_search_gql(session, kw, limit=getattr(args, "max", 0) or 25)
    if not results:
        print("No models found for '{}'.".format(kw))
        return
    enc = (sys.stdout.encoding or "utf-8")

    def _safe(t):                       # Windows consoles are often cp1252
        return t.encode(enc, "replace").decode(enc, "replace")
    print("{:<40} {:<14} version id (use as --model)".format("model", "type"))
    for m in results:
        tag = " [NSFW]" if m["is_nsfw"] else ""
        print("{:<40} {:<14} {}{}".format(
            _safe(m["title"][:40]), m["type"][:14], m["version_id"], tag))


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
    extra = params.get("extra") or {}
    # negativePrompts may live under a few keys depending on PixAI's flow; many
    # newer "structured prompt" tasks have none at all.
    neg = (params.get("negativePrompts") or detail.get("negativePrompts")
           or extra.get("negativePrompts") or params.get("negativePrompt") or "")
    clip = detail.get("clipSkip", params.get("clipSkip", ""))
    return {
        "prompt_full":    params.get("prompts", ""),
        "natural_prompt": extra.get("naturalPrompts", ""),
        "seed":           str(outputs.get("seed") or ""),
        "steps":          str(detail.get("steps") or ""),
        "sampler":        detail.get("sampler", ""),
        "cfg_scale":      str(detail.get("cfg_scale") or ""),
        "model_id":       str(params.get("modelId") or ""),
        "model_name":     "",  # filled in by caller after model_name_gql
        "loras":          "",  # filled in by caller via resolve_loras()
        "negative_prompt": neg,
        "clip_skip":      str(clip) if clip != "" else "",
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
    workers = max(1, getattr(args, "workers", 1) or 1)
    if workers > 1:
        print("Converting with {} parallel workers.".format(workers))
    _prog = getattr(args, "progress", None)
    pillow_missing = False
    for p, res in _parallel_map(
            webp_files,
            lambda f: convert_image(f, target, args.jpeg_quality, args.jpeg_bg,
                                    keep_original=args.keep_webp),
            workers, _prog):
        note = res[1] if res else "error"
        if note == "pillow-missing":
            pillow_missing = True
            break
        if note == "ok":
            ok += 1
        else:
            print("  FAILED {}: {}".format(p.name, note))
            failed += 1
        if not _prog and workers <= 1:
            sys.stdout.write("\r  {:,}/{:,}  ok {:,}  failed {:,}  ".format(
                ok + failed, total, ok, failed))
            sys.stdout.flush()
    if pillow_missing:
        raise PixAIError("--convert-existing needs Pillow:  pip install pillow")

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


ORGANIZE_MANIFEST = "organize_manifest.csv"


def cmd_organize(args, out, img_dir, db_path):
    """Normalize PixAI images into YYYY-MM/ month folders with descriptive,
    readable filenames (prompt_taskid_mediaid) -- one flat scheme, NO batch
    subfolders. Scans the WHOLE backup (flat images/, existing month folders, and
    any legacy batches/), so a single run brings everything to the same layout for
    easy Explorer browsing.

    Safety: writes a reversible move-manifest (organize_manifest.csv: old->new) so
    every move can be undone with --undo-organize. Idempotent (files already at
    their target are skipped), byte-safe (never overwrites a differing file), and
    dry-runnable. Metadata embedding (--embed-metadata) and conversion (--convert)
    are opt-in. Imported (source='local') files and videos are left untouched."""
    db_path = _ensure_db(out)
    meta_by_mid = {}
    for row in load_catalog(db_path):
        mid = row.get("media_id")
        if mid:
            meta_by_mid[mid] = row

    skip_dirs = (out / "gallery", out / "_duplicates", out / "videos", out / "imported")

    def _target(mid, row, ext):
        month = (row.get("created_at") or "")[:7] or "unknown-date"
        stem = build_stem_name(row.get("prompt_preview", ""), row.get("task_id", ""),
                               mid, args.name_length, args.name_sep)
        return out / month / (stem + ext)

    # Sources: every PixAI image on disk (catalog media), wherever it currently is.
    plan, in_place = [], 0
    for p in out.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _IMAGE_EXTS:
            continue
        if p.name.endswith(".part") or p.name.startswith("_"):
            continue
        if any(_under(p, d) for d in skip_dirs):
            continue
        row = meta_by_mid.get(media_id_of(p))
        if not row or (row.get("source") or "") == "local":
            continue                       # unknown file or user import: leave it
        dst = _target(media_id_of(p), row, p.suffix.lower())
        if p.resolve() == dst.resolve():
            in_place += 1
            continue
        plan.append((p, dst, media_id_of(p), row))

    print("Organize plan: {} file(s) -> YYYY-MM/ with descriptive names; "
          "{} already in place.".format(len(plan), in_place))
    for src, dst, mid, row in plan[:6]:
        print("  {}  ->  {}".format(src.relative_to(out), dst.relative_to(out)))
    if len(plan) > 6:
        print("  ... and {} more".format(len(plan) - 6))
    if args.convert:
        print("Will also convert to {}.".format(args.convert))
    if getattr(args, "embed_metadata", False):
        print("Will embed prompt metadata into PNG/JPEG (WebP skipped).")

    if args.dry_run:
        print("\nDry run -- nothing moved. Re-run without --dry-run to apply.")
        return
    if not plan:
        print("Nothing to do -- everything already organized.")
        return

    manifest_path = out / ORGANIZE_MANIFEST
    mf_new = not manifest_path.exists()
    mf = open(manifest_path, "a", newline="", encoding="utf-8")
    mw = csv.writer(mf)
    if mf_new:
        mw.writerow(["old_path", "new_path", "ts"])
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    moved = converted = embedded = skipped = deduped = 0
    catalog_updates = {}                   # media_id -> new basename
    month_index = defaultdict(list)
    _prog = getattr(args, "progress", None)

    for n, (src, dst, mid, row) in enumerate(plan):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.resolve() != src.resolve():
            # Target already holds this media. Byte-identical -> drop the redundant
            # source (this is the INVARIANT-7 protection). Differ -> keep both.
            if _same_bytes(src, dst):
                try:
                    src.unlink(); deduped += 1
                except OSError:
                    pass
            else:
                print("  KEPT both (differ): {} vs {}".format(src.name, dst.relative_to(out)))
            skipped += 1
            final = dst
        else:
            try:
                src.replace(dst)
                mw.writerow([str(src.relative_to(out)).replace("\\", "/"),
                             str(dst.relative_to(out)).replace("\\", "/"), ts])
                mf.flush()
                moved += 1
                final = dst
                catalog_updates[mid] = final.name
            except OSError as e:
                print("  move failed {} ({})".format(src.name, e))
                continue
        if args.convert:
            final, note = convert_image(final, args.convert, args.jpeg_quality,
                                        args.jpeg_bg, keep_original=args.keep_webp)
            if note == "pillow-missing":
                raise PixAIError("--convert needs Pillow:  pip install pillow")
            if note == "ok":
                converted += 1
            catalog_updates[mid] = final.name
        if getattr(args, "embed_metadata", False):
            note = embed_metadata(final, {
                "prompt": row.get("prompt_preview", ""), "task_id": row.get("task_id", ""),
                "media_id": mid, "width": row.get("width", ""), "height": row.get("height", ""),
                "created_at": row.get("created_at", ""), "status": row.get("status", ""),
                "source": "PixAI"})
            if note == "ok":
                embedded += 1
        month_index[final.parent.name].append({
            "filename": final.name, "media_id": mid, "task_id": row.get("task_id", ""),
            "prompt_preview": row.get("prompt_preview", ""), "width": row.get("width", ""),
            "height": row.get("height", ""), "created_at": row.get("created_at", ""),
            "status": row.get("status", "")})

        if _prog:
            _prog(n + 1, len(plan), 0)
        else:
            sys.stdout.write("\r  {:,}/{:,}  moved {:,}  ".format(n + 1, len(plan), moved))
            sys.stdout.flush()
    mf.close()
    if not _prog:
        print()

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

    # Tidy up now-empty legacy batches/ folders (drop their _prompt.txt first).
    batches_root = out / "batches"
    if batches_root.exists():
        for f in batches_root.rglob("_prompt.txt"):
            try:
                f.unlink()
            except OSError:
                pass
        for d in sorted([p for p in batches_root.rglob("*") if p.is_dir()],
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            batches_root.rmdir()
        except OSError:
            pass

    if catalog_updates:
        rows = load_catalog(db_path)
        for r in rows:
            if r["media_id"] in catalog_updates:
                r["filename"] = catalog_updates[r["media_id"]]
                r["batch"] = ""            # batches are gone
        save_catalog(db_path, rows)
        print("Updated {:,} catalog entries.".format(len(catalog_updates)))

    print("\nOrganized: moved {:,}, already-in-place {:,}.".format(moved, in_place))
    if deduped:
        print("Removed {:,} redundant byte-identical copies.".format(deduped))
    if args.convert:
        print("Converted to {}: {:,}.".format(args.convert, converted))
    if embedded:
        print("Embedded metadata into {:,} images.".format(embedded))
    print("Reversible manifest: {}  (run --undo-organize to revert)".format(manifest_path))


def cmd_undo_organize(args, out):
    """Reverse the moves recorded in organize_manifest.csv (newest run first):
    each new_path is moved back to its old_path. Safe (skips already-reverted),
    then clears the manifest. Lets a re-normalize be undone if you don't like it."""
    db_path = _ensure_db(out)
    manifest_path = out / ORGANIZE_MANIFEST
    if not manifest_path.exists():
        print("No organize manifest found ({}); nothing to undo.".format(manifest_path))
        return
    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("new_path")]
    print("Reverting {} recorded move(s)...".format(len(rows)))
    if getattr(args, "dry_run", False):
        for r in rows[:8]:
            print("  {}  ->  {}".format(r["new_path"], r["old_path"]))
        print("\nDry run -- nothing moved.")
        return
    reverted = miss = 0
    for r in reversed(rows):               # undo newest first
        new_p, old_p = out / r["new_path"], out / r["old_path"]
        if old_p.exists() and not new_p.exists():
            continue                       # already reverted
        if not new_p.exists():
            miss += 1
            continue
        old_p.parent.mkdir(parents=True, exist_ok=True)
        try:
            new_p.replace(old_p)
            reverted += 1
        except OSError as e:
            print("  revert failed {} ({})".format(new_p, e))
    # The gallery resolves files by media id (find_files_for_media_id matches both
    # naming layouts), so restored files still resolve without rewriting the
    # catalog. Clear the manifest now that it's been applied.
    manifest_path.unlink()
    print("Reverted {} file(s); {} already gone. Manifest cleared.".format(reverted, miss))


# ---------------------------------------------------------------------------
# Callable API (used by the GUI; also called by main() for the CLI)
# ---------------------------------------------------------------------------
def _make_session(token_val):
    """Validate config, load token, return a configured requests.Session.
    Re-reads config.json at call time so the GUI works even when the module
    was imported before the working directory was set correctly."""
    global PERSISTED_QUERY_HASH, U3T, USER_ID, TASK_DETAIL_HASH, MODEL_DETAIL_HASH
    global DELETE_TASK_HASH
    fresh = _load_config()
    if fresh:
        PERSISTED_QUERY_HASH = fresh.get("PERSISTED_QUERY_HASH", "") or PERSISTED_QUERY_HASH
        U3T = fresh.get("U3T", "") or U3T
        USER_ID = fresh.get("USER_ID", "") or USER_ID
        TASK_DETAIL_HASH = fresh.get("TASK_DETAIL_HASH", "") or TASK_DETAIL_HASH
        MODEL_DETAIL_HASH = fresh.get("MODEL_DETAIL_HASH", "") or MODEL_DETAIL_HASH
        DELETE_TASK_HASH = fresh.get("DELETE_TASK_HASH", "") or DELETE_TASK_HASH
    have_api_key = bool((fresh or {}).get("PIXAI_API_KEY") or _cfg.get("PIXAI_API_KEY"))
    # Persisted hashes now ship with defaults, so the API-key path needs nothing but
    # the key (USER_ID is auto-resolved below). The legacy browser-JWT path still
    # wants a U3T alongside its short-lived token.
    if not have_api_key and not U3T:
        raise PixAIError(
            "No API key found. Add PIXAI_API_KEY to config.json (recommended -- then "
            "nothing else is required), or use the legacy token path (U3T + token.txt).\n"
            "Copy config.example.json to config.json. See docs/setup.md.")
    token = load_token(token_val)
    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/json",
        "User-Agent": "pixai-personal-backup/1.0",
        "apollo-require-preflight": "true",
        "x-apollo-operation-name": OPERATION_NAME,
    })
    # Auto-resolve the user id from the API key when it isn't pinned in config.
    if not USER_ID:
        if have_api_key:
            try:
                USER_ID = resolve_user_id(session)
                vlog("resolved USER_ID from API key: {}".format(USER_ID))
            except Exception as e:
                raise PixAIError(
                    "Could not auto-resolve your user id from the API key "
                    "(me query failed: {}).\nAdd USER_ID to config.json as a fallback."
                    .format(e))
        else:
            raise PixAIError("config.json needs USER_ID (or set PIXAI_API_KEY to "
                             "auto-resolve it).")
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


def run_delete_tasks(args):
    """Delete one or more generation tasks from your PixAI account (IRREVERSIBLE).

    Guards, in order:
      1. Dry-run by default -- prints the target list and stops. Requires --apply.
      2. With --apply, a typed 'delete' confirmation (skippable with --yes, which
         is refused on a non-interactive stdin unless explicitly passed).
      3. Single-attempt per task (delete_task_gql does no retry).
    Local backups (image files + catalog.db) are NOT touched -- this only removes
    the generation from your account on PixAI's servers.
    """
    raw = getattr(args, "delete_task", None) or []
    seen, ids = set(), []
    for t in raw:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            ids.append(t)
    if not ids:
        raise PixAIError("No task ids given. Usage: --delete-task <taskId> [<taskId> ...]")

    print("Tasks targeted for deletion ({}):".format(len(ids)))
    for t in ids:
        print("  {}".format(t))

    if not getattr(args, "apply", False):
        print("\nDRY RUN -- nothing deleted. Re-run with --apply to permanently delete "
              "these from your PixAI account.")
        print("(Deletion is irreversible. Your local backups are NOT affected.)")
        return {"targeted": len(ids), "deleted": 0, "failed": 0, "dry_run": True}

    if not getattr(args, "yes", False):
        if not getattr(sys.stdin, "isatty", lambda: False)():
            raise PixAIError(
                "--apply needs interactive confirmation. Re-run attached to a terminal, "
                "or pass --yes to confirm non-interactively (irreversible -- be careful).")
        ans = input("\nPermanently delete {} task(s) from your PixAI account? "
                    "Type 'delete' to confirm: ".format(len(ids)))
        if ans.strip().lower() != "delete":
            print("Aborted -- nothing deleted.")
            return {"targeted": len(ids), "deleted": 0, "failed": 0, "aborted": True}

    session = _make_session(getattr(args, "token", None))
    delay = getattr(args, "delay", 0.4)
    deleted = failed = 0
    for i, t in enumerate(ids, 1):
        try:
            # deleteGenerationTask is a void mutation: it returns null on a
            # SUCCESSFUL delete and raises (GraphQL errors / 401 / PersistedQuery
            # NotFound) on failure. So a clean return -- whatever the payload --
            # means the task was deleted.
            delete_task_gql(session, t)
            deleted += 1
            print("  [{}/{}] deleted task {}".format(i, len(ids), t))
        except PixAIError as e:
            failed += 1
            print("  [{}/{}] FAILED task {}: {}".format(i, len(ids), t, e))
        if i < len(ids):
            time.sleep(delay)
    print("\nDeletion complete: {} deleted, {} failed.".format(deleted, failed))
    return {"targeted": len(ids), "deleted": deleted, "failed": failed}


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
        workers = max(1, getattr(args, "workers", 1) or 1)
        print("\nDownloading {} animated artwork video(s) -> videos/ {}...".format(
            len(videos), "({} workers) ".format(workers) if workers > 1 else ""))

        def _fetch_video(item):
            vmid, title = item
            if already_downloaded(out, vmid):
                return "skip"
            url, info = resolve_media(session, vmid)
            if not url:
                return "missing"
            stem = vdir / build_stem_name(title or "", "", vmid,
                                          getattr(args, "name_length", 60),
                                          getattr(args, "name_sep", "_"))
            status, path = download(session, url, stem)
            return status

        for item, status in _parallel_map(videos, _fetch_video, workers, _prog,
                                          delay=getattr(args, "delay", 0.4)):
            if status in ("ok", "skip"):
                vids_ok += 1
            elif status == "missing":
                print("  no media url for video {} ({})".format(item[0], item[1]))
        print("Videos saved/present: {} of {}.".format(vids_ok, len(videos)))
    elif videos and not with_videos:
        print("({} animated artworks have video; re-run with --with-videos to download them.)"
              .format(len(videos)))

    return {"artworks": artworks, "matched": matched, "videos": vids_ok}


def run_sync_videos(args):
    """Back up image-to-video generations. The task listing exposes only a video's
    THUMBNAIL media id; the real video media id lives in getTaskById ->
    outputs.videos[].mediaId, and its mp4 URL in the GraphQL media object's
    fileUrl. So: find i2v tasks (i2vProModel set in the summary), fetch each task,
    resolve + download the mp4 into videos/, and catalog it as a video row
    (is_video=1) with the still frame as its poster."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    session = _make_session(getattr(args, "token", None))
    vdir = out / "videos"
    workers = max(1, getattr(args, "workers", 1) or 1)
    name_length = getattr(args, "name_length", 60)
    name_sep = getattr(args, "name_sep", "_")
    _prog = getattr(args, "progress", None)

    # 1. Page the whole feed; collect the cheap i2v task summaries.
    print("Scanning generation history for image-to-video tasks...")
    i2v_nodes, before, scanned = [], None, 0
    while True:
        conn = find_connection(gql(session, page_variables(
            getattr(args, "page_size", 250) or 250, before)))
        if not conn:
            break
        edges = conn.get("edges") or []
        if not edges:
            break
        for e in edges:
            n = e.get("node") or {}
            scanned += 1
            if n.get("i2vProModel"):
                i2v_nodes.append(n)
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasPreviousPage"):
            break
        before = pi.get("startCursor")
    print("Found {} image-to-video task(s) across {} generations.".format(
        len(i2v_nodes), scanned))
    if not i2v_nodes:
        return {"i2v_tasks": 0, "videos": 0}
    vdir.mkdir(parents=True, exist_ok=True)

    # Generate a gallery poster thumbnail for a video (keyed by the VIDEO media
    # id) from its still frame, so previews work without a separate image backup.
    from pixai_gallery import make_thumbnail
    thumb_dir = out / "gallery" / "thumbs"
    poster_tmp = out / "gallery" / "_postertmp"

    def _ensure_video_thumb(video_media_id, poster_media_id, video_path=None):
        thumb_path = thumb_dir / "{}.jpg".format(video_media_id)
        if thumb_path.exists():
            return
        # Preferred: thumbnail the PixAI still-frame poster.
        if poster_media_id:
            url, _info = resolve_media(session, poster_media_id)
            if url:
                poster_tmp.mkdir(parents=True, exist_ok=True)
                status, path = download(session, url, poster_tmp / str(poster_media_id))
                if status in ("ok", "skip") and path:
                    make_thumbnail(path, thumb_path)
                    try:
                        path.unlink()
                    except OSError:
                        pass
        # Fallback (no poster, e.g. older i2v): first frame of the mp4 via ffmpeg.
        if not thumb_path.exists() and video_path:
            video_poster_thumb(video_path, thumb_path)

    # 2. Per task: getTaskById -> video outputs -> fileUrl -> download mp4.
    def _do_task(node):
        task = task_detail_gql(session, node["id"])
        outs, shared = video_outputs(task)
        detail = ((task or {}).get("outputs") or {}).get("detailParameters") or {}
        params = (task or {}).get("parameters") or {}
        rows = []
        for o in outs:
            vmid = o["video_media_id"]
            hit = [p for p in vdir.glob("*_{}.*".format(vmid))
                   if not p.name.endswith(".part") and p.stat().st_size > 0]
            if hit:
                path, status = hit[0], "skip"
            else:
                fm = media_file_gql(session, vmid)
                url = fm.get("fileUrl")
                if not url:
                    rows.append("missing")
                    continue
                stem = vdir / build_stem_name(
                    shared.get("prompt") or node.get("promptsPreview", ""),
                    node["id"], vmid, name_length, name_sep)
                status, path = download(session, url, stem)
            if status in ("ok", "skip") and path:
                full = {f: "" for f in CATALOG_FIELDS}
                full.update({
                    "task_id": str(node["id"]),
                    "media_id": vmid,
                    "filename": str(path.relative_to(out)).replace("\\", "/"),
                    "prompt_full": shared.get("prompt", ""),
                    "prompt_preview": (node.get("promptsPreview") or "")[:100],
                    "seed": str(o.get("seed") or ""),
                    "created_at": node.get("createdAt", ""),
                    "width": str(detail.get("width") or ""),
                    "height": str(detail.get("height") or ""),
                    "model_id": str(params.get("modelId") or ""),
                    "status": "completed",
                    "is_video": "1",
                    "poster_media_id": o.get("poster_media_id", ""),
                    "video_duration": str(shared.get("duration") or ""),
                })
                _ensure_video_thumb(vmid, o.get("poster_media_id"), path)
                rows.append(full)
            else:
                rows.append(status)
        return rows

    print("Resolving + downloading videos -> videos/ {}...".format(
        "({} workers) ".format(workers) if workers > 1 else ""))
    new_rows, ok, missing = [], 0, 0
    for node, result in _parallel_map(i2v_nodes, _do_task, workers, _prog,
                                      delay=getattr(args, "delay", 0.4)):
        for item in (result or []):
            if isinstance(item, dict):
                new_rows.append(item); ok += 1
            elif item == "missing":
                missing += 1
    if new_rows:
        save_catalog(db_path, new_rows)
    print("Videos saved/present: {}{}.".format(
        ok, " | {} had no resolvable file url".format(missing) if missing else ""))
    return {"i2v_tasks": len(i2v_nodes), "videos": ok}


_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".m4v"})


def _under(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _ffmpeg_path(_cache=[]):
    """Return the ffmpeg executable path if available, else '' (cached)."""
    if not _cache:
        import shutil
        _cache.append(shutil.which("ffmpeg") or "")
    return _cache[0]


def video_poster_thumb(video_path, thumb_path):
    """Extract the first frame of a video via ffmpeg and write it as the gallery
    thumbnail. OPTIONAL: returns False (no-op) if ffmpeg isn't on PATH, so videos
    just fall back to the placeholder + play badge. Used for imported videos and
    as a fallback for i2v videos with no still-frame poster."""
    ff = _ffmpeg_path()
    if not ff:
        return False
    import subprocess
    import tempfile
    from pixai_gallery import make_thumbnail
    tmp = Path(tempfile.gettempdir()) / ("poster_{}.png".format(Path(thumb_path).stem))
    try:
        subprocess.run([ff, "-y", "-ss", "0.5", "-i", str(video_path),
                        "-frames:v", "1", str(tmp)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    except Exception:                                # noqa: BLE001
        return False
    ok = tmp.exists() and tmp.stat().st_size > 0 and make_thumbnail(tmp, Path(thumb_path))
    try:
        tmp.unlink()
    except OSError:
        pass
    return bool(ok)


def run_import_local(args):
    """Catalog non-PixAI media so it shows + plays in the gallery (source='local').

    Two modes:
      * No dir (or a dir already inside the backup): scan the backup folder and
        catalog any image/video NOT already in the catalog -- i.e. files you
        dropped into videos/ or anywhere under the backup.
      * External dir: copy each media file into the backup (videos/ or imported/)
        then catalog it.

    Idempotent: files already cataloged (by relative path) are skipped, so it's
    safe to re-run. Images get a gallery thumbnail; videos play via the catalog
    filename (no still to thumbnail, so they show a placeholder + the video badge)."""
    import hashlib
    import shutil
    from pixai_gallery import make_thumbnail
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "catalog.db"
    init_db(db_path)                  # import can seed a fresh, download-free backup
    thumb_dir = out / "gallery" / "thumbs"
    media_exts = _IMAGE_EXTS | _VIDEO_EXTS

    raw = getattr(args, "import_local", None)
    src = Path(raw) if raw else out
    if not src.exists():
        raise PixAIError("import path not found: {}".format(src))
    try:
        external = not _under(src.resolve(), out.resolve()) and src.resolve() != out.resolve()
    except OSError:
        external = False

    _prog = getattr(args, "progress", None)
    catalog_rows = load_catalog(db_path)
    existing = {(r.get("filename") or "").replace("\\", "/")
                for r in catalog_rows if r.get("filename")}
    # Also key on media_id: an already-backed-up PixAI file is named after its
    # media id, so media_id_of() of an organized file matches an existing row even
    # though its on-disk path no longer equals the stored `filename` string. This
    # is what stops --import-local from re-cataloging the whole backup as 'local'.
    existing_mids = {r.get("media_id") for r in catalog_rows if r.get("media_id")}
    gallery_dir = out / "gallery"
    quarantine = out / "_duplicates"

    print("Scanning {} for media (this can take a moment on a large backup)...".format(src),
          flush=True)
    candidates, scanned = [], 0
    for p in src.rglob("*"):
        scanned += 1
        if scanned % 5000 == 0:
            vlog("scanned {} files, {} media so far...".format(scanned, len(candidates)))
        if p.is_file() and p.suffix.lower() in media_exts and not p.name.endswith(".part"):
            candidates.append(p)
    total = len(candidates)
    print("Found {} media file(s) among {} scanned; cataloging new ones...".format(
        total, scanned), flush=True)

    rows, made, skipped = [], 0, 0
    for idx, p in enumerate(candidates):
        if _prog:
            _prog(idx + 1, total, 0)
        if not external and (_under(p, gallery_dir) or _under(p, quarantine)):
            continue
        is_vid = p.suffix.lower() in _VIDEO_EXTS
        if external:
            dest_dir = out / ("videos" if is_vid else "imported")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / p.name
            if not dest.exists():
                shutil.copy2(p, dest)
            stored = dest
        else:
            stored = p
        rel = str(stored.relative_to(out)).replace("\\", "/")
        if rel in existing or media_id_of(stored) in existing_mids:
            skipped += 1                  # already cataloged (by path OR PixAI media id)
            continue
        mid = "local_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
        try:
            created = time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(stored.stat().st_mtime))
        except OSError:
            created = ""
        full = {f: "" for f in CATALOG_FIELDS}
        full.update({
            "media_id": mid, "filename": rel, "source": "local",
            "status": "imported", "created_at": created,
            "prompt_preview": stored.stem[:100],
            "is_video": "1" if is_vid else "",
        })
        rows.append(full)
        if is_vid:
            video_poster_thumb(stored, thumb_dir / "{}.jpg".format(mid))  # ffmpeg, optional
        else:
            make_thumbnail(stored, thumb_dir / "{}.jpg".format(mid))
        made += 1
        vlog("imported {} ({})".format(rel, "video" if is_vid else "image"))

    if rows:
        save_catalog(db_path, rows)
    print("Imported {} new local file(s){}; {} already cataloged.".format(
        made, " (copied into the backup)" if external else "", skipped))
    return {"imported": made, "skipped": skipped}


_GEN_MUTATION = ("mutation createGenerationTask($parameters: JSONObject!) {"
                 " createGenerationTask(parameters: $parameters) { id } }")
_GEN_STATUS = "query($id: ID!) { task(id: $id) { id status paidCredit } }"
DEFAULT_GEN_MODEL = "1983308862240288769"  # Tsubaki.2 v1 (override with --model)


def _lora_params(raw):
    """Turn LoRA specs into createGenerationTask's two fields. `raw` is a list of
    'versionId:weight' strings or (versionId, weight) tuples. Returns
    ({versionId: weight}, [{weight, versionId}])."""
    lora_map, lora_list = {}, []
    for item in (raw or []):
        if isinstance(item, (tuple, list)):
            vid, w = str(item[0]).strip(), item[1]
        else:
            vid, _sep, ws = str(item).partition(":")
            vid = vid.strip()
            w = ws.strip()
        if not vid:
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            w = 0.7
        lora_map[vid] = w
        lora_list.append({"weight": w, "versionId": vid})
    return lora_map, lora_list


def _gen_parameters(args):
    if getattr(args, "params_json", ""):
        return json.loads(args.params_json)
    def _dim(v):                          # SD models require multiples of 8
        return max(64, (int(v) // 8) * 8)
    params = {
        "prompts": args.prompt,
        # naturalPrompts is the natural-language form the prompt-helper reads; send
        # it alongside prompts (PixAI's generator does the same).
        "naturalPrompts": args.prompt,
        "modelId": args.model or DEFAULT_GEN_MODEL,
        "width": _dim(args.width),
        "height": _dim(args.height),
        "samplingSteps": args.steps,
        "cfgScale": args.cfg,
        "batchSize": args.count,
        # 1000 = high priority (faster, more credits); 500 = standard (cheaper).
        # We default to standard so a run costs less unless high is requested.
        "priority": getattr(args, "priority", 500) or 500,
    }
    # Quality mode (inferenceProfile) is MODEL-TYPE-SPECIFIC: SD_V1_MODEL accepts
    # lite/standard but rejects pro/ultra (those are for newer model types). So we
    # only send it when explicitly chosen; "auto"/"" omits it and lets PixAI pick
    # the model's default (always safe -- this is the original working behavior).
    mode = (getattr(args, "mode", "") or "").strip().lower()
    if mode and mode != "auto":
        params["inferenceProfile"] = mode
    if getattr(args, "negative", ""):
        params["negativePrompts"] = args.negative
    if getattr(args, "seed", None) is not None:
        params["seed"] = args.seed
    # LoRAs: createGenerationTask wants BOTH a {versionId: weight} map and a
    # [{weight, versionId}] array, keyed by the LoRA's version id.
    lmap, llist = _lora_params(getattr(args, "lora", None))
    if lmap:
        params["lora"] = lmap
        params["loraParameters"] = llist
    # Prompt helper (auto-interprets/enhances the natural prompt). On by default to
    # match the site; turn OFF when it mangles a carefully-built prompt.
    if getattr(args, "prompt_helper", True):
        params["promptHelper"] = {"withStage": True, "userWantToEnable": True,
                                  "forcePromptHelperDetectionSide": "server"}
    else:
        params["promptHelper"] = {"withStage": False, "userWantToEnable": False,
                                  "forcePromptHelperDetectionSide": "server"}
    return params


# --- Video (image-to-video) generation ---------------------------------------
# The i2v generator uses the SAME createGenerationTask mutation as images, but the
# `parameters` JSONObject is a nested {type, version, parameters:{i2vPro:{...}}}
# shape (reverse-engineered from a real payload, 2026-07-01). A source image
# (media_id) becomes the first frame; an optional tail image gives first/last-frame
# interpolation. This is the engine "Generate shot" will call once wired up.
DEFAULT_VIDEO_MODEL = "v4.0.1"


def build_video_parameters(prompt, media_id, model=DEFAULT_VIDEO_MODEL, *,
                           tail_media_id="", duration=5, mode="professional",
                           generate_audio=False, audio_language="english",
                           negative="", use_prompt_helper=False, kaisuuken_id=""):
    """Build createGenerationTask's `parameters` for an image-to-video (i2vPro) job.

    VERIFIED against a real submit (2026-07-01): video uses the SAME
    createGenerationTask mutation, and `variables.parameters` = {channel, i2vPro:{...}}
    -- NOT a {type,version,parameters} envelope (that earlier wrapper made the server
    ignore i2vPro and default to a plain image). `media_id` = source/first frame;
    `tail_media_id` (optional) = last frame for first/last-frame interpolation.

    NOTE: video costs FAR more than images (~27.5k credits for a 5s V4.0 clip), so
    submission stays gated behind explicit --confirm. This builder spends nothing.
    """
    i2v = {
        "model": model,
        "mediaId": str(media_id),
        "usePromptsHelper": bool(use_prompt_helper),
        "prompts": prompt or "",
        "mode": mode,                        # "basic" | "professional"
        "duration": str(duration),           # seconds, as a string ("5"/"10"/"15")
        "generateAudio": bool(generate_audio),
        "audioLanguage": audio_language,
    }
    if tail_media_id:
        i2v["tailMediaId"] = str(tail_media_id)
    if negative:
        i2v["negativePrompts"] = negative
    params = {"channel": "private", "i2vPro": i2v}
    if kaisuuken_id:
        params["kaisuukenId"] = str(kaisuuken_id)   # spend a free card instead of credits
    return params


def _gen_video_parameters(args):
    """Build the i2v `parameters` from CLI/GUI args (thin wrapper over
    build_video_parameters). `--params-json` overrides everything."""
    if getattr(args, "params_json", ""):
        return json.loads(args.params_json)
    return build_video_parameters(
        getattr(args, "prompt", "") or "",
        getattr(args, "image", "") or "",
        model=(getattr(args, "video_model", "") or getattr(args, "model", "")
               or DEFAULT_VIDEO_MODEL),
        tail_media_id=getattr(args, "tail", "") or "",
        duration=getattr(args, "duration", 5) or 5,
        mode=getattr(args, "vmode", None) or "professional",
        generate_audio=bool(getattr(args, "audio", False)),
        audio_language=getattr(args, "audio_language", None) or "english",
        negative=getattr(args, "negative", "") or "",
        use_prompt_helper=bool(getattr(args, "video_prompt_helper", False)),
        kaisuuken_id=getattr(args, "kaisuuken_id", "") or "",
    )


# --- media upload + instruct-editing (the "Edit this image" surface) --------------
# uploadMedia is a 3-step S3 handshake (verified 2026-07-01): request a presigned
# target, PUT the bytes, then register -> media_id. It's a plain GraphQL mutation, so
# gql_adhoc drives it with no persisted hash. Uploading is FREE.
_UPLOAD_MEDIA_MUT = (
    "mutation uploadMedia($input: UploadMediaInput!) {"
    " uploadMedia(input: $input) { uploadUrl externalId mediaId"
    " media { id type width height } } }")

# PixAI "Edit Pro" (instruct-editing) model. Override with --edit-model.
EDIT_PRO_MODEL_ID = "2006468692917575683"


def upload_media(session, path, media_type="IMAGE"):
    """Upload a LOCAL image file to PixAI and return its media_id.

    Three steps (verified from the live app): (1) uploadMedia({type,provider:"S3"})
    returns a presigned S3 `uploadUrl` + an `externalId`; (2) PUT the file bytes to
    that URL (raw S3, NOT our API session -- so the Bearer never leaks to S3);
    (3) uploadMedia({type,provider,externalId}) registers the object and returns the
    `mediaId`. Lets local images feed edit / i2v / reference flows. Uploading is free.
    """
    p = Path(path)
    if not p.is_file():
        raise PixAIError("upload: file not found: {}".format(p))
    data = p.read_bytes()

    r1 = gql_adhoc(session, _UPLOAD_MEDIA_MUT,
                   {"input": {"type": media_type, "provider": "S3"}})
    u = (r1 or {}).get("uploadMedia") or {}
    upload_url, external_id = u.get("uploadUrl"), u.get("externalId")
    if not upload_url or not external_id:
        raise PixAIError("upload: no presigned url/externalId returned: "
                         + json.dumps(r1)[:300])

    ct = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    try:
        put = requests.put(upload_url, data=data, headers={"Content-Type": ct}, timeout=180)
    except requests.exceptions.SSLError:
        raise PixAIError(_ssl_help())
    if put.status_code not in (200, 201, 204):
        raise PixAIError("upload: S3 PUT failed (HTTP {}): {}".format(
            put.status_code, (put.text or "")[:200]))

    r3 = gql_adhoc(session, _UPLOAD_MEDIA_MUT,
                   {"input": {"type": media_type, "provider": "S3",
                              "externalId": external_id}})
    reg = (r3 or {}).get("uploadMedia") or {}
    mid = reg.get("mediaId") or (reg.get("media") or {}).get("id")
    if not mid:
        raise PixAIError("upload: registration returned no mediaId: " + json.dumps(r3)[:300])
    return str(mid)


def _is_local_source(src):
    """A source is a local file to upload (vs an existing catalog media_id) when it
    points at a real file on disk. media_ids are big numeric strings; paths aren't."""
    try:
        return bool(src) and os.path.isfile(src)
    except (OSError, ValueError):
        return False


def build_chat_edit_parameters(prompt, media_ids, model_id=EDIT_PRO_MODEL_ID, *,
                               resolution="1K", aspect_ratio="3:4", quality="medium",
                               kaisuuken_id=""):
    """Build createGenerationTask's `parameters` for an instruct edit (the `chat`
    block), verified against a real Edit-Pro submit (2026-07-01). `media_ids` is one
    or more source media_ids (an array => multi-image reference editing); the first
    is also sent as `mediaId`. NOTE: we deliberately DO NOT attach a `kaisuukenId`
    (free-card) here -- free-card spending is a separate, explicit feature; without it
    the server charges credits, so this stays behind --confirm like all spend paths.
    """
    ids = [str(m) for m in (media_ids or []) if str(m).strip()]
    if not ids:
        raise PixAIError("edit needs at least one source media_id")
    params = {"chat": {
        "prompts": prompt or "",
        "mediaId": ids[0],
        "mediaIds": ids,
        "modelId": str(model_id or EDIT_PRO_MODEL_ID),
        "modelConfig": {"resolution": resolution,
                        "aspectRatio": aspect_ratio,
                        "quality": quality},
    }}
    if kaisuuken_id:
        params["kaisuukenId"] = str(kaisuuken_id)   # spend a free card instead of credits
    return params


def _edit_config_from_args(args):
    """Pull the modelConfig knobs (with defaults) out of CLI/GUI args."""
    return dict(
        model_id=getattr(args, "edit_model", "") or EDIT_PRO_MODEL_ID,
        resolution=getattr(args, "edit_resolution", "") or "1K",
        aspect_ratio=getattr(args, "edit_aspect", "") or "3:4",
        quality=getattr(args, "edit_quality", "") or "medium",
        kaisuuken_id=getattr(args, "kaisuuken_id", "") or "",
    )


def run_generate(args):
    """Create images via PixAI (createGenerationTask), poll to completion, download
    the results into the backup, and catalog them as source='api'. GUARDED: without
    --confirm it only prints a preview (spends no credits). Reuses gql_adhoc + the
    shared session/download/catalog plumbing."""
    out = Path(args.out)
    params = _gen_parameters(args)
    existing_task = (getattr(args, "task_id", "") or "").strip()

    if not existing_task and not getattr(args, "confirm", False):
        print("=== PixAI createGenerationTask (PREVIEW -- no credits spent) ===")
        print(json.dumps({"parameters": params}, indent=2))
        print("\nThis would SPEND PixAI credits. Re-run with --confirm to submit.")
        return {"submitted": False}

    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "catalog.db"
    init_db(db_path)                  # generation can seed a fresh backup
    session = _make_session(getattr(args, "token", None))
    thumb_dir = out / "gallery" / "thumbs"
    from pixai_gallery import make_thumbnail

    if existing_task:
        # Recover an already-created generation by id (no new credits). Tool/API
        # generations don't enter listUserTaskSummaries, so --update can't fetch
        # them -- this is how you reclaim a stranded paid generation.
        task_id = existing_task
        print("Fetching existing task (no credits):", task_id)
    else:
        print("Submitting generation task...")
        try:
            created = gql_adhoc(session, _GEN_MUTATION, {"parameters": params})
        except PixAIError as e:
            # inferenceProfile is model-type-specific; a rejected submit costs no
            # credits, so if the chosen mode isn't supported, fall back to the
            # model's default and retry once instead of failing the run.
            if "inferenceProfile" in str(e) and "inferenceProfile" in params:
                dropped = params.pop("inferenceProfile")
                print("  mode '{}' not supported by this model; retrying on the "
                      "model's default...".format(dropped))
                created = gql_adhoc(session, _GEN_MUTATION, {"parameters": params})
            else:
                raise
        task_id = (created.get("createGenerationTask") or {}).get("id")
        if not task_id:
            raise PixAIError("no task id returned: " + json.dumps(created)[:300])
        print("  task id:", task_id)

        deadline = time.time() + getattr(args, "poll_timeout", 300)
        while time.time() < deadline:
            task = (gql_adhoc(session, _GEN_STATUS, {"id": task_id}) or {}).get("task") or {}
            status = str(task.get("status", "")).lower()
            vlog("generate poll: {}".format(status or "(unknown)"))
            if status in ("completed", "succeeded", "success", "done"):
                break
            if status in ("failed", "error", "cancelled", "canceled"):
                raise PixAIError("generation ended with status: " + status)
            time.sleep(3)
        else:
            raise PixAIError("timed out after {}s (task {})".format(
                getattr(args, "poll_timeout", 300), task_id))

    # The Task type exposes its media under `outputs` (mediaId / batchMediaIds /
    # videos), NOT at the top level. getTaskById returns that whole object and is
    # already proven, so reuse it for the result rather than guessing an ad-hoc
    # selection set.
    result = task_detail_gql(session, task_id) or {}
    outputs = result.get("outputs") or {}
    mids = []
    if outputs.get("mediaId"):
        mids.append(str(outputs["mediaId"]))
    for m in outputs.get("batchMediaIds") or []:
        mids.append(str(m))
    for v in outputs.get("videos") or []:
        if v.get("mediaId"):
            mids.append(str(v["mediaId"]))
    mids = list(dict.fromkeys(mids))
    if not mids:
        raise PixAIError("task completed but no media ids found")

    # Prefer the task's actual metadata (authoritative, and the only source when
    # recovering by --task-id); fall back to the params we submitted.
    fm = extract_full_meta(result)

    def _pick(fm_key, *param_keys):
        if fm.get(fm_key):
            return str(fm[fm_key])
        for pk in param_keys:
            if params.get(pk):
                return str(params[pk])
        return ""

    img_dir = out / "images"
    rows, saved = [], []
    for mid in mids:
        url, info = resolve_media(session, mid)
        if not url:
            print("  no url for media", mid)
            continue
        prompt = fm.get("prompt_full") or params.get("prompts", "")
        stem = img_dir / build_stem_name(prompt, task_id, mid,
                                         getattr(args, "name_length", 60),
                                         getattr(args, "name_sep", "_"))
        status, path = download(session, url, stem)
        if status not in ("ok", "skip") or not path:
            continue
        full = {f: "" for f in CATALOG_FIELDS}
        full.update({
            "task_id": str(task_id), "media_id": mid,
            "filename": str(path.relative_to(out)).replace("\\", "/"),
            "url": url, "source": "api", "status": "completed",
            "created_at": result.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_full": prompt,
            "prompt_preview": (prompt or "")[:100],
            "negative_prompt": _pick("negative_prompt", "negativePrompts"),
            "seed": _pick("seed", "seed"),
            "steps": _pick("steps", "samplingSteps"),
            "cfg_scale": _pick("cfg_scale", "cfgScale"),
            "model_id": _pick("model_id", "modelId"),
            "model_name": fm.get("model_name", ""),
            "loras": fm.get("loras", ""),
            "width": str((info or {}).get("width") or params.get("width") or ""),
            "height": str((info or {}).get("height") or params.get("height") or ""),
        })
        rows.append(full)
        make_thumbnail(path, thumb_dir / "{}.jpg".format(mid))
        saved.append(str(path))

    if rows:
        save_catalog(db_path, rows)
    print("Generated + cataloged {} image(s):".format(len(saved)))
    for s in saved:
        print("  " + s)
    return {"submitted": True, "task_id": task_id, "images": len(saved)}


def run_generate_video(args):
    """Create an image-to-video clip via PixAI (createGenerationTask + i2vPro params),
    poll to completion, download the mp4 into videos/, and catalog it (source='api',
    is_video='1'). GUARDED: without --confirm it only PREVIEWS (spends nothing). Video
    is expensive (~27.5k credits for a 5s V4.0 clip), so the preview shouts the cost.
    Reuses the same submit/poll as images and the same video download as --sync-videos."""
    out = Path(args.out)
    existing_task = (getattr(args, "task_id", "") or "").strip()
    if not existing_task and not (getattr(args, "image", "") or "").strip():
        raise PixAIError("--generate-video needs --image <media_id> (a catalog image to animate).")
    params = _gen_video_parameters(args)

    if not existing_task and not getattr(args, "confirm", False):
        i2v = params.get("i2vPro") or {}
        print("=== PixAI createGenerationTask -- VIDEO (PREVIEW, no credits spent) ===")
        print(json.dumps({"parameters": params}, indent=2))
        print("\n*** VIDEO GENERATION IS EXPENSIVE ***")
        print("  model={}  mode={}  duration={}s{}{}".format(
            i2v.get("model"), i2v.get("mode"), i2v.get("duration"),
            "  +audio" if i2v.get("generateAudio") else "",
            "  (first/last-frame)" if i2v.get("tailMediaId") else ""))
        print("  A V4.0 5s clip costs ~27,500 credits. Re-run with --confirm to submit.")
        return {"submitted": False}

    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "catalog.db"
    init_db(db_path)
    session = _make_session(getattr(args, "token", None))
    vdir = out / "videos"
    vdir.mkdir(parents=True, exist_ok=True)

    if existing_task:
        task_id = existing_task
        print("Fetching existing video task (no credits):", task_id)
    else:
        print("Submitting VIDEO generation task (this spends credits)...")
        created = gql_adhoc(session, _GEN_MUTATION, {"parameters": params})
        task_id = (created.get("createGenerationTask") or {}).get("id")
        if not task_id:
            raise PixAIError("no task id returned: " + json.dumps(created)[:300])
        print("  task id:", task_id)
        deadline = time.time() + getattr(args, "poll_timeout", 600)   # video renders slower
        paid_credit = None
        while time.time() < deadline:
            task = (gql_adhoc(session, _GEN_STATUS, {"id": task_id}) or {}).get("task") or {}
            status = str(task.get("status", "")).lower()
            if task.get("paidCredit") is not None:
                paid_credit = task.get("paidCredit")   # server-authoritative actual cost
            vlog("video poll: {}".format(status or "(unknown)"))
            if status in ("completed", "succeeded", "success", "done"):
                if paid_credit is not None:
                    print("  actual cost: {:,} credits".format(int(paid_credit)))
                break
            if status in ("failed", "error", "cancelled", "canceled"):
                raise PixAIError("video generation ended with status: " + status)
            time.sleep(5)
        else:
            raise PixAIError("timed out after {}s (task {})".format(
                getattr(args, "poll_timeout", 600), task_id))

    # Result: getTaskById -> outputs.videos -> fileUrl -> download mp4 (same as --sync-videos).
    result = task_detail_gql(session, task_id) or {}
    outs, shared = video_outputs(result)
    if not outs:
        raise PixAIError("video task completed but no video outputs found")
    detail = ((result or {}).get("outputs") or {}).get("detailParameters") or {}
    i2v_sent = params.get("i2vPro") or {}
    prompt = shared.get("prompt") or i2v_sent.get("prompts", "")

    from pixai_gallery import make_thumbnail
    thumb_dir = out / "gallery" / "thumbs"
    rows, saved = [], []
    for o in outs:
        vmid = o["video_media_id"]
        fm = media_file_gql(session, vmid)
        url = fm.get("fileUrl")
        if not url:
            print("  no file url for video", vmid)
            continue
        stem = vdir / build_stem_name(prompt, task_id, vmid,
                                      getattr(args, "name_length", 60), "_")
        status, path = download(session, url, stem)
        if status not in ("ok", "skip") or not path:
            continue
        full = {f: "" for f in CATALOG_FIELDS}
        full.update({
            "task_id": str(task_id), "media_id": vmid,
            "filename": str(path.relative_to(out)).replace("\\", "/"),
            "url": url, "source": "api", "status": "completed", "is_video": "1",
            "created_at": result.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_full": prompt, "prompt_preview": (prompt or "")[:100],
            "negative_prompt": i2v_sent.get("negativePrompts", ""),
            "seed": str(o.get("seed") or ""),
            "poster_media_id": o.get("poster_media_id", ""),
            "video_duration": str(shared.get("duration") or i2v_sent.get("duration") or ""),
            "model_id": str(i2v_sent.get("model") or ""),
            "width": str(detail.get("width") or ""),
            "height": str(detail.get("height") or ""),
        })
        # Best-effort poster thumbnail: the PixAI still frame, keyed by the video id.
        pm = o.get("poster_media_id")
        if pm:
            purl, _pi = resolve_media(session, pm)
            if purl:
                ptmp = out / "gallery" / "_postertmp"
                ptmp.mkdir(parents=True, exist_ok=True)
                st, pp = download(session, purl, ptmp / str(pm))
                if st in ("ok", "skip") and pp:
                    make_thumbnail(pp, thumb_dir / "{}.jpg".format(vmid))
        rows.append(full)
        saved.append(str(path))

    if rows:
        save_catalog(db_path, rows)
    print("Generated + cataloged {} video(s):".format(len(saved)))
    for s in saved:
        print("  " + s)
    return {"submitted": True, "task_id": task_id, "videos": len(saved)}


def run_upload(args):
    """Upload a local image to PixAI and print its media_id (the reusable primitive
    behind --edit-src file support). Free; spends nothing."""
    session = _make_session(getattr(args, "token", None))
    mid = upload_media(session, args.upload_file)
    print("Uploaded media_id:", mid)
    return {"media_id": mid}


def run_edit_image(args):
    """Instruct-edit an image via PixAI (createGenerationTask with a `chat` block):
    describe the change in --prompt and pass source(s) via --edit-src (a catalog
    media_id OR a local file, uploaded automatically; repeatable for multi-image
    reference). Poll -> download the result image(s) -> catalog as source='api'.
    GUARDED: without --confirm it only PREVIEWS (uploads nothing, spends nothing).
    --task-id recovers an already-created edit for free. Mirrors run_generate."""
    out = Path(args.out)
    existing_task = (getattr(args, "task_id", "") or "").strip()
    srcs = [s for s in (getattr(args, "edit_src", None) or []) if s and str(s).strip()]
    override = getattr(args, "params_json", "") or ""
    prompt = getattr(args, "prompt", "") or ""
    cfg = _edit_config_from_args(args)

    if not existing_task and not srcs and not override:
        raise PixAIError("--edit-image needs --edit-src <media_id|file> (repeatable), "
                         "or --task-id to recover an existing edit.")

    # PREVIEW: no upload, no submit, no credits. Local files shown as placeholders.
    if not existing_task and not getattr(args, "confirm", False):
        print("=== PixAI createGenerationTask -- EDIT (PREVIEW, no credits spent) ===")
        if override:
            params = json.loads(override)
        else:
            preview_ids = [("<upload:{}>".format(s) if _is_local_source(s) else s)
                           for s in srcs] or ["<source>"]
            params = build_chat_edit_parameters(
                prompt, preview_ids, model_id=cfg["model_id"],
                resolution=cfg["resolution"], aspect_ratio=cfg["aspect_ratio"],
                quality=cfg["quality"], kaisuuken_id=cfg["kaisuuken_id"])
        print(json.dumps({"parameters": params}, indent=2))
        print("\nThis would SPEND PixAI credits (unless a free Edit card applies). "
              "Re-run with --confirm to submit.")
        return {"submitted": False}

    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "catalog.db"
    init_db(db_path)
    session = _make_session(getattr(args, "token", None))
    thumb_dir = out / "gallery" / "thumbs"
    from pixai_gallery import make_thumbnail

    params = {}
    if existing_task:
        task_id = existing_task
        print("Fetching existing edit task (no credits):", task_id)
    else:
        if override:
            params = json.loads(override)
        else:
            media_ids = []
            for s in srcs:
                if _is_local_source(s):
                    print("Uploading local image:", s)
                    media_ids.append(upload_media(session, s))
                else:
                    media_ids.append(str(s))
            params = build_chat_edit_parameters(
                prompt, media_ids, model_id=cfg["model_id"],
                resolution=cfg["resolution"], aspect_ratio=cfg["aspect_ratio"],
                quality=cfg["quality"], kaisuuken_id=cfg["kaisuuken_id"])
        print("Submitting EDIT task (spends credits unless a free card applies)...")
        created = gql_adhoc(session, _GEN_MUTATION, {"parameters": params})
        task_id = (created.get("createGenerationTask") or {}).get("id")
        if not task_id:
            raise PixAIError("no task id returned: " + json.dumps(created)[:300])
        print("  task id:", task_id)
        deadline = time.time() + getattr(args, "poll_timeout", 300)
        paid_credit = None
        while time.time() < deadline:
            task = (gql_adhoc(session, _GEN_STATUS, {"id": task_id}) or {}).get("task") or {}
            status = str(task.get("status", "")).lower()
            if task.get("paidCredit") is not None:
                paid_credit = task.get("paidCredit")
            vlog("edit poll: {}".format(status or "(unknown)"))
            if status in ("completed", "succeeded", "success", "done"):
                if paid_credit is not None:
                    print("  actual cost: {:,} credits".format(int(paid_credit)))
                break
            if status in ("failed", "error", "cancelled", "canceled"):
                raise PixAIError("edit ended with status: " + status)
            time.sleep(3)
        else:
            raise PixAIError("timed out after {}s (task {})".format(
                getattr(args, "poll_timeout", 300), task_id))

    result = task_detail_gql(session, task_id) or {}
    outputs = result.get("outputs") or {}
    mids = []
    if outputs.get("mediaId"):
        mids.append(str(outputs["mediaId"]))
    for m in outputs.get("batchMediaIds") or []:
        mids.append(str(m))
    mids = list(dict.fromkeys(mids))
    if not mids:
        raise PixAIError("edit task completed but no media ids found")

    fm = extract_full_meta(result)
    chat = (params.get("chat") or {}) if isinstance(params, dict) else {}
    prompt_used = fm.get("prompt_full") or prompt or chat.get("prompts", "")
    img_dir = out / "images"
    rows, saved = [], []
    for mid in mids:
        url, info = resolve_media(session, mid)
        if not url:
            print("  no url for media", mid)
            continue
        stem = img_dir / build_stem_name(prompt_used, task_id, mid,
                                         getattr(args, "name_length", 60),
                                         getattr(args, "name_sep", "_"))
        status, path = download(session, url, stem)
        if status not in ("ok", "skip") or not path:
            continue
        full = {f: "" for f in CATALOG_FIELDS}
        full.update({
            "task_id": str(task_id), "media_id": mid,
            "filename": str(path.relative_to(out)).replace("\\", "/"),
            "url": url, "source": "api", "status": "completed",
            "created_at": result.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_full": prompt_used, "prompt_preview": (prompt_used or "")[:100],
            "model_id": str(chat.get("modelId") or fm.get("model_id") or ""),
            "model_name": fm.get("model_name", "") or "Edit",
            "width": str((info or {}).get("width") or ""),
            "height": str((info or {}).get("height") or ""),
        })
        rows.append(full)
        make_thumbnail(path, thumb_dir / "{}.jpg".format(mid))
        saved.append(str(path))

    if rows:
        save_catalog(db_path, rows)
    print("Edited + cataloged {} image(s):".format(len(saved)))
    for s in saved:
        print("  " + s)
    return {"submitted": True, "task_id": task_id, "images": len(saved)}


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
    workers = max(1, getattr(args, "workers", 1) or 1)
    print("Resolving {} distinct model id(s) across {} rows{}...".format(
        len(to_resolve), sum(len(v) for v in to_resolve.values()),
        " ({} workers)".format(workers) if workers > 1 else ""))
    _prog = getattr(args, "progress", None)
    fixed = relabeled = unresolved = 0
    for vid, name in _parallel_map(sorted(to_resolve),
                                   lambda v: model_name_gql(session, v),
                                   workers, _prog, delay=getattr(args, "delay", 0.4)):
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

    if fixed or relabeled:
        save_catalog(db_path, rows)
    print("\nFixed {} row(s) across {} model(s); {} id(s) unresolved{}.".format(
        fixed, len(to_resolve) - unresolved, unresolved,
        " (relabeled {} rows to '{}')".format(relabeled, removed_label) if relabeled else ""))
    return {"fixed": fixed, "relabeled": relabeled, "models": len(to_resolve) - unresolved,
            "unresolved": unresolved}


# Read-only account dashboard. Ad-hoc query (no persisted hash) -- the selection
# below mirrors what the site's getMyQuota + getMyMembership return. READ ONLY:
# this only reports your credit balance / plan. It never moves money. Buying
# credits or changing your subscription is deliberately NOT implemented -- do that
# in the browser.
_ACCOUNT_QUERY = """
query {
  me {
    id
    quotaAmount
    membership { membershipId tier privilege }
    subscription { planId provider interval status startAt endAt cancelAtPeriodEnd }
  }
}
"""


def account_info(session):
    """Fetch credits + membership/subscription via ad-hoc GraphQL. Returns the
    `me` dict ({} on failure). Read-only."""
    try:
        return (gql_adhoc(session, _ACCOUNT_QUERY) or {}).get("me") or {}
    except PixAIError:
        return {}


def run_account_info(args):
    """Print a read-only account dashboard: credit balance, membership, and
    subscription status. Never initiates payment -- buy credits in the browser."""
    session = _make_session(getattr(args, "token", None))
    me = account_info(session)
    if not me:
        print("Could not read account info (check API key / connection).")
        return {}
    mem = me.get("membership") or {}
    sub = me.get("subscription") or {}
    priv = mem.get("privilege") or {}
    try:
        credits = "{:,}".format(int(me.get("quotaAmount") or 0))
    except (TypeError, ValueError):
        credits = str(me.get("quotaAmount"))
    print("Account ID       : {}".format(me.get("id") or USER_ID))
    print("Credits (balance): {}".format(credits))
    if mem:
        print("Membership       : {} (tier {})".format(
            mem.get("membershipId", "-"), mem.get("tier", "-")))
        if priv.get("dailyClaimAdded"):
            print("Daily free claim : {:,}".format(int(priv["dailyClaimAdded"])))
        if priv.get("professionalMode"):
            print("Professional mode: on")
    if sub:
        renew = "cancels at period end" if sub.get("cancelAtPeriodEnd") else "renews"
        print("Subscription     : {} {} via {} ({}); {} {}".format(
            sub.get("planId", "-"), (sub.get("interval") or "").lower(),
            sub.get("provider", "-"), sub.get("status", "-"),
            renew, (sub.get("endAt") or "")[:10]))
    print("\n(Read-only. To buy credits or change your plan, use the browser.)")
    return {"quota": me.get("quotaAmount"), "membership": mem.get("membershipId")}


# --- Free "cards" (kaisuuken / 回数券) -------------------------------------------
# PixAI grants free-generation tickets ("kaisuuken") via membership/events. When a
# generation's params match an available ticket, the client attaches a `kaisuukenId`
# to the submit and it's free instead of charging credits. We keep this READ + explicit-
# id only: --cards shows balances + ids; pass a specific --kaisuuken-id to spend one.
# We NEVER auto-consume a card. FIELD NAMES BELOW ARE RE-INFERRED (see
# private/GENERATOR_SURFACE.md) and want one live confirmation; list_kaisuukens fails soft.
_KAISUUKEN_QUERY = """
query {
  me {
    id
    kaisuukens {
      id
      categoryCode
      taskType
      total
      remaining
      consumeAmount
      expiresAt
      status
    }
  }
}
"""


def _normalize_kaisuuken(raw):
    """Tolerantly normalize one kaisuuken dict (RE-inferred field names vary). Returns
    {id, category, task_type, total, remaining, expires, status}."""
    raw = raw or {}

    def first(*keys):
        for k in keys:
            v = raw.get(k)
            if v not in (None, ""):
                return v
        return None

    total = first("total", "amount", "count", "freeAmount", "quota")
    remaining = first("remaining", "left", "balance", "remainingAmount")
    used = first("used", "usedAmount", "consumed", "consumeAmount")
    if remaining is None and total is not None and used is not None:
        try:
            remaining = int(total) - int(used)
        except (TypeError, ValueError):
            remaining = None
    return {
        "id": first("id", "kaisuukenId"),
        "category": first("categoryCode", "category", "code"),
        "task_type": first("taskType", "type", "kind"),
        "total": total,
        "remaining": remaining,
        "expires": first("expiresAt", "expiresInDays", "endAt"),
        "status": first("status", "state"),
    }


def list_kaisuukens(session):
    """Read the account's free-generation tickets (kaisuuken). Read-only; fails soft
    (returns []) if the schema differs -- fields are RE-inferred and may need a live
    re-capture. Never spends anything."""
    try:
        me = (gql_adhoc(session, _KAISUUKEN_QUERY) or {}).get("me") or {}
    except PixAIError:
        return []
    return [_normalize_kaisuuken(k) for k in (me.get("kaisuukens") or [])]


def run_cards(args):
    """Print the account's free-generation cards (kaisuuken) + their ids, so you can
    pass one to --kaisuuken-id on a generate/edit/video run. Read-only; spends nothing."""
    session = _make_session(getattr(args, "token", None))
    cards = list_kaisuukens(session)
    if not cards:
        print("No free cards found (or the kaisuuken schema needs a live re-capture -- "
              "see private notes). Read-only; nothing was spent.")
        return {"cards": 0}
    print("Free generation cards (kaisuuken):")
    for c in cards:
        rem, tot = c.get("remaining"), c.get("total")
        count = "{}/{}".format("?" if rem is None else rem, "?" if tot is None else tot)
        exp = "  exp {}".format(str(c["expires"])[:10]) if c.get("expires") else ""
        print("  {:>7}  {:<14} id={} {}{}".format(
            count, (c.get("category") or c.get("task_type") or "card"),
            c.get("id") or "-", (c.get("status") or ""), exp))
    print("\nSpend one on a run:  --kaisuuken-id <id> --confirm")
    return {"cards": len(cards)}


def run_reconcile_deleted(args):
    """Find catalog rows whose PixAI task no longer exists in your live feed -- i.e.
    generations you deleted on the website -- and flag them (deleted_remote='1') so
    the gallery can surface them for a local prune. Closes the cloud->local delete
    drift. Advisory: re-running refreshes the flags. Skips imports (no task) and
    very-recent rows (a fresh generation may not have propagated to the feed yet)."""
    out = Path(args.out)
    db_path = _ensure_db(out)
    session = _make_session(getattr(args, "token", None))
    _prog = getattr(args, "progress", None)

    print("Scanning your live PixAI feed for existing task ids...")
    live, before, page = set(), None, 0
    while True:
        conn = find_connection(gql(session, page_variables(
            getattr(args, "page_size", 250) or 250, before)))
        if not conn:
            break
        edges = conn.get("edges") or []
        if not edges:
            break
        for e in edges:
            tid = (e.get("node") or {}).get("id")
            if tid:
                live.add(str(tid))
        page += 1
        vlog("reconcile: page {}, {} live tasks so far".format(page, len(live)))
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasPreviousPage"):
            break
        before = pi.get("startCursor")
    print("Live tasks in your feed: {:,}".format(len(live)))
    if not live:
        raise PixAIError("Live feed returned no tasks -- aborting so we don't flag "
                         "your whole catalog by mistake.")

    grace = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 2 * 86400))
    rows = load_catalog(db_path)
    flagged = cleared = 0
    for r in rows:
        tid = (r.get("task_id") or "").strip()
        gone = (tid and tid not in live and (r.get("source") or "") != "local"
                and (r.get("created_at") or "") < grace)
        was = r.get("deleted_remote") == "1"
        if gone and not was:
            r["deleted_remote"] = "1"; flagged += 1
        elif not gone and was:
            r["deleted_remote"] = ""; cleared += 1
        else:
            r["deleted_remote"] = "1" if gone else ""
    save_catalog(db_path, rows)
    print("Flagged {:,} row(s) as deleted-on-PixAI; cleared {:,} stale flag(s).".format(
        flagged, cleared))
    print("Review in the gallery: Source -> 'Deleted on PixAI', then bulk Delete (local).")
    return {"live": len(live), "flagged": flagged, "cleared": cleared}


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


def _parallel_map(items, work_fn, workers=1, progress=None, delay=0.0):
    """Run work_fn(item) over items, yielding (item, result) as each finishes.

    workers<=1 runs serially (in order, sleeping `delay` between items to stay
    polite); higher uses a bounded thread pool for latency-bound network calls
    (no delay -- concurrency itself paces). progress(done, total, 0) is called on
    THIS thread, so the caller may safely mutate shared state in the yield body.
    Exceptions in a worker yield a None result rather than crashing the run."""
    items = list(items)
    total = len(items)
    if workers <= 1:
        for i, it in enumerate(items):
            yield it, work_fn(it)
            if progress:
                progress(i + 1, total, 0)
            if delay:
                time.sleep(delay)
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work_fn, it): it for it in items}
        for fut in as_completed(futs):
            it = futs[fut]
            done += 1
            try:
                res = fut.result()
            except Exception:
                res = None
            yield it, res
            if progress:
                progress(done, total, 0)


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

    workers = max(1, getattr(args, "workers", 1) or 1)
    if workers > 1:
        print("Resolving with {} parallel workers.".format(workers))
    updated = failed = 0
    _prog = getattr(args, "progress", None)
    for row, res in _parallel_map(to_fill, lambda r: resolve_media(session, r["media_id"]),
                                  workers, _prog, delay=args.delay):
        url, info = res if res else (None, {})
        if url:
            row["url"] = url
            row["width"] = str(info.get("width") or "")
            row["height"] = str(info.get("height") or "")
            updated += 1
        else:
            failed += 1
        if not _prog and workers <= 1:
            sys.stdout.write("\r  {:,}/{:,}  updated {:,}  failed {:,}  ".format(
                updated + failed, len(to_fill), updated, failed))
            sys.stdout.flush()

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

    # Fetch and cache full meta per task_id (parallelizable -- each task is an
    # independent getTaskById round-trip).
    workers = max(1, getattr(args, "workers", 1) or 1)
    if workers > 1:
        print("Fetching with {} parallel workers.".format(workers))

    def _fetch_task(tid):
        task_data = task_detail_gql(session, tid)
        fm = extract_full_meta(task_data)
        if fm.get("model_id"):
            fm["model_name"] = model_name_gql(session, fm["model_id"])
        fm["loras"] = resolve_loras(session, task_data)
        media_obj = (task_data or {}).get("media") or {}
        if media_obj:
            by_v = {str(u.get("variant", "")).upper(): u["url"]
                    for u in (media_obj.get("urls") or []) if isinstance(u, dict) and u.get("url")}
            for pref in ("PUBLIC", "ORIGINAL", "ORIG", "FULL", "THUMBNAIL"):
                if pref in by_v:
                    fm["_media_url"] = by_v[pref]
                    break
            fm["_media_width"] = str(media_obj.get("width") or "")
            fm["_media_height"] = str(media_obj.get("height") or "")
        return fm

    task_cache = {}  # task_id -> full meta dict
    fetched = failed = 0
    _prog = getattr(args, "progress", None)
    for tid, fm in _parallel_map(task_ids, _fetch_task, workers, _prog, delay=args.delay):
        fm = fm or {}
        task_cache[tid] = fm
        if fm.get("prompt_full"):
            fetched += 1
        else:
            failed += 1
        if not _prog and workers <= 1:
            sys.stdout.write("\r  Tasks {:,}/{:,}  fetched {:,}  failed {:,}  ".format(
                fetched + failed, len(task_ids), fetched, failed))
            sys.stdout.flush()

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
        _t_scan = time.monotonic()
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
        vlog("startup disk scan: {} image files ({}) indexed in {:.2f}s".format(
            already_done, _format_size(disk_bytes), time.monotonic() - _t_scan))
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
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print timestamped diagnostics (per-page fetch, per-image "
                         "resolve/download timing, disk-scan time) so you can see what a "
                         "long-running operation is doing")
    ap.add_argument("--token",
                    help="Bearer token for PixAI API auth (overrides PIXAI_TOKEN env var "
                         "and token.txt)")
    ap.add_argument("--delete-task", nargs="+", metavar="TASK_ID", default=None,
                    help="DELETE the given generation task id(s) from your PixAI account "
                         "(irreversible). Dry-run unless --apply is also given; then asks "
                         "for typed confirmation unless --yes. Local backups are untouched. "
                         "Requires DELETE_TASK_HASH in config.json.")
    ap.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation for --delete-task --apply "
                         "(use with care; deletion cannot be undone)")
    ap.add_argument("--out", default="pixai_backup",
                    help="output folder for images and catalog (default: pixai_backup)")
    ap.add_argument("--page-size", type=int, default=250,
                    help="tasks per API page (default 250; fewer round-trips. Keep <~8000)")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel download workers (default 4). 1 = serial/polite. "
                         "Higher saturates bandwidth on bulk first-time pulls; ignored for "
                         "--collect-only.")
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
                    help="normalize the WHOLE backup into YYYY-MM/ month folders with "
                         "descriptive filenames (no batch subfolders); writes a reversible "
                         "move-manifest. Idempotent + dry-runnable. Then exit")
    ap.add_argument("--organize-adv", action="store_true",
                    help="alias for --organize (kept for back-compat)")
    ap.add_argument("--undo-organize", action="store_true",
                    help="revert the last --organize-adv run using organize_manifest.csv "
                         "(move files back to their old paths), then exit")
    ap.add_argument("--embed-metadata", action="store_true",
                    help="with --organize-adv, embed prompt/IDs/date into PNG/JPEG files "
                         "(off by default; useful when pulling images into other apps)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --organize / --organize-adv / --undo-organize, show the "
                         "plan without moving anything")
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
    ap.add_argument("--sync-videos", action="store_true",
                    help="back up your image-to-video generations: find i2v tasks, download "
                         "each mp4 into videos/, and catalog them (is_video), then exit")
    ap.add_argument("--account", action="store_true",
                    help="show a read-only account dashboard (credit balance, membership, "
                         "subscription) and exit. Never moves money")
    ap.add_argument("--cards", action="store_true",
                    help="show your free-generation cards (kaisuuken) + their ids, then exit. "
                         "Read-only; pass an id to a run with --kaisuuken-id")
    ap.add_argument("--reconcile-deleted", action="store_true",
                    help="flag catalog rows whose PixAI task is gone from your live feed "
                         "(deleted on the website) so the gallery can surface them for a "
                         "local prune, then exit")
    ap.add_argument("--import-local", nargs="?", const="", default=None, metavar="DIR",
                    help="catalog non-PixAI media (source='local') so it shows in the gallery. "
                         "No DIR = scan the backup folder for files you dropped in; a DIR "
                         "outside the backup is copied in. Then exit")
    # --- Generation (createGenerationTask) -------------------------------------
    gen = ap.add_argument_group("generation (--generate)")
    gen.add_argument("--generate", action="store_true",
                     help="create images via PixAI and catalog them (source='api'). "
                          "Preview-only unless --confirm (spends credits)")
    gen.add_argument("--prompt", default="", help="positive prompt for --generate")
    gen.add_argument("--negative", default="", help="negative prompt for --generate")
    gen.add_argument("--model", default="", help="modelId for --generate (default: Tsubaki.2)")
    gen.add_argument("--width", type=int, default=512)
    gen.add_argument("--height", type=int, default=512)
    gen.add_argument("--steps", type=int, default=25)
    gen.add_argument("--cfg", type=float, default=7.0)
    gen.add_argument("--batch-size", dest="count", type=int, default=1,
                     help="number of images per --generate run (batch size)")
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("--priority", type=int, default=500,
                     help="generation priority: 500 = standard (default, cheaper), "
                          "1000 = high (faster, costs more credits)")
    gen.add_argument("--high-priority", dest="priority", action="store_const", const=1000,
                     help="shortcut for --priority 1000 (faster, more credits)")
    gen.add_argument("--mode", default="auto",
                     choices=["auto", "lite", "standard", "pro", "ultra"],
                     help="quality mode (inferenceProfile). auto (default) lets PixAI pick the "
                          "model's default -- always safe. lite/standard suit SD_V1 models; "
                          "pro/ultra are for newer model types (an unsupported mode is rejected)")
    gen.add_argument("--no-prompt-helper", dest="prompt_helper", action="store_false",
                     help="disable PixAI's prompt-helper (use your prompt more literally; "
                          "helps when auto-enhancement mangles a carefully-built prompt)")
    gen.set_defaults(prompt_helper=True)
    gen.add_argument("--lora", action="append", metavar="VERSIONID:WEIGHT",
                     help="add a LoRA by its version id and weight, e.g. "
                          "--lora 1686550608832816741:0.7 (repeatable). Find version ids "
                          "with --list-models")
    gen.add_argument("--task-id", default="",
                     help="with --generate, fetch + catalog an ALREADY-created task by id "
                          "(no new credits). Recovers a stranded generation that --update "
                          "can't see, since generated tasks don't enter the listing feed")
    gen.add_argument("--params-json", default="", help="raw parameters object (overrides the above)")
    gen.add_argument("--poll-timeout", type=int, default=300)
    gen.add_argument("--confirm", action="store_true",
                     help="REQUIRED for --generate/--generate-video to actually submit (spends credits)")
    # --- image-to-video generation (shares --prompt/--negative/--model/--confirm/--task-id) ---
    gen.add_argument("--generate-video", dest="generate_video", action="store_true",
                     help="create an image-to-video clip via PixAI from a source image "
                          "(--image). Preview-only unless --confirm. Video is EXPENSIVE "
                          "(~27,500 credits for a 5s V4.0 clip)")
    gen.add_argument("--image", default="", help="source image media_id to animate (first frame)")
    gen.add_argument("--tail", default="", help="optional last-frame image media_id "
                     "(first/last-frame interpolation)")
    gen.add_argument("--duration", type=int, default=5, help="video length in seconds (e.g. 5/10/15)")
    gen.add_argument("--video-model", dest="video_model", default="",
                     help="video model (default v4.0.1); overrides --model for --generate-video")
    gen.add_argument("--video-mode", dest="vmode", default="professional",
                     choices=["basic", "professional"], help="video quality tier")
    gen.add_argument("--audio", action="store_true", help="generate audio with the video")
    gen.add_argument("--audio-language", dest="audio_language", default="english")
    gen.add_argument("--video-prompt-helper", dest="video_prompt_helper", action="store_true",
                     help="enable PixAI's prompt-helper for video (off by default)")
    # --- instruct editing + media upload (the "Edit this image" surface) ---
    gen.add_argument("--edit-image", dest="edit_image", action="store_true",
                     help="instruct-edit an image via PixAI: describe the change in --prompt "
                          "and pass source(s) with --edit-src (a catalog media_id OR a local "
                          "file, uploaded automatically). Preview-only unless --confirm")
    gen.add_argument("--edit-src", dest="edit_src", action="append", metavar="MEDIA_ID|FILE",
                     help="source image for --edit-image: a media_id or a local image file "
                          "(local files upload automatically). Repeatable for multi-image reference")
    gen.add_argument("--edit-model", dest="edit_model", default="",
                     help="edit model id (default PixAI Edit Pro {})".format(EDIT_PRO_MODEL_ID))
    gen.add_argument("--edit-resolution", dest="edit_resolution", default="1K",
                     help="edit output resolution (default 1K; e.g. 1K/2K)")
    gen.add_argument("--edit-aspect", dest="edit_aspect", default="3:4",
                     help="edit output aspect ratio (default 3:4)")
    gen.add_argument("--edit-quality", dest="edit_quality", default="medium",
                     help="edit quality tier (default medium)")
    gen.add_argument("--upload", dest="upload_file", default="", metavar="FILE",
                     help="upload a local image to PixAI, print its media_id, then exit "
                          "(the reusable primitive behind --edit-src file support). Free")
    gen.add_argument("--kaisuuken-id", dest="kaisuuken_id", default="", metavar="ID",
                     help="spend a specific free card (kaisuuken) id on this generate/edit/"
                          "video run instead of credits. Get ids from --cards")
    gen.add_argument("--list-models", nargs="?", const="", default=None, metavar="KEYWORD",
                     help="search PixAI generation models by keyword and print their "
                          "generatable version ids (use as --model), then exit")
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
    set_verbose(getattr(args, "verbose", False))

    if args.probe and args.count:
        print("Note: --probe exits before --count runs. Run them separately:\n"
              "  python pixai_gallery_backup.py --count\n"
              "Continuing with --probe only.\n")

    out = Path(args.out)
    img_dir = out / "images"
    db_path  = out / "catalog.db"
    csv_path = out / "catalog.csv"

    try:
        if getattr(args, "delete_task", None):
            run_delete_tasks(args)
            return
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
        if args.sync_videos:
            run_sync_videos(args)
            return
        if args.account:
            run_account_info(args)
            return
        if getattr(args, "cards", False):
            run_cards(args)
            return
        if args.reconcile_deleted:
            run_reconcile_deleted(args)
            return
        if args.import_local is not None:
            run_import_local(args)
            return
        if args.list_models is not None:
            run_list_models(args)
            return
        if args.generate:
            run_generate(args)
            return
        if getattr(args, "generate_video", False):
            run_generate_video(args)
            return
        if getattr(args, "upload_file", ""):
            run_upload(args)
            return
        if getattr(args, "edit_image", False):
            run_edit_image(args)
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
        if args.undo_organize:
            cmd_undo_organize(args, out)
            return
        if args.organize or args.organize_adv:   # --organize-adv: back-compat alias
            cmd_organize(args, out, img_dir, db_path)
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
