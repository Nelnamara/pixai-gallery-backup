# CLAUDE.md — Project Context for Claude Code

This file is committed so it is available on every machine that clones the repo.

---

## What this project is

**Moonglade Athenaeum** — *"a library against the Void."* A Python CLI (`pixai_gallery_backup.py`) with a PySide6 GUI (`pixai_gui.py`) and local Flask gallery (`pixai_gallery.py`). It began as a backup tool for the **owner's own** PixAI.art generations and grew into a full local PixAI **client**: back up · browse · generate · curate. Talks to the same API the browser uses, pages the entire history at full resolution, keeps a searchable SQLite catalog, **creates** new images via the API, and manages both the local archive and the cloud account.

Built by reverse-engineering site network traffic (catalogued privately in `private/API_OPERATIONS.md`, git-ignored). The `gql_adhoc()` ad-hoc POST path means most operations need no persisted-hash capture. There is no official API for listing your own generations. Be polite to their servers (paced requests). PixAI's terms grant users copyright of their generations. User-facing docs live in `docs/`.

---

## Working across machines (home ⇄ work) — READ THIS FIRST

This repo is edited from more than one machine. Cross-machine breakage here is almost
always **config drift, not real changes** — do not blame the user, do not "fix" it with a
mass commit. Follow this protocol:

1. **Line endings are pinned by `.gitattributes`** (LF in the repo). Do NOT change
   `core.autocrlf`, do NOT run line-ending "fixes", do NOT commit a mass line-ending diff.
   If `git status` shows *every* file modified, STOP — that's line-ending drift. Re-check
   `.gitattributes` is present and run `git add --renormalize .`; never `git checkout -- .`
   away someone's real work to make it "clean."
2. **Default working branch is `video-gen`** (until merged to master). `git checkout video-gen`
   before doing anything. Do not start committing on `master`.
3. **Pull before you start, push when you stop:** `git pull --rebase --no-edit` at session
   start, `git push` at session end. This is what prevents "updates were rejected" /
   divergence. If push is rejected, it's the remote moving — pull --rebase, then push.
4. **Never `git add -A` / `git add .`** — stray untracked files live here (`config.json`,
   `.coverage`, `design_refs/`, old `pixai_*.py` side scripts). Stage **explicit paths** only.
5. **`config.json` + `private/` are git-ignored and machine-local** — they will NOT be on the
   other machine, and that's correct. Don't recreate, commit, or complain about their absence.
6. **Commits: no `Co-Authored-By: Claude` trailer** (standing preference).

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
| `run_generate()` | `--generate`: create images via `createGenerationTask` (ad-hoc POST), poll, download, catalog as `source='api'`. Preview unless `--confirm`. `--task-id` recovers an already-created task for free |
| `build_video_parameters()` / `run_generate_video()` | `--generate-video`: image-to-video (`i2vPro`) — VERIFIED submit shape `{channel, i2vPro:{model,mediaId,[tailMediaId],mode,duration,generateAudio,…}}`. Preview unless `--confirm` (video is EXPENSIVE, ~27.5k credits); captures `paidCredit` as actual cost; downloads mp4 into `videos/` |
| `upload_media()` | `--upload`: local file → `media_id` via the 3-step S3 handshake (`uploadMedia` presign → PUT bytes → `uploadMedia` register). Plain mutation over `gql_adhoc`; **free**. Unblocks inpaint / Edit / LoRA "bring your own image" |
| `build_chat_edit_parameters()` / `run_edit_image()` | `--edit-image`: instruct editing via `createGenerationTask` with a `chat` block (`prompts`+`mediaId`/`mediaIds`+`modelId`+`modelConfig`). `--edit-src` takes a catalog `media_id` OR a local file (auto-uploaded on `--confirm`); repeat for multi-image reference. Preview unless `--confirm` |
| `list_kaisuukens()` / `run_cards()` | `--cards`: read-only display of free-generation tickets ("kaisuuken" / 回数券) + their ids. Fails soft (fields RE-inferred). Spend a specific card on any create run with `--kaisuuken-id <id>` (attaches `kaisuukenId`; never auto-consumes) |

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

> **Reverse-engineering detail (frontend handler flow, sibling mutations, hash-capture
> method) lives in `private/RE_NOTES.md`** — git-ignored, not public. Read it there when
> you need it.

## Verbose logging (`-v` / `--verbose`)

- `set_verbose()` + `vlog()`: timestamped diagnostics (per-page fetch, per-image resolve/download timing, startup disk-scan time) to stdout. No-op until enabled. GUI exposes it as a "Verbose logging" checkbox in the top bar (persisted in settings). NOT a full logging framework — file logging is a separate, still-open discussion.

## Recapture procedure (when PixAI changes their frontend)

Symptoms: `PersistedQueryNotFound`, "Cannot query field…", or sudden 400s. Step-by-step
recapture is in `private/RE_NOTES.md`.

---

## Creating: generate · video · edit · upload · cards

All creation rides the SAME `createGenerationTask` mutation over `gql_adhoc` (no persisted hash),
differing only in the `parameters` object. **Every credit-spending path is preview-only until
`--confirm`**, and `--task-id` recovers an already-created task for free.

- `--generate` → image (`parameters` = the image params).
- `--generate-video --image <media_id>` → i2vPro video (`parameters` = `{channel, i2vPro:{…}}`).
- `--edit-image --edit-src <media_id|file> --prompt "…"` → instruct edit (`parameters` = `{chat:{…}}`);
  local files auto-upload via `uploadMedia`; repeat `--edit-src` for multi-image reference.
- `--upload <file>` → prints a `media_id` (free; the 3-step S3 handshake).
- `--cards` → read-only free-card (kaisuuken) balances + ids; `--kaisuuken-id <id>` spends one on a run.

Deeper RE detail (submit shapes, the full app op catalog, kaisuuken/upload/edit captures, pricing) is
in git-ignored `private/GENERATOR_SURFACE.md` + `private/APP_OPERATIONS_FULL.md`.

## Test suite

229 pytest tests in `tests/`. Run with `python -m pytest`. All tests must pass before merging to master.

---

## Current state

- **Version:** `1.5.0` on `master`
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
python pixai_gallery_backup.py --organize --dry-run       # preview month-folder normalize
python pixai_gallery_backup.py --organize                 # normalize into YYYY-MM/ (reversible; --organize-adv is an alias)
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
# --- creating (all preview-only until --confirm; --task-id recovers a task for free) ---
python pixai_gallery_backup.py --account                  # read-only credits/membership dashboard
python pixai_gallery_backup.py --cards                    # read-only free-card (kaisuuken) balances + ids
python pixai_gallery_backup.py --upload path/to/image.png # local file -> media_id (free; S3 upload)
python pixai_gallery_backup.py --generate --prompt "..."               # preview an image gen (add --confirm to spend)
python pixai_gallery_backup.py --generate-video --image <media_id> --prompt "..."   # preview i2v (EXPENSIVE; --confirm)
python pixai_gallery_backup.py --edit-image --edit-src <media_id|file> --prompt "make it night"  # preview an edit
python pixai_gallery_backup.py --edit-image --edit-src img.png --prompt "..." --kaisuuken-id <id> --confirm  # spend a free card
```
