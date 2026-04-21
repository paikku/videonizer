# Videonizer Normalize Service

브라우저에서 직접 재생 불가능한 비디오(AVI/MKV/WMV/FLV 등)를 H.264/AAC MP4로 재인코딩해서 반환하는 FastAPI 마이크로서비스. 클라이언트(`ServerNormalizeAdapter`, `features/media/service/normalize.ts`)가 기대하는 계약에 맞춰 구현됨.

---

## 1. 인터페이스 계약

### `POST /v1/normalize`

| 항목 | 값 |
|---|---|
| Content-Type (요청) | `multipart/form-data` |
| `file` (필수) | 원본 비디오 바이너리 |
| `profile` (옵션) | `web-h264` (기본, 향후 확장) |
| 응답 Content-Type | `video/mp4` |
| 응답 Body | 변환된 MP4 (스트리밍) |

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
| `upload_too_large` | 413 | `MAX_UPLOAD_BYTES` 초과 |
| `invalid_input` | 422 | ffprobe 실패 (비디오 아님) |
| `no_video_stream` | 422 | 비디오 스트림 없음 |
| `timeout` | 504 | `JOB_TIMEOUT_MS` 초과 |
| `ffmpeg_failed` | 500 | ffmpeg 비정상 종료 |
| `ffmpeg_unavailable` | 503 | `/healthz` 에서만, 바이너리 없음 |

### `GET /healthz`

- `200 {"status":"ok"}` — ffmpeg 바이너리 확인됨
- `503 {"error":"ffmpeg_unavailable",...}` — 없음

### `GET /metrics`

Prometheus exposition format.

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
| F-4.1 동시성 제한 + 큐잉 | `JobLimiter` (`asyncio.Semaphore`) |
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
│   ├── errors.py           # NormalizeError 계층 (code/status 매핑)
│   ├── probe.py            # ffprobe 래퍼, ProbeResult
│   ├── normalize.py        # ffmpeg 커맨드 빌더 + 실행 + 타임아웃
│   ├── jobs.py             # JobLimiter (세마포어 + 메트릭)
│   ├── metrics.py          # Prometheus 레지스트리/메트릭
│   └── logging_conf.py     # JSON 포매터
└── tests/
    ├── test_build_ffmpeg_cmd.py   # argv 빌더 검증 (F-2.*, F-5.2)
    ├── test_probe.py              # 회전 추출, is_web_compatible
    └── test_api.py                # 라우트 계약 (스텁된 ffmpeg)
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

```bash
docker build -t videonizer-normalize .
docker run --rm -p 8080:8080 \
  -e ALLOWED_ORIGINS=http://localhost:3000 \
  videonizer-normalize
```

### 클라이언트 설정

```bash
# .env.local (Next.js)
NEXT_PUBLIC_VIDEO_NORMALIZE_ENDPOINT=http://localhost:8080/v1/normalize
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
- F-6.2 비동기 잡 API — `POST /jobs` / `GET /jobs/{id}` / `GET /jobs/{id}/result` (클라이언트 어댑터도 교체 필요)
- F-8.1 진행률 스트리밍 — ffmpeg stderr 파싱 → SSE 또는 `X-Progress` 트레일러
- 수용 기준 통합 테스트 — 실제 샘플 영상 픽스처로 Chrome/Safari/Firefox 재생 회귀
