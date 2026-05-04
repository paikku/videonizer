# Videonizer Backend API Contract

> Frontend ↔ Backend 단일 진실. 새 stateful API + 기존 stateless 서비스.
> 모든 path 는 `/api` 또는 `/v1` prefix. 인증은 이번 iteration scope 외 (모든 라우트 anonymous).

JSON in/out 기본, 바이너리 전송시 명시. 에러는 `{ error: string, message?: string }` + HTTP status.

상태 마커:
- `[OK]` 구현 완료, 변경 없음
- `[NEW]` 이번 마이그레이션에서 추가
- `[CHANGE]` 응답 형태 변경

---

## 0. Health / Metrics (기존)

| Status | Method | Path | 응답 |
|---|---|---|---|
| `[OK]` | GET | `/healthz` | `{"status":"ok"}` 또는 503 |
| `[OK]` | GET | `/metrics` | Prometheus exposition |

---

## 1. Project

| Status | Method | Path | Body | Response |
|---|---|---|---|---|
| `[NEW]` | GET | `/api/projects` | — | `{ projects: ProjectSummary[] }` |
| `[NEW]` | POST | `/api/projects` | `{ name }` | `{ project: Project }` (201) |
| `[NEW]` | GET | `/api/projects/{id}` | — | `{ project: Project }` |
| `[NEW]` | DELETE | `/api/projects/{id}` | — | `{ ok: true }` |

```ts
type Project = {
  id: string;            // UUID
  name: string;
  createdAt: number;     // epoch ms
};

type ProjectSummary = Project & {
  resourceCount: number;
  imageCount: number;
  labelSetCount: number;
};
```

---

## 2. Resource

A Resource is a single upload container — one video, or one image batch.

| Status | Method | Path | Payload | Response |
|---|---|---|---|---|
| `[NEW]` | GET | `/api/projects/{id}/resources` | — | `{ resources: ResourceSummary[] }` |
| `[NEW]` | POST | `/api/projects/{id}/resources` | **multipart**: `type`, `name`, `tags` (JSON string), [video] `file`, `width`, `height`, `duration?`, `ingestVia?` | `{ resource: Resource }` (201) |
| `[NEW]` | GET | `/api/projects/{id}/resources/{rid}` | — | `{ resource: Resource }` |
| `[NEW]` | PATCH | `/api/projects/{id}/resources/{rid}` | `{ name?, tags? }` | `{ resource: Resource }` |
| `[NEW]` | DELETE | `/api/projects/{id}/resources/{rid}` | — | `{ ok: true }` (cascade to images) |
| `[NEW]` | POST | `/api/projects/{id}/resources/delete` | `{ resourceIds: string[] }` | `{ deleted: number }` |
| `[NEW]` | GET | `/api/projects/{id}/resources/{rid}/source` | (`Range` 헤더) | 원본 비디오 바이트. **Range 필수** — `<video>` seek는 206 의존. Unranged 는 200 + `Accept-Ranges: bytes` |
| `[NEW]` | POST | `/api/projects/{id}/resources/{rid}/previews` | **multipart**: `files[]` (preview tiles, ordered) | `{ previewCount: number }` |
| `[NEW]` | GET | `/api/projects/{id}/resources/{rid}/previews/{idx}` | — | `image/jpeg`, immutable cache |
| `[NEW]` | POST | `/api/projects/{id}/resources/{rid}/images` | **multipart**: `files[]` + `meta` (JSON 배열) | `{ images: Image[] }` (201) |

`meta` 항목 형태:

```ts
{
  fileName: string;
  width: number;
  height: number;
  // video_frame 만
  timestamp?: number;
  frameIndex?: number;
  // 클라이언트가 retry 멱등성 위해 미리 할당하는 UUID
  id?: string;
}
```

Image `source` 필드는 부모 resource type 에서 파생 (`video` → `video_frame`, `image_batch` → `uploaded`).

```ts
type ResourceType = "video" | "image_batch";

type Resource = {
  id: string;
  projectId: string;
  type: ResourceType;
  name: string;
  tags: string[];
  width?: number;
  height?: number;
  duration?: number;
  ingestVia?: "client" | "server";
  hasSource: boolean;       // type=video 만 true
  previewCount: number;
  createdAt: number;
};

type ResourceSummary = Resource & {
  imageCount: number;
};
```

---

## 3. Image

