# How It Works

Three Python modules around one SQLite catalog.

```
pixai_gallery_backup.py   CLI engine: download, organize, generate, sync, delete, reconcile
pixai_gallery.py          Flask web gallery + ALL SQLite catalog helpers (the shared base)
pixai_gui.py              PySide6 desktop app: every workflow as a tab, background threads
```

Both the engine and the GUI import `pixai_gallery.py` for catalog access — so
catalog logic lives in exactly one place.

## How it talks to PixAI — and why setup is just one key

PixAI has no official public API for managing your own work, so some operations
(listing your history, task detail, delete) reuse PixAI's own frontend GraphQL
queries, each identified by a `sha256Hash`. The practical upshot:

- **Your API key is the only credential.** Your `USER_ID` is auto-resolved from it,
  and the persisted-query hashes ship with working defaults — so setup is just the key.
- **The hashes are not secrets.** They identify PixAI's own frontend operations and
  rarely change. If a PixAI frontend update ever breaks one, you'll get a clear error
  and can update that one value — see [Troubleshooting](Troubleshooting).
- **The legacy browser token is retired** — only a fallback for users without an API key.

## Media URLs
Task summaries carry `mediaId` / `batchMediaIds`, not URLs. Full-res comes from
`GET /v1/media/<id>` (variant `PUBLIC`). Videos expose their mp4 via the GraphQL
`media` object's `fileUrl` (REST returns an empty `urls[]` for videos).

## The catalog (`catalog.db`)
SQLite, one row per media, keyed by `media_id`. All I/O goes through helpers in
`pixai_gallery.py`. Schema migrations live in **three places**: `CATALOG_FIELDS`,
the `_CREATE_TABLE` DDL, and `_MIGRATIONS` (run on every connect, so existing DBs
auto-upgrade). Columns span identity/timing, full meta (prompt/seed/steps/sampler/
cfg/model/loras/negative/clip-skip), published-artwork data, video fields, `source`
(online/api/local), and `deleted_remote`.

## On-disk layout
```
pixai_backup/
├─ images/            flat downloads (pre-organize)
├─ 2024-03/           organize: month folders, descriptive names
├─ videos/  imported/ backed-up + imported media
├─ gallery/thumbs/    768px JPEG thumbnails (immutable cache)
├─ _duplicates/       quarantine from --dedup (reversible)
├─ organize_manifest.csv   reversible move log (--undo-organize)
├─ catalog.db         the source of truth
└─ raw_tasks.jsonl    raw task data
```

## Invariants (don't break)
1. **`media_id` is the last `_`-chunk of the filename stem.**
2. **Resume is keyed on media id, checked before any network call.**
3. **Incomplete/zero-byte files don't count as done**; downloads are atomic (`*.part` → replace).
4. **`catalog.db` is the source of truth.**
5. **One shared media-id → file matcher** (`find_files_for_media_id`) recognizes both
   naming layouts, so resume / gallery / audit never drift.

## Testing
195 pytest tests in `tests/` (pure, filesystem, catalog, gallery routes, mocked
network, embedded-JS syntax). `python -m pytest`. All must pass before merging.
