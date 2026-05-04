# Videonizer HTTP API Contract

The single source of truth for any client (vision frontend, scripts, tests)
that talks to videonizer over HTTP. Every public endpoint is documented
here. If the implementation diverges from this file, the implementation is
wrong.

All endpoints live under `/v1/`. JSON bodies are UTF-8. Timestamps are
milliseconds since the Unix epoch unless noted. Coordinate values are
normalized to `[0, 1]` with origin at top-left unless noted.

> **Status legend** — `STABLE`: contract frozen, `DRAFT`: contract may shift
> in the next minor revision (frontend should pin against this file's git
> sha while DRAFT).

---

## 0. Conventions

### Error envelope

Every non-2xx JSON response uses the same shape:

```json
{ "error": "code", "message": "human readable text" }
```

`code` is a stable lowercase identifier. The frontend should branch on
`code`, not on the message text.

| code | typical HTTP | meaning |
|---|---|---|
| `not_found` | 404 | target id does not exist |
| `invalid_input` | 400 / 422 | request body or query is malformed |
| `upload_too_large` | 413 | upload exceeded the configured limit |
| `unsupported_media_type` | 415 | file type not accepted |
| `conflict` | 409 | preconditions for the action are not yet met (e.g. job not done) |
| `busy` | 503 | server is shedding load; client should retry with backoff |
| `timeout` | 504 | request exceeded server budget |
| `internal_error` | 500 | unexpected server error; safe to retry once |

Existing normalize-specific codes (`ffmpeg_failed`, `no_video_stream`,
`ffprobe_unavailable`, …) keep their meaning from §1 below.

### Authentication

None at the moment. CORS is enforced via `ALLOWED_ORIGINS`. Future:
short-lived signed token in `Authorization: Bearer …`.

### Storage layout (server-internal, but documented for parity)

The server keeps all project state on the local filesystem under
`STORAGE_ROOT`:

```
{STORAGE_ROOT}/
  projects.json                              { "projects": [<projectId>] }
  {projectId}/
    project.json                             Project
    resources.json                           [<resourceId>]
    images.json                              [<imageId>]
    labelsets.json                           [<labelSetId>]
    resources/{resourceId}/
      meta.json                              Resource
      source.<ext>                           video resources only
      previews/preview-{idx}.jpg             video resources only
    images/{imageId}/
      meta.json                              Image
      bytes.<ext>                            original bytes
      thumb-384.jpg                          lazily generated 384px thumb
    labelsets/{labelSetId}/
      meta.json                              LabelSet
      annotations.json                       LabelSetAnnotations
```

Layout is internal — the contract is the HTTP API below. Migration scripts
ship separately.

---

## 1. Media — Normalize / Segment (existing)

These have been live for some time and their contracts are repeated here
verbatim from `README.md` so the frontend has a single source of truth.

### `POST /v1/normalize` `STABLE`

Convert a non-web-friendly video to H.264/AAC MP4. See README §1 for full
detail; summary:

| field | value |
|---|---|
| Content-Type (request) | `multipart/form-data` |
| `file` (required) | source video bytes |
| `profile` (optional) | `web-h264` (default; reserved for future profiles) |
| `async_job` (optional) | `true` to return a job descriptor instead of streaming the result |
| Response Content-Type | `video/mp4` (sync) / `application/json` (async) |

Sync response headers: `X-Normalize-Duration-Ms`, `X-Normalize-Input-Codec`,
`X-Normalize-Remuxed`.

Async (`async_job=true`) returns `202` with body:

```json
{
  "jobId": "....",
  "statusUrl": "http://<host>/v1/normalize/jobs/<jobId>",
  "resultUrl": "http://<host>/v1/normalize/jobs/<jobId>/result"
}
```

Error codes: `unsupported_media_type` 415, `upload_too_large` 413,
`invalid_input` 422, `no_video_stream` 422, `timeout` 504, `ffmpeg_failed`
422, `ffprobe_unavailable` 503.

### `POST /v1/normalize/jobs` · `GET /v1/normalize/jobs/{jobId}` · `GET /v1/normalize/jobs/{jobId}/result` `STABLE`

Async normalize lifecycle. Status response:

```json
{ "jobId": "...", "status": "queued|processing|done|failed",
  "state": "queued|processing|done|failed", "progress": 0..100,
  "message": "..."|null }
```

`state` is a duplicate of `status` for legacy compatibility.

### `POST /v1/segment` · `GET /v1/segment/models` `STABLE`

Single-image, single-region instance segmentation. `model` enum:
`sam3` (default) | `sam2` | `sam` | `mask2former` | `mask-rcnn`. Full
spec in README §1.

