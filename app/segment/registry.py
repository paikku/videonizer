"""Public model id (API contract) -> internal CPU backend mapping.

The contract enum is fixed by the frontend (`SEGMENT_MODELS`). Each public
id is routed to a (backend factory, weights filename) pair; the registry
caches one instance per (factory, weights) tuple so that, e.g., sam2 and
sam can both use the SAM backend class with different .pt files without
loading either model twice.

The actual backend that ran is announced via the `X-Segment-Backend`
response header so operators can audit what really served a request.

Backends load their weights lazily on first use and stay cached for the
process lifetime. The registry is thread-safe.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from .backends import Backend, FastSAMBackend, SAMBackend, YOLOSegBackend


# Public model ids exposed to clients (must match `SEGMENT_MODELS` on the
# frontend). 400 with `unsupported model` for anything else.
SUPPORTED_MODELS: tuple[str, ...] = (
    "sam3",
    "sam2",
    "sam",
    "mask2former",
    "mask-rcnn",
)

DEFAULT_MODEL: str = "sam3"


@dataclass(frozen=True)
class _BackendSpec:
    backend_id: str   # short id reported via X-Segment-Backend
    factory_kind: str  # "fastsam" | "sam" | "yolo_seg"
    weights: str       # filename inside SEGMENT_WEIGHTS_DIR


# Each public id has its own (factory, weight) so the user-visible model
# selector actually maps to a distinct underlying network. Real
# Mask2Former weights live behind hosts that aren't reachable from the
# build environment, so we substitute the heaviest YOLO-seg variant for
# that id and rely on the X-Segment-Backend header to be honest about it.
_ROUTING: dict[str, _BackendSpec] = {
    "sam3":        _BackendSpec("fastsam-s",     "fastsam",  "FastSAM-s.pt"),
    "sam2":        _BackendSpec("sam2.1-tiny",   "sam",      "sam2.1_t.pt"),
    "sam":         _BackendSpec("mobile-sam",    "sam",      "mobile_sam.pt"),
    "mask2former": _BackendSpec("yolo11x-seg",   "yolo_seg", "yolo11x-seg.pt"),
    "mask-rcnn":   _BackendSpec("yolov8n-seg",   "yolo_seg", "yolov8n-seg.pt"),
}


class _Registry:
    """Process-wide singleton holder for backend instances.

    Keyed by (factory_kind, weights) so two routes that share a factory
    class but use different weight files (e.g. sam2 + sam, both via
    SAMBackend) hold one cached instance each.
    """

    def __init__(self, weights_dir: Path | None = None) -> None:
        self._weights_dir = weights_dir
        self._lock = threading.Lock()
        self._instances: dict[tuple[str, str], Backend] = {}

    def reset(self) -> None:
        """For tests: drop all cached backends."""
        with self._lock:
            self._instances.clear()

    def get(self, spec: _BackendSpec) -> Backend:
        key = (spec.factory_kind, spec.weights)
        with self._lock:
            inst = self._instances.get(key)
            if inst is not None:
                return inst
            inst = _build(spec, weights_dir=self._weights_dir)
            self._instances[key] = inst
            return inst


def _build(spec: _BackendSpec, *, weights_dir: Path | None) -> Backend:
    kind = spec.factory_kind
    if kind == "fastsam":
        return FastSAMBackend(weights=spec.weights, weights_dir=weights_dir)
    if kind == "sam":
        return SAMBackend(weights=spec.weights, weights_dir=weights_dir)
    if kind == "yolo_seg":
        return YOLOSegBackend(weights=spec.weights, weights_dir=weights_dir)
    raise ValueError(f"unknown backend kind: {kind}")


_default_registry = _Registry()


@dataclass
class ResolvedBackend:
    backend_id: str
    backend: Backend


def resolve_backend(
    model: str,
    *,
    registry: _Registry | None = None,
) -> ResolvedBackend:
    """Map a public `model` id to a (backend_id, Backend instance).

    Raises KeyError if the model is not in SUPPORTED_MODELS — the API layer
    converts that into the contract `400 {"error":"unsupported model"}`.
    """
    spec = _ROUTING.get(model)
    if spec is None:
        raise KeyError(model)
    reg = registry or _default_registry
    return ResolvedBackend(backend_id=spec.backend_id, backend=reg.get(spec))


def configure_weights_dir(path: Path | str | None) -> None:
    """Re-create the default registry rooted at `path`. Call once at startup."""
    global _default_registry
    _default_registry = _Registry(weights_dir=Path(path) if path else None)


def reset_default_registry() -> None:
    """Test helper."""
    _default_registry.reset()


def install_test_backend(model: str, backend: Backend, backend_id: str = "test") -> None:
    """Test helper: pin a fake backend instance for a given public model id.

    Bypasses the lazy-load path entirely.
    """
    spec = _ROUTING.get(model)
    if spec is None:
        raise KeyError(model)
    with _default_registry._lock:  # noqa: SLF001 - intentional test seam
        _default_registry._instances[(spec.factory_kind, spec.weights)] = backend
    # Override backend_id reporting too, just for this test session.
    _ROUTING[model] = _BackendSpec(
        backend_id=backend_id,
        factory_kind=spec.factory_kind,
        weights=spec.weights,
    )
