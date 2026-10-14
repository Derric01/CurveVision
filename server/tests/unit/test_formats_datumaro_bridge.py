"""The Datumaro bridge: the long tail of formats, when Datumaro is installed.

The first group needs nothing: what the bridge registers, and from what. The second needs
Datumaro and runs where it is installed — `.github/workflows/datumaro.yml` — round-tripping
real data through a sample of the bridged formats. What each format carries is measured by
`scripts/datumaro_bridge_probe.py`, which the same job re-runs with ``--check``.
"""

from __future__ import annotations

import io

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.formats import (
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
)
from curvevision.formats import datumaro_bridge as bridge
from curvevision.formats.rle import decode as rle_decode
from curvevision.formats.rle import encode as rle_encode


class TestRegistration:
    def test_the_measured_table_names_real_shape_types(self) -> None:
        table = bridge.load_table()
        assert {row.datumaro_id for row in table} >= {"datumaro", "imagenet", "dota", "camvid"}
        for row in table:
            assert row.shape_types or row.tags, row.datumaro_id
            assert set(row.becomes) <= {kind.value for kind in row.shape_types}

    def test_nothing_is_registered_without_datumaro(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bridge, "available", lambda: False)
        assert bridge.register_available() == []

    def test_a_bridged_format_declares_what_was_measured(self) -> None:
        row = next(r for r in bridge.load_table() if r.datumaro_id == "dota")
        fmt = bridge.DatumaroFormat(row)
        assert fmt.id == "datumaro-dota"
        assert fmt.name == "DOTA (via Datumaro)"
        assert fmt.capabilities.unsupported([ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE]) == [
            ShapeType.RECTANGLE
        ]
        assert "measured" in (fmt.capabilities.notes or "")

    def test_with_datumaro_every_measured_format_is_offered(self) -> None:
        pytest.importorskip("datumaro")
        from curvevision.formats import all_formats

        offered = {f.id for f in all_formats()}
        assert {"datumaro-" + row.datumaro_id for row in bridge.load_table()} <= offered

    def test_archive_paths_outside_the_archive_are_refused(self) -> None:
        assert bridge._safe_relative("a/b.png") is not None
        for bad in ("../x", "/etc/passwd", "a/../../x", ""):
            assert bridge._safe_relative(bad) is None


Image = pytest.importorskip("PIL.Image")
WIDTH, HEIGHT = 48, 36


def picture() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "#336699").save(buffer, format="PNG")
    return buffer.getvalue()


def fmt(datumaro_id: str) -> bridge.DatumaroFormat:
    pytest.importorskip("datumaro")
    row = next(r for r in bridge.load_table() if r.datumaro_id == datumaro_id)
    return bridge.DatumaroFormat(row)


def round_trip(datumaro_id: str, frames: list[FrameRecord], labels: list[LabelSpec]):
    chosen = fmt(datumaro_id)
    sink = MemoryExportSink()
    chosen.export(DatasetView("Bridge", labels, iter(frames)), sink)
    files = {k: v if isinstance(v, bytes) else v.encode() for k, v in sink.files.items()}
    return chosen.import_(
        MemoryImportSource(files),
        ImportContext(
            known_labels={},
            frame_by_name={frame.name: frame.index for frame in frames},
            frame_count=len(frames),
            frame_sizes={frame.index: (WIDTH, HEIGHT) for frame in frames},
        ),
    )


LABELS = [LabelSpec("1", "car"), LabelSpec("2", "road"), LabelSpec("3", "night")]


