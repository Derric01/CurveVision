"""Format plugin registry.

Adding a format is: implement ``DatasetFormat``, call ``register()``. Nothing in the core
dataset system changes, which is the whole point of the seam.

Third-party formats can register from an installed package via the
``curvevision.formats`` entry-point group.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from curvevision.core.errors import UnsupportedFormatError
from curvevision.core.logging import get_logger
from curvevision.formats.base import DatasetFormat

logger = get_logger(__name__)

_FORMATS: dict[str, DatasetFormat] = {}
_plugins_loaded = False


def register(fmt: DatasetFormat) -> DatasetFormat:
    if fmt.id in _FORMATS and _FORMATS[fmt.id] is not fmt:
        raise ValueError(f"A dataset format with id {fmt.id!r} is already registered")
    _FORMATS[fmt.id] = fmt
    return fmt


def get_format(format_id: str) -> DatasetFormat:
    _load_plugins()
    try:
        return _FORMATS[format_id]
    except KeyError as exc:
        available = ", ".join(sorted(_FORMATS))
        raise UnsupportedFormatError(
            f"Unknown dataset format {format_id!r}. Available: {available}"
        ) from exc


def all_formats() -> list[DatasetFormat]:
    _load_plugins()
    return sorted(_FORMATS.values(), key=lambda fmt: fmt.id)


def _load_plugins() -> None:
    """Load third-party formats declared under the ``curvevision.formats`` entry point."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    try:
        discovered = entry_points(group="curvevision.formats")
    except Exception:
        logger.warning("could not enumerate format plugins")
        return
    for entry in discovered:
        try:
            register(entry.load()())
        except Exception:
            logger.exception("failed to load format plugin", extra={"plugin": entry.name})
