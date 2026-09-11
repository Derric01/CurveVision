# PyInstaller spec for the CurveVision local server.
#
# The desktop shell ships a self-contained copy of the server and spawns it as a child
# process. This is the alternative to asking every person who installs an annotation tool
# to also install Python, PostgreSQL, Redis and an object store; it is the whole reason a
# desktop build can be a download rather than a tutorial.
#
# Build it with `python desktop/sidecar/build.py`, which pins the output name per platform
# and does the smoke test. Running `pyinstaller` on this file directly also works.
#
# Notes on what is here and why:
#
# * `curvevision/migrations` is **data**, not code. Alembic reads `env.py` and each
#   revision script from disk at runtime, so they have to exist as files inside the
#   bundle. Without them a second release could not upgrade a database written by the
#   first, which is the property that makes the desktop database safe to keep.
# * `uvicorn`, `aiosqlite` and the anyio backend are imported by name at runtime, so
#   static analysis never sees them.
# * Nothing S3, Redis, Dramatiq or PostgreSQL is excluded on purpose: those extras are
#   simply not installed in the build environment, and a local installation has no use
#   for them.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - PyInstaller injects SPECPATH
SERVER_DIR = (SPEC_DIR.parents[1] / "server").resolve()

hidden_imports = [
    # The database driver is named in a URL string, never imported.
    "aiosqlite",
    # uvicorn picks its protocol, loop and lifespan implementations by string.
    *collect_submodules("uvicorn"),
    # anyio's backend is resolved by name from sniffio's answer.
    "anyio._backends._asyncio",
    # Alembic loads revision scripts by path; they import these.
    "curvevision.core.types",
    "curvevision.domain",
    # Registered by import side effect at startup.
    "curvevision.formats",
    "curvevision.jobs.tasks",
    "curvevision.ml",
]

datas = [
    (str(SERVER_DIR / "curvevision" / "migrations"), "curvevision/migrations"),
    *collect_data_files("alembic"),
]


analysis = Analysis(  # noqa: F821 - PyInstaller builtins
    [str(SERVER_DIR / "curvevision" / "desktop.py")],
    pathex=[str(SERVER_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Present in the build environment for other reasons; none of it is used by a local
    # installation, and each costs tens of megabytes.
    excludes=["tkinter", "pytest", "mypy", "ruff", "matplotlib", "numpy.testing"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="curvevision-local",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Compression costs a second of start-up on every launch to save a few megabytes of
    # download, once. The person waiting for their annotation tool to open is the one who
    # matters here.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