### `GET /healthz` · `GET /metrics` `STABLE`

Liveness probe + Prometheus exposition.

---

## 2. Projects `DRAFT`

A Project is the top-level container for resources, images and label
sets. Project ids are server-issued UUIDs.

### Types

```ts
type Project = {
  id: string;
  name: string;
  createdAt: number;
  members: { id: string; name: string; role: string }[];
};

type ProjectSummary = {
  id: string;
  name: string;
  createdAt: number;
  resourceCount: number;
  imageCount: number;
  labelSetCount: number;
};
```

### `GET /v1/projects`

List all projects, newest first.

`200 OK` →

```json
{ "projects": [ProjectSummary, ...] }
```

### `POST /v1/projects`

Create a project.

Request body — `application/json`:

```json
{ "name": "string (required, trimmed)" }
```

`201 Created` → `{ "project": Project }`
`400` → `{ "error": "invalid_input", "message": "name is required" }`

### `GET /v1/projects/{id}`

`200 OK` → `{ "project": Project }`
`404` → `{ "error": "not_found" }`

### `DELETE /v1/projects/{id}`

Cascade-deletes the project, its resources (incl. source files +
previews), images (incl. bytes + thumbs) and label sets.

`200 OK` → `{ "ok": true }`. Idempotent — deleting a missing project
returns 200.

---

## 3. Resources `DRAFT`

A Resource is one upload unit. Two flavors:

* `video` — a single video file. Owns its source bytes (browser-seekable
  via `/source`) and an optional preview-reel of JPEGs.
* `image_batch` — a logical container into which images are uploaded
  later via `/images`.

### Types

```ts
type ResourceType = "video" | "image_batch";

type Resource = {
  id: string;
  type: ResourceType;
  name: string;
  tags: string[];
  createdAt: number;
  // video-only:
  sourceExt?: string;
  duration?: number;
  width?: number;
  height?: number;
  ingestVia?: "original" | "ffmpeg-wasm" | "server";
  previewCount?: number;
};

type ResourceSummary = Resource & { imageCount: number };
```

### `GET /v1/projects/{id}/resources`

`200 OK` → `{ "resources": ResourceSummary[] }` sorted by `createdAt`.

### `POST /v1/projects/{id}/resources`

Create a resource.

Request — `multipart/form-data`:

| field | type | applies to | description |
|---|---|---|---|
| `type` | string (required) | both | `video` \| `image_batch` |
| `name` | string (required, trimmed) | both | display name |
| `tags` | JSON-encoded string[] (optional) | both | upload-level tags |
| `file` | binary (required for `video`) | `video` | source bytes |
| `width` | number (required for `video`) | `video` | original width |
| `height` | number (required for `video`) | `video` | original height |
| `duration` | number (optional) | `video` | seconds |
| `ingestVia` | enum (optional) | `video` | `original`\|`ffmpeg-wasm`\|`server` |

Server derives `sourceExt` from the uploaded filename (fallback `mp4`).

`201 Created` → `{ "resource": Resource }`
`400` → invalid `type`, missing fields, malformed `tags`.

### `GET /v1/projects/{id}/resources/{rid}`

`200 OK` → `{ "resource": Resource }`
`404` → `{ "error": "not_found" }`

### `PATCH /v1/projects/{id}/resources/{rid}`

Partial update — only `name` and `tags` are mutable.

Request — `application/json`:

```json
{ "name": "string?", "tags": ["string", ...]? }
```

`200 OK` → `{ "resource": Resource }`
`404` → `{ "error": "not_found" }`

### `DELETE /v1/projects/{id}/resources/{rid}`

Cascade-deletes the resource, its source bytes, previews, and **every
image whose `resourceId` points to it**, plus every annotation whose
`imageId` was on one of those images. Idempotent.

`200 OK` → `{ "ok": true }`

### `GET /v1/projects/{id}/resources/{rid}/source`

Stream the original video bytes. Supports HTTP byte-ranges; required for
`<video>` seeking in browsers.

* No `Range` header → `200 OK`, full body, `Accept-Ranges: bytes`.
* `Range: bytes=START-END` → `206 Partial Content`,
  `Content-Range: bytes START-END/TOTAL`. Suffix ranges (`bytes=-N`)
  supported.
* Malformed or out-of-bound range → `416 Range Not Satisfiable` with
  `Content-Range: bytes */TOTAL`.

Other headers: `Content-Type` derived from the stored extension,
`Cache-Control: private, max-age=0, must-revalidate`.

