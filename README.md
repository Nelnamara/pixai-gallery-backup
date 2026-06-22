# PixAI Gallery Backup

> **Language:** Python 3.8+ · **Platform:** Windows / macOS / Linux · **Author:** Nelnamara

A command-line tool (with optional desktop GUI) that backs up **your own** PixAI.art generated images at full resolution. PixAI's gallery UI only shows 20 images at a time; this talks to the same API the browser uses, pages through your entire generation history, downloads every image, and keeps a fully searchable catalog of prompts, seeds, model names, dimensions, and dates.

PixAI's terms grant users copyright of their own generations. This tool is rate-paced to be polite to their servers.

---

## Features

- **Full-resolution downloads** — bypasses the 20-image gallery limit; fetches every generation at the original size
- **Fast parallel downloads** — `--workers N` (default 4) fetches images concurrently; tuned for bulk first-time pulls
- **Instant resume** — an in-memory media-ID index makes re-runs skip already-saved images with no per-item disk scan (resume stays fast no matter how large the library grows)
- **Incremental update mode** — `--update` stops paging once it reaches your already-downloaded history, so routine "grab what's new" runs finish in seconds instead of re-walking everything
- **Persistent catalog** — `catalog.db` (SQLite) is a deduplicated, indexed database keyed by `media_id`; prior-session rows are never lost across interrupted or multi-session downloads; auto-migrates from `catalog.csv` if upgrading
- **Full generation metadata** — `--full-meta` captures the complete prompt, seed, steps, sampler, CFG scale, and human-readable model name; `--backfill-full-meta` fills existing catalog rows retroactively
- **Duplicate audit & dedup** — `--audit` scans the whole backup folder for duplicate images (same `media_id` across folders, plus byte-identical copies); `--dedup` quarantines the redundant copies (keeping the most-organized one) and `--verify-dupes` proves the quarantine is safe before you delete it
- **Local web gallery** — browse, filter, rate, and delete your images from a browser; wildcard prompt search, searchable model/batch filters, year/month date pickers, adjustable thumbnail size, and a per-page selector
- **Collection Health dashboard** — `/health` page summarizing storage used, full-meta coverage, duplicates, missing files, images-by-month, and top models
- **Format conversion** — convert WebP to PNG or JPEG on download, or batch-convert existing files
- **Organize mode** — sorts files into `batches/` and `YYYY-MM/` folders; embeds metadata into PNG/JPEG files
- **Rate limiting** — configurable delay between requests (default 0.4 s)
- **SSL safety** — HTTPS verification always on; `truststore` support for corporate/antivirus environments

---

## GUI

A PySide6 desktop GUI (`pixai_gui.py`) wraps the full workflow in a tabbed window with a dark Catppuccin Mocha theme, background threads, and live log output.

| Tab | What it does |
|---|---|
| **Download** | Configure token, output folder, page size, **workers**, **update mode**, organize mode, conversion, collect-only, and full-meta; Start / Stop |
| **Organize** | Post-download rename (`--organize`) or full folder sort (`--organize-adv`); dry-run preview |
| **Convert** | Batch-convert existing `.webp` files to PNG or JPEG in place |
| **Utilities** | Probe, Count, Catalog Stats, Backfill url/width/height, Backfill Full Meta, Export CSV, **Audit Duplicates / Dedup / Verify Quarantine**; configurable API delay |
| **Gallery** | Launch / stop the local gallery server; configurable port; LAN mode; auto-builds thumbnails on start |

![GUI Download tab](screenshots/04_gui_download.png)

![GUI Gallery tab](screenshots/06_gui_gallery.png)

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

`config.json` lives next to the scripts and is git-ignored.

### Recommended: use an official API key (no expiring login)

