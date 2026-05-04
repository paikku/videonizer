# `app/export/` — LabelSet → JSON / dataset ZIP / validation report

Pure pipeline. No filesystem IO except the ZIP it returns; image bytes are pulled through an injected callable so the storage layer stays the only place that opens files.

---

## Purpose

Three artifacts off one input (LabelSet + images + resources + annotations):

1. `build_labelset_export(...)` — the JSON dump returned by `GET /export`.
2. `build_bundle(...)` — the ZIP returned by `POST /export/dataset`.
3. `validate_export(...)` — the counts + warnings returned by `POST /export/validate`.

The ZIP layout is documented in `API_CONTRACT.md §6`. Image bytes are optional inside the ZIP and pulled lazily through `read_image_bytes(image_id) -> (bytes, ext) | None`.

---

## Public surface (re-exported from `__init__.py`)

| Function | Output |
|---|---|
| `build_labelset_export` | `LabelSetExport` JSON dict |
| `build_bundle` | `{"zip": bytes, "fileName": str, "report": ValidationReport}` |
| `validate_export` | `ValidationReport` dict |
| `split_images` | `{imageId: SplitName \| None}` |
| `format_for_labelset_type` | `"yolo-detection" \| "yolo-segmentation" \| "classify-csv"` |

Internal helpers (`yoloFormats.py`, `classificationCsv.py`, `classRemap.py`) are imported by `bundle.py` and not part of the public surface, but they're standalone units that can be unit-tested in isolation.

---

## Format selection

```
LabelSet.type == "bbox"     → yolo-detection      (labels/<split>/<name>.txt: cx cy w h)
LabelSet.type == "polygon"  → yolo-segmentation   (labels/<split>/<name>.txt: ring x/y pairs)
LabelSet.type == "classify" → classify-csv        (data.csv: filename, class_name, class_id [, split])
```

The choice is *fixed by the LabelSet type at creation time*. Clients that want a different format must create a new LabelSet of the right type — there is no `format` option.

---

## Determinism

`split_images` for `mode: random` uses Mulberry32 PRNG + Fisher-Yates shuffle. Given the same seed and the same image id list, the assignment is identical across runs. The PRNG implementation in `split.py::_mulberry32` matches the TS implementation byte-for-byte for the same seed input — same shuffle, same ratios.

`build_bundle` is otherwise deterministic — the same input always produces the same archive contents (modulo ZIP timestamps, which `zipfile` writes as the time of `writestr` and we don't currently override).

---

## ZIP construction

`bundle.py` uses stdlib `zipfile.ZipFile` with `ZIP_DEFLATED`. The whole archive is built in `io.BytesIO` and returned as `bytes` — see `REFACTORING.md` for the streaming refactor candidate.

`_sanitize_basename` strips directory prefixes (`name.replace(/^.*[\\/]/, '')`) and replaces non-`[A-Za-z0-9._-]` runs with `_`. `_ensure_unique` deduplicates archive filenames by suffixing `_2`, `_3`, … so two images named `frame.jpg` from different resources don't collide in the archive.

---

## Filtering

The set of images that ship in the archive is **labeled OR explicitly excluded** (i.e. members of `LabelSet.excludedImageIds`). Pure unlabeled images are dropped. This matches the `validate_export` "usable" count — preview and bundle never disagree because they share the filter.

`splits[id] is None` → image is dropped (manual mode with no assignment, or by-tag with no matching tag). This is a legitimate "skip" signal, not an error.

---

## Pitfalls

- **`mode: by-tag` is first-match-wins.** An image carrying both `tagTrain` and `tagVal` lands in `train`. Document this in the FE if it ever exposes the option.
- **Mulberry32 produces *different* shuffles than Python's `random.shuffle` for the same seed.** Don't "modernize" the PRNG to use `random` without coordinating with the FE — a seeded "preview my dataset" feature needs the same numbers on both sides.
- **`includeImages` requires `read_image_bytes`.** If the route forgets to pass the provider when `includeImages=True`, `build_bundle` raises `ValueError`. The route layer surfaces this as 400 via the global handler.
- **`out-of-bounds` warning clamps polygons silently.** `yoloSegmentationFile` clamps coordinates to `[0, 1]` before serializing — the YOLO file has the clamped values, while the warning tells the user upstream that their annotation needs cleanup. Don't change one without the other.