`404` → `not_found`.

### `POST /v1/projects/{id}/resources/{rid}/previews`

Replace the preview reel for a video resource. Wipes all prior previews
before writing the new set.

Request — `multipart/form-data`:

| field | type | description |
|---|---|---|
| `files` (repeating) | binary JPEG | preview frames in display order |

`200 OK` → `{ "previewCount": <int> }`
`404` → resource not found.
`422` → resource is not type `video`.

### `GET /v1/projects/{id}/resources/{rid}/previews/{idx}`

Read one preview JPEG by zero-based index.

`200 OK`, `Content-Type: image/jpeg`,
`Cache-Control: private, max-age=31536000, immutable`.
`404` → preview missing or `idx` out of range.
`400` → `idx` is not an integer.

### `POST /v1/projects/{id}/resources/{rid}/images`

Add Image rows to a Resource. The body provides `meta` (a JSON-encoded
array, one entry per file) and `files` (the same number of binaries, in
the same order).

```ts
type ImageMetaEntry = {
  fileName: string;
  width: number;
  height: number;
  // video_frame only
  timestamp?: number;
  frameIndex?: number;
  // optional client-allocated UUID for retries
  id?: string;
};
```

Image `source` is derived: video resources → `video_frame`, image_batch
→ `uploaded`.

Request — `multipart/form-data`:

| field | type | description |
|---|---|---|
| `meta` | JSON string of `ImageMetaEntry[]` | one per file |
| `files` (repeating) | binary | image bytes |

`201 Created` → `{ "images": Image[] }`
`400` → mismatched `files`/`meta` count, malformed `meta`.
`404` → resource not found.

---

## 4. Images `DRAFT`

An Image is a single labeling target.

### Types

```ts
type ImageSource = "uploaded" | "video_frame";

type VideoFrameMeta = {
  timestamp: number;     // seconds in the source video
  frameIndex?: number;   // tie-breaker for stable ordering
};

type Image = {
  id: string;
  resourceId: string;
  source: ImageSource;
  fileName: string;
  ext: string;
  width: number;
  height: number;
  tags: string[];
  videoFrameMeta?: VideoFrameMeta;
  createdAt: number;
};

type ImageFilter = {
  resourceId?: string;
  source?: ImageSource;
  tag?: string;
};
```

### `GET /v1/projects/{id}/images`

List all images in the project.

Query parameters (any subset):

| query | type | filter |
|---|---|---|
| `resourceId` | string | only images whose `resourceId` matches |
| `source` | `uploaded`\|`video_frame` | only images of that source |
| `tag` | string | only images that include this tag |

`200 OK` → `{ "images": Image[] }`

### `GET /v1/projects/{id}/images/{iid}`

`200 OK` → `{ "image": Image }`
`404` → `not_found`

### `PATCH /v1/projects/{id}/images/{iid}`

Only `tags` is mutable.

Request — `application/json`: `{ "tags": ["string", ...] }`

`200 OK` → `{ "image": Image }`
`404` → `not_found`

### `DELETE /v1/projects/{id}/images/{iid}`

Cascade-deletes the image, its bytes/thumb, **and removes the image from
every LabelSet's `imageIds` plus every annotation whose `imageId`
matches**. Idempotent.

`200 OK` → `{ "ok": true }`

### `GET /v1/projects/{id}/images/{iid}/bytes`

Original image bytes.
`200 OK`, `Content-Type` derived from `image.ext`,
`Cache-Control: private, max-age=31536000, immutable`.
`404` → not found.

### `GET /v1/projects/{id}/images/{iid}/thumb`

Lazy-generated 384px JPEG thumbnail (`fit: inside`, no enlargement,
EXIF-rotated). Repeat callers for the same image are serialized so
generation never duplicates.
`200 OK`, `Content-Type: image/jpeg`,
`Cache-Control: private, max-age=31536000, immutable`.
`404` → image not found (no bytes on disk).

### `POST /v1/projects/{id}/images/tags`

Bulk tag mutation across many images.

Request — `application/json`:

```json
{
  "imageIds": ["...", "..."],
  "tags": ["string", ...],
  "mode": "add" | "remove" | "replace"
}
```

* `add` (default) — union with existing tags.
* `remove` — subtract.
* `replace` — overwrite.

Empty `imageIds` returns `200 { "updated": 0 }` without touching
anything. Per-image meta is locked so concurrent single+bulk edits
cannot clobber each other.

`200 OK` → `{ "updated": <int> }`

---

## 5. LabelSets `DRAFT`

