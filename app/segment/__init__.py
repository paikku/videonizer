"""Image segmentation pipeline for /v1/segment.

Public surface used by the API layer:

    from .service import segment_image, SegmentResult, parse_region
    from .registry import resolve_backend, SUPPORTED_MODELS, DEFAULT_MODEL
"""

from .registry import DEFAULT_MODEL, SUPPORTED_MODELS, resolve_backend
from .service import SegmentResult, parse_region, segment_image

__all__ = [
    "DEFAULT_MODEL",
    "SUPPORTED_MODELS",
    "SegmentResult",
    "parse_region",
    "resolve_backend",
    "segment_image",
]
