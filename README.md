# Videonizer Normalize Service

브라우저에서 직접 재생 불가능한 비디오(AVI/MKV/WMV/FLV 등)를 H.264/AAC MP4로 재인코딩해서 반환하는 FastAPI 마이크로서비스. 클라이언트(`ServerNormalizeAdapter`, `features/media/service/normalize.ts`)가 기대하는 계약에 맞춰 구현됨.

같은 프로세스에서 이미지 세그먼테이션 엔드포인트 (`POST /v1/segment`) 도 제공한다. 라벨 hover + `H` 단축키로 어노테이션 경계를 모델로 다시 맞추는 용도. **CPU-only** 전제. 다섯 개 public model id 는 각각 별개의 weight (`FastSAM-s`, `SAM 2.1-tiny`, `MobileSAM`, `YOLOv8n-seg`, `YOLO11x-seg`) 로 라우팅되며, weight 파일 전부 `./weights/` 에 리포와 함께 포함돼 airgapped 이미지 빌드 시 추가 다운로드가 필요 없다. 100MB 가 넘는 weight 는 60MB 청크로 split 해서 commit, Dockerfile 빌드 단계에서 자동 reassemble. 자세한 내용은 §1.5 / §9 참조.

---

## 1. 인터페이스 계약

### `POST /v1/normalize`

| 항목 | 값 |
|---|---|
| Content-Type (요청) | `multipart/form-data` |
| `file` (필수) | 원본 비디오 바이너리 |
| `profile` (옵션) | `web-h264` (기본, 향후 확장) |
| `async_job` (옵션) | `true`면 비동기 잡 생성 모드로 동작 |
| 응답 Content-Type | `video/mp4` |
| 응답 Body | 변환된 MP4 (스트리밍) |

#### 동기 모드 (기본)

- `async_job` 미지정(또는 `false`)이면 기존처럼 변환된 MP4를 바로 스트리밍 응답한다.

#### 비동기 모드 (`async_job=true`)

- `202 Accepted` + `application/json` 응답:

```json
{
  "jobId": "....",
  "statusUrl": "http://<host>/v1/normalize/jobs/<jobId>",
  "resultUrl": "http://<host>/v1/normalize/jobs/<jobId>/result"
}
```

- `statusUrl`, `resultUrl`는 절대 URL로 반환된다.

**응답 헤더**

- `Content-Length`
- `X-Normalize-Duration-Ms` — 서버 처리 시간
- `X-Normalize-Input-Codec` — 원본 비디오 코덱명 (디버깅용)
- `X-Normalize-Remuxed` — `1`이면 재인코딩 없이 remux만 수행됨

**에러 응답 (non-2xx)**

```json
{ "error": "code", "message": "..." }
```

| code | HTTP | 의미 |
|---|---|---|
| `unsupported_media_type` | 415 | 지원하지 않는 컨테이너/코덱 |
| `upload_too_large` | 413 | `MAX_UPLOAD_BYTES` 초과 |
| `invalid_input` | 422 | ffprobe가 입력을 거부 (비디오 아님) |
| `no_video_stream` | 422 | 비디오 스트림 없음 |
| `timeout` | 504 | `JOB_TIMEOUT_MS` 초과 |
| `ffmpeg_failed` | 422 | ffmpeg 디코딩/인코딩 실패 |
| `ffprobe_unavailable` | 503 | ffprobe 바이너리가 실행 불가(링커 오류, 미설치) |
| `ffmpeg_unavailable` | 503 | `/healthz`에서만, ffmpeg 바이너리 실행 불가 |

> `ffprobe_unavailable` 503은 `/v1/normalize` 응답에서도 발생. 클라이언트는 non-2xx를 보고 자동으로 wasm 폴백으로 전환하므로 사용자 입장에서는 투명하게 넘어감.

### `POST /v1/segment`

라벨 hover + `H` 단축키로 호출되는 이미지 세그먼테이션 엔드포인트. 한 번의 요청 = 한 어노테이션 재-맞춤.

