# PixAI Gallery Backup

> **Language:** Python 3.8+ · **Platform:** Windows / macOS / Linux · **Author:** Nelnamara

A command-line tool that backs up **your own** PixAI.art generated images at full resolution. PixAI's gallery UI only shows 20 images at a time; this talks to the same API the browser uses, pages through your entire generation history, downloads every image, and keeps a searchable catalog of prompts, dimensions, and dates.

PixAI's terms grant users copyright of their own generations. This tool is rate-paced to be polite to their servers.

---

## Features

- **Full-resolution downloads** — bypasses the 20-image gallery limit; fetches every generation at the original size
- **Automatic resume** — interrupt any time and re-run; already-saved images are skipped by media ID
- **Catalog** — `catalog.csv` records each image's prompt preview, dimensions, date, task ID, and media ID
- **Backward pagination** — walks newest → oldest through your entire generation history
- **Format conversion** — optionally convert WebP to PNG (lossless re-container) or JPEG on download
- **Organize mode** — sorts downloaded files into `batches/` folders (multi-image generations) and `YYYY-MM/` month folders (singles), writes per-folder `_prompt.txt` and `_index.csv` info files
- **Embedded metadata** — writes prompt, IDs, and date directly into PNG text chunks or JPEG EXIF on organize
- **Count mode** — tallies total tasks and images via the API without downloading
- **Probe mode** — connection sanity check; confirms it can see and resolve a full-res URL before committing to a run
- **Rate limiting** — configurable delay between requests (default 0.4 s)
- **SSL safety** — HTTPS verification always on; `truststore` support for corporate/antivirus environments

---

## GUI

A PySide6 desktop GUI (`pixai_gui.py`) is included alongside the CLI. It wraps the full backup workflow in a tabbed window with a dark Catppuccin Mocha theme, background download thread, and live log output.

| Tab | What it does |
|---|---|
| **Download** | Configure token, output folder, page size, organize mode, and conversion; Start / Stop |
| **Organize** | Post-download rename (`--organize`) or full folder sort (`--organize-adv`); dry-run preview |
| **Convert** | Batch-convert existing `.webp` files to PNG or JPEG in place |
| **Utilities** | Probe, Count, and Catalog Stats buttons |

Settings (token, output folder, options) are saved to `pixai_gui_settings.json` next to the script (git-ignored).

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
| `pillow` | ❌ | Only needed for `--convert` and metadata embedding in `--organize` |
| `PySide6` | ❌ | Only needed for the GUI (`pixai_gui.py`) |

Install all at once:

```
pip install requests truststore pillow PySide6
```

---

## Installation

1. Install Python 3.8 or newer — check with `python --version`
2. Install dependencies (above)
3. Put `pixai_gallery_backup.py` and `config.example.json` in a folder of their own (e.g. a `pixai` folder on your Desktop)
4. Copy `config.example.json` to `config.json` in the same folder and fill in your values (see Configuration below)
5. All output is created next to the script

> **Tip:** Use a dedicated folder — the script creates a `pixai_backup/` output directory alongside itself.

---

## Configuration

The script reads three values from `config.json` next to the script. These are captured once from your browser and stay until PixAI changes their frontend.

1. Copy `config.example.json` to `config.json`
2. Fill in the three fields:

| Field | Where to find it |
|---|---|
| `USER_ID` | Your PixAI profile URL — the numeric ID at the end |
| `U3T` | Network tab → `graphql` row → Payload → `u3t` parameter |
| `PERSISTED_QUERY_HASH` | Network tab → `graphql` row → Payload → `extensions.persistedQuery.sha256Hash` |

