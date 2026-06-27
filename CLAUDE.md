# CLAUDE.md — Project Context for Claude Code

This file is committed so it is available on every machine that clones the repo.

---

## What this project is

A Python CLI (`pixai_gallery_backup.py`) with an optional PySide6 GUI (`pixai_gui.py`) and local Flask gallery (`pixai_gallery.py`) that backs up the **owner's own** PixAI.art generated images at full resolution. PixAI's UI only shows 20 images at a time; this talks to the same API the browser uses, pages through the entire generation history, downloads every image, and keeps a fully searchable SQLite catalog.

Built by reverse-engineering site network traffic. There is no official PixAI API for listing your own generations. Be polite to their servers (paced requests). PixAI's terms grant users copyright of their generations.

---

## Architecture / request flow

1. **Listing query is an Apollo persisted query (GET).** The site sends `operationName` + a `sha256Hash`; the query body lives on PixAI's server. Constants (`OPERATION_NAME`, `PERSISTED_QUERY_HASH`, `U3T`, `USER_ID`) are captured from the browser and stored in git-ignored `config.json`.

2. **It's a GET** with `operation`, `u3t`, `operationName`, `variables`, and `extensions` (carrying the persisted hash) as URL query params. See `gql()`.

3. **Apollo CSRF headers required** on that GET: `apollo-require-preflight: true` and `x-apollo-operation-name`. Set on the session in `main()`.

4. **Pagination is BACKWARD.** Variables are `{last, before, userId}`. Start with no `before` (newest page), follow `pageInfo.startCursor` → `before` while `hasPreviousPage` is true.

5. **Task summaries contain `mediaId` + `batchMediaIds`, NOT image URLs.** To get a URL, fetch `https://api.pixai.art/v1/media/<mediaId>`. Its `urls` list has variants; full-resolution is `variant: "PUBLIC"`. See `resolve_media()`.

6. **Auth** is a Bearer token (JWT) from the logged-in browser. Via `PIXAI_TOKEN` env, `token.txt`, or `--token`. HTTPS verification always on.

7. **SSL trust store**: `truststore.inject_into_ssl()` called at import if present — fixes corporate/antivirus HTTPS interception.

---

## Three-file architecture

| File | Role |
|---|---|
| `pixai_gallery_backup.py` | CLI downloader: download, organize, backfill, catalog stats |
| `pixai_gallery.py` | Flask gallery server + ALL SQLite catalog helpers; imported by both other files |
| `pixai_gui.py` | PySide6 GUI wrapping CLI commands in background Worker threads |

### Key functions in `pixai_gallery_backup.py`

| Function | Role |
|---|---|
| `gql()` | Replay persisted GraphQL GET; retry/backoff; surfaces errors clearly |
| `find_connection()` | Schema-agnostic: walks JSON for Relay connection (`edges`+`pageInfo`) |
| `media_ids_for()` | `mediaId` + `batchMediaIds` for a task node |
| `extract_meta()` | Pulls `id`, `createdAt`, `promptsPreview`, `status` |
| `resolve_media()` | Fetch media object, pick the `PUBLIC` full-res URL |
| `download()` | Stream to disk with resume + retries; optional convert |
| `convert_image()` | WebP→PNG/JPEG via Pillow; flattens alpha for JPEG |
| `embed_metadata()` | Write prompt/IDs/date into PNG text chunks or JPEG EXIF |
| `build_stem_name()` | Filesystem-safe names from prompt |
| `already_downloaded()` | Resume check: rglob the whole tree for `*_<mediaId>.*` |
| `cmd_organize()` | Sort flat files into `batches/` and `YYYY-MM/`; writes batch name to catalog |
| `_ensure_db()` | Auto-migrates catalog.csv → catalog.db if needed; used by all commands |
| `audit_collection()` | Filesystem-truth duplicate audit: Class A (same media_id in >1 folder) + Class B (byte-identical, different id via size-bucketed hashing) |
| `cmd_audit()` / `cmd_dedup()` | Read-only report / quarantine-or-delete redundant copies, keep most-organized, reconcile catalog |
| `reconcile_catalog_with_disk()` | Repoint each catalog row's filename/batch at the surviving on-disk file |
| `delete_task_gql()` | Replay the `deleteGenerationTask` persisted **mutation** (POST, not the GET listing path). VOID mutation: returns `null` on success, raises on error. Single-attempt — no retry, so a flaky network can't double-fire a delete |
| `run_delete_tasks()` | Guarded `--delete-task` driver: dry-run by default, `--apply` + typed `delete` confirm (or `--yes`), counts deleted/failed. Leaves local files + `catalog.db` untouched |
| `vlog()` / `set_verbose()` | `-v/--verbose` diagnostics: timestamped per-page / per-image / download timing to stdout (the GUI log pane captures it). No-op until enabled |
| `gql_adhoc()` | Generic ad-hoc GraphQL **POST** (full query document, no persisted hash). Works for queries AND mutations under the API-key Bearer. The foundation for client ops beyond the reverse-engineered listing path; `media_file_gql` + `account_info` use it. Raises `PixAIError` on GraphQL/HTTP error |
| `account_info()` / `run_account_info()` | Read-only account dashboard (credits/membership/subscription) via `gql_adhoc`. **Never moves money** — no payment/subscription mutations are implemented, by design |

