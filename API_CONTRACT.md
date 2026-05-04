# Videonizer Backend

Frontend ↔ Backend 단일 진실 + 운영 가이드. 두 갈래의 API:

- **Stateful API** (`/api/*`) — Project / Resource / Image / LabelSet / Annotation / Export. PostgreSQL + MinIO 백킹.
- **Stateless 서비스** (`/v1/*`) — 비디오 normalize (`/v1/normalize`) 와 어노테이션 재맞춤 세그먼테이션 (`/v1/segment`). DB/스토리지 의존성 없음, frontend wasm 폴백 가능.

JSON in/out 기본, 바이너리 전송시 명시. 에러는 `{ error: string, message?: string }` + HTTP status.

작업 분할/마이그레이션 계획은 `WORK_PLAN.md` 참조.

상태 마커:
- `[OK]` 구현 완료, 변경 없음
- `[NEW]` 이번 마이그레이션에서 추가
- `[CHANGE]` 응답 형태 변경

인증/세션은 이번 iteration scope 외 (모든 라우트 anonymous).

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

## 5. Stateless 서비스 (변경 없음)

브라우저에서 직접 재생 불가능한 비디오(AVI/MKV/WMV/FLV 등)를 H.264/AAC MP4 로 재인코딩, 그리고 라벨 어노테이션 경계를 모델로 다시 맞춰주는 두 엔드포인트. 인증/세션 없음 — frontend 가 client-side wasm 으로 폴백 가능한 best-effort 서비스.

### 5.1 `POST /v1/normalize`

| 항목 | 값 |
|---|---|
| Content-Type (요청) | `multipart/form-data` |
| `file` (필수) | 원본 비디오 바이너리 |
| `profile` (옵션) | `web-h264` (기본, 향후 확장) |
| `async_job` (옵션) | `true` 면 비동기 잡 모드 |
| 응답 Content-Type | `video/mp4` (sync) 또는 `application/json` (async) |

#### 동기 모드 (기본)

`async_job` 미지정 또는 `false` → 변환된 MP4를 즉시 스트리밍.

#### 비동기 모드 (`async_job=true`)

`202 Accepted` + JSON:

```json
{
  "jobId": "...",
  "statusUrl": "http://<host>/v1/normalize/jobs/<jobId>",
  "resultUrl": "http://<host>/v1/normalize/jobs/<jobId>/result"
}
```

폴링용 라우트:

| Method | Path | 응답 |
|---|---|---|
| POST | `/v1/normalize/jobs` | `202` + 위 JSON |
| GET | `/v1/normalize/jobs/{jobId}` | `{ status, state, progress }` (`progress` 0–100, ffmpeg `-progress out_time_ms` 추정) |
| GET | `/v1/normalize/jobs/{jobId}/result` | 완료시 MP4 스트리밍 |

`state` 는 `status` 의 호환 alias.

**응답 헤더 (성공시)**

- `Content-Length`
- `X-Normalize-Duration-Ms` — 서버 처리 시간
- `X-Normalize-Input-Codec` — 원본 비디오 코덱명 (디버깅용)
- `X-Normalize-Remuxed` — `1` 이면 재인코딩 없이 remux 만 수행

**에러**

```json
{ "error": "code", "message": "..." }
```

| code | HTTP | 의미 |
|---|---|---|
| `unsupported_media_type` | 415 | 지원하지 않는 컨테이너/코덱 |
| `upload_too_large` | 413 | `MAX_UPLOAD_BYTES` 초과 |
| `invalid_input` | 422 | ffprobe 가 입력 거부 (비디오 아님) |
| `no_video_stream` | 422 | 비디오 스트림 없음 |
| `timeout` | 504 | `JOB_TIMEOUT_MS` 초과 |
| `ffmpeg_failed` | 422 | ffmpeg 디코딩/인코딩 실패 |
| `ffprobe_unavailable` | 503 | ffprobe 바이너리 실행 불가 (링커 오류, 미설치) |
| `ffmpeg_unavailable` | 503 | (`/healthz` 응답에서만) ffmpeg 바이너리 실행 불가 |

> `ffprobe_unavailable` 503 을 `/v1/normalize` 에서 받으면 클라이언트가 wasm 폴백으로 자동 전환 — 사용자 입장에선 투명.

#### 변환 규격

`features/media/service/normalize.ts` 의 wasm 어댑터와 동일.

**재인코딩 경로** (기본):

```
ffmpeg -nostdin -y -i <IN>
  -map 0:v:0 [-map 0:a:0?]
  -c:v libx264 -profile:v main -pix_fmt yuv420p
  -vf scale=trunc(iw/2)*2:trunc(ih/2)*2
  -fps_mode passthrough
  [-c:a aac -b:a 128k]
  -metadata:s:v:0 rotate=0
  -movflags +faststart
  <OUT>
```

