#!/usr/bin/env python3
"""Build the CurveVision local server as a single self-contained executable.

The desktop application ships this binary and spawns it as a child process. Packaging it
is what turns "install Python, PostgreSQL, Redis and an object store" into "download the
app", so the build is not an afterthought -- it is the thing that makes a desktop release
possible at all.

    python desktop/sidecar/build.py                 # build, then smoke test
    python desktop/sidecar/build.py --skip-tests    # build only
    python desktop/sidecar/build.py --out DIR       # choose the output directory

A build that produces a binary which does not start is worse than no build, so unless
`--skip-tests` is given this script launches the executable it just made, waits for the
handshake, drives a request through the API and reports how long the launch took.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "curvevision-local.spec"
HANDSHAKE_PREFIX = "CURVEVISION_READY "

#: PyInstaller names the executable from the spec; Windows adds the suffix.
BINARY_NAME = "curvevision-local.exe" if sys.platform == "win32" else "curvevision-local"


def build(dist: Path, work: Path) -> Path:
    print(f"building with {sys.executable}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            str(SPEC),
        ],
        check=True,
        cwd=REPO / "server",
    )

    binary = dist / BINARY_NAME
    if not binary.is_file():
        raise SystemExit(f"the build produced no executable at {binary}")
    binary.chmod(0o755)
    print(f"\n  {binary}  ({binary.stat().st_size / 1024 / 1024:.0f} MB)")
    _write_tauri_alias(binary)
    return binary


def _write_tauri_alias(binary: Path) -> Path:
    """Copy the executable to the name Tauri expects for an external binary.

    Tauri identifies a sidecar by target triple so that one configuration can describe a
    build for every platform. It strips the suffix again when it installs the file, so the
    running application sees the plain name.
    """
    alias = binary.with_name(f"{binary.stem}-{target_triple()}{binary.suffix}")
    shutil.copy2(binary, alias)
    alias.chmod(0o755)
    print(f"  {alias.name}  (for the desktop bundler)")
    return alias


def target_triple() -> str:
    """The Rust target triple for this machine.

    Asked of `rustc`, which is authoritative and is installed wherever the desktop shell
    gets built anyway. The fallbacks cover building the sidecar alone, on a machine with
    no Rust toolchain.
    """
    try:
        output = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True).stdout
        for line in output.splitlines():
            if line.startswith("host: "):
                return line.removeprefix("host: ").strip()
    except (OSError, subprocess.CalledProcessError):
        pass

    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        machine, machine
    )
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform == "win32":
        return f"{arch}-pc-windows-msvc"
    return f"{arch}-unknown-linux-gnu"


def smoke_test(binary: Path) -> None:
    """Launch the binary and prove it actually serves the API.

    A packaged Python application fails in ways source never does -- a module imported by
    name that the bundler could not see, a data file that did not make it in. Only running
    it finds those.
    """
    with tempfile.TemporaryDirectory(prefix="curvevision-smoke-") as raw:
        data_dir = Path(raw) / "CurveVision"
        started = time.perf_counter()
        process = subprocess.Popen(
            [str(binary), "--data-dir", str(data_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            handshake = _await_handshake(process)
            elapsed = time.perf_counter() - started
            print(f"\n  handshake after {elapsed * 1000:.0f} ms at {handshake['url']}")

            health = _get_json(f"{handshake['url']}/api/v1/health")
            _check(health["status"] == "ok", f"health reported {health['status']}")
            _check(health["local_mode"] is True, "the packaged server is not in local mode")
            _check(
                health["version"] == handshake["version"],
                "the handshake and the API disagree about the version",
            )

            me = _get_json(f"{handshake['url']}/api/v1/auth/me", token=handshake["token"])
            _check(me["username"] == "local", f"unexpected local account {me['username']!r}")

            anonymous = _status(f"{handshake['url']}/api/v1/auth/me")
            _check(anonymous == 401, f"an unauthenticated request returned {anonymous}, not 401")

            _check(
                (data_dir / "curvevision.db").is_file(),
                "the database was not created in the data directory",
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()

    print("  smoke test passed: migrated, provisioned, served, and refused an anonymous call")


def _await_handshake(process: subprocess.Popen[str], timeout: float = 120.0) -> dict[str, str]:
    assert process.stdout is not None
    deadline = time.time() + timeout
    noise: list[str] = []
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        if line.startswith(HANDSHAKE_PREFIX):
            parsed: dict[str, str] = json.loads(line[len(HANDSHAKE_PREFIX) :])
            return parsed
        noise.append(line.rstrip())
    raise SystemExit("the packaged server never announced itself.\n" + "\n".join(noise[-40:]))


def _get_json(url: str, token: str | None = None) -> dict[str, object]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for _ in range(80):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body: dict[str, object] = json.loads(response.read())
                return body
        except urllib.error.URLError:
            # The handshake is printed just before uvicorn binds, so a first attempt can
            # arrive a few milliseconds early.
            time.sleep(0.1)
    raise SystemExit(f"never got a response from {url}")


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"smoke test failed: {message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "desktop" / "sidecar" / "dist",
        help="Where to put the executable (default: desktop/sidecar/dist).",
    )
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="Scratch directory for the build (default: a sibling of --out).",
    )
    parser.add_argument("--skip-tests", action="store_true", help="Build without verifying.")
    args = parser.parse_args(argv)

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            raise SystemExit(
                "PyInstaller is not installed. Install the desktop extras:\n"
                "  pip install -e 'server[dev,media,desktop]'"
            ) from None

    dist = args.out.resolve()
    work = (args.work or dist.parent / "build").resolve()
    binary = build(dist, work)

    if not args.skip_tests:
        smoke_test(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
