# The Gallery

A local web gallery over your whole catalog. Launch it:

```bash
python pixai_gallery.py --out pixai_backup                 # http://127.0.0.1:5000
python pixai_gallery.py --out pixai_backup --port 5757
python pixai_gallery.py --out pixai_backup --host 0.0.0.0 --https   # LAN + PWA
python pixai_gallery.py --out pixai_backup --rebuild-thumbs         # regenerate thumbnails
```

…or use the GUI **Gallery** tab (Launch/Stop, port, LAN, HTTPS). The gallery is a
read-only viewer of `catalog.db` + your files for browsing, but it can also make
authenticated API calls for prune/reconcile (see below).

## Browsing & filtering

The filter bar:
- **Prompt** — wildcard (`night*`, `a?c`) and multi-word AND search.
- **Model / Batch** — searchable dropdowns.
- **From / To** — year + month pickers.
- **Min rating**, **Tag / contest**, **LoRA**, **Published only**.
- **Media** — All / Images / Videos.
- **Source** — All / PixAI history / Generated / Imported / **Deleted on PixAI**.
- **Sort** — newest/oldest, rating, aesthetic, likes, resolution.
- Per-page selector, thumbnail-size slider, saved filter presets, privacy blur.

Cards show a ▶ badge on videos and **AI** / **local** badges by source. Videos
play inline on the detail page (with a poster). Click a card for the lightbox
(swipe, prev/next, `F`/Space slideshow); the detail page has Copy Prompt, Find
Similar, View Batch, **Edit Prompt**, and full metadata (incl. negative + clip-skip).

## Editing & curating

- **Star ratings** (0–5) per image, set inline, stored in `catalog.db`.
- **Collections** — select images → **+ Collection** → name it. Groups images into
  named collections **without moving any files**, stored in the catalog, so it
  *survives Organize* (unlike physical sub-folders). An image can be in several
  collections; filter by them via the **Collection** dropdown. The detail page
  lists an image's collections.
- **Edit Prompt** — fix/annotate a single image's prompt on its detail page.
- **Find/Replace** — bulk substring replace across selected prompts.
- **Select mode** — toggle the **Select** button for fast multi-select: tap/click an
  image to toggle it, or **drag across images to paint a selection** (mouse or touch).
  No lightbox opens while it's on, so no accidental opens — ideal on a tablet. Drag on
  the gaps to scroll, or toggle Select off to go back to normal browsing.
- **Download ZIP** — bundle the selected full-res images (selection persists across pages).

## Deleting & keeping cloud + local in sync

Two delete buttons appear when images are selected:

- **Delete (local)** — removes from your backup only (catalog rows + files). The
  cloud task is untouched.
- **Delete from PixAI** — deletes the whole **task** from your account *and*
  locally, so they never drift. **Task-level**: selecting one image deletes its
  whole batch. Irreversible on the cloud side — gated behind a confirm dialog +
  typing `DELETE`. Requires `DELETE_TASK_HASH` in config.

### Reconcile (clean up what you deleted on the website)
Deleting a task on PixAI doesn't touch your local backup (by design). To find and
prune those orphans:

1. GUI **Utilities → Reconcile Deleted** (or `--reconcile-deleted`). It pages your
   live feed (~1–2 min) and flags catalog rows whose task is gone.
2. Gallery → **Source → "Deleted on PixAI"** → select → **Delete (local)**.

It skips imports and anything generated in the last ~2 days (so a fresh generation
isn't false-flagged), and aborts if the feed comes back empty.

## Collection Health & duplicates

- **`/health`** — storage used, full-meta %, missing files, total likes,
  images-by-month, top models / LoRAs / tags, and a prompt word-cloud.
- **`/duplicates`** — review cross-folder duplicate copies side-by-side before you
  dedup (links from `/health`).

## Reviewing & deduping on disk

For filesystem-level cleanup (independent of the catalog):

```bash
python pixai_gallery_backup.py --audit            # report -> audit_report.csv
python pixai_gallery_backup.py --dedup            # dry-run plan
python pixai_gallery_backup.py --dedup --apply    # quarantine redundant copies
python pixai_gallery_backup.py --verify-dupes     # confirm quarantine is safe to delete
```

## Importing your own media

Drop non-PixAI images/videos into the backup folder and catalog them:

```bash
python pixai_gallery_backup.py --import-local          # scan the backup for dropped-in files
python pixai_gallery_backup.py --import-local <DIR>    # copy an external folder in
```

They're tagged `source='local'`, get thumbnails (videos get an ffmpeg poster if
available), and show under **Source → Imported**.