**remux 경로** — 입력이 H.264 + AAC + MP4 + 회전메타 없음 + 짝수 해상도일 때:

```
ffmpeg -nostdin -y -i <IN>
  -map 0:v:0 [-map 0:a:0?]
  -c copy -movflags +faststart
  <OUT>
```

### 5.2 `POST /v1/segment`

라벨 hover + `H` 단축키로 호출되는 이미지 세그먼테이션. 한 요청 = 한 어노테이션 재맞춤.

| 항목 | 값 |
|---|---|
| Content-Type (요청) | `multipart/form-data` |
| `file` (필수) | 단일 프레임 JPEG/PNG 바이트 |
| `region` (필수) | JSON 문자열 `{"x":0..1,"y":0..1,"w":>0,"h":>0}` (정규화 좌표) |
| `model` (옵션) | `sam3` (기본) / `sam2` / `sam` / `mask2former` / `mask-rcnn` |
| `classHint` (옵션) | 라벨 클래스 힌트 — 향후 모델 conditioning 용 (현재 로깅만) |

**응답** (`200 OK`, `application/json`):

```json
{
  "polygon": [[[x,y], ...], ...],
  "rect":  {"x":..., "y":..., "w":..., "h":...},
  "score": 0.93
}
```

- `polygon` ring 0 = 외곽 boundary, ring 1.. = holes (even-odd fill)
- 좌표는 정규화 `[0..1]`, 좌상단 원점
- 객체를 못 찾으면 `200 {}` (no-op) — 클라이언트는 기존 라벨 유지

**응답 헤더**

- `X-Segment-Backend` — 실제 추론한 CPU 백엔드 id (`fastsam-s` / `sam2.1-tiny` / `mobile-sam` / `yolov8n-seg` / `yolo11x-seg`)
- `X-Segment-Duration-Ms` — 서버 처리 시간

**에러**

| 시나리오 | HTTP | `error` | 비고 |
|---|---|---|---|
| 지원하지 않는 `model` id | 400 | `unsupported model` | |
| `region` JSON 파싱/범위 실패 | 400 | `invalid_region` | |
| 이미지 디코딩 실패 | 400 | `image_decode_failed` | |
| 인식 못 하는 이미지 포맷 | 415 | `unsupported_media_type` | |
| 업로드 크기 초과 | 413 | `upload_too_large` | `SEGMENT_MAX_UPLOAD_BYTES` |
| 추론 시간 초과 | 504 | `timeout` | 슬롯 획득 후 추론 단계 |
| 큐 대기 시간 초과 | 503 | `busy` | `Retry-After: 2` |
| 큐 길이 cap 초과 | 503 | `busy` | `Retry-After: 1` |
| 백엔드 로드 실패 (가중치 누락 등) | 503 | `backend_unavailable` | |

#### 부하 제어 / 동시성 정책

CPU 추론이 무거워 burst 트래픽에 약함. 3단 방어로 *서버 무응답* 상태 차단:

1. **큐 길이 cap (`SEGMENT_MAX_QUEUE`)** — 가득 차면 즉시 `503 + Retry-After: 1`. 무제한 큐가 쌓여 프록시 타임아웃이 먼저 터지는 패턴(클라이언트는 hang 으로 보임) 차단.
2. **acquire timeout (`SEGMENT_ACQUIRE_TIMEOUT_MS`)** — 슬롯을 잡지 못하고 대기하던 요청도 일정 시간 지나면 `503 + Retry-After: 2`. `SEGMENT_TIMEOUT_MS` 와 별도.
3. **슬롯-스레드 결합** — 추론이 `SEGMENT_TIMEOUT_MS` 초과시 클라이언트엔 즉시 `504` 보내지만 워커 스레드(파이썬은 cooperative cancel 불가)가 종료될 때까지 세마포어를 잡고 있음. 이 결합이 없으면 좀비 스레드 위에 새 추론이 누적돼 `SEGMENT_MAX_CONCURRENT` 보다 훨씬 많은 동시 추론이 돌고, 박스가 OOM/스왑으로 wedge.

프런트(`vision/segment.ts`) 는 `503/5xx/timeout` 을 transient 로 분류해 지수 백오프 재시도하므로, 빠르게 503 을 돌려주면 사용자 입장에선 잠깐 지연될 뿐 작업 실패하지 않음.

#### 모델 라우팅

