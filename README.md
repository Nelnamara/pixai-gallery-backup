# PixAI Gallery Backup

> **Language:** Python 3.8+ · **Platform:** Windows / macOS / Linux · **Author:** Nelnamara

A command-line tool (with optional desktop GUI) that backs up **your own** PixAI.art generated images at full resolution. PixAI's gallery UI only shows 20 images at a time; this talks to the same API the browser uses, pages through your entire generation history, downloads every image, and keeps a fully searchable catalog of prompts, seeds, model names, dimensions, and dates.

PixAI's terms grant users copyright of their own generations. This tool is rate-paced to be polite to their servers.

---

## Features

- **Full-resolution downloads** — bypasses the 20-image gallery limit; fetches every generation at the original size
- **Automatic resume** — interrupt any time and re-run; already-saved images are skipped by media ID
- **Persistent catalog** — `catalog.csv` is a deduplicated, append-safe database keyed by `media_id`; prior-session rows are never lost across interrupted or multi-session downloads
- **Full generation metadata** — `--full-meta` captures the complete prompt, seed, steps, sampler, CFG scale, and human-readable model name; `--backfill-full-meta` fills existing catalog rows retroactively
- **Local web gallery** — browse, filter, rate, and delete your images from a browser via the built-in Flask gallery server
- **Progress meter** — pre-flight library count feeds a live progress bar; resume runs open at the correct position
- **Format conversion** — convert WebP to PNG or JPEG on download, or batch-convert existing files
- **Organize mode** — sorts files into `batches/` and `YYYY-MM/` folders; embeds metadata into PNG/JPEG files
- **Rate limiting** — configurable delay between requests (default 0.4 s)
- **SSL safety** — HTTPS verification always on; `truststore` support for corporate/antivirus environments

---

## GUI

A PySide6 desktop GUI (`pixai_gui.py`) wraps the full workflow in a tabbed window with a dark Catppuccin Mocha theme, background threads, and live log output.

| Tab | What it does |
|---|---|
| **Download** | Configure token, output folder, page size, organize mode, conversion, collect-only, and full-meta; Start / Stop |
| **Organize** | Post-download rename (`--organize`) or full folder sort (`--organize-adv`); dry-run preview |
| **Convert** | Batch-convert existing `.webp` files to PNG or JPEG in place |
| **Utilities** | Probe, Count, Catalog Stats, Backfill url/width/height, Backfill Full Meta; configurable API delay |
| **Gallery** | Launch / stop the local gallery server; configurable port; auto-builds thumbnails on start |

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

`config.json` lives next to the scripts and is git-ignored. It holds values captured once from your browser. You only need to do this once — they only change if PixAI updates their frontend.

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
python pixai_gallery_backup.py                # download everything
python pixai_gallery_backup.py --full-meta    # download + capture full prompt/seed/model
```

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

![Local web gallery showing image grid with filters and star ratings](screenshots/05_gallery_view.png)

### Modes

| Flag | What it does |
|---|---|
| *(none)* | Download full history into `images/`, named `prompt_taskid_mediaid.ext` |
| `--probe` | Resolve one full-res URL and exit — connection sanity check |
| `--count` | Tally total tasks and images via the API (no downloads) |
| `--catalog-stats` | Summarize existing `catalog.csv` and count files on disk (no token needed) |
| `--collect-only` | Page through and write the catalog without downloading images |
| `--backfill-meta` | Fill missing `url`/`width`/`height` in catalog via `resolve_media` |
| `--backfill-full-meta` | Fill full prompt/seed/model in catalog via `getTaskById`; also fills url/width/height |
| `--organize` | Rename files in `images/` to `prompt_taskid_mediaid` scheme using `catalog.csv` |
| `--organize-live` | Same naming applied live during download |
| `--organize-adv` | Folder sort: move files into `batches/` and `YYYY-MM/` folders; embed metadata into PNG/JPEG |
| `--organize-adv-live` | Same folder sort applied live during download |
| `--convert-existing` | Convert all already-downloaded `.webp` files to `--convert` format (default `png`) |

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--token TOKEN` | — | Bearer token (else `PIXAI_TOKEN` env or `token.txt`) |
| `--out DIR` | `pixai_backup` | Output folder |
| `--page-size N` | `20` | Tasks per request during download (try `5000` for speed; keep ≤ ~8000) |
| `--max N` | `0` (all) | Stop after N tasks — use small numbers for testing |
| `--delay SECONDS` | `0.4` | Pause between requests |
| `--count-page-size N` | `5000` | Page size for `--count` |
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
├─ catalog.csv             one row per image (see Catalog Columns below)
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
└─ catalog.csv
```

> **Keep `catalog.csv`.** It is the source of truth for full prompts, seeds, dates, model names, ratings, and generation parameters. Single images are named by `media_id` only — the catalog is the only complete record. Use `--organize` if you also want prompt-based filenames.

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
| `rating` | Star rating 1–5 set in the gallery (0 or blank = unrated) |

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

### v1.0.0 — First stable release

This release merges the `feature/gallery-server` branch and marks the completion of all planned features.

- **Local web gallery** (`pixai_gallery.py`) — browse, filter by prompt/model/date, paginate, and delete images from a browser; Flask + Jinja2 with Catppuccin Mocha theme
- **Star ratings** (0–5) per image — stored in `catalog.csv`; set via the gallery with no page reload; sort by rating high/low
- **Gallery tab in GUI** — launch/stop the Flask server in a background thread; configurable port; auto-builds missing thumbnails on start with progress log; Open in Browser button
- **Back-navigation state** — returning from a detail page lands back on the correct gallery page and filters
- **Clickable full-res image** on detail page; separate "Open on PixAI CDN" button
- **New sort options** — Rating ↓/↑, Model name, Width, Height (alongside Newest/Oldest)
- **`--collect-only` checkbox** on GUI Download tab
- **Configurable API delay** on GUI Utilities tab
- **`--organize-adv` simplified** — single images named `<mediaid>.ext` in `YYYY-MM/` folders; prompt-based renaming removed; use `--organize` separately if you want prompt filenames
- **`rating` column** added to `catalog.csv` (backward compatible)
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
- Persistent `catalog.csv`; `truststore` integration

</details>

---

## Feature Requests

Planned future enhancements:

- **SQLite catalog backend** — replace `catalog.csv` with a single `catalog.db` file for indexed queries, faster filter/sort, and cleaner single-row rating updates. CSV export will be retained for interop. Tracked on branch `feature/sqlite-catalog`.
- **Persistent cross-page selection** — checkbox selections that survive pagination
- **Bulk prompt edit** — edit `prompt_full` in the gallery and write back to `catalog.csv`
- **Tag system** — freeform tags stored as an extra catalog column
- **Export selected** — download a ZIP of checked images from the gallery
- **PixAI favorites sync** — filter downloads to only favorited generations via `favoritedAt`

---

## License

Personal use. Not affiliated with or endorsed by PixAI.