| 항목 | 값 |
|---|---|
| Method | `POST` |
| Content-Type (요청) | `multipart/form-data` |
| `file` (필수) | 단일 프레임 JPEG/PNG 바이트 |
| `region` (필수) | JSON 문자열 `{"x":0..1,"y":0..1,"w":>0,"h":>0}` (정규화 좌표) |
| `model` (옵션) | `sam3` (기본) / `sam2` / `sam` / `mask2former` / `mask-rcnn` |
| `classHint` (옵션) | 라벨 클래스 힌트 — 향후 모델 conditioning 용 (현재 MVP 는 로깅만) |

**응답** (`200 OK`, `application/json`)

```json
{
  "polygon": [[[x,y], ...], ...],
  "rect":  {"x":..., "y":..., "w":..., "h":...},
  "score": 0.93
}
```

- `polygon` ring 0 = 외곽 boundary, ring 1.. = holes (even-odd fill).
- 좌표는 정규화 `[0..1]`, 좌상단 원점.
- 모델이 객체를 못 찾으면 `200 {}` (no-op) — 클라이언트는 기존 라벨 유지.

**응답 헤더**

- `X-Segment-Backend` — 실제로 추론한 CPU 백엔드 id (예: `fastsam-s`, `sam2.1-tiny`, `mobile-sam`, `yolov8n-seg`, `yolo11x-seg`)
- `X-Segment-Duration-Ms` — 서버 처리 시간

**에러**

| 시나리오 | HTTP | `error` | 비고 |
|---|---|---|---|
| 지원하지 않는 `model` id | 400 | `unsupported model` | |
| `region` JSON 파싱/범위 실패 | 400 | `invalid_region` | |
| 이미지 디코딩 실패 | 400 | `image_decode_failed` | |
| 인식 못 하는 이미지 포맷 | 415 | `unsupported_media_type` | |
| 업로드 크기 초과 (`SEGMENT_MAX_UPLOAD_BYTES`) | 413 | `upload_too_large` | |
| 추론 시간 초과 (`SEGMENT_TIMEOUT_MS`) | 504 | `timeout` | 슬롯 획득 후 추론 단계 |
| 큐 대기 시간 초과 (`SEGMENT_ACQUIRE_TIMEOUT_MS`) | 503 | `busy` | `Retry-After: 2` |
| 큐 길이 cap 초과 (`SEGMENT_MAX_QUEUE`) | 503 | `busy` | `Retry-After: 1` |
| 백엔드 로드 실패 (가중치 누락 등) | 503 | `backend_unavailable` | |

#### 부하 제어 / 동시성 정책

`/v1/segment` 는 CPU 추론이 무거워 burst 트래픽에 약하다. 다음 3단 방어로 *서버가 응답을 못하는* 상태를 막는다:

1. **큐 길이 cap (`SEGMENT_MAX_QUEUE`)** — 대기 큐가 가득 차면 즉시 `503 + Retry-After: 1` 반환. 무제한 큐가 쌓여 프록시 타임아웃이 먼저 터지는 패턴(클라이언트는 hang 으로 보임)을 차단한다.
2. **acquire timeout (`SEGMENT_ACQUIRE_TIMEOUT_MS`)** — 슬롯을 잡지 못하고 대기하던 요청도 일정 시간이 지나면 `503 + Retry-After: 2` 로 반환. `SEGMENT_TIMEOUT_MS` (추론 단계) 와 별도.
3. **슬롯-스레드 결합** — `asyncio.wait_for` 가 추론 타임아웃되면 클라이언트엔 `504` 를 즉시 보내지만 워커 스레드(파이썬 스레드는 cooperative cancel 불가)가 실제로 종료될 때까지 세마포어를 잡고 있는다. 이 결합이 없으면 좀비 스레드 위에 새 추론이 누적돼 결국 `SEGMENT_MAX_CONCURRENT` 보다 훨씬 많은 동시 추론이 돌고, 박스가 OOM/스왑으로 wedge 되어 *어떤 요청에도 응답하지 못하는* 상태가 된다.

프런트(`vision/segment.ts`) 는 `503/5xx/timeout` 을 transient 로 분류해 지수 백오프 재시도하므로, 서버가 빠르게 503 을 돌려주면 사용자 입장에선 잠깐 지연될 뿐 작업이 실패하지 않는다.