`model` enum 은 프론트 계약. 실제 추론은 내부 CPU 백엔드로 라우팅. 운영시 `X-Segment-Backend` 헤더와 `GET /v1/segment/models` 로 실제 구현 확인 가능. 모든 백엔드가 ultralytics 기반이라 의존성 스택 단일.

| 클라이언트 `model` | 백엔드 | weight | 크기 | 비고 |
|---|---|---|---|---|
| `sam3` (default) | FastSAM-s | `weights/FastSAM-s.pt` | 23MB | bbox prompt, 일반 세그먼테이션, CPU 0.5–1.5s |
| `sam2` | SAM 2.1-tiny | `weights/sam2.1_t.pt` | 75MB | Meta SAM 2.1 정식 (tiny), CPU 4–6s |
| `sam` | MobileSAM | `weights/mobile_sam.pt` | 39MB | 원본 SAM 호환 경량, CPU 1–2s |
| `mask-rcnn` | YOLOv8n-seg | `weights/yolov8n-seg.pt` | 7MB | COCO 80 class, `classHint` 필터, CPU 0.1–0.3s |
| `mask2former` | YOLO11x-seg | `weights/yolo11x-seg.pt` (split) | 120MB | 가장 무거운 instance seg. **120MB → 60MB 두 청크 split 후 commit, 빌드시 자동 reassemble** |

> Mask2Former 정식 weight 는 huggingface / dl.fbaipublicfiles.com 에 있어서 빌드 환경에서 직접 다운로드가 어려움. 대안으로 가장 무거운 ultralytics seg 모델 YOLO11x-seg 로 라우팅.

**모델 weight 는 리포에 직접 포함**(`./weights/`) 되어 airgapped 환경에서 추가 다운로드 없이 즉시 사용 가능. Dockerfile 이 `/opt/segment-weights/` 로 복사 + `SEGMENT_WEIGHTS_DIR` env 설정.

### 5.3 `GET /v1/segment/models`

서버가 받는 model id, 기본값, 실제 매핑된 백엔드 노출 (introspection / 디버깅).

### 5.4 `GET /healthz`, `GET /metrics`

- `/healthz` → `200 {"status":"ok"}` 또는 `503 {"error":"ffmpeg_unavailable"|"ffprobe_unavailable"}`
- `/metrics` → Prometheus exposition format. 메트릭: 카운터(outcome), 히스토그램(duration, bytes), 게이지(concurrent, queue)

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

---

## 9. 환경 변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `PORT` | `8080` | listen 포트 |
| `ALLOWED_ORIGINS` | — | CORS 화이트리스트 (쉼표 구분, 운영시 필수) |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `MAX_UPLOAD_BYTES` | `2147483648` (2GB) | normalize/resource 업로드 최대 크기 |
| `MAX_CONCURRENT_JOBS` | CPU 수 | 동시 ffmpeg 작업 수 |
| `JOB_TIMEOUT_MS` | `600000` (10분) | 단일 normalize 작업 타임아웃 |
| `FFMPEG_PATH` | `ffmpeg` | ffmpeg 바이너리 경로 |
| `FFPROBE_PATH` | `ffprobe` | ffprobe 바이너리 경로 |
| `FFMPEG_EXTRA_ARGS` | — | 튜닝용 추가 인자 (shell-split) |
| `TEMP_DIR` | 시스템 기본 | 임시 작업 디렉토리 |
| **Stateful API** | | |
| `DATABASE_URL` | — | SQLAlchemy URL (`postgresql+asyncpg://…`); 비우면 stateful API 비활성, legacy `/v1/*` 만 동작 |
| `DATA_DIR` | — | non-DB / non-S3 상태 루트 (현재는 tmp 만 사용) |
| `AUTO_MIGRATE` | `true` | startup 시 `alembic upgrade head` 자동 실행 (multi-replica 배포에선 비활성화) |
| **Object Storage** | | |
| `S3_ENDPOINT` | — | MinIO/S3 endpoint URL. 비우면 stateful API 비활성 |
| `S3_REGION` | `us-east-1` | |
| `S3_BUCKET` | `videonizer` | |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | — | |
| `S3_FORCE_PATH_STYLE` | `true` | MinIO 는 path-style 필수 |
| **Segmentation** | | |
| `SEGMENT_MAX_CONCURRENT` | `2` | 동시 추론 슬롯 (CPU 부하 제한) |
| `SEGMENT_TIMEOUT_MS` | `30000` | 슬롯 획득 후 단일 추론 wall-clock 한도. 초과 시 504 |
| `SEGMENT_MAX_QUEUE` | `16` | 슬롯 대기 큐 cap. 초과 시 즉시 503 + `Retry-After: 1` (0=무제한, 권장 X) |
| `SEGMENT_ACQUIRE_TIMEOUT_MS` | `10000` | 슬롯 대기 한도. 초과 시 503 + `Retry-After: 2` |
| `SEGMENT_MAX_UPLOAD_BYTES` | `16777216` (16MB) | 단일 프레임 업로드 한도 |
| `SEGMENT_CROP_PADDING` | `0.20` | bbox 주변 crop 패딩 비율 |
| `SEGMENT_POLYGON_EPSILON` | `0.002` | Douglas-Peucker tolerance (정규화 좌표) |
| `SEGMENT_WEIGHTS_DIR` | — | 모델 weight 디렉토리. Docker 이미지: `/opt/segment-weights` 기본 |
| `SEGMENT_PRELOAD_MODELS` | — | startup 시 미리 로드할 model id (쉼표 구분) |

