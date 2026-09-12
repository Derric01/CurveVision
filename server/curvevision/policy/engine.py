"""Authorization.

The whole permission system is this file plus its table. That is deliberate: a new
contributor should be able to read every rule in one sitting, and every rule should be
unit-testable without a database, an HTTP request or a second process.

Design:

* A ``Principal`` is who is asking (a user, possibly acting through an API token).
* A ``ResourceContext`` is what they are asking about, already loaded by the router: the
  organization role they hold in that scope, plus assignment facts about the resource.
* ``can()`` is a pure function of those two. No I/O.

Two rule layers compose:

1. **Role floor** -- the minimum role required for ``(resource, action)``.
2. **Assignment scope** -- rules that let a lower role act on resources assigned to them,
   or that deny an otherwise-permitted action (writing to an accepted job, for instance).

Constraints that hold for *everyone* regardless of role -- a locked job, a released
dataset version -- are not permissions and are not here. Services enforce those and
return 409 with a message that says what is actually wrong.

Rationale for not using Open Policy Agent: the decision is a pure
function of rows already loaded to serve the request. A network hop and a second policy
language per authorization check is a poor trade for self-hosters, and a real barrier for
contributors. We keep the good idea -- centralised, declarative, table-driven policy,
never ad-hoc checks in view bodies -- without the process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from curvevision.core.errors import PermissionDeniedError
from curvevision.domain.enums import ROLE_RANK, JobKind, JobState, Role


class ResourceType(StrEnum):
    ORGANIZATION = "organization"
    MEMBERSHIP = "membership"
    PROJECT = "project"
    LABEL = "label"
    TASK = "task"
    JOB = "job"
    ASSET = "asset"
    ANNOTATION = "annotation"
    ISSUE = "issue"
    COMMENT = "comment"
    DATASET_VERSION = "dataset_version"
    WEBHOOK = "webhook"
    MODEL = "model"
    BACKGROUND_TASK = "background_task"


class Action(StrEnum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ASSIGN = "assign"
    SUBMIT = "submit"  # annotator hands a job to review
    REVIEW = "review"  # accept/reject a job
    EXPORT = "export"
    IMPORT = "import"
    RUN_INFERENCE = "run_inference"
    MANAGE_MEMBERS = "manage_members"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request."""

    user_id: uuid.UUID
    username: str
    is_superuser: bool = False
    #: Set when the request authenticated with an API token rather than a session.
    api_token_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ResourceContext:
    """What the request is about, pre-loaded by the router.

    Everything the policy needs must be here: ``can()`` performs no I/O so that it stays a
    pure, exhaustively testable function.
    """

    resource_type: ResourceType
    #: The principal's role in the owning organization, or ``None`` if not a member.
    organization_role: Role | None = None
    #: Whoever owns the resource (project owner, task owner).
    owner_id: uuid.UUID | None = None
    #: Whoever the work is assigned to (task assignee, job assignee).
    assignee_id: uuid.UUID | None = None
    #: Whoever reviews it (job reviewer).
    reviewer_id: uuid.UUID | None = None
    #: For issues/comments: who wrote it.
    author_id: uuid.UUID | None = None
    #: Job state, when the resource is a job or scoped to one.
    job_state: JobState | None = None
    #: Job kind, when the resource is a job or scoped to one. Ground truth is restricted:
    #: see `_ground_truth_visible`.
    job_kind: JobKind | None = None
    #: Project setting: may any annotator pick up unassigned work?
    open_assignment: bool = True
    extra: dict[str, object] = field(default_factory=dict)


