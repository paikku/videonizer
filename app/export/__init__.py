"""LabelSet export pipeline.

Three artifacts off the same input:

* ``build_labelset_export`` — a single JSON dump (LabelSet metadata +
  per-image stub + every annotation). No image bytes inlined.
* ``build_bundle`` — a YOLO-detection / YOLO-segmentation / classify-CSV
  ZIP archive, format chosen by LabelSet ``type``.
* ``validate_export`` — counts and warnings without producing any bytes;
  used by the Export-preview UI before the user confirms.

Each piece mirrors ``vision/src/features/export/service/*.ts`` 1:1 so the
two implementations agree on every output byte for the same input.
"""
from .bundle import build_bundle
from .exportJson import build_labelset_export
from .split import split_images
from .validation import format_for_labelset_type, validate_export

__all__ = [
    "build_bundle",
    "build_labelset_export",
    "format_for_labelset_type",
    "split_images",
    "validate_export",
]