A LabelSet is a labeling unit — one task type, its own classes,
membership list of images, and the annotations against them.

### Types

```ts
type LabelSetType = "polygon" | "bbox" | "classify";

type LabelClass = {
  id: string;
  name: string;
  color: string;
  shortcutKey?: "q" | "w" | "e" | "r";
};

type LabelSet = {
  id: string;
  name: string;
  type: LabelSetType;
  description?: string;
  classes: LabelClass[];
  imageIds: string[];
  excludedImageIds: string[];
  createdAt: number;
};

type ShapeRect = { kind: "rect"; x: number; y: number; w: number; h: number };
type ShapePolygon = {
  kind: "polygon";
  rings: { x: number; y: number }[][];
};

type RectAnnotation = {
  id: string; imageId: string; classId: string;
  kind: "rect"; shape: ShapeRect; createdAt: number;
};
type PolygonAnnotation = {
  id: string; imageId: string; classId: string;
  kind: "polygon"; shape: ShapePolygon; createdAt: number;
};
type ClassifyAnnotation = {
  id: string; imageId: string; classId: string;
  kind: "classify"; createdAt: number;
};

type LabelSetAnnotation = RectAnnotation | PolygonAnnotation | ClassifyAnnotation;
type LabelSetAnnotations = { annotations: LabelSetAnnotation[] };
```

`LabelSetSummary` adds aggregate counts the list endpoint computes on
the fly:

```ts
type SummaryShape = { classId: string; shape: ShapeRect | ShapePolygon };

type LabelSetSummary = LabelSet & {
  imageCount: number;
  annotationCount: number;
  labeledImageCount: number;
  excludedImageCount: number;
  classStats: { classId: string; imageCount: number }[];
  imageLabels: Record<string /*imageId*/, string[] /*classIds*/>;
  imageShapes: Record<string /*imageId*/, SummaryShape[]>;
};
```

### `GET /v1/projects/{id}/labelsets`

`200 OK` → `{ "labelsets": LabelSetSummary[] }`, sorted by
`createdAt` ascending.

### `POST /v1/projects/{id}/labelsets`

Create a label set. `type` is fixed at creation time.

Request — `application/json`:

```json
{
  "name": "string (required)",
  "type": "polygon" | "bbox" | "classify",
  "description": "string?",
  "imageIds": ["...", "..."]?
}
```

`201 Created` → `{ "labelset": LabelSet }`
`400` → missing `name` or invalid `type`.

### `GET /v1/projects/{id}/labelsets/{lsid}`

`200 OK` → `{ "labelset": LabelSet }`
`404` → `not_found`

### `PATCH /v1/projects/{id}/labelsets/{lsid}`

Mutable fields: `name`, `description`, `classes`, `imageIds`,
`excludedImageIds`.

Request — `application/json`:

```json
{
  "name": "string?",
  "description": "string?",
  "classes": [LabelClass, ...]?,
  "imageIds": ["...", ...]?,
  "excludedImageIds": ["...", ...]?
}
```

`200 OK` → `{ "labelset": LabelSet }`
`404` → `not_found`

### `DELETE /v1/projects/{id}/labelsets/{lsid}`

Idempotent. Removes the LabelSet and its annotations file.

`200 OK` → `{ "ok": true }`

### `GET /v1/projects/{id}/labelsets/{lsid}/annotations`

`200 OK` → `LabelSetAnnotations`

### `PUT /v1/projects/{id}/labelsets/{lsid}/annotations`

Replace the entire annotations list. The body is `LabelSetAnnotations`
verbatim. Atomic write on the server.

`200 OK` → `{ "ok": true }`
`400` → body is not the expected shape.

---

## 6. Export `DRAFT`

Three endpoints over the same LabelSet: a JSON dump, a YOLO/CSV ZIP
bundle, and a dry-run validator.

### `GET /v1/projects/{id}/labelsets/{lsid}/export`

A single `LabelSetExport` JSON containing the LabelSet, the labeled
images' metadata, and every annotation. Image bytes are not inlined —
the consumer fetches them via `/images/{iid}/bytes` if needed.

```ts
type LabelSetExport = {
  version: 2;
  labelSet: {
    id: string; name: string; type: LabelSetType;
    classes: LabelClass[]; createdAt: number;
  };
  images: {
    id: string; fileName: string;
    width: number; height: number;
    source: ImageSource;
    resource: { id: string; name: string; type: ResourceType } | null;
    tags: string[];
    videoFrameMeta?: VideoFrameMeta;
  }[];
  annotations: LabelSetAnnotation[];
};
```

