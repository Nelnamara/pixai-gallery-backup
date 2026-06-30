<div align="center">

# 🌙 Moonglade Athenaeum

### *A library against the Void.*

**Back up · browse · generate · curate** — a complete local companion for **your own** [PixAI.art](https://pixai.art) work.

![Version](https://img.shields.io/github/v/release/Nelnamara/moonglade-athenaeum?color=8839ef) ![Python](https://img.shields.io/badge/python-3.8%2B-blue) ![Platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-lightgrey)

![Gallery hero](docs/img/hero.png)
<sub>*The local web gallery — your entire PixAI history, full-resolution, searchable.*</sub>

</div>

---

PixAI's site only shows a handful of images at a time and nothing older is easy to reach. Moonglade Athenaeum talks to the same API your browser uses to pull your **entire** generation history at full resolution, keeps it in a searchable SQLite catalog (prompts, seeds, models, LoRAs, dates), serves a local web gallery, **creates** new images, and helps you prune both your local archive and your cloud account — so nothing is ever lost to the Void.

📖 **[Full documentation lives in the Wiki →](../../wiki)**

---

## ⚡ Quickstart — one key, that's it

```bash
pip install requests pillow PySide6 flask truststore
```

1. Generate an API key at **[platform.pixai.art](https://platform.pixai.art)** (lifetime up to ~2 years).
2. Copy `config.example.json` to `config.json` and paste your key:
   ```json
   { "PIXAI_API_KEY": "your-key-here" }
   ```
3. Go:
   ```bash
   python pixai_gui.py                      # desktop app, or…
   python pixai_gallery_backup.py --count   # …headless: how many images you have
   python pixai_gallery_backup.py           # back up everything
   ```

That's the whole setup. Your `USER_ID` is auto-resolved from the key, and everything else has working defaults. No DevTools, no token to recapture. *([Why so simple? →](../../wiki/How-It-Works))*

---

## 🔴 Please read before you run

> [!IMPORTANT]
> **This is a personal-use tool for your *own* PixAI account.** It is built to *preserve and organize what you made* — not to game the platform. It defaults to **cheaper generation priority**, has **no credit-buying or farming automation**, and every destructive action touches **only your own account**. Please keep it that way.

> [!WARNING]
> **"Delete from PixAI" is irreversible.** It deletes the whole task from your cloud account *and* locally. It's gated behind a confirm dialog and typing `DELETE`, but there is no undo on PixAI's side.

> [!NOTE]
> **Your credentials never leave your machine.** `config.json`, `token.txt`, and your backup are git-ignored and local-only. Nothing phones home.

> [!NOTE]
> **Unofficial.** Not affiliated with or endorsed by PixAI. It uses your own API key plus PixAI's private frontend queries, so a major PixAI frontend change *can* break a feature — you'll get a clear error telling you what to recapture. PixAI's terms grant you copyright of your own generations; this tool is rate-paced to be polite to their servers.

---

## ✨ What it can do

| | |
|---|---|
| **Back up everything** | Full-resolution downloads past the gallery limit · fast parallel workers · instant incremental `--update` · deduplicated SQLite catalog · image-to-video backup · published-artwork sync |
| **Browse & search** | Local web gallery: wildcard prompt search, model/LoRA/tag/rating filters, date pickers, lightbox, ZIP export, saved views, privacy blur, mobile/PWA |
| **Generate** | Create images via the API (GUI **or** CLI): model + LoRA pickers, quality modes, aspect presets; results drop straight into your catalog |
| **Curate** | **Collections** (group images/videos without moving files) · **Select mode** with drag-paint multi-select · star ratings · inline prompt edit · bulk find/replace |
| **Stay in sync** | Bulk delete locally or cloud-side · `--reconcile-deleted` to find cloud-deleted orphans · Collection Health dashboard |

### Collections & Select mode
![Collections and select mode](docs/img/curation.png)
<sub>*Toggle **Select**, drag across images to paint a selection, then **+ Add to Collection** — files never move, and it survives Organize.*</sub>

### Generate, and watch it appear
![Generate](docs/img/generate.png)
<sub>*Model/LoRA pickers, quality modes, cheaper priority by default. New images land in the same catalog you browse.*</sub>

### Collection Health
![Collection Health](docs/img/health.png)
<sub>*Storage, full-meta coverage, duplicates, images-by-month, top models/LoRAs/tags, prompt word-cloud.*</sub>

---

## 🖥️ The desktop app

A PySide6 GUI (`pixai_gui.py`) wraps the whole workflow — Download, Generate, Organize, Convert, Utilities, and a one-click Gallery launcher — with live logs and a dark theme. Prefer the terminal? Every feature has a CLI flag. Want a double-click launcher? Use **`Moonglade Athenaeum.pyw`** (no console window).

![GUI](docs/img/gui.png)

---

## 📚 Documentation

Everything deep lives in the **[Wiki](../../wiki)**:

[Setup & Configuration](../../wiki/Setup) · [Backing Up](../../wiki/Backing-Up) · [The Gallery](../../wiki/Gallery) · [Generating Images](../../wiki/Generating) · [Collections & Curation](../../wiki/Collections) · [Deleting & Cloud Sync](../../wiki/Deleting) · [Collection Health](../../wiki/Health) · [Troubleshooting](../../wiki/Troubleshooting) · [How It Works](../../wiki/How-It-Works)

In-repo: [`docs/architecture.md`](docs/architecture.md) (contributors).

---

## Requirements

`requests` is the only hard dependency. `pillow` (thumbnails/convert), `PySide6` (GUI), `flask` (gallery), and `truststore` (HTTPS behind AV/proxies) are recommended; `ffmpeg` on PATH is optional for video posters.

```bash
pip install requests pillow PySide6 flask truststore
```

---

<div align="center">
<sub>Unofficial · personal-use · MIT-spirited. Made for Nelnamara's archive, shared in case it helps yours.</sub>
</div>
