from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

JOBS_TOTAL = Counter(
    "normalize_jobs_total",
    "Total normalize jobs processed, by outcome.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

JOB_DURATION_SECONDS = Histogram(
    "normalize_job_duration_seconds",
    "Job wall-clock time in seconds.",
    labelnames=("mode",),  # "remux" | "encode"
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

CONCURRENT_JOBS = Gauge(
    "normalize_concurrent_jobs",
    "Currently running normalize jobs.",
    registry=REGISTRY,
)

QUEUE_LENGTH = Gauge(
    "normalize_queue_length",
    "Jobs waiting for a concurrency slot.",
    registry=REGISTRY,
)

INPUT_BYTES = Histogram(
    "normalize_input_bytes",
    "Size of input files in bytes.",
    buckets=(1e6, 1e7, 1e8, 5e8, 1e9, 2e9),
    registry=REGISTRY,
)

OUTPUT_BYTES = Histogram(
    "normalize_output_bytes",
    "Size of output files in bytes.",
    buckets=(1e6, 1e7, 1e8, 5e8, 1e9, 2e9),
    registry=REGISTRY,
)

# --- Segmentation -----------------------------------------------------------

SEGMENT_TOTAL = Counter(
    "segment_requests_total",
    "Total /v1/segment requests, by outcome and public model id.",
    labelnames=("outcome", "model"),
    registry=REGISTRY,
)

SEGMENT_DURATION_SECONDS = Histogram(
    "segment_request_duration_seconds",
    "Per-request wall-clock latency in seconds.",
    labelnames=("model", "backend"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30),
    registry=REGISTRY,
)

SEGMENT_CONCURRENT = Gauge(
    "segment_concurrent",
    "Currently running segment inferences.",
    registry=REGISTRY,
)

SEGMENT_QUEUE_LENGTH = Gauge(
    "segment_queue_length",
    "Segment requests waiting for a worker slot.",
    registry=REGISTRY,
)
