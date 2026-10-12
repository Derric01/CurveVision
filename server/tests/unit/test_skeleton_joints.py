"""Turning joints from outside the project into the form this project stores them in."""

from __future__ import annotations

from curvevision.services.skeletons import resolve_joints

JOINTS = [("id-nose", "nose"), ("id-tail", "tail")]


def test_joints_are_found_by_name_and_put_in_the_label_s_order() -> None:
    resolved, unknown = resolve_joints(
        [
            {"name": "tail", "points": [5, 6], "occluded": True},
            {"name": "nose", "points": [1, 2]},
        ],
        JOINTS,
    )
    assert resolved == [
        {"label_id": "id-nose", "points": [1.0, 2.0], "occluded": False, "outside": False},
        {"label_id": "id-tail", "points": [5.0, 6.0], "occluded": True, "outside": False},
    ]
    assert unknown == []


def test_a_joint_the_archive_lacks_is_one_nobody_could_see() -> None:
    """As the editor records a skipped joint, so the list still lines up with the label's."""
    resolved, _ = resolve_joints([{"name": "nose", "points": [1, 2]}], JOINTS)
    assert resolved[1] == {
        "label_id": "id-tail",
        "points": [0.0, 0.0],
        "occluded": False,
        "outside": True,
    }


def test_a_joint_the_label_does_not_declare_is_reported() -> None:
    _, unknown = resolve_joints(
        [{"name": "nose", "points": [1, 2]}, {"name": "wing", "points": [3, 4]}], JOINTS
    )
    assert unknown == ["wing"]


def test_joints_with_no_names_are_matched_by_position() -> None:
    """A native export from before joints carried names: ids from another project, in order."""
    resolved, _ = resolve_joints(
        [
            {"label_id": "elsewhere-1", "points": [1, 2]},
            {"label_id": "elsewhere-2", "points": [3, 4], "outside": True},
        ],
        JOINTS,
    )
    assert [(joint["label_id"], joint["outside"]) for joint in resolved] == [
        ("id-nose", False),
        ("id-tail", True),
    ]