#### 모델 라우팅 (MVP)

`model` enum 은 프론트 계약. 실제 추론은 내부 CPU 백엔드로 라우팅되며, 운영시 `X-Segment-Backend` 헤더와 `GET /v1/segment/models` 로 실제 구현을 확인할 수 있다. 모든 백엔드가 ultralytics 기반이라 의존성 스택은 단일하게 유지된다.

| 클라이언트 `model` | 백엔드 | weight | 크기 | 비고 |
|---|---|---|---|---|
| `sam3` (default) | FastSAM-s | `weights/FastSAM-s.pt` | 23MB | bbox prompt, 일반 세그먼테이션, CPU 0.5–1.5s |
| `sam2` | SAM 2.1-tiny | `weights/sam2.1_t.pt` | 75MB | Meta SAM 2.1 정식 (tiny), CPU 4–6s |
| `sam` | MobileSAM | `weights/mobile_sam.pt` | 39MB | 원본 SAM 호환 경량 변종, CPU 1–2s |
| `mask-rcnn` | YOLOv8n-seg | `weights/yolov8n-seg.pt` | 7MB | COCO 80 class, `classHint` 로 필터, CPU 0.1–0.3s |
| `mask2former` | YOLO11x-seg | `weights/yolo11x-seg.pt` (split) | 120MB | 가장 무거운 instance seg, CPU 1–2s. **120MB 라 60MB 두 청크로 split 후 커밋, 빌드시 자동 reassemble** |

> Mask2Former 정식 weight 는 huggingface / dl.fbaipublicfiles.com 에 있어서 빌드 환경에서 직접 다운로드가 어렵다. 대안으로 가장 무거운 ultralytics seg 모델인 YOLO11x-seg 로 라우팅. 추후 정식 Mask2Former 가 필요하면 §9 참조.

### `GET /v1/segment/models`

서버가 받아들이는 model id, 기본값, 실제 매핑된 백엔드를 노출 (introspection / 디버깅).

### `GET /healthz`

- `200 {"status":"ok"}` — ffmpeg 바이너리 확인됨
- `503 {"error":"ffmpeg_unavailable",...}` — 없음

### `GET /metrics`

Prometheus exposition format.

### 디코딩 진행률용 비동기 API

- `POST /v1/normalize/jobs` : 업로드 + 잡 생성 (`202 Accepted`)
- `GET /v1/normalize/jobs/{jobId}` : `{status, state, progress}` 폴링
- `GET /v1/normalize/jobs/{jobId}/result` : 완료 후 MP4 스트리밍 반환

`progress` 는 ffmpeg `-progress pipe:2` 출력(`out_time_ms`) 기반 추정치로, 긴 디코딩 구간을 프런트에서 `decoding` 퍼센트로 표시할 수 있다.
`state`는 `status`와 동일 값(호환용 alias)이다.

---

## 2. 변환 규격

`features/media/service/normalize.ts` 의 wasm 어댑터와 동일(F-2.*). `app/normalize.py::build_ffmpeg_cmd` 참조.

**재인코딩 경로** (기본)

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

**remux 경로** — 입력이 이미 H.264 + AAC + MP4 + 회전메타 없음 + 짝수 해상도일 때

```
ffmpeg -nostdin -y -i <IN>
  -map 0:v:0 [-map 0:a:0?]
  -c copy -movflags +faststart
  <OUT>
```

---

## 3. 기능 요구사항 매핑