### Key helpers in `pixai_gallery.py`

| Symbol | Role |
|---|---|
| `CATALOG_FIELDS` | Single source of truth for all column names |
| `_IMAGE_EXTS` | Single source of truth for image extensions — import this, never redefine |
| `_MIGRATIONS` | List of ALTER TABLE statements run on every `_connect()` call — add new columns here |
| `init_db()` | Creates table + runs migrations; called at gallery startup and by `save_catalog` |
| `save_catalog()` | Upsert list of dicts; calls `init_db` first |
| `query_catalog()` | SQL-backed filter/sort/paginate for gallery index |
| `list_media_ids()` | Returns ordered media IDs for prev/next navigation |
| `backfill_batches()` | Scans `batches/` on disk and fills empty `batch` column; called on gallery startup |
| `media_id_of()` | Canonical media_id from a path (last `_`-chunk of stem). INVARIANT 1, single source |
| `find_files_for_media_id()` | SHARED matcher: all on-disk files for a media_id, BOTH layouts (prefixed `*_<mid>.*` AND bare `<mid>.*`), exact-id checked, gallery excluded. Resume, gallery, and audit all use it so they never drift apart |
| `create_app()` | Flask app factory; calls `init_db` + `backfill_batches` before serving |

---

## Catalog / SQLite

- **File:** `catalog.db` (SQLite), stored in `out_dir/`. Auto-migrated from `catalog.csv` on first run.
- **All catalog I/O** goes through helpers in `pixai_gallery.py` — never raw SQL elsewhere.
- **Schema migrations:** new columns go in THREE places: `CATALOG_FIELDS` list, `_CREATE_TABLE` DDL, and `_MIGRATIONS` list. The `_MIGRATIONS` list runs on every `_connect()` so existing DBs get the column automatically.
- **`_IMAGE_EXTS`:** defined once in `pixai_gallery.py`, imported by `pixai_gallery_backup.py`. Never redefine locally.

---

## GUI module cache

`pixai_gui.py` imports `pixai_gallery` at module level. Changes to `pixai_gallery.py` require a **full GUI restart** (close and reopen the app) — stopping and restarting just the gallery server thread is not enough to reload the Python module.

---

## INVARIANTS — do not break

1. **`media_id` is always the last `_`-delimited chunk of the filename stem.** Resume, `--organize`, and catalog lookup all parse it as `stem.split("_")[-1]`. Never append anything after the media id.

2. **Resume is keyed on media id, checked BEFORE any network call.** `already_downloaded(out, mid)` runs before `resolve_media()`/`download()`. Keep that order.

3. **Incomplete files must not count as done.** `.part` temp files and zero-byte files are treated as not-downloaded. Downloads write to `*.part` then atomically `replace()` the final name.

4. **`catalog.db` is the source of truth** for `--organize` and related commands. Don't make those modes depend on re-querying the API.

5. **`--organize` only moves flat files in `images/`** (non-recursive glob). This makes it idempotent. Do not switch to rglob.

6. **`find_image_file` excludes `out_dir/gallery/`** to prevent thumbnails from being returned as full-res images.

