"""Unit tests for the pure export pipeline (no FS, no HTTP)."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.export import (
    build_bundle,
    build_labelset_export,
    format_for_labelset_type,
    split_images,
    validate_export,
)
from app.export.classificationCsv import classification_csv
from app.export.classRemap import build_class_mapping
from app.export.split import _mulberry32
from app.export.yoloFormats import yolo_detection_file, yolo_segmentation_file


# ----- format_for_labelset_type ---------------------------------------


def test_format_mapping():
    assert format_for_labelset_type("bbox") == "yolo-detection"
    assert format_for_labelset_type("polygon") == "yolo-segmentation"
    assert format_for_labelset_type("classify") == "classify-csv"


# ----- mulberry32 PRNG (parity sanity check) --------------------------


def test_mulberry32_deterministic():
    a = _mulberry32(42)
    b = _mulberry32(42)
    assert [a() for _ in range(5)] == [b() for _ in range(5)]


def test_mulberry32_different_seeds_diverge():
    assert _mulberry32(1)() != _mulberry32(2)()


# ----- split_images ----------------------------------------------------


def test_split_none_puts_everyone_in_train():
    imgs = [{"id": f"i{i}", "tags": []} for i in range(3)]
    out = split_images(imgs, {"mode": "none"})
    assert out == {"i0": "train", "i1": "train", "i2": "train"}


def test_split_manual_uses_assignments():
    imgs = [{"id": "a", "tags": []}, {"id": "b", "tags": []}]
    out = split_images(imgs, {"mode": "manual", "assignments": {"a": "train"}})
    assert out == {"a": "train", "b": None}


def test_split_by_tag_first_match_wins():
    imgs = [
        {"id": "a", "tags": ["t-train", "t-val"]},
        {"id": "b", "tags": ["t-test"]},
        {"id": "c", "tags": ["other"]},
    ]
    out = split_images(
        imgs,
        {
            "mode": "by-tag",
            "tagTrain": "t-train",
            "tagVal": "t-val",
            "tagTest": "t-test",
        },
    )
    assert out == {"a": "train", "b": "test", "c": None}


def test_split_random_deterministic_under_seed():
    imgs = [{"id": f"i{i}", "tags": []} for i in range(20)]
    cfg = {"mode": "random", "train": 8, "val": 1, "test": 1, "seed": 7}
    a = split_images(imgs, cfg)
    b = split_images(imgs, cfg)
    assert a == b


def test_split_random_distributes_into_three_buckets():
    imgs = [{"id": f"i{i}", "tags": []} for i in range(100)]
    out = split_images(
        imgs, {"mode": "random", "train": 8, "val": 1, "test": 1, "seed": 1}
    )
    counts = {"train": 0, "val": 0, "test": 0}
    for v in out.values():
        counts[v] += 1
    assert counts["train"] == 80
    assert counts["val"] == 10
    assert counts["test"] == 10


def test_split_random_zero_total_falls_back_to_all_train():
    imgs = [{"id": "i", "tags": []}]
    out = split_images(imgs, {"mode": "random", "train": 0, "val": 0, "test": 0, "seed": 1})
    assert out == {"i": "train"}


# ----- buildLabelSetExport --------------------------------------------


def test_buildLabelSetExport_orders_by_labelset_image_ids():
    labelset = {
        "id": "ls",
        "name": "L",
        "type": "bbox",
        "createdAt": 0,
        "classes": [],
        "imageIds": ["b", "a"],
    }
    images = [
        {"id": "a", "resourceId": "r", "source": "uploaded",
         "fileName": "a.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": []},
        {"id": "b", "resourceId": "r", "source": "uploaded",
         "fileName": "b.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": []},
    ]
    resources = [{"id": "r", "name": "R", "type": "image_batch"}]
    out = build_labelset_export(
        labelset=labelset, images=images, resources=resources, annotations=[]
    )
    assert [i["id"] for i in out["images"]] == ["b", "a"]
    assert out["images"][0]["resource"] == {"id": "r", "name": "R", "type": "image_batch"}


def test_buildLabelSetExport_includes_videoFrameMeta_when_present():
    labelset = {"id": "ls", "name": "L", "type": "bbox", "createdAt": 0,
                "classes": [], "imageIds": ["a"]}
    images = [{
        "id": "a", "resourceId": "r", "source": "video_frame",
        "fileName": "a.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": [],
        "videoFrameMeta": {"timestamp": 1.5, "frameIndex": 30},
    }]
    out = build_labelset_export(
        labelset=labelset, images=images, resources=[], annotations=[]
    )
    assert out["images"][0]["videoFrameMeta"] == {"timestamp": 1.5, "frameIndex": 30}


# ----- validation ------------------------------------------------------


def test_validate_warns_on_no_classes_no_annotations():
    labelset = {"type": "bbox", "classes": [], "excludedImageIds": []}
    out = validate_export(labelset=labelset, images=[], annotations=[], splits={})
    codes = {w["code"] for w in out["warnings"]}
    assert "no-classes" in codes
    assert "no-annotations" in codes


def test_validate_counts_unusable_unassigned():
    labelset = {"type": "bbox", "classes": [{"id": "c", "name": "c", "color": "#000"}], "excludedImageIds": []}
    images = [
        {"id": "labeled", "tags": []},
        {"id": "unlabeled", "tags": []},
    ]
    annotations = [{"imageId": "labeled", "classId": "c", "kind": "rect"}]
    splits = {"labeled": "train", "unlabeled": None}
    out = validate_export(labelset=labelset, images=images, annotations=annotations, splits=splits)
    assert out["usableImages"] == 1
    assert out["unusableImages"] == 1


def test_validate_classify_multi_class_warning():
    labelset = {"type": "classify", "classes": [
        {"id": "a", "name": "a", "color": "#000"},
        {"id": "b", "name": "b", "color": "#000"},
    ], "excludedImageIds": []}
    annotations = [
        {"imageId": "i1", "classId": "a", "kind": "classify"},
        {"imageId": "i1", "classId": "b", "kind": "classify"},
    ]
    out = validate_export(
        labelset=labelset,
        images=[{"id": "i1", "tags": []}],
        annotations=annotations,
        splits={"i1": "train"},
    )
    codes = {w["code"] for w in out["warnings"]}
    assert "multi-class-classify" in codes


def test_validate_polygon_out_of_bounds():
    labelset = {"type": "polygon", "classes": [{"id": "c", "name": "c", "color": "#000"}], "excludedImageIds": []}
    annotations = [{
        "imageId": "i1", "classId": "c", "kind": "polygon",
        "shape": {"kind": "polygon", "rings": [[{"x": -0.1, "y": 0.5}, {"x": 1.5, "y": 0}, {"x": 0, "y": 1}]]},
    }]
    out = validate_export(
        labelset=labelset,
        images=[{"id": "i1", "tags": []}],
        annotations=annotations,
        splits={"i1": "train"},
    )
    codes = {w["code"] for w in out["warnings"]}
    assert "out-of-bounds" in codes


# ----- yoloFormats -----------------------------------------------------


def test_yolo_detection_rect_to_cx_cy_w_h():
    line = {"classIndex": 0, "shape": {"kind": "rect", "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.5}}
    out = yolo_detection_file([line])
    # cx=0.3, cy=0.45, w=0.4, h=0.5
    assert out == "0 0.300000 0.450000 0.400000 0.500000\n"


def test_yolo_detection_polygon_uses_bbox():
    line = {
        "classIndex": 1,
        "shape": {"kind": "polygon", "rings": [[{"x": 0.1, "y": 0.2}, {"x": 0.5, "y": 0.6}, {"x": 0.3, "y": 0.4}]]},
    }
    out = yolo_detection_file([line])
    assert out.startswith("1 0.300000 0.400000 0.400000 0.400000")  # cx=0.3, cy=0.4, w=0.4, h=0.4


def test_yolo_detection_skips_zero_size_rect():
    out = yolo_detection_file([
        {"classIndex": 0, "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0, "h": 1}},
    ])
    assert out == ""


def test_yolo_segmentation_rect_emits_4_corners():
    out = yolo_segmentation_file([
        {"classIndex": 0, "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5}},
    ])
    # 4 corners: (0,0) (0.5,0) (0.5,0.5) (0,0.5)
    assert out == "0 0.000000 0.000000 0.500000 0.000000 0.500000 0.500000 0.000000 0.500000\n"


def test_yolo_segmentation_clamps_out_of_range():
    out = yolo_segmentation_file([
        {
            "classIndex": 0,
            "shape": {"kind": "polygon", "rings": [[{"x": -1.5, "y": 0}, {"x": 2.0, "y": 0.5}, {"x": 0.5, "y": 1.5}]]},
        },
    ])
    assert "0.000000 0.000000" in out
    assert "1.000000 0.500000" in out
    assert "0.500000 1.000000" in out


# ----- classification CSV ---------------------------------------------


def test_csv_header_and_quoting():
    csv = classification_csv(
        [{"fileName": "a,b.png", "className": 'c"x', "classIndex": 0}],
        include_split=False,
    )
    assert "filename,class_name,class_id\n" in csv
    assert '"a,b.png"' in csv
    assert '"c""x"' in csv


def test_csv_with_split_column():
    csv = classification_csv(
        [{"fileName": "a.png", "className": "x", "classIndex": 1, "split": "val"}],
        include_split=True,
    )
    assert csv.endswith("a.png,x,1,val\n")


# ----- classRemap ------------------------------------------------------


def test_class_remap_off_keeps_all_in_order():
    classes = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    m = build_class_mapping(classes, [], remap=False)
    assert m["indexById"] == {"a": 0, "b": 1}
    assert m["classes"] == classes


def test_class_remap_on_drops_unused():
    classes = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"}]
    annotations = [{"imageId": "i", "classId": "b"}, {"imageId": "j", "classId": "a"}]
    m = build_class_mapping(classes, annotations, remap=True)
    assert {c["id"] for c in m["classes"]} == {"a", "b"}
    # Order preserved (declaration order, not annotation order).
    assert [c["id"] for c in m["classes"]] == ["a", "b"]
    assert m["indexById"] == {"a": 0, "b": 1}


# ----- bundle (no images) ----------------------------------------------


@pytest.mark.asyncio
async def test_bundle_yolo_detection_layout():
    labelset = {
        "id": "ls", "name": "MyLabel", "type": "bbox", "createdAt": 0,
        "classes": [{"id": "c1", "name": "thing", "color": "#000"}],
        "imageIds": ["img1"],
        "excludedImageIds": [],
    }
    images = [{
        "id": "img1", "resourceId": "r1", "source": "uploaded",
        "fileName": "photo 1.jpg", "ext": "jpg", "width": 100, "height": 100, "tags": [],
    }]
    annotations = [{
        "id": "a1", "imageId": "img1", "classId": "c1", "kind": "rect",
        "shape": {"kind": "rect", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
        "createdAt": 0,
    }]
    out = await build_bundle(
        labelset=labelset, images=images, annotations=annotations,
        options={"split": {"mode": "none"}, "includeImages": False, "remapClassIds": False},
    )
    assert out["fileName"] == "MyLabel-yolo-detection.zip"
    with zipfile.ZipFile(io.BytesIO(out["zip"])) as zf:
        names = set(zf.namelist())
        assert "labels/train/photo_1.txt" in names
        assert "classes.txt" in names
        assert "data.yaml" in names
        assert "manifest.json" in names
        labels = zf.read("labels/train/photo_1.txt").decode("utf-8")
        assert labels.startswith("0 ")
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format"] == "yolo-detection"
        assert manifest["images"][0]["imageId"] == "img1"


@pytest.mark.asyncio
async def test_bundle_classify_csv():
    labelset = {
        "id": "ls", "name": "L", "type": "classify", "createdAt": 0,
        "classes": [{"id": "c1", "name": "x", "color": "#000"}],
        "imageIds": ["img1"], "excludedImageIds": [],
    }
    images = [{
        "id": "img1", "resourceId": "r1", "source": "uploaded",
        "fileName": "a.png", "ext": "png", "width": 1, "height": 1, "tags": [],
    }]
    annotations = [{
        "id": "a1", "imageId": "img1", "classId": "c1", "kind": "classify",
        "createdAt": 0,
    }]
    out = await build_bundle(
        labelset=labelset, images=images, annotations=annotations,
        options={"split": {"mode": "none"}, "includeImages": False, "remapClassIds": False},
    )
    with zipfile.ZipFile(io.BytesIO(out["zip"])) as zf:
        assert "data.csv" in zf.namelist()
        csv = zf.read("data.csv").decode("utf-8")
        assert "filename,class_name,class_id" in csv
        assert "a.png,x,0" in csv


@pytest.mark.asyncio
async def test_bundle_drops_unlabeled_images():
    labelset = {
        "id": "ls", "name": "L", "type": "bbox", "createdAt": 0,
        "classes": [{"id": "c1", "name": "x", "color": "#000"}],
        "imageIds": ["img1", "img2"], "excludedImageIds": [],
    }
    images = [
        {"id": "img1", "resourceId": "r", "source": "uploaded",
         "fileName": "a.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": []},
        {"id": "img2", "resourceId": "r", "source": "uploaded",
         "fileName": "b.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": []},
    ]
    annotations = [{
        "id": "a1", "imageId": "img1", "classId": "c1", "kind": "rect",
        "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
        "createdAt": 0,
    }]
    out = await build_bundle(
        labelset=labelset, images=images, annotations=annotations,
        options={"split": {"mode": "none"}, "includeImages": False, "remapClassIds": False},
    )
    with zipfile.ZipFile(io.BytesIO(out["zip"])) as zf:
        names = zf.namelist()
        # img1 has annotations → emitted; img2 is unlabeled → dropped.
        labels = [n for n in names if n.startswith("labels/")]
        assert len(labels) == 1
        manifest = json.loads(zf.read("manifest.json"))
        assert [m["imageId"] for m in manifest["images"]] == ["img1"]


@pytest.mark.asyncio
async def test_bundle_includes_image_bytes_when_requested():
    labelset = {
        "id": "ls", "name": "L", "type": "bbox", "createdAt": 0,
        "classes": [{"id": "c1", "name": "x", "color": "#000"}],
        "imageIds": ["img1"], "excludedImageIds": [],
    }
    images = [{
        "id": "img1", "resourceId": "r", "source": "uploaded",
        "fileName": "a.jpg", "ext": "jpg", "width": 1, "height": 1, "tags": [],
    }]
    annotations = [{
        "id": "a1", "imageId": "img1", "classId": "c1", "kind": "rect",
        "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
        "createdAt": 0,
    }]

    async def reader(image_id: str):
        assert image_id == "img1"
        return (b"PNGDATA", "jpg")

    out = await build_bundle(
        labelset=labelset, images=images, annotations=annotations,
        options={"split": {"mode": "none"}, "includeImages": True, "remapClassIds": False},
        read_image_bytes=reader,
    )
    with zipfile.ZipFile(io.BytesIO(out["zip"])) as zf:
        assert "images/train/a.jpg" in zf.namelist()
        assert zf.read("images/train/a.jpg") == b"PNGDATA"
