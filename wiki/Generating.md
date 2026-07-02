# Generating images

Moonglade Athenaeum can **create** images via PixAI, not just back them up. Every
generation is downloaded into your backup and catalogued as `source='api'`, so it
appears in the gallery alongside your history.

> **Generation spends PixAI credits.** Downloading/cataloging is free; the generation
> is the paid part. The tool **previews unless you explicitly confirm**, and defaults
> to the cheaper priority.

## In the GUI (recommended)

The **Generate** tab:

| Control | Maps to | Notes |
|---|---|---|
| **Prompt** / **Negative** | `prompts` / `negativePrompts` | natural language is fine |
| **Model** dropdown | `modelId` | pre-filled with models **you've used** (valid version ids), most-used first |
| **Search PixAI…** | — | search the catalog; resolves the correct *version* id automatically |
| **LoRAs** → Add LoRA… | `lora` + `loraParameters` | search → pick → weight; stack several |
| **Aspect** + ⇄ Swap | `width`/`height` | presets at SDXL-friendly dims |
| Width / Height / Steps / CFG / Count / Seed | the obvious params | blank seed = random; dims rounded to /8 |
| **Mode** | `inferenceProfile` | Auto (default) · Lite · Standard · Pro · Ultra |
| **Prompt helper** | `promptHelper` | on by default; uncheck to use your prompt literally |
| **High priority** | `priority` | off (500, cheaper) by default; on = 1000 (faster, more credits) |
| **Confirm** | — | **required** to actually submit and spend credits |

Click **Generate** and watch the log: `Generated + cataloged N image(s)`.

### The model-vs-version-id gotcha
`createGenerationTask` needs a model's **version id**, not its model id. A model page
URL (`pixai.art/model/<id>`) gives the *model* id, which generation rejects
("Invalid modelId"). The dropdown and **Search PixAI…** hand you the correct version
id — prefer those.

### Modes are model-specific
Lite/Standard suit older SD models; Pro/Ultra are for newer types. Picking an
unsupported mode is harmless — the tool **auto-falls-back** to the model's default (a
rejected submit costs no credits) and generates anyway.

### LoRAs are add-ons, not base models
A LoRA can't be the **base** model. The base picker excludes LoRAs; add them via the
**LoRAs** row.

## On the CLI

```bash
# preview only (no credits):
python pixai_gallery_backup.py --generate --prompt "a night elf druid, moonlit grove"

# really generate (spends credits):
python pixai_gallery_backup.py --generate --confirm \
    --prompt "..." --negative "lowres, text" \
    --model 1983308862240288769 --batch-size 1 \
    --mode standard --lora 1686550608832816741:0.7

# find model / LoRA version ids:
python pixai_gallery_backup.py --list-models "anime"

# recover an already-created task by id (no new credits):
python pixai_gallery_backup.py --generate --task-id <id>
```

| Flag | Default | Meaning |
|---|---|---|
| `--prompt` / `--negative` | — | the prompts |
| `--model` | Tsubaki.2 | model **version** id |
| `--lora VERSIONID:WEIGHT` | — | repeatable |
| `--mode` | `auto` | `auto`/`lite`/`standard`/`pro`/`ultra` |
| `--priority` / `--high-priority` | `500` | 500 = standard (cheaper), 1000 = high |
| `--no-prompt-helper` | off | use the prompt literally |
| `--width`/`--height`/`--steps`/`--cfg`/`--batch-size`/`--seed` | 512/512/25/7/1/random | |
| `--confirm` | off | **required** to spend credits |
| `--task-id` | — | fetch/catalog an existing task instead of creating one |

Generated images are tagged `source='api'` — filter to them in the gallery via
**Source → Generated**.

---

## Animate an image → video (`--generate-video`)

Turn any catalog image into a short clip (image-to-video). Same preview/confirm safety —
but **video is expensive** (a V4.0 5-second clip is ~27,500 credits, ~50–100× an image),
so the preview shouts the cost, and the actual charge is read back from the server
(`paidCredit`) after it runs. Clips download into `videos/` and catalog as `is_video`.

**GUI:** the **Video** tab — paste a source image `media_id`, pick model / duration
(5/10/15s; 15 is V4.0-only) / mode (Basic cheaper, Professional = Plus), optional audio,
optional **End frame id** for first/last-frame interpolation, then Confirm.

```bash
# preview (free): prints the exact request + the ~credit cost
python pixai_gallery_backup.py --generate-video --image <media_id> --prompt "she turns slowly toward camera"
# really animate (EXPENSIVE — spends credits):
python pixai_gallery_backup.py --generate-video --image <media_id> --prompt "..." \
    --video-model v4.0.1 --duration 5 --video-mode professional --confirm
# recover a finished clip for free:
python pixai_gallery_backup.py --generate-video --task-id <id>
```

## Edit an image with words (`--edit-image`)

Describe a change and let PixAI's Edit model apply it — "make it nighttime", "add a hat".
Source can be a **catalog `media_id`** or a **local file** (uploaded automatically); pass
`--edit-src` more than once for multi-image reference. Results catalog as `source='api'`.

**GUI:** the **Edit** tab — put media_id(s) or a file path in *Source*, or click **Browse…**
to pick a local image; type the change; set resolution/aspect/quality; Confirm.

```bash
# preview (free; local files show as placeholders, nothing uploads):
python pixai_gallery_backup.py --edit-image --edit-src <media_id> --prompt "make it nighttime, add snow"
# edit a LOCAL image (uploads it, then edits) — spends credits:
python pixai_gallery_backup.py --edit-image --edit-src "C:\pics\her.png" --prompt "..." --confirm
```

## Upload a local image (`--upload`)

Get a reusable `media_id` for any local file — **free**. Useful to pre-upload once and
reuse the id across edit/video runs.

```bash
python pixai_gallery_backup.py --upload "C:\pics\her.png"     # prints: Uploaded media_id: <id>
```

## Free cards (`--cards` / `--kaisuuken-id`)

PixAI grants free-generation tickets — **kaisuuken** (回数券, "ticket book") — through
membership and events. When one matches your run, it's free instead of charging credits.

```bash
python pixai_gallery_backup.py --cards        # read-only: your cards + their ids and balances
# spend a specific card on a run (instead of credits):
python pixai_gallery_backup.py --edit-image --edit-src <media_id> --prompt "..." --kaisuuken-id <id> --confirm
```

The tool **never auto-spends a card** — you pass a specific id from `--cards`, still behind
`--confirm`.