7. **Media-id → file resolution goes through `find_files_for_media_id` ONLY.** It matches BOTH naming layouts — prefixed `*_<mid>.*` (flat/batch) and bare `<mid>.*` (single-image month files). Resume (`already_downloaded`), the gallery (`find_image_file`), and the audit all share it. Never reintroduce a `*_<mid>.*`-only glob: that mismatch (bare month files invisible to resume) is exactly what caused the historical images/+month duplication — re-downloads recreated flat copies that organize then orphaned.

---

## Critical constraints

- **NEVER** append `Co-Authored-By: Claude` trailers to commits.
- **NEVER** commit `config.json` — git-ignored; contains real user credentials (USER_ID, U3T, hashes).
- `token.txt`, `pixai_backup/`, `*.webp` are also git-ignored.
- No real credentials or user-specific values should appear in any committed file.
- All traffic is HTTPS with verification on; do not add `verify=False` anywhere.
- **Server page-size cap:** `last` above ~8,000–10,000 triggers a Prisma `Internal server error`. Keep download `--page-size` ≤ ~8,000.

---

## Security & GitHub hygiene

- `config.json` is git-ignored and will never be committed.
- `config.example.json` (committed) shows the required structure with placeholder values only.
- The output folder (`pixai_backup/`) contains images, prompts, and catalog — git-ignored.
- The repo is public on GitHub at `Nelnamara/pixai-gallery-backup`.

---

## Deleting tasks from your account (`--delete-task`)

- `deleteGenerationTask` is a persisted **mutation** sent by POST (Apollo blocks mutations over GET), unlike the GET listing/query path. It is a **void mutation: it returns `null` on success** — the meaningful signal is the ABSENCE of a GraphQL error, NOT the payload. (Verified against a real task via the site, which shows a "Task has been deleted" toast off that same null/no-error response. `getTaskById` is NOT a valid post-delete existence check — it still resolves deleted tasks.)
- Hash lives in `config.json` as `DELETE_TASK_HASH` with **no built-in default** — capturing it is a deliberate manual step so deletion can't fire without explicit setup.
- Guards: dry-run by default; `--apply` to perform; typed `delete` confirmation unless `--yes` (refused on non-interactive stdin). Single-attempt per task.
- Deletes ONLY the cloud generation; local image files + `catalog.db` are left intact.

### Frontend handler (reverse-engineered from the site bundle, 2026-06-25)

Located via DevTools → Network → the `deleteGenerationTask` request → **Initiator** tab → "Request call stack". Ignoring the `vendor-apollo` / `react-aria` frames, the app-code frames point to async function **`G`** in the bundle chunk **`ChatWorkspaceCreatorButton-B3KFiFFE.js`** (a misleadingly named chunk that bundles the task-action buttons together with chat-workspace components). Click chain: React click → react-aria `onClick`→`onPress` → `Button` → `G` → a GraphQL util wrapper (`utils-Dbi0XznS.js`) → Apollo `mutate`.

`G(task, opts)` does, in order:
1. `O()` resolves the task object (`getTask` if given an id string).
2. Shows a confirm modal (`M.confirm`, i18n keys `artwork:delete-task.confirm-dialog.*`) **unless `opts.skipConfirm`** is set — the site's own equivalent of our `--yes`. A red "a favorite will be deleted" warning is shown if the task is favorited.
3. Calls **`ke({ taskId })`** — this IS `deleteGenerationTask` (imported `o as ke` from `task-B1n13Pzx.js`).
4. On no-throw: `p.success("artwork:delete-task.notice.success")` → the **"Task has been deleted"** toast. On throw: `p.error("artwork:delete-task.notice.fail (…)")`.