`200 OK` — `Content-Type: application/json; charset=utf-8`,
`Content-Disposition: attachment; filename="<labelSet.name>.json"`.
`404` → `not_found`.

### `POST /v1/projects/{id}/labelsets/{lsid}/export/dataset`

Produce a YOLO-detection / YOLO-segmentation / classify-csv ZIP based
on the LabelSet `type`. Format is auto-selected:

| LabelSet `type` | format | files in archive |
|---|---|---|
| `bbox` | `yolo-detection` | `images/<split>/`, `labels/<split>/<name>.txt`, `classes.txt`, `data.yaml`, `manifest.json` |
| `polygon` | `yolo-segmentation` | same as detection (segmentation polygons in label .txt) |
| `classify` | `classify-csv` | `data.csv`, `classes.txt`, `manifest.json` (and optional `images/<split>/` if `includeImages`) |

Request — `application/json`:

```json
{
  "split": SplitConfig,
  "includeImages": boolean,
  "remapClassIds": boolean
}
```

```ts
type SplitName = "train" | "val" | "test";
type SplitConfig =
  | { mode: "none" }
  | { mode: "random"; train: number; val: number; test: number; seed: number }
  | { mode: "by-tag"; tagTrain: string; tagVal: string; tagTest: string }
  | { mode: "manual"; assignments: Record<string /*imageId*/, SplitName> };
```

* `includeImages: false` (default) → `images/` folder is omitted; the
  archive contains labels and metadata only.
* `remapClassIds: true` → output indices are 0..N-1 of *used* classes
  in declaration order; `false` keeps the original class declaration
  order.

`200 OK` — `Content-Type: application/zip`,
`Content-Disposition: attachment; filename="<safeName>-<format>.zip"`.
`404` → `not_found`.

The archive always includes `manifest.json` mapping each
`archiveFileName ↔ imageId / split / originalFileName`, plus the
chosen `options` and `classes` for round-tripping.

### `POST /v1/projects/{id}/labelsets/{lsid}/export/validate`

Dry-run the export — same filtering and split logic as `/dataset`, but
returns counts and warnings instead of bytes. Use this to power the
"Export preview" panel before the user clicks download.

Request — `application/json`:

```json
{ "split": SplitConfig }
```

(`split` defaults to `{ "mode": "none" }` if omitted.)

`200 OK` →

```ts
type ValidationReport = {
  format: "yolo-detection" | "yolo-segmentation" | "classify-csv";
  totalImages: number;
  usableImages: number;     // labeled or excluded
  unusableImages: number;   // unlabeled, will be dropped
  excludedImages: number;
  annotationCount: number;
  classCount: number;
  splitCounts: { train: number; val: number; test: number; unassigned: number };
  warnings: { code: string; message: string; imageId?: string }[];
};

type ValidationItem = {
  imageId: string;
  fileName: string;
  split: SplitName | null;  // null = excluded by splitter
  excluded: boolean;        // member of LabelSet.excludedImageIds
  labeled: boolean;
  tags: string[];
};

// response body:
{ "report": ValidationReport, "items": ValidationItem[] }
```

Warning `code` enum (frontend can branch on these):
`no-classes`, `no-annotations`, `unusable-images`, `unassigned-split`,
`multi-class-classify`, `out-of-bounds`.

---

## 7. Implementation parity with the Next.js predecessor

This contract is a 1:1 carry-over of vision's `app/api/projects/*`
routes onto videonizer's `/v1/` namespace. Behavioural notes the
frontend should rely on:

* All write endpoints are serialized per affected file via per-path
  mutex on the server, so two concurrent PATCHes against the same
  Image / Resource / LabelSet meta will not lose updates.
* All cross-resource cascades (project delete → resources/images/labelsets,
  resource delete → its images + their annotations, image delete →
  labelset membership + annotations) are server-enforced. The frontend
  does NOT need to issue follow-up DELETEs.
* Bulk tag operations (`POST /images/tags`) and bulk annotation writes
  (`PUT /labelsets/.../annotations`) are atomic; either every change
  lands or none of them do.
* Identifiers in URLs are server-issued UUIDs (or, for images,
  optionally client-allocated UUIDs supplied during creation). Any id
  containing path separators or `..` is rejected with `400
  invalid_input`.
* Cache-control: image bytes / thumbs / previews are `immutable`
  (long-lived); video source is `must-revalidate` because of
  resource-level edits.

---

## 8. Versioning

The `/v1` prefix is frozen for the lifetime of this contract.
Backwards-incompatible changes will land under `/v2`. Additive
changes (new optional fields, new endpoints) stay on `/v1`.
