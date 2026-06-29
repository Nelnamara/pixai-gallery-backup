# The Gallery

A local web gallery over your whole catalog.

```bash
python pixai_gallery.py --out pixai_backup                 # http://127.0.0.1:5000
python pixai_gallery.py --out pixai_backup --port 5757
python pixai_gallery.py --out pixai_backup --host 0.0.0.0 --https   # LAN + PWA
python pixai_gallery.py --out pixai_backup --rebuild-thumbs         # regenerate thumbnails
```

…or use the GUI **Gallery** tab (Launch/Stop, port, LAN, HTTPS). It's a viewer of
`catalog.db` + your files, but can also make authenticated API calls for prune /
reconcile (see [Deleting & Sync](Deleting)).

## Browsing & filtering

The filter bar:
- **Prompt** — wildcard (`night*`, `a?c`) and multi-word AND search.
- **Model / Batch** — searchable dropdowns.
- **From / To** — year + month pickers.
- **Min rating**, **Tag / contest**, **LoRA**, **Published only**.
- **Media** — All / Images / Videos.
- **Source** — All / PixAI history / Generated / Imported / **Deleted on PixAI**.
- **Collection** — filter to a named [collection](Collections).
- **Sort** — newest/oldest, rating, aesthetic, likes, resolution.
- Per-page selector, thumbnail-size slider, saved filter presets, privacy blur.

Cards show a ▶ badge on videos and **AI** / **local** badges by source. **Videos play
right in the lightbox** (and on the detail page), so you can browse a mixed grid of
images and videos with the arrow keys without leaving the overlay.

## The lightbox & detail page

- **Click an image** → the lightbox overlay: swipe / `←` `→` to browse, `F`/Space
  slideshow, `Esc` or ✕ to close. Arrow keys **roll over page boundaries** — reach the
  end of a page and it loads the next one, continuing seamlessly. Closing leaves your
  scroll and selections intact.
- **Detail page** (via the lightbox's *Details*, or by clicking a video): full
  metadata (incl. negative + clip-skip), Copy Prompt, Find Similar, View Batch, Edit
  Prompt. Keys: `←` `→` prev-next, **`Esc` / `↑` back to gallery**, `F` focus mode.

Scroll position and your selections are preserved when you open an image and come
back (even via the browser Back button).

## Editing & curating

- **Star ratings** (0–5) per image, inline, stored in `catalog.db`.
- **Edit Prompt** — fix/annotate a single image's prompt on its detail page.
- **Find/Replace** — bulk substring replace across selected prompts.
- **Download ZIP** — bundle the selected full-res images (selection persists across pages).
- **[Collections](Collections)** and **Select mode** — see that page.
