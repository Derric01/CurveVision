"""Authorization rules.

The policy engine is a pure function, so this file can enumerate the whole matrix without
a database, an HTTP request or a second process -- which is precisely why it is a pure
function.
"""

from __future__ import annotations

import uuid

import pytest

from curvevision.core.errors import PermissionDeniedError
from curvevision.domain.enums import JobState, Role
from curvevision.policy import (
    Action,
    Principal,
    ResourceContext,
    ResourceType,
    can,
    require,
)

ALICE = Principal(uuid.uuid4(), "alice")
BOB = Principal(uuid.uuid4(), "bob")
ROOT = Principal(uuid.uuid4(), "root", is_superuser=True)


def ctx(resource: ResourceType, role: Role | None, **kwargs: object) -> ResourceContext:
    return ResourceContext(resource_type=resource, organization_role=role, **kwargs)  # type: ignore[arg-type]


class TestRoleFloor:
    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            (Role.VIEWER, False),
            (Role.ANNOTATOR, False),
            (Role.REVIEWER, False),
            (Role.MAINTAINER, True),
            (Role.ADMIN, True),
            (Role.OWNER, True),
        ],
    )
    def test_project_creation_requires_maintainer(self, role: Role, allowed: bool) -> None:
        assert can(ALICE, Action.CREATE, ctx(ResourceType.PROJECT, role)) is allowed

    def test_non_members_can_do_nothing(self) -> None:
        for action in Action:
            assert can(ALICE, action, ctx(ResourceType.PROJECT, None)) is False

    def test_unlisted_action_resource_pairs_deny_by_default(self) -> None:
        # There is no (ASSET, RUN_INFERENCE) rule, so it must be denied rather than
        # accidentally permitted by a fallthrough.
        assert can(ALICE, Action.RUN_INFERENCE, ctx(ResourceType.ASSET, Role.OWNER)) is False

    def test_superuser_bypasses_everything(self) -> None:
        assert can(ROOT, Action.DELETE, ctx(ResourceType.ORGANIZATION, None)) is True


class TestAnnotationWrites:
    def test_annotator_may_write_on_their_own_job(self) -> None:
        context = ctx(ResourceType.ANNOTATION, Role.ANNOTATOR, assignee_id=ALICE.user_id)
        assert can(ALICE, Action.UPDATE, context) is True

    def test_annotator_may_not_write_on_someone_elses_job(self) -> None:
        context = ctx(ResourceType.ANNOTATION, Role.ANNOTATOR, assignee_id=BOB.user_id)
        assert can(ALICE, Action.UPDATE, context) is False

    def test_annotator_may_pick_up_unassigned_work_when_the_project_allows_it(self) -> None:
        context = ctx(
            ResourceType.ANNOTATION, Role.ANNOTATOR, assignee_id=None, open_assignment=True
        )
        assert can(ALICE, Action.CREATE, context) is True

    def test_closed_assignment_blocks_unassigned_work(self) -> None:
        context = ctx(
            ResourceType.ANNOTATION, Role.ANNOTATOR, assignee_id=None, open_assignment=False
        )
        assert can(ALICE, Action.CREATE, context) is False

    def test_viewer_never_writes_annotations(self) -> None:
        context = ctx(ResourceType.ANNOTATION, Role.VIEWER, assignee_id=ALICE.user_id)
        assert can(ALICE, Action.UPDATE, context) is False

    def test_accepted_work_is_frozen_for_annotators(self) -> None:
        context = ctx(
            ResourceType.ANNOTATION,
            Role.ANNOTATOR,
            assignee_id=ALICE.user_id,
            job_state=JobState.ACCEPTED,
        )
        assert can(ALICE, Action.UPDATE, context) is False

    def test_maintainer_may_still_correct_accepted_work(self) -> None:
        context = ctx(ResourceType.ANNOTATION, Role.MAINTAINER, job_state=JobState.ACCEPTED)
        assert can(ALICE, Action.UPDATE, context) is True


class TestJobWorkflow:
    def test_assignee_may_submit_their_own_job(self) -> None:
        context = ctx(ResourceType.JOB, Role.ANNOTATOR, assignee_id=ALICE.user_id)
        assert can(ALICE, Action.SUBMIT, context) is True

    def test_annotator_may_not_submit_another_persons_job(self) -> None:
        context = ctx(ResourceType.JOB, Role.ANNOTATOR, assignee_id=BOB.user_id)
        assert can(ALICE, Action.SUBMIT, context) is False

    def test_nobody_reviews_their_own_annotation_work(self) -> None:
        """Self-review defeats the purpose of review, so it is denied at every rank."""
        for role in (Role.REVIEWER, Role.MAINTAINER, Role.ADMIN, Role.OWNER):
            context = ctx(
                ResourceType.JOB,
                role,
                assignee_id=ALICE.user_id,
                reviewer_id=ALICE.user_id,
            )
            assert can(ALICE, Action.REVIEW, context) is False

    def test_named_reviewer_may_review_at_annotator_rank(self) -> None:
        context = ctx(
            ResourceType.JOB, Role.ANNOTATOR, assignee_id=BOB.user_id, reviewer_id=ALICE.user_id
        )
        assert can(ALICE, Action.REVIEW, context) is True

    def test_reviewer_role_may_review_unnamed_jobs(self) -> None:
        context = ctx(ResourceType.JOB, Role.REVIEWER, assignee_id=BOB.user_id)
        assert can(ALICE, Action.REVIEW, context) is True


class TestOwnership:
    def test_issue_author_may_edit_their_own_issue(self) -> None:
        context = ctx(ResourceType.ISSUE, Role.ANNOTATOR, author_id=ALICE.user_id)
        assert can(ALICE, Action.UPDATE, context) is True

    def test_issue_author_rules_do_not_leak_to_other_people(self) -> None:
        context = ctx(ResourceType.ISSUE, Role.ANNOTATOR, author_id=BOB.user_id)
        assert can(ALICE, Action.UPDATE, context) is False

    def test_project_owner_may_update_their_project_below_the_role_floor(self) -> None:
        context = ctx(ResourceType.PROJECT, Role.ANNOTATOR, owner_id=ALICE.user_id)
        assert can(ALICE, Action.UPDATE, context) is True

    def test_ownership_does_not_grant_rights_to_a_pure_viewer(self) -> None:
        context = ctx(ResourceType.PROJECT, Role.VIEWER, owner_id=ALICE.user_id)
        assert can(ALICE, Action.UPDATE, context) is False


class TestRequire:
    def test_require_raises_with_a_useful_message(self) -> None:
        with pytest.raises(PermissionDeniedError) as excinfo:
            require(ALICE, Action.DELETE, ctx(ResourceType.PROJECT, Role.VIEWER))
        assert "alice" in str(excinfo.value)
        assert excinfo.value.status_code == 403

    def test_require_is_silent_when_permitted(self) -> None:
        require(ALICE, Action.VIEW, ctx(ResourceType.PROJECT, Role.VIEWER))