To capture from the browser: log in to [pixai.art](https://pixai.art), open your gallery, press **F12 → Network**, filter by `graphql`, scroll once so a request appears, then click the `listUserTaskSummaries` row and read from the **Payload** tab.

> **`config.json` is git-ignored** and will never be committed. Keep it next to the script only.

---

## Usage

### Getting Your Token

The script never sees your password. It uses a short-lived **Bearer token** from your logged-in browser.

1. Log in to [pixai.art](https://pixai.art) and open your gallery
2. Open DevTools — press **F12**
3. Click the **Network** tab and type `graphql` in the filter box
4. Scroll your gallery so a request appears; click any `graphql` row
5. In **Request Headers**, find `authorization: Bearer eyJ...` — copy everything **after** `Bearer `

Keep the token private — treat it like a password. It expires on its own (hours to a few days).

### Providing the Token

**Windows (PowerShell):**
```
$env:PIXAI_TOKEN="eyJ...your token..."
```

**macOS / Linux:**
```
export PIXAI_TOKEN="eyJ...your token..."
```

Or create `token.txt` next to the script containing just the token. Or pass `--token "eyJ..."` on the command line.

### First Run

```
python pixai_gallery_backup.py --probe        # confirm connection + full-res URL
python pixai_gallery_backup.py --count        # how many images you have
python pixai_gallery_backup.py --max 40       # small test download
python pixai_gallery_backup.py                # download everything
```

### Organizing Downloads

**Post-download (run after a download session):**
```
python pixai_gallery_backup.py --organize --dry-run        # preview rename plan
python pixai_gallery_backup.py --organize                  # rename to prompt_taskid_mediaid
python pixai_gallery_backup.py --organize-adv --dry-run    # preview full folder sort
python pixai_gallery_backup.py --organize-adv --convert png  # sort into folders + convert
```

**Live (sort as files download — one step, no separate organize pass):**
```
python pixai_gallery_backup.py --organize-adv-live --convert png   # download + folder sort
```

### Modes

| Flag | What it does |
|---|---|
| *(none)* | Download full history into `images/`, named `prompt_taskid_mediaid.ext` |
| `--probe` | Resolve one full-res URL and exit — connection sanity check |
| `--count` | Tally total tasks and images via the API (no downloads) |
| `--catalog-stats` | Summarize existing `catalog.csv` and count files on disk (no token needed) |
| `--collect-only` | Page through and write the catalog, skip image downloads |
| `--organize` | Rename files in `images/` to `prompt_taskid_mediaid` scheme using `catalog.csv` |
| `--organize-live` | Same naming applied live during download (default behavior made explicit) |
| `--organize-adv` | Full sort: move files into `batches/` and `YYYY-MM/` folders, embed metadata |
| `--organize-adv-live` | Full sort applied live during download — files land directly in batch/month folders |
| `--convert-existing` | Convert all already-downloaded `.webp` files to `--convert` format (default `png`). No token needed. |

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--token TOKEN` | — | Bearer token (else `PIXAI_TOKEN` env or `token.txt`) |
| `--out DIR` | `pixai_backup` | Output folder |
| `--page-size N` | `20` | Tasks per request during download (try `5000` for speed; keep ≤ ~8000) |
| `--max N` | `0` (all) | Stop after N tasks — use small numbers for testing |
| `--delay SECONDS` | `0.4` | Pause between requests |
| `--count-page-size N` | `10000` | Page size for `--count` |
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
├─ catalog.csv             one row per image: prompt, dimensions, date, IDs, filename
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
│   ├─ <prompt>_<taskid>_<mediaid>.png
│   └─ _index.csv
├─ 2026-06/
│   └─ ...
├─ images/                       (empties out as files are moved by --organize-adv)
└─ catalog.csv
```

> **Keep `catalog.csv`.** It is the source of truth for full prompt previews, dates, and dimensions. Both `--organize` and `--organize-adv` read from it — filenames are shortened and stripped of punctuation, so the catalog is the complete record.

---

## Known Issues

| Issue | Status |
|---|---|
| `promptsPreview` is truncated — full prompt, seed, and model are not available in task summaries | Requires a separate task-detail API query; on the roadmap |
| WebP metadata embedding is unreliable | `--organize-adv` and `--organize-adv-live` skip WebP; pair with `--convert png` to get embedded metadata |
| Windows MAX_PATH (260 chars) | Batch images use short names (`NN_<mediaid>.ext`) inside prompt-named folders; `--name-length` defaults to 60 |
| `--convert` only affects new downloads | Use `--convert-existing` to convert already-downloaded `.webp` files in place |
| Server errors above ~10,000 tasks per page | `--count-page-size` defaults to 10,000; lower it if you see `Internal server error` on `--count` |

---

## Changelog

### v4.2
- Download progress meter — pre-flight `_quick_count()` pass (500/page, safe page size) followed by `\r`-overwriting ASCII bar in the CLI and a `QProgressBar` + label in the GUI
- Resume-aware progress — seeds the bar from actual image files on disk via recursive glob, so a resume run opens at the correct position (`Resuming: 17234 image files already on disk`) rather than restarting at 0; works for flat, `--organize-adv`, and `--organize-adv-live` layouts
- Config and token path resolution anchored to script directory (`Path(__file__).resolve().parent`) so the GUI finds `config.json` and `token.txt` regardless of working directory
- `_make_session()` re-reads and refreshes module-level globals on every call, fixing the case where the module was imported before the working directory was set correctly
- `gql()` non-JSON error converted from `sys.exit(1)` to `raise PixAIError` so the GUI Worker catches it cleanly
- GUI Worker catches `SystemExit` as a safety net alongside `PixAIError` and `Exception`
- GUI output folder default anchored to script directory
- `pixai_gui_settings.json` added to `.gitignore`

### v4.1
- PySide6 GUI (`pixai_gui.py`) — tabbed Download / Organize / Convert / Utilities window with dark Catppuccin Mocha theme, background Worker thread, and settings persistence
- Callable API surface extracted from CLI for GUI integration: `run_download`, `run_probe`, `run_count`, `run_catalog_stats`, `cmd_rename`, `_make_session`
- `PixAIError` exception class; all `sys.exit()` in library functions replaced with raises so GUI can display clean error messages

### v4.0
- Switched to media object resolution: fetch `/v1/media/<id>` JSON and pick the `PUBLIC` (full-resolution) variant URL, replacing direct variant-URL probing
- Backward pagination (`last` / `before` / `hasPreviousPage`) with full resume support
- `--organize-adv` mode: sort into `batches/` and `YYYY-MM/` folders, per-folder `_prompt.txt` and `_index.csv`, embedded PNG/JPEG metadata
- `--organize-adv-live` mode: same folder sorting applied live as files download
- `--organize` mode: rename files to `prompt_taskid_mediaid` scheme in-place
- `--convert-existing` mode: batch-convert all `.webp` files in the backup tree; supports `--dry-run` and `--keep-webp`
- `--convert` (WebP → PNG / JPEG) with Pillow, atomic `.part` temp writes
- `--count`, `--probe`, `--catalog-stats`, `--collect-only` modes
- Apollo CSRF headers (`apollo-require-preflight`, `x-apollo-operation-name`) required on all GraphQL requests
- `truststore` integration for corporate/antivirus HTTPS interception

---

## Roadmap

- [x] **`config.json` for captured constants** — `USER_ID`, `U3T`, and `PERSISTED_QUERY_HASH` loaded from git-ignored `config.json`; `config.example.json` ships with the repo
- [ ] **Full prompt + seed + model** — capture the task-detail persisted query to store complete generation parameters (currently only the truncated preview)
- [x] **`--convert-existing`** — convert already-downloaded `.webp` files in place; supports `--dry-run`, `--keep-webp`, `--convert`, `--jpeg-quality`, `--jpeg-bg`
- [x] **Foldering during live download** — `--organize-adv-live` sorts files into batch/month folders as they download; `--organize-live` for explicit prompt-naming intent
- [ ] **Persistent catalog** — change `catalog.csv` from a per-session overwrite to a persistent, deduplicated database keyed by `media_id`; download runs update `filename` in-place so `--collect-only` can be used as a true phase-1 pre-flight and `--organize-adv` always has complete prompt data regardless of how many sessions the download took
- [ ] **`tests/` with pytest** — mocked network layer for offline testing of API logic
- [x] **GUI port** — PySide6 desktop app (`pixai_gui.py`) with tabbed layout, dark theme, background worker thread, and settings persistence

---

## License

Personal use. Not affiliated with or endorsed by PixAI.