Generate an API key at [platform.pixai.art](https://platform.pixai.art) (you can set its lifetime up to ~2 years) and put it in `config.json` as `PIXAI_API_KEY`. It's sent as the Bearer credential for **every** call — listing, media resolution, full-meta, model names — so you **don't need `U3T` or a browser token**, and you never have to recapture an expiring login. You still need `USER_ID` and `PERSISTED_QUERY_HASH` (captured once from DevTools, below); they only change if PixAI updates their frontend.

```json
"PIXAI_API_KEY": "your-key",
"USER_ID": "your-numeric-id",
"PERSISTED_QUERY_HASH": "captured-hash"
```

If you'd rather not create a key, leave `PIXAI_API_KEY` blank and use the browser-JWT path (capture `U3T` too, and supply a token via `token.txt` / `PIXAI_TOKEN` / `--token`).

Copy `config.example.json` to `config.json` and fill in the fields below.

---

### Step 1 — Find your values in DevTools

All required config values — including your numeric `USER_ID` — come from the browser's Network panel. PixAI uses your username (`@yourname`) in the URL, not your numeric ID, so the address bar won't help.

1. Log in to [pixai.art](https://pixai.art) and open your gallery or profile page
2. Press **F12** to open DevTools → click the **Network** tab
3. Type `graphql` in the filter box at the top of the Network panel
4. Scroll the page slightly so requests fire
5. Click any row named `listUserTaskSummaries` in the request list
6. Click the **Payload** tab (Chrome) or **Request** tab (Firefox)

You will see something like this — **these are example field names only, your values will be different:**

```
operation      listUserTaskSummaries
u3t            <your U3T value>
operationName  listUserTaskSummaries
variables      {"before":"...","last":20,"userId":"<your USER_ID>"}
extensions     {"persistedQuery":{"sha256Hash":"<your PERSISTED_QUERY_HASH>"}}
```

![DevTools Payload tab showing u3t, userId, and sha256Hash fields](screenshots/02_devtools_payload.png)

Copy these three values into `config.json`:
- `u3t` field → `U3T`
- `userId` inside `variables` → `USER_ID`
- `sha256Hash` inside `extensions.persistedQuery` → `PERSISTED_QUERY_HASH`

---

### Step 2 — Capture your Bearer token

The Bearer token is **not** stored in `config.json` — it expires in hours or days and needs to be re-captured periodically (the GUI has a token field for this).

While you still have DevTools open from Step 1:

1. Stay on the **Headers** tab of the same `listUserTaskSummaries` request
2. Scroll down to **Request Headers**
3. Find the `Authorization` header — it starts with `Bearer eyJ...`
4. Copy everything **after** `Bearer ` (the token itself)

![DevTools Headers tab showing the Authorization Bearer token](screenshots/03_devtools_headers.png)

Keep the token private — treat it like a password.

**Provide it one of three ways:**

```
# Windows PowerShell
$env:PIXAI_TOKEN="eyJ...your token..."

# macOS / Linux
export PIXAI_TOKEN="eyJ...your token..."
```

Or create `token.txt` next to the script containing just the token. Or paste it directly into the Token field in the GUI.

---

### Optional — Full meta hashes

Only needed for `--full-meta` and `--backfill-full-meta`. See [Full Meta](#full-meta-full-prompt-seed-model) for capture instructions.

> **`config.json` is git-ignored** and will never be committed.

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
python pixai_gallery.py --out pixai_backup    # launch at http://127.0.0.1:5000
python pixai_gallery.py --out pixai_backup --port 5757
```

Or use the **Gallery tab** in the GUI to launch and stop the server with one click.

The gallery filter bar supports wildcard prompt search (`night*`, `a?c`, multiple words ANDed), searchable Model/Batch fields, Year/Month date pickers, a Min-rating filter, a per-page selector, and a thumbnail-size slider. The header links to a **Collection Health** dashboard (`/health`) showing storage used, full-meta coverage, duplicate count, missing files, images-by-month, and top models.

![Local web gallery showing image grid with filters and star ratings](screenshots/05_gallery_view.png)

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
| `--backfill-full-meta` | Fill full prompt/seed/model in catalog via `getTaskById`; also fills url/width/height |
| `--export-csv` | Export `catalog.db` to `catalog_export.csv` (interop / spreadsheet backup) |
| `--sync-artworks` | Fetch published-artwork metadata (title, NSFW, likes, comments, tags) via `listArtworks` and merge onto catalog rows by `media_id` |
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
| `--token TOKEN` | — | Bearer token (else `PIXAI_TOKEN` env or `token.txt`) |
| `--out DIR` | `pixai_backup` | Output folder |
| `--page-size N` | `250` | Tasks per request during download (higher = fewer round-trips; keep ≤ ~8000) |
| `--workers N` | `4` | Parallel download workers. 1 = serial/polite; 6–8 for bulk pulls. Ignored for `--collect-only` and `--organize-adv-live` |
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
| `--full-meta` | off | Fetch full prompt, seed, steps, sampler, CFG, and model name per task |
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

---

## Full Meta (Full Prompt, Seed, Model)

By default the download only captures `prompt_preview` — a truncated ~100-character summary. The `--full-meta` flag makes one additional `getTaskById` call per unique task to capture complete generation parameters. Batches of images share one call, so it scales efficiently.

### One-time config setup

Open an image detail page on pixai.art, then in DevTools → Network → filter `graphql`:

1. Find `getTaskById` — copy its `extensions.persistedQuery.sha256Hash` → `TASK_DETAIL_HASH`
2. Find `getGenerationModelByVersionId` — copy its hash → `MODEL_DETAIL_HASH`

Add both to `config.json` (**do not copy the placeholder text below — use your own captured values**):

```json
"TASK_DETAIL_HASH": "<your value from getTaskById>",
"MODEL_DETAIL_HASH": "<your value from getGenerationModelByVersionId>"
```

If these hashes stop working after a PixAI frontend update, recapture them the same way.

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

### Unreleased

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

Planned future enhancements, grouped by area:

### Backend / Catalog
- **Tag system** — freeform tags per image stored as a catalog relation.

### Gallery UI
- **Persistent cross-page selection** — checkbox selections that survive pagination
- **Bulk prompt edit** — edit `prompt_full` in the gallery and write back to the catalog
- **Export selected** — download a ZIP of checked images from the gallery

### Download & Sync
- **PixAI favorites sync** — filter downloads to only favorited generations via `favoritedAt`

---

## License

Personal use. Not affiliated with or endorsed by PixAI.
