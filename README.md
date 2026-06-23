# PixAI Gallery Backup

> **Language:** Python 3.8+ · **Platform:** Windows / macOS / Linux · **Author:** Nelnamara

A command-line tool (with optional desktop GUI) that backs up **your own** PixAI.art generated images at full resolution. PixAI's gallery UI only shows 20 images at a time; this talks to the same API the browser uses, pages through your entire generation history, downloads every image, and keeps a fully searchable catalog of prompts, seeds, model names, dimensions, and dates.

PixAI's terms grant users copyright of their own generations. This tool is rate-paced to be polite to their servers.

---

## Features

- **Full-resolution downloads** — bypasses the 20-image gallery limit; fetches every generation at the original size
- **Stable API-key auth** — set `PIXAI_API_KEY` (an official key, lifetime up to ~2 years) and it authenticates every call; no expiring browser login to recapture
- **Fast parallel downloads** — `--workers N` (default 4) fetches images concurrently; the same flag also parallelizes the batch jobs (backfill, fix-model-names, sync videos, convert, thumbnails)
- **Instant resume** — an in-memory media-ID index makes re-runs skip already-saved images with no per-item disk scan (resume stays fast no matter how large the library grows)
- **Incremental update mode** — `--update` stops paging once it reaches your already-downloaded history, so routine "grab what's new" runs finish in seconds instead of re-walking everything
- **Persistent catalog** — `catalog.db` (SQLite) is a deduplicated, indexed database keyed by `media_id`; prior-session rows are never lost across interrupted or multi-session downloads; auto-migrates from `catalog.csv` if upgrading
- **Full generation metadata** — `--full-meta` captures the complete prompt, seed, steps, sampler, CFG scale, human-readable model name, and **LoRAs**; `--backfill-full-meta` fills existing catalog rows retroactively
- **Published-artwork sync** — `--sync-artworks` pulls your published pieces' titles, tags/contest labels, NSFW flag, and like/comment/aesthetic data into the catalog; `--with-videos` backs up animated-artwork video files too
- **Duplicate audit & dedup** — `--audit` scans the whole backup folder for duplicate images (same `media_id` across folders, plus byte-identical copies); `--dedup` quarantines the redundant copies (keeping the most-organized one) and `--verify-dupes` proves the quarantine is safe before you delete it
- **Local web gallery** — browse, filter, rate, and delete from a browser: wildcard prompt search; searchable model/batch, tag/contest, LoRA, min-rating, and published-only filters; year/month date pickers; aesthetic/likes/resolution sorts; a lightbox (swipe + slideshow); cross-page selection with **Download ZIP**; saved filter presets; privacy blur; mobile/tablet layout and PWA
- **Edit prompts in the gallery** — fix or annotate a single image's prompt inline on its detail page, or select many and **Find/Replace** a substring across all of them; writes straight to `catalog.db`
- **Collection Health dashboard** — `/health` page: storage used, full-meta %, duplicates, missing files, total likes, images-by-month, top models, top LoRAs, top tags, and a prompt word-cloud; plus a **`/duplicates`** review page that shows cross-folder duplicate copies side-by-side before you dedup
- **Format conversion** — convert WebP to PNG or JPEG on download, or batch-convert existing files
- **Organize mode** — sorts files into `batches/` and `YYYY-MM/` folders; embeds metadata into PNG/JPEG files
- **Rate limiting** — configurable delay between requests (default 0.4 s)
- **SSL safety** — HTTPS verification always on; `truststore` support for corporate/antivirus environments

---

## GUI

A PySide6 desktop GUI (`pixai_gui.py`) wraps the full workflow in a tabbed window with a dark Catppuccin Mocha theme, background threads, and live log output.

| Tab | What it does |
|---|---|
| **Download** | Output folder, page size, **workers**, **update mode**, organize mode, conversion, collect-only, and full-meta; Start / Stop. (Auth comes from `PIXAI_API_KEY` in `config.json`; an optional Token field remains for the legacy browser-token path) |
| **Organize** | Post-download rename (`--organize`) or full folder sort (`--organize-adv`); dry-run preview |
| **Convert** | Batch-convert existing `.webp` files to PNG or JPEG in place (parallel) |
| **Utilities** | Probe, Count, Catalog Stats, Backfill url/width/height, Backfill Full Meta (+ incl. LoRAs), Export CSV, **Sync Artworks** (+ incl. videos), **Fix Model Names**, **Account Info**, **Audit Duplicates / Dedup / Verify Quarantine**; configurable API delay and **Workers** |
| **Gallery** | Launch / stop the local gallery server; configurable port; LAN mode; **HTTPS** option; auto-builds thumbnails on start (parallel) |

