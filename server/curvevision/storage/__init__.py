from curvevision.core.config import Settings, get_settings
from curvevision.storage.base import (
    ObjectNotFoundError,
    Storage,
    StorageError,
    StoredObject,
)
from curvevision.storage.local import LocalStorage

__all__ = [
    "LocalStorage",
    "ObjectNotFoundError",
    "Storage",
    "StorageError",
    "StoredObject",
    "get_storage",
    "reset_storage_cache",
]


_cache: dict[tuple[str, str], Storage] = {}


def get_storage(settings: Settings | None = None) -> Storage:
    """Return the configured storage backend, memoised per configuration.

    Settings objects are not hashable, so the cache is keyed on the values that actually
    determine which backend is built.
    """
    settings = settings or get_settings()
    key = (settings.storage_backend, settings.storage_local_root or settings.s3_bucket)
    if key not in _cache:
        if settings.storage_backend == "s3":
            from curvevision.storage.s3 import S3Storage  # boto3 is an optional extra

            _cache[key] = S3Storage(settings)
        else:
            _cache[key] = LocalStorage(settings.storage_local_root)
    return _cache[key]


def reset_storage_cache() -> None:
    """Test hook: drop memoised backends so a new temp root takes effect."""
    _cache.clear()