| Status | Method | Path | Payload | Response |
|---|---|---|---|---|
| `[NEW]` | GET | `/api/projects/{id}/images?resourceId&source&tag` | — | `{ images: Image[] }` |
| `[NEW]` | GET | `/api/projects/{id}/images/{iid}` | — | `{ image: Image }` |
| `[NEW]` | PATCH | `/api/projects/{id}/images/{iid}` | `{ tags? }` | `{ image: Image }` |
| `[NEW]` | DELETE | `/api/projects/{id}/images/{iid}` | — | `{ ok: true }` |
| `[NEW]` | POST | `/api/projects/{id}/images/delete` | `{ imageIds: string[] }` | `{ deleted: number }` |
| `[NEW]` | GET | `/api/projects/{id}/images/{iid}/bytes` | — | 원본 이미지 바이트, immutable cache |
| `[NEW]` | GET | `/api/projects/{id}/images/{iid}/thumb` | — | `image/jpeg` 썸네일, immutable cache |
| `[NEW]` | POST | `/api/projects/{id}/images/tags` | `{ imageIds, tags, mode: "add" \| "replace" \| "remove" }` | `{ updated: number }` |

### 필터

쿼리 파라미터는 AND. 생략시 해당 필터 skip.

- `resourceId` — 특정 리소스로 한정
- `source` — `uploaded` | `video_frame`
- `tag` — `Image.tags` exact match (다중 태그 교집합 미지원, 클라가 narrow)

```ts
type ImageSource = "uploaded" | "video_frame";

type Image = {
  id: string;
  projectId: string;
  resourceId: string;
  source: ImageSource;
  fileName: string;
  width: number;
  height: number;
  timestamp?: number;       // video_frame 만
  frameIndex?: number;      // video_frame 만
  tags: string[];
  createdAt: number;
};
```

### 썸네일 규격

- 가로 256px max, 비율 유지
- JPEG quality 80
- 색공간 RGB

---

## 4. LabelSet

`type` 은 생성시 고정. 한 이미지가 여러 LabelSet 에 속할 수 있음 (class identity 는 set 경계 안에서만).

| Status | Method | Path | Payload | Response |
|---|---|---|---|---|
| `[CHANGE]` | GET | `/api/projects/{id}/labelsets` | — | `{ labelsets: LabelSetListItem[] }` (lightweight) |
| `[NEW]` | POST | `/api/projects/{id}/labelsets` | `{ name, type, description?, imageIds? }` | `{ labelset: LabelSet }` (201) |
| `[NEW]` | GET | `/api/projects/{id}/labelsets/{lsid}` | — | `{ labelset: LabelSet }` (full) |
| `[NEW]` | GET | `/api/projects/{id}/labelsets/{lsid}/summary` | — | `{ summary: LabelSetSummary }` |
| `[NEW]` | PATCH | `/api/projects/{id}/labelsets/{lsid}` | `{ name?, description?, classes?, imageIds?, excludedImageIds? }` | `{ labelset: LabelSet }` |
| `[NEW]` | DELETE | `/api/projects/{id}/labelsets/{lsid}` | — | `{ ok: true }` |

### list vs summary 분리

`LabelSetSummary` 는 무겁다 (`imageShapes`, `imageLabels` 포함). 라벨셋이 많으면 list 응답이 multi-MB 로 커지므로:
- list 는 lightweight (통계 카운트 + classStats만)
- 무거운 필드 필요시 `/summary` 별도 호출

```ts
type LabelSetType = "polygon" | "bbox" | "classify";

type LabelClass = {
  id: string;
  name: string;
  color?: string;
};

type LabelSet = {
  id: string;
  projectId: string;
  name: string;
  type: LabelSetType;
  description?: string;
  classes: LabelClass[];
  imageIds: string[];
  excludedImageIds: string[];
  createdAt: number;
};

type LabelSetClassStat = {
  classId: string;
  imageCount: number;
  annotationCount: number;
};

type LabelSetListItem = {
  id: string;
  projectId: string;
  name: string;
  type: LabelSetType;
  description?: string;
  classes: LabelClass[];
  imageIds: string[];
  excludedImageIds: string[];
  imageCount: number;
  annotationCount: number;
  labeledImageCount: number;
  excludedImageCount: number;
  classStats: LabelSetClassStat[];
  createdAt: number;
};

type LabelSetSummary = LabelSetListItem & {
  imageLabels: Record<string, string[]>;     // imageId → distinct classIds
  imageShapes: Record<string, AnnotationShape[]>;
};
```

`/summary` 는 `Cache-Control: no-store`.

### 4.1 Annotations

| Status | Method | Path | Body | Response |
|---|---|---|---|---|
| `[NEW]` | GET | `/api/projects/{id}/labelsets/{lsid}/annotations` | — | `LabelSetAnnotations` |
| `[NEW]` | PUT | `/api/projects/{id}/labelsets/{lsid}/annotations` | `LabelSetAnnotations` (full replace) | `{ ok: true }` |
| `[NEW]` | PATCH | `/api/projects/{id}/labelsets/{lsid}/annotations` | `{ upsert?, deleteIds?, replaceImageIds? }` | `{ annotations: LabelSetAnnotation[] }` |