| 항목 | 구현 위치 |
|---|---|
| F-1.1 다양한 컨테이너 수용 | ffprobe로 실제 판별 (MIME 무시) |
| F-1.2 업로드 크기 제한 (413) | `_stream_to_disk` 청크 누적 + Content-Length 사전 체크 |
| F-1.3 MIME 불신 | `app/probe.py::ffprobe` |
| F-1.4 비디오 스트림 없으면 422 | `NoVideoStream` |
| F-2.1~2.4 변환 파라미터 | `build_ffmpeg_cmd` 재인코딩 분기 |
| F-2.5 remux 분기 | `ProbeResult.is_web_compatible` |
| F-3.1 VFR 타임스탬프 보존 | `-fps_mode passthrough` |
| F-3.2 회전 반영 + 메타 제거 | rotation != 0이면 재인코딩 + `rotate=0` |
| F-3.3 duration ±0.1s | passthrough + 코덱 레벨 timestamp 보존 |
| F-4.1 동시성 제한 + 큐잉 | `JobLimiter` (`asyncio.Semaphore`) — segment 경로는 `_run_segment_in_slot` 가 큐 cap·acquire timeout·스레드-슬롯 결합까지 추가 |
| F-4.2 타임아웃 + 강제종료 | `_run_ffmpeg`: `start_new_session` + `os.killpg(SIGKILL)` |
| F-4.3 임시파일 정리 | 성공/실패/타임아웃 모두 `shutil.rmtree` |
| F-4.4 하드웨어 인코더 튜닝 | `FFMPEG_EXTRA_ARGS` 로 외부 주입 |
| F-5.1 CORS 화이트리스트 | `CORSMiddleware` + `ALLOWED_ORIGINS` |
| F-5.2 command injection 차단 | `asyncio.create_subprocess_exec`(argv) — shell 미사용 |
| F-5.3 디스크 스트리밍 업로드 | `_stream_to_disk` 1MiB 청크, 메모리 적재 X |
| F-5.4 ffprobe 선검증 | 항상 수행 |
| F-5.6 영속 저장 금지 | 응답 종료 후 `shutil.rmtree` |
| F-6.1 동기 응답 | `StreamingResponse` |
| F-7.1 헬스체크 | `/healthz` + lifespan에서 `ffmpeg -version` |
| F-7.2 구조화 로그 | `JsonFormatter` + `job_id`, 입력/출력 크기, 소요시간 |
| F-7.3 메트릭 | `/metrics` — 카운터(outcome), 히스토그램(duration, bytes), 게이지(concurrent, queue) |

---

## 4. 환경 변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `PORT` | `8080` | listen 포트 |
| `MAX_UPLOAD_BYTES` | `2147483648` (2GB) | 업로드 최대 크기 |
| `MAX_CONCURRENT_JOBS` | CPU 수 | 동시 ffmpeg 작업 수 |
| `JOB_TIMEOUT_MS` | `600000` (10분) | 단일 작업 타임아웃 |
| `ALLOWED_ORIGINS` | — | CORS 화이트리스트 (쉼표 구분) |
| `FFMPEG_PATH` | `ffmpeg` | ffmpeg 바이너리 경로 |
| `FFPROBE_PATH` | `ffprobe` | ffprobe 바이너리 경로 |
| `FFMPEG_EXTRA_ARGS` | — | 튜닝용 추가 인자 (shell-split) |
| `TEMP_DIR` | 시스템 기본 | 임시 작업 디렉토리 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `SEGMENT_MAX_CONCURRENT` | `2` | `/v1/segment` 동시 추론 슬롯 (CPU 부하 제한) |
| `SEGMENT_TIMEOUT_MS` | `30000` | 슬롯 획득 후 단일 추론 wall-clock 한도. 초과 시 504 반환 |
| `SEGMENT_MAX_QUEUE` | `16` | 슬롯 대기 큐 길이 cap. 초과 시 즉시 503 + `Retry-After: 1` (0=무제한, 권장 X) |
| `SEGMENT_ACQUIRE_TIMEOUT_MS` | `10000` | 슬롯 대기 한도. 초과 시 503 + `Retry-After: 2` |
| `SEGMENT_MAX_UPLOAD_BYTES` | `16777216` (16MB) | 단일 프레임 업로드 한도 |
| `SEGMENT_CROP_PADDING` | `0.20` | bbox 주변 crop 패딩 비율 (CPU 절약) |
| `SEGMENT_POLYGON_EPSILON` | `0.002` | Douglas-Peucker 단순화 tolerance (정규화 좌표) |
| `SEGMENT_WEIGHTS_DIR` | — | 모델 weight 디렉토리. Docker 이미지에서는 `/opt/segment-weights` 로 기본 설정됨 |
| `SEGMENT_PRELOAD_MODELS` | — | startup 시 미리 로드할 model id (쉼표 구분, 예: `sam3`) |

---

## 5. 파일 구조

