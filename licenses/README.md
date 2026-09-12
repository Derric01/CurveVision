# Third-party license texts

Full license texts for dependencies whose licenses require reproduction when the software
is redistributed. The per-dependency attribution table lives in
[`../docs/THIRD_PARTY_NOTICES.md`](../docs/THIRD_PARTY_NOTICES.md).

If you package CurveVision for distribution, ship this directory alongside the binary or
image. For LGPL dependencies (Dramatiq, psycopg, and FFmpeg via PyAV) you also need to
satisfy the source-availability and relinking obligations described in the notices file.

| File | Covers |
| --- | --- |
| `MIT-video-decoding.txt` | Not a dependency — CurveVision *adapts source* from this project, in `server/curvevision/media/video.py`. The obligation is the same, and the file carries the upstream copyright header in place. See [ADR 0007](../docs/adr/0007-reusing-third-party-code.md) and [THIRD_PARTY_NOTICES](../docs/THIRD_PARTY_NOTICES.md#adapted-source). |
