"""Train/val/test split assignment for export.

Four modes mirror vision's ``SplitConfig`` discriminated union:

* ``none``    — every image lands in ``train``.
* ``manual``  — explicit per-image map; missing entries are excluded.
* ``by-tag``  — image tags decide; first matching of (train, val, test) wins.
* ``random``  — deterministic split given a seed (Mulberry32 PRNG +
  Fisher-Yates shuffle, ported byte-for-byte from the TS so the two
  sides agree on assignment for the same seed).
"""
from __future__ import annotations

from typing import Any, Callable


SPLIT_NAMES = ("train", "val", "test")


def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _mulberry32(seed: int) -> Callable[[], float]:
    """Tiny deterministic PRNG. Not cryptographic. Identical to the TS impl
    so seeded splits produce the same shuffle on either platform.
    """
    a = _u32(seed)

    def rand() -> float:
        nonlocal a
        a = _u32(a + 0x6D2B79F5)
        t = a
        t = _u32((t ^ (t >> 15)) * (t | 1))
        t = _u32(t ^ _u32(t + _u32((t ^ (t >> 7)) * (t | 61))))
        return _u32(t ^ (t >> 14)) / 4294967296

    return rand


def _shuffled(items: list[str], rand: Callable[[], float]) -> list[str]:
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = int(rand() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def _math_round(x: float) -> int:
    """JS Math.round semantics — half-up for non-negative values, which is
    all we ever pass here. Python's built-in ``round`` is banker's-round
    and would skew counts at exact .5 boundaries.
    """
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def split_images(
    images: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, str | None]:
    """Return ``{imageId: SplitName | None}``.

    ``None`` only happens in ``by-tag`` (no matching tag) and ``manual``
    (id missing from the map). Every image always has an entry in the
    returned dict.
    """
    out: dict[str, str | None] = {}
    mode = config.get("mode")
    if mode == "none":
        for img in images:
            out[img["id"]] = "train"
        return out
    if mode == "manual":
        assignments = config.get("assignments") or {}
        for img in images:
            v = assignments.get(img["id"])
            out[img["id"]] = v if v in SPLIT_NAMES else None
        return out
    if mode == "by-tag":
        t_train = config.get("tagTrain")
        t_val = config.get("tagVal")
        t_test = config.get("tagTest")
        for img in images:
            tags = set(img.get("tags") or [])
            if t_train in tags:
                out[img["id"]] = "train"
            elif t_val in tags:
                out[img["id"]] = "val"
            elif t_test in tags:
                out[img["id"]] = "test"
            else:
                out[img["id"]] = None
        return out
    if mode == "random":
        train = float(config.get("train") or 0)
        val = float(config.get("val") or 0)
        test = float(config.get("test") or 0)
        s = train + val + test
        if s <= 0:
            for img in images:
                out[img["id"]] = "train"
            return out
        train_p = train / s
        val_p = val / s
        seed = int(config.get("seed") or 1)
        rand = _mulberry32(seed)
        ids = _shuffled([img["id"] for img in images], rand)
        total = len(ids)
        train_end = _math_round(total * train_p)
        val_end = min(total, train_end + _math_round(total * val_p))
        for i, iid in enumerate(ids):
            if i < train_end:
                out[iid] = "train"
            elif i < val_end:
                out[iid] = "val"
            else:
                out[iid] = "test"
        return out
    # Unknown mode — be conservative: drop everything.
    for img in images:
        out[img["id"]] = None
    return out