```
.
├── Dockerfile              # python:3.12-slim + ffmpeg apt 설치
├── requirements.txt        # 런타임 의존성
├── requirements-dev.txt    # + pytest, httpx
├── .env.example
├── app/
│   ├── main.py             # FastAPI 앱, 라우트, 미들웨어, lifespan
│   ├── config.py           # Settings (pydantic-settings)
│   ├── errors.py           # ServiceError → NormalizeError / SegmentError 계층
│   ├── probe.py            # ffprobe 래퍼, ProbeResult
│   ├── normalize.py        # ffmpeg 커맨드 빌더 + 실행 + 타임아웃
│   ├── jobs.py             # JobLimiter (세마포어 + 메트릭)
│   ├── metrics.py          # Prometheus 레지스트리/메트릭
│   ├── logging_conf.py     # JSON 포매터
│   └── segment/            # /v1/segment 파이프라인
│       ├── service.py      # 입력 파싱 → crop → 추론 → 폴리곤 변환
│       ├── registry.py     # public model id → CPU 백엔드 매핑 (lazy load)
│       ├── polygon.py      # mask → polygon ring 변환 (cv2 + DP simplify)
│       └── backends/       # FastSAM / SAM(2,Mobile) / YOLO-seg (전부 ultralytics)
├── weights/                # 5종 모델 weight 리포 포함 — airgapped 즉시 사용
│   ├── FastSAM-s.pt        # sam3 (23MB)
│   ├── sam2.1_t.pt         # sam2 (75MB)
│   ├── mobile_sam.pt       # sam (39MB)
│   ├── yolov8n-seg.pt      # mask-rcnn (7MB)
│   └── yolo11x-seg.pt.part_00 / .part_01   # mask2former (120MB, split)
└── tests/
    ├── test_build_ffmpeg_cmd.py   # argv 빌더 검증 (F-2.*, F-5.2)
    ├── test_probe.py              # 회전 추출, is_web_compatible
    ├── test_api.py                # 라우트 계약 (스텁된 ffmpeg)
    └── test_segment.py            # /v1/segment + 폴리곤 유틸 (스텁된 백엔드)
```

---

## 6. 실행

### 로컬 개발

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/python -m app.main
```

### Docker

이미지는 2-stage 빌드: `jrottenberg/ffmpeg:7.1-ubuntu`에서 ffmpeg/ffprobe 바이너리와 `/usr/local/lib` 전체를 `/opt/ffmpeg/`로 복사하고, `/usr/local/bin/` 아래에 `LD_LIBRARY_PATH=/opt/ffmpeg/lib`를 설정하는 얇은 래퍼를 둬서 Python이 시스템 libssl을 계속 쓰게 분리함. 빌드 중 `ffmpeg -version`/`ffprobe -version` + `python -c "import ssl"`로 linkage 회귀를 막음.

```bash
docker build -t videonizer-normalize .
docker run --rm   -p 0.0.0.0:8000:8000   -e ALLOWED_ORIGINS=http://12.54.79.86:3000   -e UVICORN_RELOAD=1   -v "$(pwd)/app:/srv/app"   videonizer-normalize   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

컨테이너 내부 포트는 `8000` (Dockerfile `ENV PORT=8000`). 로컬 개발(`python -m app.main`)은 코드 기본값 `8080` 사용.

#### 소스 변경 시 재빌드 없이 개발하기

`UVICORN_RELOAD=1` + bind mount로 실행하면 코드 변경이 컨테이너에서 자동 반영된다.

```bash
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  -e UVICORN_RELOAD=1 \
  -v "$(pwd)/app:/srv/app" \
  videonizer-normalize
```

#### 폐쇄망 (airgapped) 빌드

모델 weight 는 `./weights/FastSAM-s.pt` 로 **리포에 직접 포함** 되어 있고, Dockerfile 이 `/opt/segment-weights/` 로 복사한 뒤 `SEGMENT_WEIGHTS_DIR` / `YOLO_CONFIG_DIR` env 를 걸어두기 때문에, 추가 다운로드 없이 이미지만 빌드하면 된다. 런타임에서 네트워크를 안 친다.