#: Minimum organization role required for ``(resource, action)``.
#: A missing entry denies by default -- an unlisted action is never accidentally allowed.
ROLE_FLOOR: dict[tuple[ResourceType, Action], Role] = {
    # Organizations
    (ResourceType.ORGANIZATION, Action.VIEW): Role.VIEWER,
    (ResourceType.ORGANIZATION, Action.UPDATE): Role.ADMIN,
    (ResourceType.ORGANIZATION, Action.DELETE): Role.OWNER,
    (ResourceType.ORGANIZATION, Action.MANAGE_MEMBERS): Role.ADMIN,
    (ResourceType.MEMBERSHIP, Action.VIEW): Role.VIEWER,
    (ResourceType.MEMBERSHIP, Action.CREATE): Role.ADMIN,
    (ResourceType.MEMBERSHIP, Action.UPDATE): Role.ADMIN,
    (ResourceType.MEMBERSHIP, Action.DELETE): Role.ADMIN,
    # Projects
    (ResourceType.PROJECT, Action.VIEW): Role.VIEWER,
    (ResourceType.PROJECT, Action.CREATE): Role.MAINTAINER,
    (ResourceType.PROJECT, Action.UPDATE): Role.MAINTAINER,
    (ResourceType.PROJECT, Action.DELETE): Role.ADMIN,
    (ResourceType.PROJECT, Action.EXPORT): Role.ANNOTATOR,
    (ResourceType.PROJECT, Action.IMPORT): Role.MAINTAINER,
    # Label schema
    (ResourceType.LABEL, Action.VIEW): Role.VIEWER,
    (ResourceType.LABEL, Action.CREATE): Role.MAINTAINER,
    (ResourceType.LABEL, Action.UPDATE): Role.MAINTAINER,
    (ResourceType.LABEL, Action.DELETE): Role.MAINTAINER,
    # Tasks
    (ResourceType.TASK, Action.VIEW): Role.VIEWER,
    (ResourceType.TASK, Action.CREATE): Role.MAINTAINER,
    (ResourceType.TASK, Action.UPDATE): Role.MAINTAINER,
    (ResourceType.TASK, Action.DELETE): Role.MAINTAINER,
    (ResourceType.TASK, Action.ASSIGN): Role.REVIEWER,
    (ResourceType.TASK, Action.EXPORT): Role.ANNOTATOR,
    (ResourceType.TASK, Action.IMPORT): Role.MAINTAINER,
    # Jobs
    (ResourceType.JOB, Action.VIEW): Role.VIEWER,
    (ResourceType.JOB, Action.UPDATE): Role.MAINTAINER,  # relaxed for assignees below
    (ResourceType.JOB, Action.ASSIGN): Role.REVIEWER,
    (ResourceType.JOB, Action.SUBMIT): Role.MAINTAINER,  # relaxed for assignees below
    (ResourceType.JOB, Action.REVIEW): Role.REVIEWER,
    # Media
    (ResourceType.ASSET, Action.VIEW): Role.VIEWER,
    (ResourceType.ASSET, Action.CREATE): Role.MAINTAINER,
    (ResourceType.ASSET, Action.DELETE): Role.MAINTAINER,
    # Annotations
    (ResourceType.ANNOTATION, Action.VIEW): Role.VIEWER,
    (ResourceType.ANNOTATION, Action.CREATE): Role.MAINTAINER,  # relaxed for assignees
    (ResourceType.ANNOTATION, Action.UPDATE): Role.MAINTAINER,  # relaxed for assignees
    (ResourceType.ANNOTATION, Action.DELETE): Role.MAINTAINER,  # relaxed for assignees
    (ResourceType.ANNOTATION, Action.RUN_INFERENCE): Role.ANNOTATOR,
    # Review
    (ResourceType.ISSUE, Action.VIEW): Role.VIEWER,
    (ResourceType.ISSUE, Action.CREATE): Role.ANNOTATOR,
    (ResourceType.ISSUE, Action.UPDATE): Role.REVIEWER,  # relaxed for authors below
    (ResourceType.ISSUE, Action.DELETE): Role.MAINTAINER,
    (ResourceType.COMMENT, Action.CREATE): Role.ANNOTATOR,
    (ResourceType.COMMENT, Action.UPDATE): Role.MAINTAINER,  # relaxed for authors below
    (ResourceType.COMMENT, Action.DELETE): Role.MAINTAINER,
    # Datasets
    (ResourceType.DATASET_VERSION, Action.VIEW): Role.VIEWER,
    (ResourceType.DATASET_VERSION, Action.CREATE): Role.REVIEWER,
    (ResourceType.DATASET_VERSION, Action.UPDATE): Role.REVIEWER,
    (ResourceType.DATASET_VERSION, Action.DELETE): Role.MAINTAINER,
    (ResourceType.DATASET_VERSION, Action.EXPORT): Role.ANNOTATOR,
    # Integrations
    (ResourceType.WEBHOOK, Action.VIEW): Role.MAINTAINER,
    (ResourceType.WEBHOOK, Action.CREATE): Role.MAINTAINER,
    (ResourceType.WEBHOOK, Action.UPDATE): Role.MAINTAINER,
    (ResourceType.WEBHOOK, Action.DELETE): Role.MAINTAINER,
    (ResourceType.MODEL, Action.VIEW): Role.ANNOTATOR,
    (ResourceType.MODEL, Action.CREATE): Role.MAINTAINER,
    (ResourceType.MODEL, Action.UPDATE): Role.MAINTAINER,
    (ResourceType.MODEL, Action.DELETE): Role.MAINTAINER,
    (ResourceType.MODEL, Action.RUN_INFERENCE): Role.ANNOTATOR,
    # Background tasks
    (ResourceType.BACKGROUND_TASK, Action.VIEW): Role.VIEWER,
    (ResourceType.BACKGROUND_TASK, Action.DELETE): Role.MAINTAINER,
}

