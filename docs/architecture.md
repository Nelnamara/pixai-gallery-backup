# Architecture

Moonglade Athenaeum is three Python modules around one SQLite catalog.

```
pixai_gallery_backup.py   CLI engine: download, organize, generate, sync, delete, reconcile
pixai_gallery.py          Flask web gallery + ALL SQLite catalog helpers (the shared base)
pixai_gui.py              PySide6 desktop app: every workflow as a tab, run in background threads
```

`pixai_gallery_backup.py` and `pixai_gui.py` both import `pixai_gallery.py` for
catalog access — so catalog logic lives in exactly one place.

## How it talks to PixAI

There is **no official API for listing your own generations**, so the tool
reverse-engineers the website's traffic:

1. **Listing** — `listUserTaskSummaries` is an Apollo *persisted query* (GET): the
   client sends an `operationName` + `sha256Hash`; the query body lives on PixAI's
   server. Paginated **backward** (`{last, before, userId}`), newest page first.
2. **Media URLs** — task summaries carry `mediaId` / `batchMediaIds`, not URLs. The
   full-res URL comes from `GET /v1/media/<id>` (variant `PUBLIC`). Videos expose
   their mp4 via the GraphQL `media` object's `fileUrl`.
3. **Everything else** — `gql_adhoc()` POSTs full GraphQL query documents directly.
   PixAI accepts these under the API-key Bearer, so most operations (generate,
   delete, account, model search) need **no persisted-hash capture**. This is the
   foundation that turned a backup tool into a full client.
4. **Auth** — the official API key (`PIXAI_API_KEY`) is the Bearer credential for
   every call. HTTPS verification is always on.

## The catalog (`catalog.db`)

SQLite, one row per media, keyed by `media_id`. All I/O goes through helpers in
`pixai_gallery.py` — never raw SQL elsewhere. Schema migrations go in **three
places**: the `CATALOG_FIELDS` list, the `_CREATE_TABLE` DDL, and the
`_MIGRATIONS` list (run on every connect, so existing DBs auto-upgrade).

Notable columns: identity/timing (`media_id`, `task_id`, `filename`, `created_at`),
full meta (`prompt_full`, `seed`, `steps`, `sampler`, `cfg_scale`, `model_id/name`,
`loras`, `negative_prompt`, `clip_skip`), published-artwork data (`title`,
`is_published`, `liked_count`, `art_tags`, …), video (`is_video`,
`poster_media_id`, `video_duration`), provenance (`source` = online/api/local), and
`deleted_remote` (flagged by reconcile).

## On-disk layout

```
pixai_backup/
├─ images/            flat downloads (pre-organize)
├─ 2024-03/           organize: month folders, descriptive names
│   └─ <prompt>_<taskid>_<mediaid>.<ext>
├─ videos/            backed-up + imported videos (mp4)
├─ imported/          external media copied in via --import-local
├─ gallery/thumbs/    768px JPEG thumbnails (content-addressed, immutable cache)
├─ _duplicates/       quarantine from --dedup (reversible)
├─ organize_manifest.csv   reversible move log (--undo-organize)
├─ catalog.db         the source of truth
└─ raw_tasks.jsonl    raw task data (for re-processing)
```

**Organize** normalizes everything into `YYYY-MM/` month folders with readable
`<prompt>_<taskid>_<mediaid>` names (no batch subfolders), writing a reversible
manifest. It's idempotent, byte-safe, and dry-runnable. See the
[Backing Up](../../wiki/Backing-Up) wiki page for usage.

## Invariants (do not break)

1. **`media_id` is always the last `_`-chunk of the filename stem.** Resume,
   organize, and lookup all parse `stem.split("_")[-1]`.
2. **Resume is keyed on media id, checked before any network call.**
3. **Incomplete files don't count as done** — `.part` temp files and zero-byte
   files are treated as not-downloaded; downloads are atomic (`*.part` → replace).
4. **`catalog.db` is the source of truth** for organize and friends.
5. **Media-id → file resolution goes through one shared matcher**
   (`find_files_for_media_id`) that recognizes both naming layouts — prefixed
   `*_<mid>.*` and bare `<mid>.*`. Resume, the gallery, and the audit all share it
   so they never drift (the historical `images/`+month duplication came from a
   matcher that only knew one layout).

## Testing

185+ pytest tests in `tests/` (pure functions, filesystem, catalog, gallery
routes, mocked network). `python -m pytest`. All must pass before merging.