```ts
type LabelSetAnnotation =
  | { id: string; imageId: string; classId: string; kind: "rect"; rect: NormRect }
  | { id: string; imageId: string; classId: string; kind: "polygon"; polygon: NormPoint[][] }
  | { id: string; imageId: string; classId: string; kind: "classify" };

type LabelSetAnnotations = {
  labelSetId: string;
  annotations: LabelSetAnnotation[];
};

type NormRect = { x: number; y: number; w: number; h: number };  // [0,1]
type NormPoint = [number, number];
```

PATCH 의미론 (단일 트랜잭션):
- `replaceImageIds`: 해당 imageId 의 annotations 전체 삭제
- `deleteIds`: 지정 id 삭제
- `upsert`: id 기준 INSERT 또는 OVERWRITE

순서: `replaceImageIds` → `deleteIds` → `upsert`. 응답은 PATCH 후 전체 annotation 리스트.

PUT 은 import 플로우용 full replace 보존.

### 4.2 Export

| Status | Method | Path | Payload | Response |
|---|---|---|---|---|
| `[NEW]` | GET | `/api/projects/{id}/labelsets/{lsid}/export` | — | `application/json` 다운로드 (`Content-Disposition: attachment`) |
| `[NEW]` | POST | `/api/projects/{id}/labelsets/{lsid}/export/validate` | `{ split?: SplitConfig }` | `{ report: ValidationReport; items: ExportPreviewItem[] }` |
| `[NEW]` | POST | `/api/projects/{id}/labelsets/{lsid}/export/dataset` | `{ split?, includeImages?, remapClassIds? }` | `application/zip` 스트리밍 다운로드 |

```ts
type SplitName = "train" | "val" | "test";
type SplitConfig = { train: number; val: number; test: number };  // 합 = 1

type ValidationReport = {
  totalImages: number;
  labeledImages: number;
  unlabeledImages: number;
  splitCounts: Record<SplitName, number>;
  warnings: string[];
};

type ExportPreviewItem = {
  imageId: string;
  fileName: string;
  split: SplitName | null;
  excluded: boolean;
  labeled: boolean;
  tags: string[];
};
```

`validate` 는 export modal 의 dry-run preflight (옵션 변경시마다 호출).
`dataset` 은 동기 스트리밍 빌드 (잡 큐 없음).

---

## 5. 기존 stateless 서비스 (변경 없음)

### 5.1 `POST /v1/normalize`

`features/media/service/normalize.ts` 와 동일 계약. multipart `file` + `profile?` + `async_job?`. 응답은 `video/mp4` 스트리밍 또는 `202 + jobId`.

자세한 헤더/에러 코드는 기존 `README.md §1` 참조.

### 5.2 `POST /v1/segment`

라벨 hover + `H` 단축키로 호출. multipart `file` + `region` (정규화 좌표) + `model?` + `classHint?`. 응답은 `polygon` + `rect` + `score`.

자세한 모델 라우팅 / 부하 제어는 기존 `README.md §1` 참조.

---

## 6. 에러 응답 공통

```json
{ "error": "snake_case_code", "message": "human readable" }
```

| HTTP | 의미 |
|---|---|
| 400 | 요청 형식 오류 (JSON 파싱, 잘못된 enum, range 위반) |
| 404 | 엔티티 없음 |
| 409 | 충돌 (예: bulk delete 중 일부 권한 X) |
| 413 | 업로드 크기 초과 |
| 415 | 지원 안 하는 미디어 타입 |
| 416 | 잘못된 Range |
| 422 | 의미적 검증 실패 |
| 503 | 의존성 실패 (DB, S3) |
| 504 | 서버 처리 타임아웃 |

---

## 7. Out of scope (이번 iteration 외)

- **Auth / Sessions** — 현재 모두 anonymous. 추가시 `401`/`403` + 클라이언트 fetch 래퍼에 redirect 추가.
- **Image list pagination** — `GET /images` 는 프로젝트 전체 반환. 수천개 수준은 OK, 그 이상이면 cursor 도입.
- **Server-side frame extraction** — `Resource.ingestVia: "server"` 필드는 모델에 있으나 trigger 라우트 없음 (현재는 client-side ffmpeg-wasm).
- **Async dataset export** — `dataset` 동기. 매우 큰 번들은 향후 잡 + 폴링.

---

## 8. Open questions (이번 마이그레이션에서 확정)

1. `/summary` cache header → **`Cache-Control: no-store`** 확정
2. PATCH annotations 응답 → **full list 반환** 확정 (측정 후 회귀 발견시 슬라이싱)
3. Bulk delete cascade 카운트 → resources 카운트만 (`{deleted}`), image 별도 카운트 미제공