#: Job states in which annotation content may not be modified.
IMMUTABLE_JOB_STATES = frozenset({JobState.ACCEPTED})

_WRITE_ACTIONS = frozenset({Action.CREATE, Action.UPDATE, Action.DELETE})


def _has_role(context: ResourceContext, minimum: Role) -> bool:
    if context.organization_role is None:
        return False
    return ROLE_RANK[context.organization_role] >= ROLE_RANK[minimum]


def _is_assigned(principal: Principal, context: ResourceContext) -> bool:
    return context.assignee_id is not None and context.assignee_id == principal.user_id


def _annotation_write_allowed(principal: Principal, context: ResourceContext) -> bool:
    """An annotator may write annotations on work that is theirs.

    Either the job is assigned to them, or the job is unassigned and the project allows
    open assignment. Anything stricter makes self-serve annotation impossible; anything
    looser lets any org member edit anyone's work.
    """
    if not _has_role(context, Role.ANNOTATOR):
        return False
    if _is_assigned(principal, context):
        return True
    return context.assignee_id is None and context.open_assignment


def _ground_truth_visible(principal: Principal, context: ResourceContext) -> bool:
    """Who may see a ground-truth job's annotations.

    Ground truth is the answer key. An annotator who can read it can copy it, and the score
    that comes back then measures nothing — which is worse than having no score, because
    the number looks like evidence. So reading it takes reviewer rank, with one exception:
    whoever is assigned to annotate the ground-truth job obviously has to see it.

    This is about the *annotations*, not the job's existence. A job listing still shows that
    a ground-truth job exists and which frames it covers; that leaks nothing an annotator
    cannot infer, and hiding it would make the frame ranges in their own job list confusing.
    """
    return _has_role(context, Role.REVIEWER) or _is_assigned(principal, context)


def can(principal: Principal, action: Action, context: ResourceContext) -> bool:
    """Return whether ``principal`` may perform ``action`` on ``context``. Pure function."""
    if principal.is_superuser:
        return True

    resource = context.resource_type

    # --- role-dependent state rules ----------------------------------------------------
    if (
        resource is ResourceType.ANNOTATION
        and context.job_kind is JobKind.GROUND_TRUTH
        and not _ground_truth_visible(principal, context)
    ):
        return False

    # On the split: a constraint that depends on *who is asking* lives here and surfaces
    # as 403. A constraint that holds for everyone regardless of role -- a locked job, a
    # released dataset version -- is state, not permission: services enforce those and
    # return 409 with a message saying what is actually wrong. Encoding them here as well
    # would give one condition two different status codes depending on the caller.
    if (
        resource is ResourceType.ANNOTATION
        and action in _WRITE_ACTIONS
        and context.job_state in IMMUTABLE_JOB_STATES
        and not _has_role(context, Role.MAINTAINER)
    ):
        # Accepted work is frozen; a maintainer may still correct it deliberately.
        return False

    # --- assignment-scoped allowances --------------------------------------------------
    if (
        resource is ResourceType.ANNOTATION
        and action in _WRITE_ACTIONS
        and _annotation_write_allowed(principal, context)
    ):
        return True

    if (
        resource is ResourceType.JOB
        and action in (Action.UPDATE, Action.SUBMIT)
        and _is_assigned(principal, context)
        and _has_role(context, Role.ANNOTATOR)
    ):
        return True

    if resource is ResourceType.JOB and action is Action.REVIEW:
        # Nobody reviews their own annotation work, at any rank.
        if context.assignee_id == principal.user_id:
            return False
        # A reviewer explicitly named on the job may review it even at annotator rank.
        if context.reviewer_id == principal.user_id and _has_role(context, Role.ANNOTATOR):
            return True

    if (
        resource in (ResourceType.ISSUE, ResourceType.COMMENT)
        and action in (Action.UPDATE, Action.DELETE)
        and context.author_id == principal.user_id
        and _has_role(context, Role.ANNOTATOR)
    ):
        return True

    if (
        resource in (ResourceType.PROJECT, ResourceType.TASK)
        and action in (Action.UPDATE, Action.DELETE)
        and context.owner_id == principal.user_id
        and _has_role(context, Role.ANNOTATOR)
    ):
        return True

    # --- role floor --------------------------------------------------------------------
    floor = ROLE_FLOOR.get((resource, action))
    if floor is None:
        return False
    return _has_role(context, floor)


def require(principal: Principal, action: Action, context: ResourceContext) -> None:
    """``can()``, but raises ``PermissionDeniedError``."""
    if not can(principal, action, context):
        raise PermissionDeniedError(
            f"{principal.username} may not {action.value} this {context.resource_type.value}",
            resource_type=context.resource_type.value,
            action=action.value,
        )
