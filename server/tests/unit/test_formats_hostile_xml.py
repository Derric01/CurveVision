"""Imported XML is untrusted: a crafted file is refused, never expanded or followed.

An annotation archive is something a person uploads, and it may have come from anywhere.
The two importers that read XML, CVAT XML and Pascal VOC, parse with defusedxml, so:

* an **entity-expansion bomb** ("billion laughs") is refused instead of growing to
  gigabytes in memory;
* an **external entity** (an XXE probe asking for a local file) is refused instead of
  read.

Either way the import reports why and imports nothing; it does not crash the request.
"""

from __future__ import annotations

import pytest

from curvevision.formats import ImportContext, MemoryImportSource, get_format

BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<{root}><filename>a.png</filename><name>&lol9;</name></{root}>
"""

XXE = """<?xml version="1.0"?>
<!DOCTYPE probe [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<{root}><filename>a.png</filename><name>&secret;</name></{root}>
"""

CONTEXT = ImportContext(known_labels={"car": "1"}, frame_by_name={"a.png": 0}, frame_count=1)

CASES = [
    ("cvat_xml", "annotations.xml", "annotations"),
    ("voc", "Annotations/a.xml", "annotation"),
]


@pytest.mark.parametrize(("fmt", "path", "root"), CASES)
@pytest.mark.parametrize("payload", [BOMB, XXE], ids=["entity-bomb", "external-entity"])
def test_a_hostile_file_is_refused_and_nothing_is_imported(
    fmt: str, path: str, root: str, payload: str
) -> None:
    result = get_format(fmt).import_(
        MemoryImportSource({path: payload.format(root=root).encode()}), CONTEXT
    )
    assert result.shapes == []
    assert any("refused" in warning for warning in result.warnings), result.warnings
    # Nothing from /etc/passwd, and no expansion, reached the warnings either.
    assert all("root:" not in warning and "lollol" not in warning for warning in result.warnings)


@pytest.mark.parametrize(("fmt", "path", "root"), CASES)
def test_an_ordinary_file_still_imports(fmt: str, path: str, root: str) -> None:
    """The guard refuses entity declarations, not XML: a normal file parses as before."""
    plain = f'<?xml version="1.0"?><{root}><filename>a.png</filename></{root}>'
    result = get_format(fmt).import_(MemoryImportSource({path: plain.encode()}), CONTEXT)
    assert not any("refused" in warning for warning in result.warnings), result.warnings
