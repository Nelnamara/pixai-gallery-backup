# Setup

## 1. Install

Python 3.8+ (`python --version`), then:

```bash
pip install requests pillow PySide6 flask truststore
```

| Package | Needed for |
|---|---|
| `requests` | all network operations (required) |
| `pillow` | thumbnails, conversion, metadata embedding |
| `PySide6` | the desktop GUI (`pixai_gui.py`) |
| `flask` | the local web gallery (`pixai_gallery.py`) |
| `truststore` | optional — fixes HTTPS cert errors behind corporate proxies / AV |
| `cryptography` | optional — only for the gallery's `--https` mode |
| `ffmpeg` (on PATH) | optional — generates posters for backed-up/imported videos |

## 2. Configure

Copy `config.example.json` to `config.json` (git-ignored) and set **one** value:

```json
"PIXAI_API_KEY": "your-api-key"
```

That's it. Generate a key at [platform.pixai.art](https://platform.pixai.art)
(lifetime up to ~2 years) and paste it. It's the Bearer credential for **every**
call, and:

- **`USER_ID` is auto-resolved** from the key (via the `me` query) — no DevTools.
- **The persisted-query hashes ship with working defaults**, so there's nothing to
  capture from the browser.

### Why are there still "hashes" in the example file?
PixAI's public API (what your key talks to) exposes generation and model search,
but **not** the private operations that list *your own* history, fetch task detail,
or delete tasks. Those are reached by replaying PixAI's own frontend GraphQL
queries, identified by a persisted-query **hash**. These hashes are **public, not
secret, and the same for everyone** — so the tool bakes the current ones in. You
only ever touch them if PixAI overhauls their frontend and a default goes stale
(you'll get a clear `PersistedQueryNotFound` / "Cannot query field" error telling
you to recapture that one — see below). All the `config.json` hash fields are
optional overrides; leave them blank.

### Recapturing a hash (only if a default ever breaks)
1. Log in to [pixai.art](https://pixai.art), **F12 → Network**, filter `graphql`.
2. Click the relevant request (`listUserTaskSummaries` for the feed, `getTaskById`
   for full-meta, `deleteGenerationTask` for delete) → **Payload**.
3. Copy `extensions.persistedQuery.sha256Hash` into the matching `config.json` key
   (`PERSISTED_QUERY_HASH` / `TASK_DETAIL_HASH` / `DELETE_TASK_HASH`).

> No API key? A legacy browser-token path still exists (leave `PIXAI_API_KEY`
> blank, add `U3T`, supply the short-lived token via `token.txt` / `PIXAI_TOKEN` /
> `--token`, and set `USER_ID`). It expires every few hours — the API key exists to
> avoid all of this.

## 3. First run

Desktop:
```bash
python pixai_gui.py
```

Or headless:
```bash
python pixai_gallery_backup.py --probe        # connection sanity check
python pixai_gallery_backup.py --count        # how many images you have
python pixai_gallery_backup.py --max 40       # small test download
python pixai_gallery_backup.py                # download everything (parallel)
python pixai_gallery_backup.py --update       # later: grab only what's new
```

Everything lands in `pixai_backup/` (git-ignored): `images/`, `catalog.db`,
`raw_tasks.jsonl`, and (once organized) `YYYY-MM/` month folders.

When PixAI changes their frontend you may see `PersistedQueryNotFound` or
"Cannot query field" — just recapture the relevant hash the same way.