Why this matters for our tool:
- **Success = the awaited mutation didn't throw; the null return value is ignored.** The official client shows the success toast regardless of payload — *identical* to `delete_task_gql` (raise on error, else success). This is the authoritative confirmation that null == success, and it retroactively validates the fix.
- The toast is **purely client-side** (`p` = a react-hot-toast-style lib with `.success/.error/.info`; text via i18next keys). There is **no network notification** for it; `listNotifications` is unrelated (that's the bell-icon / `NEWS` feed).

### Sibling task mutations (same chunk; all imported from `task-B1n13Pzx.js`) — NOT yet implemented

Candidates for future tool commands. Capture each persisted hash the same way as `DELETE_TASK_HASH`:

| Action | Frontend call | Notes |
|---|---|---|
| Delete ONE image from a batch | `deleteBatchMedia` — `fe({ id: taskId, input: { deleteBatchMedia: { mediaId } } })` (handler `ve()`) | removes a single media, not the whole task |
| Cancel a running generation | `he({ taskId })` (handler `Ye`) | toast "Task cancelled" |
| Rerun a task | `we({ taskId })` (handler `aa`) | toast `artwork:task.rerun.success` |

### Repeatable method to capture a new mutation

1. Perform the action on the site with DevTools → Network open (filter `graphql`).
2. Click the request → **Payload**: copy `operationName`, `variables`, and `extensions.persistedQuery.sha256Hash` into `config.json`.
3. (Optional, to understand the flow) Click the request → **Initiator** → "Request call stack" → open the named app chunk in **Sources** → pretty-print (`{}`) → read the handler's success/error branches.

## Verbose logging (`-v` / `--verbose`)

- `set_verbose()` + `vlog()`: timestamped diagnostics (per-page fetch, per-image resolve/download timing, startup disk-scan time) to stdout. No-op until enabled. GUI exposes it as a "Verbose logging" checkbox in the top bar (persisted in settings). NOT a full logging framework — file logging is a separate, still-open discussion.

## Recapture procedure (when PixAI changes their frontend)

Symptoms: `PersistedQueryNotFound`, "Cannot query field…", or sudden 400s.

Fix: DevTools → Network → filter `graphql` → click `listUserTaskSummaries` row → Payload tab. Copy `u3t`, `userId` from `variables`, and `sha256Hash` from `extensions.persistedQuery` into `config.json`. For full meta: capture `getTaskById` and `getGenerationModelByVersionId` hashes similarly.

---

## Test suite

181 pytest tests in `tests/`. Run with `python -m pytest`. All tests must pass before merging to master.

---

## Current state

- **Version:** `1.4.4` on `master`
- **Branch strategy:** feature branches, merge to master with `--no-ff`, tag releases
- **Owner:** Nelnamara / Kil'jaeden — Balance Druid, WoW addon dev

---

## Quick command reference

```
python pixai_gallery_backup.py --probe                    # connection sanity check
python pixai_gallery_backup.py --count                    # tally tasks + images
python pixai_gallery_backup.py --max 40                   # small test download
python pixai_gallery_backup.py                            # full download (4 workers, 250/page)
python pixai_gallery_backup.py --update                   # fast incremental: stop at already-downloaded history
python pixai_gallery_backup.py --update --workers 8       # incremental + higher concurrency
python pixai_gallery_backup.py --workers 8 --page-size 500  # fast full backfill
python pixai_gallery_backup.py --full-meta                # download + full prompt/seed/model
python pixai_gallery_backup.py --backfill-full-meta       # fill existing rows
python pixai_gallery_backup.py --organize-adv --dry-run   # preview folder sort
python pixai_gallery_backup.py --organize-adv             # sort into batches/ + YYYY-MM/
python pixai_gallery_backup.py --catalog-stats            # summarize catalog.db
python pixai_gallery_backup.py --export-csv               # export catalog.db → CSV
python pixai_gallery_backup.py --sync-artworks            # merge published-artwork metadata (title/likes/tags) by media_id
python pixai_gallery_backup.py --audit                    # read-only duplicate report → audit_report.csv
python pixai_gallery_backup.py --audit --no-content       # fast: same-media_id location dupes only
python pixai_gallery_backup.py --dedup                    # dry-run dedup plan (nothing changes)
python pixai_gallery_backup.py --dedup --apply            # quarantine redundant copies to _duplicates/
python pixai_gallery_backup.py --dedup --apply --dedup-delete  # delete instead of quarantine
python pixai_gallery_backup.py --verify-dupes             # confirm _duplicates/ is safe to delete
python pixai_gallery.py --out pixai_backup                # launch gallery at :5000 (+ /health dashboard)
python pixai_gallery_backup.py --delete-task <id> [<id> ...]        # DRY-RUN: list what would be deleted (nothing happens)
python pixai_gallery_backup.py --delete-task <id> --apply --yes     # actually delete from your account (irreversible; null=success)
python pixai_gallery_backup.py -v --update                # verbose: per-page / per-image timing diagnostics
```
