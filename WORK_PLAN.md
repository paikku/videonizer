# Backend Migration Work Plan

> 단일 진실: 새 stateful API (Project / Resource / Image / LabelSet / Annotation / Export)
> 를 기존 stateless 서비스 (`/v1/normalize`, `/v1/segment`) 위에 점진 추가한다.
> 인프라는 PostgreSQL 16 + MinIO + docker compose. 서버 root: `/appdata/storage/videonizer`.

---

## 0. 원칙

- 기존 `/v1/normalize` · `/v1/segment` 라우트와 테스트는 손대지 않는다. 신규 코드는 `app/api/`, `app/storage/`, `app/services/`, `app/domain/` 아래에만.
- PR 단위는 작게 (6개), 각 PR 단독 머지·배포 가능.
- 각 PR마다 `tests/test_<area>*.py` 추가, CI 그린 유지.
- 의존성은 PR마다 필요한 것만 `requirements.txt` 에 추가.
- 단일 PC default 운영 시나리오:
  1. `docker compose up -d postgres minio`
  2. `python -m app.main`
  3. `pytest`

---

## 1. 인프라 결정 (확정)

| 레이어 | 선택 |
|---|---|
| Metadata DB | PostgreSQL 16 (asyncpg) |
| Object Storage | MinIO (S3 호환, boto3) |
| 배포 | docker compose (postgres + minio), app은 host venv 또는 컨테이너 |
| 데이터 루트 | `/appdata/storage/videonizer/{pg,minio,tmp}` |
| 백업 루트 | `/appdata/backup/videonizer/` |
| 계정 | `appuser:appgroup` |

DB 이름 / 버킷 이름 모두 `videonizer`. 비밀번호/액세스키는 `.env.example` placeholder, 실제 값은 서버에서 채움.

---

## 2. 디스크 / 버킷 레이아웃

### 디스크

```
/appdata/storage/videonizer/
  pg/              # postgres data (compose 볼륨)
  minio/           # minio data  (compose 볼륨)
  tmp/             # 업로드 스트리밍 임시 (TEMP_DIR)
```

### Blob 키 규칙 (단일 버킷 `videonizer`)

```
p/<pid>/r/<rid>/source.<ext>
p/<pid>/r/<rid>/previews/0000.jpg
p/<pid>/i/<iid>/bytes.<ext>
p/<pid>/i/<iid>/thumb.jpg
```

DB는 `blob_key` 만 보유 (S3 endpoint URL은 박지 않음).

---

## 3. 모듈 구조 (목표 형태)

```
app/
  main.py                       # 라우터 등록 + lifespan
  config.py                     # +DATABASE_URL, S3_*, DATA_DIR
  errors.py                     # NotFound, ValidationError 추가
  api/
    _deps.py                    # get_session, current_user_id (anonymous)
    _range.py                   # Range 파서 + 206 응답 헬퍼
    projects.py
    resources.py
    images.py
    labelsets.py
    annotations.py
    export.py
  domain/                       # pydantic 모델 (frontend types.ts 미러)
    common.py
    projects.py
    resources.py
    images.py
    labelsets.py
    annotations.py
  storage/
    db.py                       # async engine, SessionLocal
    blobs.py                    # BlobStore 인터페이스 + S3BlobStore
    repo/
      projects.py
      resources.py
      images.py
      labelsets.py
      annotations.py
  services/
    uploads.py                  # 스트리밍 업로드 → blob
    previews.py
    thumbnails.py               # PIL 썸네일
    image_ingest.py
    annotations_patch.py
    export_validate.py
    export_zip.py
  migrations/
    env.py
    versions/
      0001_init.py              # baseline
      0002_projects.py
      0003_resources.py
      0004_images.py
      0005_labelsets.py
      0006_annotations.py
```

---

## 4. PR 분할

### PR #1 — Infra 부트스트랩 (라우트 0개)

**목적**: DB·MinIO 연결 + 마이그레이션 + 추상 Blob 스토어 배선.

**산출물**:
- `docker-compose.yml` (postgres:16-alpine + minio/minio)
- `alembic.ini` + `app/migrations/`
- `app/storage/db.py`, `app/storage/blobs.py`
- `app/api/_deps.py` (`get_session`, `current_user_id`)
- `app/main.py` lifespan 확장
- `.env.example` 갱신
- `tests/test_storage_smoke.py` (DB ping, bucket head, blob put/get/delete/range)

**의존성 추가**: `sqlalchemy[asyncio]>=2`, `asyncpg`, `alembic`, `boto3`

**수용 기준**:
- `docker compose up -d` → `python -m app.main` → `/healthz` 200
- `pytest -q` 그린 (기존 + smoke)
- 기존 `/v1/normalize`, `/v1/segment` 회귀 없음

### PR #2 — Projects