class TestThroughDatumaro:
    def test_datumaros_own_format_keeps_every_shape_it_can_hold(self) -> None:
        flags = [True, False, True, True, True, False]
        mask = {"rle": rle_encode(flags, 3, 2), "left": 30, "top": 20, "width": 3, "height": 2}
        shapes = [
            ShapeRecord(
                "car",
                ShapeType.RECTANGLE,
                [2, 3, 20, 15],
                occluded=True,
                z_order=2,
                attributes={"parked": True},
            ),
            ShapeRecord("road", ShapeType.POLYGON, [0, 30, 47, 30, 47, 35]),
            ShapeRecord("road", ShapeType.POLYLINE, [0, 25, 47, 25]),
            ShapeRecord("car", ShapeType.POINTS, [5, 5, 6, 6]),
            ShapeRecord("car", ShapeType.ELLIPSE, [24, 18, 4, 3]),
            ShapeRecord("car", ShapeType.MASK, [30, 20, 32, 21], mask=mask),
        ]
        frames = [
            FrameRecord(0, "a.png", WIDTH, HEIGHT, shapes=shapes, tags=["night"], media=picture())
        ]
        result = round_trip("datumaro", frames, LABELS)
        assert result.warnings == []
        assert [t for _, t in result.tags] == ["night"]
        back = {s.shape_type: s for _, s in result.shapes}
        assert set(back) == {s.shape_type for s in shapes}
        box = back[ShapeType.RECTANGLE]
        assert box.points == [2, 3, 20, 15] and box.occluded and box.z_order == 2
        assert box.attributes == {"parked": True}
        assert back[ShapeType.ELLIPSE].points == [24, 18, 4, 3]
        m = back[ShapeType.MASK].mask or {}
        assert (m["left"], m["top"], rle_decode(m["rle"], m["width"], m["height"])) == (
            30,
            20,
            flags,
        )
        created = {spec.name: {a.name for a in spec.attributes} for spec in result.new_labels}
        assert created["car"] == {"parked"}

    def test_imagenet_folders_are_image_level_labels(self) -> None:
        frames = [
            FrameRecord(0, "a.png", WIDTH, HEIGHT, tags=["car"], media=picture()),
            FrameRecord(1, "b.png", WIDTH, HEIGHT, tags=["road"], media=picture()),
        ]
        result = round_trip("imagenet", frames, LABELS)
        assert sorted(result.tags) == [(0, "car"), (1, "road")]

    def test_dota_keeps_a_rotated_box_turned(self) -> None:
        frames = [
            FrameRecord(
                0,
                "a.png",
                WIDTH,
                HEIGHT,
                media=picture(),
                shapes=[
                    ShapeRecord("car", ShapeType.ROTATED_RECTANGLE, [10, 10, 30, 20], rotation=30),
                ],
            )
        ]
        result = round_trip("dota", frames, LABELS)
        ((_, turned),) = result.shapes
        assert turned.shape_type is ShapeType.ROTATED_RECTANGLE
        assert turned.points == pytest.approx([10, 10, 30, 20], abs=0.5)
        assert turned.rotation % 180 == pytest.approx(30, abs=0.5)

    def test_what_is_not_this_tasks_is_reported_once(self) -> None:
        chosen = fmt("imagenet")
        sink = MemoryExportSink()
        frames = [
            FrameRecord(i, f"f{i}.png", WIDTH, HEIGHT, tags=["car"], media=picture())
            for i in range(7)
        ]
        chosen.export(DatasetView("Bridge", LABELS, iter(frames)), sink)
        files = {k: v if isinstance(v, bytes) else v.encode() for k, v in sink.files.items()}
        files["../escape.png"] = b"x"
        result = chosen.import_(
            MemoryImportSource(files),
            ImportContext(known_labels={}, frame_by_name={"f0.png": 0}, frame_count=1),
        )
        assert [t for _, t in result.tags] == ["car"]
        assert "../escape.png: not a path inside the archive" in result.warnings[0]
        assert "6 item(s) not in this task were skipped" in result.warnings[1]

    def test_a_dataset_datumaro_cannot_read_is_a_warning_not_a_crash(self) -> None:
        result = fmt("camvid").import_(
            MemoryImportSource({"nonsense.txt": b"not camvid"}),
            ImportContext(known_labels={}, frame_by_name={}, frame_count=0),
        )
        assert result.shapes == []
        assert "Datumaro could not read this as 'camvid'" in result.warnings[0]
