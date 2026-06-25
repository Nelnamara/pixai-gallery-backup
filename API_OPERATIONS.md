# PixAI GraphQL API — Operations Catalog

Reverse-engineered from the site bundle `graphql-C43wChcJ.js` (full operation
documents) + `task-B1n13Pzx.js` (task hooks), 2026-06-25. This is the map for
turning the backup tool into a full PixAI client.

## How to call these

**Key fact:** the client bundle contains the *full* GraphQL query/mutation
documents (not just persisted hashes). PixAI's endpoint accepts **ad-hoc
queries** under Bearer auth — `media_file_gql()` already does this. So for any
operation below we can POST `{ operationName, query, variables }` directly; we do
**not** need to capture a persisted `sha256Hash` per operation. (Persisted-query
GET is just an optimization the site uses; ad-hoc POST works for our API key.)

- Endpoint: `https://api.pixai.art/graphql`
- Auth: `Authorization: Bearer <PIXAI_API_KEY>` (confirmed: the key has read AND
  write/delete permission).
- Mutations must be POST. Reuse the existing `_make_session()` + a generic
  `gql_adhoc(session, operationName, query, variables)` helper (to build).

Status legend: ✅ in use · 🟡 have hash, not wired · 🔵 home-machine work · ⬜ not yet built

---

## Create / generate  ⭐ (the headline feature)

| Operation | Type | Variables | Enables |
|---|---|---|---|
| `createGenerationTask` | mutation | `parameters: JSONObject!` → returns `TaskDetail` | **Submit a txt2img/img2img/upscale/etc. job.** 🔵 home |
| `cancelGenerationTask` | mutation | `id` (`taskId`), `reason` | cancel a running job |
| `rerunGenerationTask` | mutation | `id` (`taskId`) | re-run a task |
| `updateGenerationTask` | mutation | `id`, `input: UpdateGenerationTaskInput!` | edit a task |
| `createArtworkFromTaskV2` | mutation | `taskId`, `input: CreateArtworkFromTaskInput!` | publish a generation as an artwork |
| `createTrainingTask` | mutation | `input: CreateTrainingTaskInput!` | train a LoRA |

**Generation parameter shape** (the `parameters` JSONObject) — from the
`TaskBaseTyped.typedParameters` fields: `priority, model, modelVersionId,
workflowId, batchSize, animateDiff, i2vPro, referenceVideo, t2i2v, chat, upscale,
enlarge, mediaId`, plus prompts/seed/sampler/cfg in the raw `parameters`. Default
fields come from `taskParams-DD7wjR4a.js`. **To finalize: capture one real
`createGenerationTask` request payload** (or reuse the home-machine version) for
the exact JSON.

**Completion detection** — two options, both visible in the bundle:
1. **Poll** `getTaskById` every ~5 s while `status ∈ {running, waiting}`, stop on
   `{completed, failed, cancelled}`. This is exactly what the site does
   (`task-B1n13Pzx.js`, `O = 5e3`). Simplest for a CLI.
2. **Subscribe** (WebSocket) — see Real-time below.

---

## Real-time events (GraphQL subscriptions / WebSocket)

This is the answer to the earlier "where do async events live" question.

| Operation | Type | Pushes |
|---|---|---|
| `subscribePersonalEvents` | subscription | `taskUpdated` (full `TaskDetail`) + `newNotification` (`NotificationBase`) |
| `subscribeGeneratorPreviewEvents` | subscription | `generationPreview { taskId, images }` — **live preview frames while rendering** |
| `subscribeWorkflowEvents` | subscription | `workflowProgress(taskId)` |

Subscriptions ride a `graphql-ws` WebSocket. Heavier to implement in Python than
polling; only worth it for live-preview or a long-running daemon. For a CLI,
polling (above) is enough.

---

## Manage existing tasks / images

| Operation | Type | Variables | Notes |
|---|---|---|---|
| `deleteGenerationTask` | mutation | `id` (`taskId`) | ✅ our `--delete-task`. Void mutation, null=success |
| `getTaskById` | query | `id` → `TaskDetail` (stages, outputs, artworks) | ✅ full-meta |
| `listUserTaskSummaries` | query | `userId`, paging | ✅ the backup listing |
| `listMyTasks` / `listMyTasksTyped` | query | `status`, paging, `parameterFields` | own tasks; *Typed* exposes model/batch/etc. |
| `listUserTasks` / `…Typed` | query | `userId`, `status`, `keyword`, `createdAt`, `workflowId` | richer filtering than the summary feed |
| `getMyTaskStats` | query | — → `runningTaskCount, waitingTaskCount` | queue status |
| `deleteBatchMedia` (via `upsert… input`) | mutation | `{ id: taskId, input: { deleteBatchMedia: { mediaId } } }` | delete ONE image from a batch (handler `ve`) |

---

## Account, credits & membership