Settings are saved to `pixai_gui_settings.json` next to the script (git-ignored).

**Run the GUI:**
```
pip install PySide6
python pixai_gui.py
```

---

## Requirements

| Package | Required | Notes |
|---|:---:|---|
| `requests` | ✅ | All network operations |
| `truststore` | ❌ | Recommended — fixes HTTPS cert errors from corporate proxies or antivirus (Python 3.10+) |
| `pillow` | ❌ | Needed for `--convert`, `--convert-existing`, thumbnail generation, and metadata embedding |
| `PySide6` | ❌ | Desktop GUI only (`pixai_gui.py`) |
| `flask` | ❌ | Local web gallery (`pixai_gallery.py`) |
| `cryptography` | ❌ | Only for the gallery's `--https` mode (self-signed cert for mobile PWA) |
| `pytest` + `pytest-mock` | ❌ | Development / testing only |

Install all at once:

```
pip install requests truststore pillow PySide6 flask
```

---

## Installation

1. Install Python 3.8 or newer — check with `python --version`
2. Install dependencies (above)
3. Put `pixai_gallery_backup.py`, `pixai_gui.py`, `pixai_gallery.py`, and `config.example.json` in a folder of their own
4. Copy `config.example.json` to `config.json` and fill in your values (see [Configuration](#configuration) below)
5. All output is written to `pixai_backup/` next to the scripts

> **Tip:** Use a dedicated folder — the scripts create their output directory alongside themselves.

---

## Configuration

`config.json` lives next to the scripts and is git-ignored. Copy `config.example.json` to `config.json` and fill in three values:

```json
"PIXAI_API_KEY": "your-api-key",
"USER_ID": "your-numeric-id",
"PERSISTED_QUERY_HASH": "captured-hash"
```

### Step 1 — Get an API key (one minute, no expiry headaches)

Generate an official API key at [platform.pixai.art](https://platform.pixai.art) — you can set its lifetime **up to ~2 years**. Paste it into `config.json` as `PIXAI_API_KEY`.

That key is sent as the Bearer credential for **every** call — listing, media resolution, full-meta, model names. There is **no expiring browser token to capture or re-capture**, and you never touch the `Authorization` header by hand. This is the only credential that ever needs refreshing, and it lasts up to two years.

### Step 2 — Capture `USER_ID` + `PERSISTED_QUERY_HASH` (once)

PixAI has no public endpoint for listing *your own* generation history, so the listing replays the same persisted GraphQL query the website uses. That query needs two values, captured once from the browser (they only change if PixAI overhauls their frontend):

1. Log in to [pixai.art](https://pixai.art) and open your gallery or profile page
2. Press **F12** → **Network** tab → type `graphql` in the filter box
3. Scroll the page slightly so requests fire, then click a `listUserTaskSummaries` row
4. Open the **Payload** tab (Chrome) / **Request** tab (Firefox)

Copy two values into `config.json` (your numeric `USER_ID` isn't in the address bar — PixAI uses `@username` in URLs — which is why this step is needed):

- `userId` inside `variables` → `USER_ID`
- `sha256Hash` inside `extensions.persistedQuery` → `PERSISTED_QUERY_HASH`

That's the entire setup. (For `--full-meta` you'll capture one more hash — see [Full Meta](#full-meta-full-prompt-seed-model).)

> **`config.json` is git-ignored** and will never be committed.

> **No API key?** You can instead run the legacy browser-token path: leave `PIXAI_API_KEY` blank, add a `U3T` value (also on the Payload tab), and supply the short-lived `Authorization: Bearer` token via `token.txt`, the `PIXAI_TOKEN` env var, or `--token`. This token expires every few hours and must be re-captured — the API key exists specifically to avoid that, so it's the recommended path.

---

## Usage

### First Run

```
python pixai_gallery_backup.py --probe        # confirm connection
python pixai_gallery_backup.py --count        # how many images you have
python pixai_gallery_backup.py --max 40       # small test download
python pixai_gallery_backup.py                # download everything (4 workers, 250/page)
python pixai_gallery_backup.py --full-meta    # download + capture full prompt/seed/model
```

### Fast Downloads & Incremental Updates

The download path is parallel and incremental by default. For a routine "grab
what's new" run, use `--update` — it pages newest-first and stops as soon as it
reaches images you already have, instead of re-walking your whole history:

```
python pixai_gallery_backup.py --update                       # fast follow-up run
python pixai_gallery_backup.py --update --workers 8           # push concurrency higher
python pixai_gallery_backup.py --workers 8 --page-size 500    # fast full backfill
```

- `--workers N` (default 4) controls how many images download at once. 1 = serial/polite; 6–8 saturates most connections. It composes with every other flag, including `--update`.
- `--update` stops after `--update-grace` consecutive pages that are entirely on disk (default 2). Use a **plain run (no `--update`)** to backfill items missing from the *middle* of your history (e.g. after deleting files) — `--update` only reaches the newest items.
- The progress total is taken from your catalog (instant). Pass `--accurate-count` to force the old full-history API count.

### Organizing Downloads

**Post-download:**
```
python pixai_gallery_backup.py --organize --dry-run        # preview rename plan
python pixai_gallery_backup.py --organize                  # rename to prompt_taskid_mediaid
python pixai_gallery_backup.py --organize-adv --dry-run    # preview folder sort
python pixai_gallery_backup.py --organize-adv --convert png  # sort into folders + convert
```

**Live (sort as files download):**
```
python pixai_gallery_backup.py --organize-adv-live --convert png
```

### Local Gallery

```
python pixai_gallery.py --out pixai_backup            # launch at http://127.0.0.1:5000
python pixai_gallery.py --out pixai_backup --port 5757
python pixai_gallery.py --out pixai_backup --host 0.0.0.0 --https  # LAN + self-signed HTTPS (mobile PWA)
python pixai_gallery.py --out pixai_backup --rebuild-thumbs        # regenerate all thumbnails
```

Gallery flags: `--port` (default 5000), `--host` (use `0.0.0.0` for LAN), `--https` (self-signed cert for mobile PWA install; needs `pip install cryptography`), `--rebuild-thumbs` (force-regenerate thumbnails). The gallery needs no API key — it reads only `catalog.db` and local files.

Or use the **Gallery tab** in the GUI to launch and stop the server with one click.

The gallery filter bar supports wildcard prompt search (`night*`, `a?c`, multiple words ANDed), searchable Model/Batch fields, Year/Month date pickers, a Min-rating filter, a per-page selector, and a thumbnail-size slider. The header links to a **Collection Health** dashboard (`/health`) showing storage used, full-meta coverage, duplicate count, missing files, images-by-month, and top models, plus a **`/duplicates`** review page.

### Finding & Removing Duplicates

If your `images/` folder has grown large or you suspect duplicate copies across
folders, audit first (read-only), then dedup, then verify before reclaiming space:

```
python pixai_gallery_backup.py --audit                 # read-only report -> audit_report.csv
python pixai_gallery_backup.py --dedup                 # dry-run plan (nothing changes)
python pixai_gallery_backup.py --dedup --apply         # quarantine redundant copies to _duplicates/
python pixai_gallery_backup.py --verify-dupes          # confirm the quarantine is safe to delete
```

- **`--audit`** finds two kinds of duplicates: the same `media_id` living in more than one folder (Class A), and byte-identical files saved under different IDs (Class B, via size-bucketed hashing). It's filesystem-truth — independent of `catalog.db`.
- **`--dedup`** keeps the most-organized copy (`batches/` > `YYYY-MM/` > `images/`) and moves the rest to `_duplicates/` (reversible). Add `--dedup-delete` to delete instead of quarantine, or `--no-content` to skip the slower Class-B hashing. It reconciles `catalog.db` afterward and auto-runs a verify pass.
- **`--verify-dupes`** confirms every quarantined file is byte- or pixel-identical to a surviving copy before you delete `_duplicates/`. `--restore-orphans` moves back anything that turns out to have no surviving copy.

The same three actions are available as buttons in the GUI **Utilities** tab.

### Modes

| Flag | What it does |
|---|---|
| *(none)* | Download full history into `images/`, named `prompt_taskid_mediaid.ext` |
| `--probe` | Resolve one full-res URL and exit — connection sanity check |
| `--count` | Tally total tasks and images via the API (no downloads) |
| `--catalog-stats` | Summarize `catalog.db` and count files on disk (no token needed) |
| `--collect-only` | Page through and write the catalog without downloading images |
| `--backfill-meta` | Fill missing `url`/`width`/`height` in catalog via `resolve_media` |
| `--backfill-full-meta` | Fill full prompt/seed/model/LoRAs in catalog via `getTaskById`; also fills url/width/height. Add `--with-loras` to re-fill older rows that predate LoRA tracking |
| `--export-csv` | Export `catalog.db` to `catalog_export.csv` (interop / spreadsheet backup) |
| `--sync-artworks` | Fetch published-artwork metadata (title, NSFW, likes, comments, tags) via `listArtworks` and merge onto catalog rows by `media_id`. Add `--with-videos` to also download animated-artwork video files into `videos/` |
| `--fix-model-names` | Re-resolve readable model names for rows whose `model_name` is blank or a raw numeric id (one API call per distinct model) |
| `--audit` | Read-only duplicate report of the whole backup folder → `audit_report.csv` |
| `--dedup` | Quarantine redundant duplicate copies to `_duplicates/` (dry-run unless `--apply`); reconciles the catalog |
| `--verify-dupes` | Confirm every file in `_duplicates/` is redundant (byte/pixel-identical to a kept copy) before deleting |
| `--organize` | Rename files in `images/` to `prompt_taskid_mediaid` scheme using `catalog.db` |
| `--organize-live` | Same naming applied live during download |
| `--organize-adv` | Folder sort: move files into `batches/` and `YYYY-MM/` folders; embed metadata into PNG/JPEG |
| `--organize-adv-live` | Same folder sort applied live during download |
| `--convert-existing` | Convert all already-downloaded `.webp` files to `--convert` format (default `png`) |

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--token TOKEN` | — | Bearer credential override for the legacy browser-token path. Normally unused — set `PIXAI_API_KEY` in `config.json` instead |
| `--out DIR` | `pixai_backup` | Output folder |
| `--version` | — | Print the version and exit |
| `--variant NAME` | auto | Force a media variant instead of auto-detecting the full-res `PUBLIC` one (e.g. `original`) |
| `--page-size N` | `250` | Tasks per request during download (higher = fewer round-trips; keep ≤ ~8000) |
| `--workers N` | `4` | Parallel workers. Applies to downloads **and** the batch jobs (`--backfill-meta`, `--backfill-full-meta`, `--fix-model-names`, `--sync-artworks --with-videos`, `--convert-existing`). 1 = serial/polite; 6–8 for bulk. Ignored for `--collect-only` and `--organize-adv-live` |
| `--update` | off | Incremental run: stop paging once a run of pages is fully on disk (newest-first) |
| `--update-grace N` | `2` | Consecutive all-on-disk pages before `--update` stops (raise if your history has gaps) |
| `--accurate-count` | off | Walk the whole API to count library size for the progress bar (default uses the fast catalog estimate) |
| `--max N` | `0` (all) | Stop after N tasks — use small numbers for testing |
| `--delay SECONDS` | `0.4` | Pause between requests (serial mode; concurrency paces parallel runs) |
| `--count-page-size N` | `5000` | Page size for `--count` |
| `--dedup-delete` | off | With `--dedup --apply`, delete redundant copies instead of quarantining |
| `--no-content` | off | With `--audit`/`--dedup`, skip Class-B content hashing (faster; same-media_id dupes only) |
| `--apply` | off | With `--dedup`, actually perform the moves/deletes (default is dry-run) |
| `--restore-orphans` | off | With `--verify-dupes`, move any orphaned quarantined files back to `images/` |
| `--with-loras` | off | With `--backfill-full-meta`, also re-fetch older rows missing LoRA data (populates the `loras` column) |
| `--with-videos` | off | With `--sync-artworks`, download animated-artwork video files into `videos/` |
| `--relabel-removed` | off | With `--fix-model-names`, relabel ids PixAI no longer resolves to "Unknown or removed model" |
| `--full-meta` | off | Fetch full prompt, seed, steps, sampler, CFG, model name, and LoRAs per task |
| `--name-length N` | `60` | Max prompt characters used in filenames |
| `--name-sep CHAR` | `_` | Word separator in filenames (`_` or `-`) |
| `--convert FMT` | off | Convert downloads: `png` or `jpeg` |
| `--jpeg-quality N` | `92` | JPEG quality (1–100) |
| `--jpeg-bg COLOR` | `white` | Background for transparency when converting to JPEG |
| `--keep-webp` | off | Keep the original `.webp` after converting |
| `--dry-run` | off | Preview `--organize` or `--organize-adv` without making changes |

---

## Output Structure

After a plain download:

```
pixai_backup/
├─ images/                 all images, flat — <prompt>_<taskid>_<mediaid>.<ext>
├─ catalog.db              SQLite database — one row per image (see Catalog Columns below)
└─ raw_tasks.jsonl         full raw task data (kept for re-processing)
```

After `--organize-adv` or `--organize-adv-live`:

```
pixai_backup/
├─ batches/
│   └─ <prompt>_<taskid>/        one folder per multi-image generation
│       ├─ 01_<mediaid>.png
│       ├─ 02_<mediaid>.png
│       └─ _prompt.txt           shared prompt, IDs, date, image list
├─ 2025-03/                      single-image generations grouped by month
│   ├─ <mediaid>.png
│   └─ _index.csv
├─ 2026-06/
│   └─ ...
├─ images/                       (empties out as files are moved)
├─ gallery/thumbs/               gallery thumbnails (auto-generated, git-ignored)
├─ videos/                       animated-artwork videos from --sync-artworks --with-videos
├─ _duplicates/                  quarantine from --dedup (reversible; delete to reclaim space)
├─ audit_report.csv              written by --audit
└─ catalog.db
```

> **Keep `catalog.db`.** It is the source of truth for full prompts, seeds, dates, model names, ratings, batch names, and generation parameters. Single images are named by `media_id` only — the catalog is the only complete record. Use `--organize` if you also want prompt-based filenames. Use `--export-csv` to get a spreadsheet-compatible copy.

### Catalog Columns

| Column | Description |
|---|---|
| `task_id` | PixAI task ID |
| `media_id` | PixAI media ID (primary key) |
| `filename` | Local filename |
| `url` | Full-res media URL |
| `width` | Image width (px) |
| `height` | Image height (px) |
| `prompt_preview` | Truncated ~100-char prompt from task summary |
| `status` | Task status (e.g. `completed`) |
| `created_at` | ISO 8601 creation timestamp |
| `prompt_full` | Full untruncated prompt (`--full-meta`) |
| `natural_prompt` | Auto-generated natural language prompt (`--full-meta`) |
| `seed` | Generation seed (`--full-meta`) |
| `steps` | Inference steps (`--full-meta`) |
| `sampler` | Sampler name, e.g. "Euler a" (`--full-meta`) |
| `cfg_scale` | CFG scale (`--full-meta`) |
| `model_id` | Model version ID (`--full-meta`) |
| `model_name` | Human-readable model name, e.g. "Tsubaki.2 v1" (`--full-meta`) |
| `batch` | Batch folder name populated by `--organize-adv` (blank for single-image months) |
| `rating` | Star rating 1–5 set in the gallery (0 or blank = unrated) |
| `artwork_id` | PixAI artwork ID, if this image was published (`--sync-artworks`) |
| `title` | Published-artwork title (`--sync-artworks`) |
| `is_published` | `1` if published with PUBLIC visibility (`--sync-artworks`) |
| `is_nsfw` | `1` if the published artwork is flagged NSFW (`--sync-artworks`) |
| `liked_count` / `comment_count` | Engagement counts at sync time (`--sync-artworks`) |
| `aes_score` | PixAI aesthetic score (`--sync-artworks`) |
| `art_tags` | Comma-joined tag / contest labels from the artwork's tacks (`--sync-artworks`) |
| `loras` | LoRAs used, as `Name:weight, …` (`--full-meta` / `--backfill-full-meta --with-loras`) |
| `negative_prompt` | Negative prompt, if the task had one (`--full-meta` / `--backfill-full-meta`) |
| `clip_skip` | Clip-skip value (`--full-meta` / `--backfill-full-meta`) |

---

## Full Meta (Full Prompt, Seed, Model)

By default the download only captures `prompt_preview` — a truncated ~100-character summary. The `--full-meta` flag makes one additional `getTaskById` call per unique task to capture complete generation parameters. Batches of images share one call, so it scales efficiently.

### One-time config setup

Full meta needs one more persisted-query hash. Open an image detail page on pixai.art, then in DevTools → Network → filter `graphql`:

1. Find `getTaskById` — copy its `extensions.persistedQuery.sha256Hash` → `TASK_DETAIL_HASH`

```json
"TASK_DETAIL_HASH": "<your value from getTaskById>"
```

The model-name lookup (`getGenerationModelByVersionId`) ships with a working default, so `MODEL_DETAIL_HASH` is optional — only add it if model names stop resolving after a PixAI frontend update. Recapture either hash the same way if they break.

### Usage

```
# Fetch full meta on new downloads:
python pixai_gallery_backup.py --full-meta

# Backfill existing catalog rows:
python pixai_gallery_backup.py --backfill-full-meta
```

`--backfill-full-meta` makes one API call per unique `task_id`. It also fills any missing `url`/`width`/`height` as a free bonus, making `--backfill-meta` unnecessary if you run both.

---

## Known Issues

| Issue | Notes |
|---|---|
| WebP metadata embedding | `--organize-adv` skips WebP files; pair with `--convert png` to get embedded metadata |
| Windows MAX_PATH (260 chars) | Batch images use short names (`NN_<mediaid>.ext`); `--name-length` defaults to 60 |
| `--count` server errors | Lower `--count-page-size` to 1,000–2,000 if you see `Internal server error` (PixAI rejects very large page requests) |
| Gallery after `--organize-adv` | Thumbnails are keyed by `media_id` and unaffected; gallery falls back to media-ID search if the `filename` column is stale |

---

## Changelog

### v1.3.2 — fuller reproduction metadata, duplicate-review browser, prompt editing

- **Negative prompt + clip-skip captured** — `getTaskById` carries `negativePrompts` and `clipSkip`; both are now stored (`negative_prompt`, `clip_skip` columns) and shown on the detail page. Re-run `--backfill-full-meta --with-loras` to fill them on existing rows. (Many newer "structured prompt" generations have no separate negative — that's expected, not a miss.)
- **Duplicate-review browser** — a new `/duplicates` gallery page lists every media id that exists in more than one folder, side-by-side with thumbnails, marking the keeper vs. the copies `--dedup` would quarantine. Linked from `/health`. Read-only review; the actual move still happens via Dedup.
- **Bulk prompt edit** — edit a single image's prompt inline on its detail page (**Edit Prompt** → Save), or select several thumbnails and **Find/Replace** a substring across all their prompts at once. Writes straight to `catalog.db`.

### v1.3.1 — parallel workers for the batch jobs

- **Parallel workers across the slow jobs** — `--workers N` now also speeds up the latency-bound and CPU-bound batch operations, not just downloads: `--backfill-full-meta` (incl. `--with-loras`), `--backfill-meta`, `--fix-model-names`, `--sync-artworks --with-videos`, `--convert-existing`, and gallery **thumbnail generation**. A shared `_parallel_map` helper pools the work, keeps catalog writes + progress on the main thread, and defaults to serial (`workers=1`). The GUI Utilities tab gains a **Workers** control (default 4). On the stable API key these network jobs run dramatically faster.

### v1.3.0 — API-key auth, artwork sync, LoRA tracking, dashboards, mobile & video

- **Backfill LoRAs into existing rows** — `--backfill-full-meta --with-loras` (GUI: "incl. LoRAs" checkbox) re-fetches older rows that have full meta but no LoRA data yet, populating the `loras` column so the LoRA filter and "Top LoRAs" dashboard work for your whole library.
- **Gallery HTTPS** — `python pixai_gallery.py --https` (GUI: "Serve over HTTPS" checkbox) serves the gallery over self-signed HTTPS, so a phone on your LAN can install it as a PWA and use the offline service worker (over plain HTTP, browsers block PWA install / service workers by design). Requires `pip install cryptography`; browsers show a one-time certificate warning.
- **Official API-key auth (stable, no expiring login)** — set `PIXAI_API_KEY` in `config.json` (an official key from platform.pixai.art, lifetime up to ~2 years) and it's used as the Bearer credential for **all** calls, including the bulk `listUserTaskSummaries` listing. Verified end-to-end: listing, media resolution, full-meta, and model lookups all authenticate with the key — so `U3T` and the browser token become unnecessary and there's nothing to recapture as it expires. The browser-JWT path remains as a fallback.
- **Animated-artwork (video) backup** — `--sync-artworks --with-videos` downloads animated-artwork video files (`videoMediaId`) into a `videos/` folder; `ext_from_ct` now recognizes mp4/webm/mov/avif so video files are saved with the correct extension. GUI: "incl. videos" checkbox next to Sync Artworks.
- **Aesthetic-score / Most-liked sorts**, **lightbox swipe + double-tap zoom**, **tablet breakpoint**, **PWA** (offline thumbnails / add-to-home-screen), **LoRA tracking** (filter + detail display + dashboard "Top LoRAs"), **Account info** (quota/membership), **prompt word-cloud**, **saved filter presets**, **privacy blur**.
- **Published-artwork sync** (`--sync-artworks`) — pulls title, NSFW flag, like/comment counts, aesthetic score, and tag/contest labels for your published pieces (via `listArtworks`) into the catalog by `media_id`; 8 new catalog columns; GUI Utilities button.
- **Model-name cleanup** (`--fix-model-names`) — re-resolves readable model names for rows that show a raw numeric id (caused by an earlier run without `MODEL_DETAIL_HASH`, which now ships with a working default). GUI "Fix Model Names" button. Ids PixAI no longer recognizes (deleted models) are relabeled to "Unknown or removed model" with `--relabel-removed` (on by default from the GUI button), collapsing them into one tidy menu entry.
- **Artwork data in the gallery** — synced titles show on cards (with like counts); a **Published-only** checkbox and a **Tag / contest** filter in the filter bar; a **Privacy blur** toggle (blurs all thumbnails until hover — useful on LAN/mobile; NSFW-flagged cards blur more heavily); and **Published / Total-likes** stat cards plus a clickable **Top tags & contests** panel on the Collection Health dashboard.
- **Gallery performance** — the server now handles requests concurrently (thumbnails load in parallel instead of one-at-a-time, in both the CLI and GUI launchers); thumbnails and full images are served with immutable 1-year cache headers so pagination, back-navigation, and re-visits are instant with no re-download (big win on mobile / LAN); HTML pages are gzip-compressed; thumbnails decode asynchronously (`decoding="async"`).
- **Mobile filter bar** — on narrow screens the filter controls collapse behind a "Filters" toggle so the image grid leads; controls go full-width and the bar auto-opens when a filter is active.
- **Lightbox + keyboard navigation** — click any thumbnail for an in-page lightbox with prev/next, a Details link, and an `F`/Space slideshow; in the grid, arrow keys move focus and Enter opens the lightbox.
- **Cross-page selection + ZIP export** — image selections now persist across pages (stored in the browser); the bulk bar gains a **Download ZIP** button that streams the selected full-res images as a single archive.
- **Published-artwork sync** — `--sync-artworks` fetches your published-artwork metadata (title, NSFW flag, like/comment counts, aesthetic score, and tag/contest labels) via the `listArtworks` API and merges it onto matching catalog rows by `media_id`. Adds catalog columns `artwork_id`, `title`, `is_published`, `is_nsfw`, `liked_count`, `comment_count`, `aes_score`, `art_tags`. Also a **Sync Artworks** button in the GUI Utilities tab.

### v1.2.0 — Duplicate audit/dedup, gallery overhaul, parallel & incremental downloads

**Downloads — much faster, especially follow-up runs**
- **Parallel downloads** — `--workers N` (default 4) fetches images concurrently via a bounded thread pool. `--workers 1` keeps the old serial/polite behavior.
- **Instant resume** — resume is now an O(1) in-memory media-ID index built from a single startup scan, instead of a full-tree scan per image. Re-runs no longer slow down as the library grows.
- **Incremental `--update` mode** — stops paging once a run of pages is already fully on disk (newest-first), so routine updates finish in seconds. `--update-grace` tunes the stop threshold.
- **No more network pre-count** — progress total comes from the catalog (instant); `--accurate-count` forces the old full-history API count. Startup disk scan rewritten with `os.scandir`.
- **`--page-size` default raised** from 20 to 250 (far fewer round-trips).

**Duplicate audit & dedup**
- **`--audit`** — read-only duplicate report (same `media_id` across folders + byte-identical-different-id via size-bucketed hashing); writes `audit_report.csv`; independent of `catalog.db`.
- **`--dedup`** — quarantines redundant copies to `_duplicates/` (keeps the most-organized copy), dry-run by default; `--apply`, `--dedup-delete`, `--no-content`; reconciles the catalog and auto-runs a verify pass.
- **`--verify-dupes`** — proves every quarantined file is byte/pixel-identical to a surviving copy before deletion; `--restore-orphans` recovers any with no surviving copy.
- **Root-cause fix** — single-image organize files were renamed to bare `<mediaid>.ext`, which resume's old `*_<mediaid>.*` matcher missed, causing re-downloads and orphaned flat copies (the `images/`+month duplication). Media-ID → file resolution now goes through one shared matcher that recognizes both naming layouts.

**Gallery**
- **Wildcard prompt search** — `*` / `?` wildcards and multi-word AND (plain words stay broad substring matches).
- **Year/Month date pickers**, **searchable Model/Batch** fields (datalist), **per-page selector**, **Min-rating filter**, **thumbnail-size slider**, **Resolution/Aspect sorts**.
- **Active-filter chips** with one-click removal; thumbnail loading skeletons; friendlier empty state.
- **Collection Health dashboard** (`/health`) — storage, full-meta %, duplicates + reclaimable, missing-file count, images-by-month, top models.
- **Detail page** — Copy Prompt, Find Similar (by model), View Batch.
- **Visual refresh** — brand mark + favicon; dark theme retuned to a deep-violet/teal/gold palette.

**GUI**
- Download tab: **Workers** and **Update mode** controls; page-size default 250.
- Utilities tab: **Audit Duplicates / Dedup / Verify Quarantine** buttons.
- Gallery LAN mode; progress bars on Organize/Convert/Utilities; auto-load `token.txt` on startup.

**Other**
- Fixed an `UnboundLocalError` when running with `--full-meta`.
- Fixed the progress bar overshooting 100% on resume runs (it no longer seeds the counter with the on-disk count and then re-ticks each skipped item).
- **121 tests** (up from 81).

### v1.1.0 — SQLite catalog, gallery performance, batch filter, focus mode

- **SQLite catalog** — `catalog.csv` replaced by `catalog.db`; faster indexed queries, crash-safe upserts, no corruption on large libraries; existing `catalog.csv` auto-migrated on first run; `--export-csv` for interop backup
- **Gallery SQL performance** — all filtering, sorting, and pagination now done in SQL (~20× faster index page on large libraries); prev/next navigation respects active filters
- **Batch filter** — `--organize-adv` writes the batch folder name to the catalog; gallery filter bar shows a Batch dropdown; auto-backfilled from disk on gallery startup for previously organized libraries
- **Focus / theater mode** — `F` key or Focus button on detail page hides metadata and expands image to ~90% viewport; preference persisted in `localStorage`
- **GUI: Export CSV button** added to Utilities tab
- **GUI: version** shown in window title bar
- **Token.txt file dialog** now opens in the script directory instead of a system folder
- **`--version` flag** on CLI
- **81 tests** (up from 68)
- Various bug fixes (stale module cache, Unicode crash, gallery thumbnail false-match after organize)

---

### v1.0.0 — First stable release

This release merges the `feature/gallery-server` branch and marks the completion of all planned features.

- **Local web gallery** (`pixai_gallery.py`) — browse, filter by prompt/model/date, paginate, and delete images from a browser; Flask + Jinja2 with Catppuccin Mocha theme
- **Star ratings** (0–5) per image — stored in `catalog.db`; set via the gallery with no page reload; sort by rating high/low
- **Gallery tab in GUI** — launch/stop the Flask server in a background thread; configurable port; auto-builds missing thumbnails on start with progress log; Open in Browser button
- **Back-navigation state** — returning from a detail page lands back on the correct gallery page and filters
- **Clickable full-res image** on detail page; separate "Open on PixAI CDN" button
- **New sort options** — Rating ↓/↑, Model name, Width, Height (alongside Newest/Oldest)
- **`--collect-only` checkbox** on GUI Download tab
- **Configurable API delay** on GUI Utilities tab
- **`--organize-adv` simplified** — single images named `<mediaid>.ext` in `YYYY-MM/` folders; prompt-based renaming removed; use `--organize` separately if you want prompt filenames
- **`rating` column** added to catalog (backward compatible)
- Screenshots added to documentation

---

<details>
<summary>Pre-1.0 Development History</summary>

### v4.5
- Gallery tab in GUI, `--collect-only` checkbox, configurable Utilities delay
- `--organize-adv` simplified to folder-sort only

### v4.4
- `--full-meta` / `--backfill-full-meta` via `getTaskById` + `getGenerationModelByVersionId`
- 8 new catalog columns: `prompt_full`, `natural_prompt`, `seed`, `steps`, `sampler`, `cfg_scale`, `model_id`, `model_name`
- GUI: full-meta checkbox, Backfill buttons

### v4.3
- `tests/` with pytest — 68 tests covering pure functions, filesystem, catalog, and mocked network

### v4.2
- Download progress meter with resume-aware seeding from disk
- Config and token path resolution anchored to `Path(__file__)` for GUI compatibility
- `_make_session()` re-reads globals on every call

### v4.1
- PySide6 GUI — tabbed Download / Organize / Convert / Utilities; dark Catppuccin Mocha theme; background Worker thread

### v4.0
- Apollo persisted GraphQL GET queries; backward pagination; full resume support
- `--organize-adv`, `--organize`, `--convert-existing`, `--count`, `--probe`, `--catalog-stats`, `--collect-only`
- Persistent catalog; `truststore` integration

</details>

---

## Feature Requests

Planned future enhancements:

- **Tag system** — freeform tags per image stored as a catalog relation
- **PixAI favorites sync** — filter downloads to only favorited generations via `favoritedAt`
- **Engagement-over-time** — snapshot like/view counts on each `--sync-artworks` run to chart growth (no historical backfill possible)

---

## License

Personal use. Not affiliated with or endorsed by PixAI.