---

## 10. 실행

### 로컬 개발

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# stateful API 쓸 때만 — 인프라 띄우고 .env 채우기
cp .env.example .env
docker compose up -d postgres minio

.venv/bin/pytest
.venv/bin/python -m app.main      # http://localhost:8080
```

`DATABASE_URL` / `S3_ENDPOINT` 비우면 stateful API 가 꺼지고 legacy `/v1/normalize`, `/v1/segment` 만 동작 — 외부 의존성 0개로 기존 서비스만 실행 가능.

### Docker (legacy + segment 단독 이미지)

2-stage 빌드: `jrottenberg/ffmpeg:7.1-ubuntu` 에서 ffmpeg/ffprobe + 의존 라이브러리를 `/opt/ffmpeg/` 로 복사하고, 얇은 wrapper 가 `LD_LIBRARY_PATH=/opt/ffmpeg/lib` 만 ffmpeg/ffprobe 에 적용해서 Python 의 시스템 libssl 과 분리. 빌드 중 `ffmpeg -version` / `ffprobe -version` + `python -c "import ssl"` 로 linkage 회귀 차단.

```bash
docker build -t videonizer .
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  videonizer
```

컨테이너 내부 포트는 `8000` (Dockerfile `ENV PORT=8000`); 로컬 개발 (`python -m app.main`) 은 코드 기본값 `8080`.

#### 소스 변경 시 재빌드 없이 개발

```bash
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  -e UVICORN_RELOAD=1 \
  -v "$(pwd)/app:/srv/app" \
  videonizer
```

#### 폐쇄망 (airgapped) 빌드

모델 weight 는 리포에 포함되어 런타임 네트워크 접근 불필요. 내부 pypi 미러 사용시:

```bash
docker build \
  --build-arg PIP_INDEX_URL=<your-internal-index-url> \
  --build-arg PIP_EXTRA_INDEX_URL=<your-internal-extra-index-url> \
  --build-arg PIP_TRUSTED_HOST=<your-internal-host> \
  -t videonizer .
```

**주의**

- 내부 미러에 `torch==2.5.1` 확인 필요. 일반 pypi proxy 면 CUDA wheel (~800MB) 이 깔림. 디스크 절약하려면 CPU wheel 을 미러에 별도 업로드 또는 미러에 `download.pytorch.org/whl/cpu` proxy 추가 권장.
- `ultralytics` 8.3.45 / 8.3.46 은 upstream yank 됨. 현재 pin `8.3.44`.

### Stateful API 운영 (compose)

`docker-compose.yml` 이 postgres + minio 를 host-only bind 로 띄움 (`/appdata/storage/videonizer/{pg,minio}` 볼륨). 자세한 운영 체크리스트는 `WORK_PLAN.md §6` 참조.

```bash
docker compose up -d postgres minio
python -m app.main              # 또는 docker compose 에 app 추가
```

### 클라이언트 설정

```bash
# .env.local (Next.js)
NEXT_PUBLIC_VIDEO_NORMALIZE_ENDPOINT=http://localhost:8080/v1/normalize?async_job=true
NEXT_PUBLIC_API_BASE=http://localhost:8080/api
```

---

## 11. 수용 기준 (legacy 서비스)

- [ ] AVI (xvid/divx), MKV (h264+aac), WMV, FLV 샘플 각 1개 정규화 결과가 Chrome/Safari/Firefox 에서 재생됨
- [ ] 이미 H.264/AAC/MP4 인 파일은 remux 만 수행, < 5초
- [ ] 2GB 초과 업로드는 413
- [ ] 텍스트 파일을 `.mp4` 로 rename 한 입력은 422 (5xx 아님)
- [ ] 10분 초과 작업은 504 + 임시파일 0개 잔존
- [ ] wasm 경로와 출력 규격 동일 — 동일 입력시 시각적으로 동등
- [ ] 서버 다운시 클라이언트가 wasm 폴백으로 자동 전환