| Operation | Type | Returns / does |
|---|---|---|
| `getMyQuota` | query | `me.quotaAmount` — **your credit balance** |
| `getMeWithQuotaForCurrency` | query | quota for a currency (`free`/`paid`) |
| `getUserQuota` | query | `total / free / paid` aliases |
| `listMyQuotaLogs` / `listUserQuotaLogs` | query | credit transaction history |
| `getMyMembership` | query | `me.membership` + `subscription` (tier, status) |
| `getMyInfo` | query | `me` (full `UserDetail`) |
| `getAllPaymentItems` | query | plans/pricing |
| `intentToPay` / `cancelSubscription` / `resumeSubscription` / `cancelOrder` | mutation | **money movement — do NOT call automatically** |

---

## Models & LoRAs (for picking models when generating)

| Operation | Type | Use |
|---|---|---|
| `listGenerationModels` | query | browse/search models; filter by `type`/`types` (`GenerationModelType` enum: `SDXL_MODEL`, `DIT7_MODEL`, `*_LORA`, `VIDEO_MODEL`, …), `keyword`, `tag`, `loraBaseModelTypes` |
| `listGenerationModelVersions` | query | versions for a `modelId` |
| `getGenerationModel` / `getGenerationModelByVersionId` | query | model detail (latter ✅ in use, `MODEL_DETAIL_HASH`) |
| `listMyBookmarkedGenerationModels` / `listUserLikedGenerationModel` | query | your saved/liked models |
| `markGenerationModel` | mutation | like/bookmark a model |
| `listLoraRecommendationsByModel` / `listSimilarLoras` | query | LoRA discovery |

---

## Published artworks (extends current `--sync-artworks`)

| Operation | Type | Use |
|---|---|---|
| `listArtworks` | query | ✅ `--sync-artworks`. Huge filter set (tag, model, author, time, type…) |
| `getArtwork` / `getArtworkWithTaskDetail` | query | artwork detail; the latter includes `taskParameters` + `loras` |
| `listUserLikedArtworks` | query | back up which artworks you liked |
| `upsertArtwork` | mutation | publish / edit an artwork |
| `deleteArtwork` | mutation | delete a published artwork |
| `markArtwork` | mutation | like/bookmark/etc. (`MarkType`) |

---

## Media / file upload (needed for img2img & training inputs)

| Operation | Type | Use |
|---|---|---|
| `uploadMedia` | mutation | `input: UploadMediaInput!` → `uploadUrl, mediaId` — upload a reference image |
| `uploadFileMultiPart` | mutation | chunked upload (large files / model weights) |
| `getMedia` | query | media object by id (we use the REST `/v1/media` for full-res) |
| `listFiles` / `getFileStatistics` | query | your uploaded files |
| `deleteFile` | mutation | delete an uploaded file |

---

## Notifications / inbox

| Operation | Type | Use |
|---|---|---|
| `listNotifications` | query | `type` filter (`NEWS`, `COMMENT`, `LIKE`, `FOLLOW`, `GENERATION_TASK_COMPLETED`, …) — the paste from earlier |
| `listMyNotifications` | query | richer `me.notifications` with artwork/user refs |
| `getMyUnreadNotiCount` | query | unread badge counts |
| `markNotificationRead` | mutation | `ids` / `types` / `all` |

Note the `NotificationType` enum includes **`GENERATION_TASK_COMPLETED`** and
`TRAINING_TASK_COMPLETED` — a second (notification-based) way to detect job
completion, alongside polling and the subscription.

---

## Social, bookmarks, auth (lower priority for a backup tool)

- **Social:** `setFollowState`, `setBlockState`, `listUserFollowings`, `listUserFollowers`, `getUserInfoById`, `getUserInfoByUsername`.
- **Comments/messages:** `sendMessage(V2)`, `deleteMessage`, `listMessages`, `getMessage`.
- **Bookmarks/collections:** `upsertBookmark`, `deleteBookmark`, `updateBookmarkItem`, `listMyBookmarkItems`, `listBookmarkItems`, `listUserBookmarks`.
- **Auth/automation:** `createAccessToken` (→ token+secret; this is how API keys are minted), `revokeAccessToken`, `listMyAccessTokens`, `createWebhook` / `deleteWebhook` / `listMyWebhooks` (event callbacks), `refreshToken`.

---

## Suggested build order (toward a full client)

1. **`gql_adhoc()` helper** — generic ad-hoc POST; unlocks everything below without hash capture.
2. **Account read:** `getMyQuota` + `getMyMembership` → a `--account` command (cheap, safe, validates the helper).
3. **Task management:** `--cancel-task`, `--rerun-task`, per-media delete (we already have the handlers + this catalog).
4. **Generation:** `createGenerationTask` + poll `getTaskById` → `--generate`. Coordinate with the home-machine implementation so we don't duplicate.
5. **Models picker:** `listGenerationModels` to choose a model for generation.
6. **(Optional) real-time:** subscriptions for live preview / a watch daemon.