**라우트** (4):
- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{id}`
- `DELETE /api/projects/{id}`

**스키마**: `projects(id, name, created_at)`

**산출물**:
- `app/domain/{common,projects}.py`
- `app/storage/repo/projects.py`
- `app/api/projects.py`
- `app/migrations/versions/0002_projects.py`
- `tests/test_projects_api.py`

`ProjectSummary` 의 카운트 필드는 PR #2 시점엔 0 으로 두고, PR #3/#4/#5 에서 SQL COUNT(*) 로 단계적 교체.

### PR #3 — Resources

**라우트** (9):
- list, create (multipart), get, patch, delete, **bulk delete**, source (Range), previews POST, previews GET

**스키마**: `resources(id, project_id, type, name, tags JSONB, width, height, duration, ingest_via, source_blob_key, preview_count, created_at)`

**핵심 위험**:
- 업로드 스트림: `services/uploads.py::stream_upload_to_blob` (S3 multipart upload)
- Range 응답: `app/api/_range.py` (단일 range, 206)
- Cascade delete: `BlobStore.delete_prefix(p/<pid>/r/<rid>/)`

POST `/resources/[rid]/images` 는 Image 도메인이 PR #4 에 있으니 PR #3 에서는 스텁만, PR #4 에서 본구현.

### PR #4 — Images

**라우트** (8 + 1 본구현):
- list (`?resourceId&source&tag`), get, patch, delete, **bulk delete**, bytes, thumb, bulk tag, **(완성) `POST /resources/[rid]/images`**

**스키마**: `images(id, project_id, resource_id, source, file_name, width, height, timestamp, frame_index, tags JSONB, bytes_blob_key, thumb_blob_key, created_at)`

**썸네일**: PIL `thumbnail((256,256))` 유지비율, JPEG q=80, RGB 강제. 이미지 INSERT 트랜잭션 안에서 생성 (실패시 row 미생성).

**Idempotence**: client-allocated `id` 가 있으면 동일 키에 INSERT 충돌시 기존 row 반환 → retry 안전.

### PR #5 — LabelSets (annotations 제외)

**라우트** (6):
- list (lightweight), create, get (full), **summary**, patch, delete

**스키마**: `labelsets(id, project_id, name, type, description, classes JSONB, image_ids JSONB, excluded_image_ids JSONB, created_at)`

**`/summary` 캐시**: `Cache-Control: no-store` (annotation save 후 즉시 invalidate).

annotations 테이블이 없는 시점이라 통계 필드는 0/빈 배열, PR #6 에서 실집계로 교체.

### PR #6 — Annotations + Export

**라우트** (6):
- annotations GET / PUT / **PATCH**
- export JSON, export validate, export dataset (ZIP stream)

**스키마**: `annotations(id, labelset_id, image_id, class_id, kind, data JSONB, created_at)` + `(labelset_id, image_id)` index

**의존성 추가**: `zipstream-ng`

**PATCH 트랜잭션 순서** (단일 트랜잭션):
1. `replaceImageIds` → DELETE WHERE image_id IN (...)
2. `deleteIds` → DELETE WHERE id IN (...)
3. `upsert` → INSERT ON CONFLICT (id) DO UPDATE
4. SELECT 전체 반환 (full list — open question #2 결정)

**Export ZIP**: `StreamingResponse(zipstream(...), media_type="application/zip")`. ZIP 안에 `annotations.json` + (옵션) `images/<file_name>` (S3 GET → ZIP stream 직결).

---

## 5. 횡단 작업

각 PR에서 함께 손봄:
- **로깅**: 신규 라우트마다 `extra={project_id, resource_id, ...}` 구조화
- **메트릭**: `request_total{route, status}` + `request_duration_seconds{route}` 추가
- **에러 응답**: `ServiceError` 계층 그대로 → `{error, message}` 직렬화
- **CORS**: 기존 `ALLOWED_ORIGINS`. PATCH/DELETE 메소드 허용 추가 (PR #1 에서)
- **Auth deferral**: `current_user_id() -> "anonymous"`, 모든 라우트가 `Depends` 받음

---

## 6. 운영 체크리스트 (PR #1 머지 직후)

```bash
# 디스크
sudo mkdir -p /appdata/storage/videonizer/{pg,minio,tmp}
sudo chown -R appuser:appgroup /appdata/storage/videonizer

# .env (서버에서 직접)
cp .env.example .env
# POSTGRES_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD,
# S3_ACCESS_KEY, S3_SECRET_KEY 설정

# 인프라 기동
docker compose up -d postgres minio

# 앱 기동
python -m app.main
curl localhost:8080/healthz   # {"status":"ok"}

# 백업 (cron daily)
docker compose exec -T postgres pg_dump -U videonizer videonizer | \
  gzip > /appdata/backup/videonizer/pg/$(date +%F).sql.gz
mc mirror --overwrite --remove minio/videonizer \
  /appdata/backup/videonizer/minio/
```

---

## 7. 마일스톤 요약

| PR | 추가 라우트 | 누적 | 핵심 위험 |
|---|---|---|---|
| #1 Infra | 0 | 기존 | 마이그레이션 자동화, 회귀 |
| #2 Projects | 4 | +4 | 패턴 고정 |
| #3 Resources | 9 | +13 | 업로드 스트림, Range, S3 multipart |
| #4 Images | 8 (+1 본구현) | +21 | 썸네일 트랜잭션, bulk tag SQL |
| #5 LabelSets | 6 | +27 | summary 분리, imageIds JSONB |
| #6 Annotations + Export | 6 | +33 | PATCH 트랜잭션 순서, ZIP streaming |

이 6 PR 끝에 contract 의 모든 `[OK]`/`[NEW]`/`[CHANGE]` 라우트가 채워진다.
