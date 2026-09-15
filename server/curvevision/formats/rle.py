"""Run-length encoding for instance masks.

A mask is stored as ``{"rle": [...], "left": int, "top": int, "width": int, "height": int}``.
The box is the mask's own sub-rectangle in frame coordinates; the runs describe the pixels
inside it and nothing outside it. That is what keeps a 4K instance mask in the low kilobytes
instead of sixteen megabytes of booleans.

**The convention, stated once so nothing has to guess it.**

* The runs alternate, **starting with background**. ``[0, 4]`` is "no background, then four
  foreground pixels"; ``[3, 2]`` is three background then two foreground. A leading zero is
  therefore normal and load-bearing, not a quirk to strip.
* They are read **row-major inside the sub-rectangle** — left to right, then top to bottom,
  with no padding between rows.
* They sum to ``width * height``. A run list that stops early leaves the rest background,
  which is what every encoder that trims a trailing background run produces; `decode` accepts
  that and `encode` emits it, so the pair round-trips exactly.

**What was checked against CVAT, and what was not.** The element this serialises into —
``rle``, ``left``, ``top``, ``width``, ``height``, with width and height being *inclusive*
spans (``right - left + 1``) — was read out of CVAT's own XML serialiser, so the shape of the
attributes is verified rather than assumed. The run *parity* was not: that file does not say
whether the list starts with zeros or ones. Starting with background is the standard reading
and the one that makes a leading ``0`` meaningful, and it is what this module documents and
implements. If a real CVAT export ever disagrees, this docstring is the thing to correct, and
`decode`/`encode` are the only two places that would change.

Nothing here imports Pillow or numpy: the encoder is used on the write path of every mask
export, and a dependency on the media extras would make masks unexportable on a server
installed without them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from curvevision.core.errors import ValidationError

#: Keys a stored mask must carry. `points` is not among them: a mask's geometry is its runs.
MASK_KEYS = ("rle", "left", "top", "width", "height")


def decode(rle: list[int], width: int, height: int) -> list[bool]:
    """Runs to a flat row-major ``width * height`` list of booleans.

    Liberal in what it accepts, because a trailing background run is routinely trimmed: a
    list that stops short leaves the remaining pixels background, and one that overruns is
    truncated at the end of the box rather than raising. A *negative* run is not a trimming
    convention but corrupt data, and does raise -- silently treating it as zero would shift
    every pixel after it and produce a mask that is wrong rather than absent.
    """
    if width <= 0 or height <= 0:
        raise ValidationError(f"a mask needs a positive box, got {width}x{height}")

    total = width * height
    flags = [False] * total
    index = 0
    value = False  # runs start with background
    for run in rle:
        if run < 0:
            raise ValidationError(f"a mask run length cannot be negative, got {run}")
        if index >= total:
            break
        stop = min(index + run, total)
        if value:
            for position in range(index, stop):
                flags[position] = True
        index = stop
        value = not value
    return flags


def encode(flags: list[bool], width: int, height: int) -> list[int]:
    """A flat row-major boolean list back to runs, starting with background.

    The inverse of `decode` for every input `decode` produces. A trailing background run is
    trimmed, which is why `decode` has to tolerate a short list -- the two halves of that
    decision live next to each other on purpose.
    """
    if width <= 0 or height <= 0:
        raise ValidationError(f"a mask needs a positive box, got {width}x{height}")
    if len(flags) != width * height:
        raise ValidationError(
            f"expected {width * height} pixels for a {width}x{height} box, got {len(flags)}"
        )

    runs: list[int] = []
    value = False
    run = 0
    for flag in flags:
        if flag == value:
            run += 1
            continue
        runs.append(run)
        value = flag
        run = 1
    if value:
        # Ends on foreground, so the final run carries pixels and has to be written.
        runs.append(run)
    # A trailing background run says nothing a reader cannot infer from the box.
    return runs


def validate_mask(mask: Any) -> dict[str, Any]:
    """Check a stored mask is the shape this module can read, and return it.

    Raises rather than returning None: every caller here is on an export path, and a mask
    that quietly becomes no mask is the failure this whole iteration exists to remove.
    """
    if not isinstance(mask, dict):
        raise ValidationError(f"a mask must be an object, got {type(mask).__name__}")
    missing = [key for key in MASK_KEYS if key not in mask]
    if missing:
        raise ValidationError(f"a mask is missing {', '.join(missing)}")
    if not isinstance(mask["rle"], list):
        raise ValidationError("a mask's rle must be a list of run lengths")
    return mask


def pixels(mask: dict[str, Any]) -> Iterator[tuple[int, int]]:
    """Every foreground pixel of a stored mask, as absolute ``(x, y)`` in the frame.

    Yields rather than building a list: a large mask is hundreds of thousands of pixels and
    every caller is painting them one at a time anyway.
    """
    validate_mask(mask)
    left, top = int(mask["left"]), int(mask["top"])
    width, height = int(mask["width"]), int(mask["height"])
    flags = decode([int(run) for run in mask["rle"]], width, height)
    for index, flag in enumerate(flags):
        if flag:
            yield left + index % width, top + index // width


def mask_bounds(mask: dict[str, Any]) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` in frame coordinates, with both ends inclusive.

    Inclusive because that is what CVAT's element means: it writes ``width`` as
    ``right - left + 1``.
    """
    validate_mask(mask)
    left, top = int(mask["left"]), int(mask["top"])
    return left, top, left + int(mask["width"]) - 1, top + int(mask["height"]) - 1


def to_attribute(rle: list[int]) -> str:
    """The runs as CVAT writes them: comma-separated, no brackets, no spaces."""
    return ",".join(str(int(run)) for run in rle)


def from_attribute(value: str) -> list[int]:
    """CVAT's comma-separated runs back to a list.

    Tolerates the spaces a hand-edited file picks up, and an empty attribute, which means an
    empty mask rather than a parse error.
    """
    stripped = value.strip()
    if not stripped:
        return []
    try:
        return [int(part) for part in stripped.replace(" ", "").split(",") if part]
    except ValueError as exc:
        raise ValidationError(f"unreadable mask rle: {value[:60]!r}") from exc


__all__ = [
    "MASK_KEYS",
    "decode",
    "encode",
    "from_attribute",
    "mask_bounds",
    "pixels",
    "to_attribute",
    "validate_mask",
]
