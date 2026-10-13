"""Pluggable dataset import/export.

Importing this package registers the built-in formats. Third-party formats register
themselves through the ``curvevision.formats`` entry-point group.
"""

from curvevision.formats import (  # noqa: F401
    coco,
    cvat_xml,
    kitti,
    labelme,
    mot,
    native,
    registry,
    segmentation,
    voc,
    yolo,
    yolo_variants,
)
from curvevision.formats.base import (
    AttributeSpec,
    DatasetFormat,
    DatasetView,
    ExportSink,
    FormatCapabilities,
    FrameRecord,
    ImportContext,
    ImportResult,
    ImportSource,
    LabelSpec,
    ShapeRecord,
    TagRecord,
)
from curvevision.formats.io import (
    MemoryExportSink,
    MemoryImportSource,
    ZipExportSink,
    ZipImportSource,
)
from curvevision.formats.registry import all_formats, get_format, register

__all__ = [
    "AttributeSpec",
    "DatasetFormat",
    "DatasetView",
    "ExportSink",
    "FormatCapabilities",
    "FrameRecord",
    "ImportContext",
    "ImportResult",
    "ImportSource",
    "LabelSpec",
    "MemoryExportSink",
    "MemoryImportSource",
    "ShapeRecord",
    "TagRecord",
    "ZipExportSink",
    "ZipImportSource",
    "all_formats",
    "get_format",
    "register",
    "registry",
]