내부 pypi 미러 사용 시 `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_TRUSTED_HOST` build-arg 로 pip 인덱스를 우회한다:

```bash
docker build \
  --build-arg PIP_INDEX_URL=<your-internal-index-url> \
  --build-arg PIP_EXTRA_INDEX_URL=<your-internal-extra-index-url> \
  --build-arg PIP_TRUSTED_HOST=<your-internal-host> \
  -t videonizer .
```

**주의사항**

- 내부 미러에 `torch==2.5.1` 이 있는지 확인할 것. 일반 pypi proxy 라면 CUDA wheel (~800MB install) 이 깔린다. 디스크 아끼려면 **CPU wheel** 을 미러에 별도 업로드하거나, 미러에 `download.pytorch.org/whl/cpu` proxy 를 추가해달라고 요청 권장.
- `ultralytics` 버전 중 8.3.45 / 8.3.46 은 upstream 에서 yank 되어 미러에서 404 가 날 수 있다. 현재 pin 은 `8.3.44`.

### 클라이언트 설정

```bash
# .env.local (Next.js)
NEXT_PUBLIC_VIDEO_NORMALIZE_ENDPOINT=http://localhost:8080/v1/normalize?async_job=true
```

---

## 7. 수용 기준

- [ ] AVI (xvid/divx), MKV (h264+aac), WMV, FLV 샘플 각 1개 정규화 결과가 Chrome/Safari/Firefox에서 재생됨
- [ ] 이미 H.264/AAC/MP4인 파일은 remux만 수행, < 5초
- [ ] 2GB 초과 업로드는 413
- [ ] 텍스트 파일을 `.mp4`로 rename한 입력은 422 (5xx 아님)
- [ ] 10분 초과 작업은 504 + 임시파일 0개 잔존
- [ ] wasm 경로와 출력 규격 동일 (F-2.*) — 동일 입력 시 시각적으로 동등
- [ ] 서버 다운 시 클라이언트가 wasm 폴백으로 자동 전환

---

## 8. 향후 작업

- F-4.4 NVENC/QSV/VAAPI 프로필 — CRF/bitrate 튜닝 후 `FFMPEG_EXTRA_ARGS` 로 주입하거나 `profile` 파라미터 매핑
- F-5.5 인증 — 단기 서명 토큰 헤더 검증 미들웨어
- F-8.1 진행률 스트리밍 — ffmpeg stderr 파싱 → SSE 또는 `X-Progress` 트레일러
- 수용 기준 통합 테스트 — 실제 샘플 영상 픽스처로 Chrome/Safari/Firefox 재생 회귀

---

## 9. 세그먼테이션 다음 단계 (MVP → Phase 2)

현재 MVP 는 **다섯 개 client model id 가 각각 별개 weight + 백엔드 인스턴스로 라우팅**된다 (`app/segment/registry.py::_ROUTING`). 후속 패스 후보:

1. **Mask2Former 정식 모델** — 현재 `mask2former` id 는 YOLO11x-seg 로 라우팅 중. 진짜 Mask2Former Swin-Tiny (HF `facebook/mask2former-swin-tiny-coco-instance`) 로 가려면 ~200MB 가중치를 split 4 청크로 commit + `transformers` 패키지 추가 + 새 백엔드 클래스 (`Mask2FormerBackend`) 작성 필요. 빌드 환경에서 huggingface 접속이 막혀 MVP 에서 미구현.
2. **이미지 임베딩 캐시** — `(frame_hash, model)` LRU. 같은 프레임에서 여러 라벨을 다시-맞출 때 image encoder 재실행 회피 (SAM 계열 비용의 80%).
3. **ONNX Runtime + INT8** — 양자화된 ONNX 변종으로 교체. 의존성 무게도 가벼워짐 (`ultralytics` / `torch` 제거 가능).
4. **GPU 분리** — `SEGMENT_REMOTE_URL` env 로 추론을 별도 GPU 서비스에 위임. 본 서비스는 프록시 + 폴리곤 변환만 담당.
5. **다중 오브젝트 탐지 엔드포인트** — `POST /v1/detect` 로 분리. 현재 `/v1/segment` 는 단일 오브젝트 계약 유지.
