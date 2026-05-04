"""Unit tests for ``app.storage``.

Uses pytest-asyncio in auto mode — every ``async def test_*`` runs in its
own event loop. The ``storage`` fixture clears the per-path lock dict on
teardown so locks created in one test's loop never leak into the next.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.storage import (
    bulk_tag_images,
    clear_locks,
    configure_storage_root,
    create_image,
    create_labelset,
    create_project,
    create_resource,
    delete_image,
    delete_labelset,
    delete_project,
    delete_resource,
    ext_from_name,
    get_image,
    get_labelset,
    get_labelset_annotations,
    get_project,
    list_images,
    list_labelsets,
    list_projects,
    list_resources,
    mime_for_ext,
    read_image_bytes,
    read_image_thumb,
    read_preview,
    read_resource_source,
    safe_id,
    save_labelset_annotations,
    stat_resource_source,
    update_image,
    update_labelset,
    update_resource,
    write_previews,
)


@pytest.fixture
def storage(tmp_path: Path):
    configure_storage_root(tmp_path)
    clear_locks()
    yield tmp_path
    configure_storage_root(None)
    clear_locks()


# ---------- pure helpers ----------


def test_safe_id_allows_uuid_like():
    assert safe_id("abc-123") == "abc-123"


def test_safe_id_rejects_traversal():
    for bad in ("../etc/passwd", "a/b", "a\\b", "", ".."):
        with pytest.raises(ValueError):
            safe_id(bad)


def test_ext_from_name():
    assert ext_from_name("foo.MP4", "mp4") == "mp4"
    assert ext_from_name("no-ext", "jpg") == "jpg"
    assert ext_from_name("foo.bar.png", "x") == "png"


def test_mime_for_ext():
    assert mime_for_ext("mp4") == "video/mp4"
    assert mime_for_ext("JPG") == "image/jpeg"
    assert mime_for_ext("weird") == "application/octet-stream"


# ---------- projects ----------


async def test_create_and_list_project(storage):
    project = await create_project("Demo")
    assert project["name"] == "Demo"
    assert project["members"] == []
    listed = await list_projects()
    assert len(listed) == 1
    assert listed[0]["id"] == project["id"]
    assert listed[0]["resourceCount"] == 0
    assert listed[0]["imageCount"] == 0
    assert listed[0]["labelSetCount"] == 0


async def test_get_project_returns_none_for_missing(storage):
    assert await get_project("missing") is None


async def test_create_project_strips_and_falls_back(storage):
    p = await create_project("   ")
    assert p["name"] == "Untitled"


async def test_delete_project_removes_index_and_dir(storage):
    p = await create_project("X")
    await delete_project(p["id"])
    assert await list_projects() == []
    assert not (storage / p["id"]).exists()


async def test_delete_project_idempotent(storage):
    await delete_project("missing-on-purpose")


# ---------- resources ----------


async def test_create_video_resource_writes_source(storage):
    p = await create_project("P")
    r = await create_resource(
        p["id"],
        type="video",
        name="clip",
        source_ext="mp4",
        source_buffer=b"fake-video-bytes",
        width=640,
        height=480,
        duration=12.5,
        ingest_via="server",
    )
    assert r["type"] == "video"
    assert r["sourceExt"] == "mp4"
    src = await read_resource_source(p["id"], r["id"])
    assert src is not None
    assert src[0] == b"fake-video-bytes"
    assert src[1] == "mp4"


async def test_create_image_batch_resource(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="batch")
    assert r["type"] == "image_batch"
    listed = await list_resources(p["id"])
    assert listed[0]["imageCount"] == 0


async def test_update_resource_only_name_and_tags(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    out = await update_resource(p["id"], r["id"], name="B", tags=["t1"])
    assert out is not None
    assert out["name"] == "B"
    assert out["tags"] == ["t1"]


async def test_delete_resource_cascades_images(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    img = await create_image(
        p["id"],
        resource_id=r["id"],
        source="uploaded",
        file_name="a.jpg",
        ext="jpg",
        width=10,
        height=10,
        bytes_=b"fake",
    )
    await delete_resource(p["id"], r["id"])
    assert await get_image(p["id"], img["id"]) is None
    assert await list_resources(p["id"]) == []


async def test_stat_resource_source(storage):
    p = await create_project("P")
    r = await create_resource(
        p["id"],
        type="video",
        name="clip",
        source_ext="mp4",
        source_buffer=b"x" * 1234,
        width=1,
        height=1,
    )
    st = await stat_resource_source(p["id"], r["id"])
    assert st is not None
    assert st["size"] == 1234
    assert st["ext"] == "mp4"


async def test_previews_write_and_read(storage):
    p = await create_project("P")
    r = await create_resource(
        p["id"],
        type="video",
        name="clip",
        source_ext="mp4",
        source_buffer=b"v",
        width=1,
        height=1,
    )
    n = await write_previews(p["id"], r["id"], [b"jpg-0", b"jpg-1"])
    assert n == 2
    assert await read_preview(p["id"], r["id"], 0) == b"jpg-0"
    assert await read_preview(p["id"], r["id"], 1) == b"jpg-1"
    assert await read_preview(p["id"], r["id"], 2) is None
    # Rewriting a shorter set wipes index 1.
    await write_previews(p["id"], r["id"], [b"jpg-0-new"])
    assert await read_preview(p["id"], r["id"], 0) == b"jpg-0-new"
    assert await read_preview(p["id"], r["id"], 1) is None


async def test_previews_rejected_on_image_batch(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="B")
    with pytest.raises(ValueError):
        await write_previews(p["id"], r["id"], [b"x"])


# ---------- images ----------


async def test_create_and_list_images_with_filter(storage):
    p = await create_project("P")
    r1 = await create_resource(p["id"], type="image_batch", name="A")
    r2 = await create_resource(p["id"], type="image_batch", name="B")
    a = await create_image(
        p["id"], resource_id=r1["id"], source="uploaded",
        file_name="a.jpg", ext="jpg", width=1, height=1,
        bytes_=b"a", tags=["x"],
    )
    b = await create_image(
        p["id"], resource_id=r2["id"], source="uploaded",
        file_name="b.jpg", ext="jpg", width=1, height=1,
        bytes_=b"b", tags=["y"],
    )
    by_r1 = await list_images(p["id"], resource_id=r1["id"])
    assert [i["id"] for i in by_r1] == [a["id"]]
    by_tag = await list_images(p["id"], tag="y")
    assert [i["id"] for i in by_tag] == [b["id"]]
    by_source = await list_images(p["id"], source="uploaded")
    assert {i["id"] for i in by_source} == {a["id"], b["id"]}


async def test_update_image_replaces_tags(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    img = await create_image(
        p["id"], resource_id=r["id"], source="uploaded",
        file_name="a.jpg", ext="jpg", width=1, height=1, bytes_=b"x",
        tags=["t1", "t2"],
    )
    out = await update_image(p["id"], img["id"], tags=["only"])
    assert out["tags"] == ["only"]


async def test_bulk_tag_modes(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")

    async def mk(name: str, tags: list[str]) -> dict:
        return await create_image(
            p["id"], resource_id=r["id"], source="uploaded",
            file_name=name, ext="jpg", width=1, height=1,
            bytes_=b"x", tags=tags,
        )

    a = await mk("a.jpg", ["t1"])
    b = await mk("b.jpg", ["t1", "t2"])

    out = await bulk_tag_images(p["id"], [a["id"], b["id"]], ["t3"], "add")
    assert out == {"updated": 2}
    a_after = await get_image(p["id"], a["id"])
    assert set(a_after["tags"]) == {"t1", "t3"}

    await bulk_tag_images(p["id"], [b["id"]], ["t1"], "remove")
    b_after = await get_image(p["id"], b["id"])
    assert "t1" not in b_after["tags"]

    await bulk_tag_images(p["id"], [a["id"]], ["only"], "replace")
    a2 = await get_image(p["id"], a["id"])
    assert a2["tags"] == ["only"]


async def test_bulk_tag_skips_missing(storage):
    p = await create_project("P")
    out = await bulk_tag_images(p["id"], ["missing"], ["t"], "add")
    assert out == {"updated": 0}


async def test_bulk_tag_invalid_mode(storage):
    p = await create_project("P")
    with pytest.raises(ValueError):
        await bulk_tag_images(p["id"], [], [], "chaos")


async def test_image_thumb_jpeg_cached(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    src = PILImage.new("RGB", (1024, 768), (255, 0, 0))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    img = await create_image(
        p["id"], resource_id=r["id"], source="uploaded",
        file_name="big.png", ext="png", width=1024, height=768,
        bytes_=buf.getvalue(),
    )
    out = await read_image_thumb(p["id"], img["id"])
    assert out is not None
    # JPEG SOI
    assert out[:3] == b"\xff\xd8\xff"
    cached = await read_image_thumb(p["id"], img["id"])
    assert cached == out


async def test_read_image_bytes_roundtrip(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    img = await create_image(
        p["id"], resource_id=r["id"], source="uploaded",
        file_name="a.jpg", ext="jpg", width=1, height=1, bytes_=b"hello",
    )
    got = await read_image_bytes(p["id"], img["id"])
    assert got == (b"hello", "jpg")


# ---------- labelsets + cascade ----------


async def test_image_delete_strips_annotations_and_membership(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    img = await create_image(
        p["id"], resource_id=r["id"], source="uploaded",
        file_name="a.jpg", ext="jpg", width=1, height=1, bytes_=b"x",
    )
    ls = await create_labelset(
        p["id"],
        name="L",
        type="bbox",
        classes=[{"id": "c1", "name": "x", "color": "#fff"}],
        image_ids=[img["id"]],
    )
    await save_labelset_annotations(
        p["id"],
        ls["id"],
        {
            "annotations": [
                {
                    "id": "a1",
                    "imageId": img["id"],
                    "classId": "c1",
                    "kind": "rect",
                    "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
                    "createdAt": 0,
                },
            ]
        },
    )
    await delete_image(p["id"], img["id"])
    ann = await get_labelset_annotations(p["id"], ls["id"])
    assert ann["annotations"] == []
    refetched = await get_labelset(p["id"], ls["id"])
    assert refetched["imageIds"] == []


async def test_create_labelset_validates_type(storage):
    p = await create_project("P")
    with pytest.raises(ValueError):
        await create_labelset(p["id"], name="L", type="invalid")


async def test_list_labelsets_summary_counts(storage):
    p = await create_project("P")
    r = await create_resource(p["id"], type="image_batch", name="A")
    img = await create_image(
        p["id"], resource_id=r["id"], source="uploaded",
        file_name="a.jpg", ext="jpg", width=1, height=1, bytes_=b"x",
    )
    ls = await create_labelset(
        p["id"],
        name="L",
        type="classify",
        classes=[{"id": "c1", "name": "x", "color": "#000"}],
        image_ids=[img["id"]],
    )
    await save_labelset_annotations(
        p["id"],
        ls["id"],
        {
            "annotations": [
                {
                    "id": "a1",
                    "imageId": img["id"],
                    "classId": "c1",
                    "kind": "classify",
                    "createdAt": 0,
                }
            ],
        },
    )
    summaries = await list_labelsets(p["id"])
    assert len(summaries) == 1
    s = summaries[0]
    assert s["imageCount"] == 1
    assert s["annotationCount"] == 1
    assert s["labeledImageCount"] == 1
    assert s["classStats"] == [{"classId": "c1", "imageCount": 1}]


async def test_update_labelset_partial(storage):
    p = await create_project("P")
    ls = await create_labelset(p["id"], name="L", type="bbox")
    out = await update_labelset(
        p["id"],
        ls["id"],
        name="L2",
        description="d",
        classes=[{"id": "c1", "name": "a", "color": "#fff"}],
        image_ids=["img-a"],
        excluded_image_ids=["img-b"],
    )
    assert out is not None
    assert out["name"] == "L2"
    assert out["description"] == "d"
    assert out["imageIds"] == ["img-a"]
    assert out["excludedImageIds"] == ["img-b"]


async def test_delete_labelset_idempotent(storage):
    p = await create_project("P")
    await delete_labelset(p["id"], "missing")
    ls = await create_labelset(p["id"], name="L", type="bbox")
    await delete_labelset(p["id"], ls["id"])
    assert await list_labelsets(p["id"]) == []
