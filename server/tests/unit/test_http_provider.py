"""Reading a model server's response: what `HttpModelProvider` accepts and how it converts it.

The API tests drive a fake model end to end; these pin the parsing a fake built for one test
would not exercise.
"""

from __future__ import annotations

import pytest

from curvevision.core.errors import ProviderError
from curvevision.domain.enums import ShapeType
from curvevision.ml import HttpModelProvider
from curvevision.ml.base import InferenceFrame, InferenceRequest


def parse(payload: dict[str, object]):
    request = InferenceRequest(
        model="pose",
        frames=[InferenceFrame(frame=0, image=b"", width=200, height=100)],
        confidence_threshold=0.0,
    )
    return HttpModelProvider()._parse_result(payload, request)


def skeleton(*joints: object) -> dict[str, object]:
    return {"frame": 0, "label": "person", "type": "skeleton", "points": [], "elements": joints}


def test_a_normalised_skeleton_s_joints_are_rescaled_with_everything_else() -> None:
    """They were passed through in `[0, 1]`, so every joint landed in the top-left pixel."""
    result = parse(
        {
            "normalised": True,
            "shapes": [
                skeleton(
                    {"name": "nose", "points": [0.5, 0.25]},
                    {"name": "tail", "outside": True},
                )
            ],
        }
    )

    [shape] = result.shapes
    assert shape.shape_type is ShapeType.SKELETON
    assert shape.elements == [
        {"name": "nose", "points": [100.0, 25.0]},
        {"name": "tail", "outside": True, "points": []},
    ]


def test_joints_in_pixels_are_kept_as_sent() -> None:
    [shape] = parse({"shapes": [skeleton({"name": "nose", "points": [12, 34]})]}).shapes
    assert shape.elements == [{"name": "nose", "points": [12.0, 34.0]}]


@pytest.mark.parametrize(
    "joint",
    [[1, 2], "nose", {"name": "nose", "points": [1, 2, 3, 4]}, {"name": "nose", "points": "x"}],
)
def test_a_joint_that_is_not_one_is_a_malformed_response(joint: object) -> None:
    with pytest.raises(ProviderError, match="Malformed shape"):
        parse({"shapes": [skeleton(joint)]})
