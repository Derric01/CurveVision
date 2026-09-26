/**
 * API contract types.
 *
 * Hand-written to mirror the server's Pydantic models, and **nothing checks that they still
 * do**. This header used to claim they were verified against the live OpenAPI schema in CI
 * by `scripts/check-api-types.mjs`; no such script has ever existed and no workflow
 * referenced it, so the reassurance was worse than silence — it invited trusting a net that
 * was not there. Drift shows up as a field that is quietly `undefined` at runtime, because
 * `tsc` is only ever checking this file against itself.
 *
 * Until something does check it, the discipline is manual: change a Pydantic schema, change
 * the interface here in the same commit. `handoff.md` carries generating these from the
 * OpenAPI document as a candidate piece of work.
 */

export type Role = 'viewer' | 'annotator' | 'reviewer' | 'maintainer' | 'admin' | 'owner';

export type JobState = 'new' | 'in_progress' | 'submitted' | 'accepted' | 'rejected';

export type TaskStatus = 'draft' | 'ready' | 'in_progress' | 'completed' | 'archived';

export type AnnotationSourceApi =
  | 'manual'
  | 'model'
  | 'model_corrected'
  | 'imported'
  | 'interpolated';

export interface Page<T> {
  count: number;
  limit: number;
  offset: number;
  results: T[];
}

export interface UserBrief {
  id: string;
  username: string;
  full_name: string | null;
}

/** One person's place in an organization, as the members endpoint reports it. */
export interface Membership {
  id: string;
  organization_id: string;
  role: Role;
  user: UserBrief;
}

export interface User extends UserBrief {
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  role?: Role;
}

export interface AttributeDefinition {
  id: string;
  name: string;
  attribute_type: 'select' | 'radio' | 'checkbox' | 'text' | 'number';
  values: string[];
  default_value: string | null;
  mutable: boolean;
  required: boolean;
  position: number;
}

export interface Label {
  id: string;
  project_id: string;
  parent_id: string | null;
  name: string;
  color: string;
  position: number;
  allowed_shape_types: string[];
  skeleton_edges: number[][];
  attributes: AttributeDefinition[];
  children: Label[];
}

/**
 * A label as `PUT`/`POST /projects/{id}/labels` accept it — the server's `LabelIn`.
 *
 * Not `Label` minus a couple of fields: the server's input schema is strict, so sending a
 * `Label` straight back is a 422 on `project_id` and `parent_id`. Build one with
 * `labelToPayload`, which is also where the reason each field has to be present lives.
 */
export interface LabelPayload {
  name: string;
  color: string;
  position: number;
  allowed_shape_types: string[];
  skeleton_edges: number[][];
  attributes: AttributePayload[];
}

/**
 * An attribute as `LabelIn` accepts it. With `id`, the existing definition is kept — and
 * with it the values annotations recorded under it; without, a new one is created.
 */
export type AttributePayload = Omit<AttributeDefinition, 'id'> & { id?: string };

export interface Project {
  id: string;
  organization_id: string;
  slug: string;
  name: string;
  description: string | null;
  open_assignment: boolean;
  created_at: string;
  updated_at: string;
  owner: UserBrief | null;
  labels?: Label[];
  task_count?: number;
}

export interface ProjectStatistics {
  project_id: string;
  task_count: number;
  job_count: number;
  frame_count: number;
  shape_count: number;
  track_count: number;
  tag_count: number;
  label_distribution: Record<string, number>;
  jobs_by_state: Record<string, number>;
  frames_without_annotations: number;
}

export interface TaskProgress {
  task_id: string;
  job_count: number;
  jobs_by_state: Record<string, number>;
  completed_frames: number;
  total_frames: number;
  completion: number;
}

export interface Task {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  status: TaskStatus;
  media_kind: 'image' | 'video';
  frame_count: number;
  segment_size: number;
  overlap: number;
  created_at: string;
  updated_at: string;
  owner: UserBrief | null;
  assignee: UserBrief | null;
  progress?: TaskProgress | null;
}

export interface TaskMediaMeta {
  task_id: string;
  media_kind: 'image' | 'video';
  frame_count: number;
  /** How many frames one chunk holds. Zero means chunking is switched off server-side. */
  frames_per_chunk: number;
  chunk_count: number;
  /**
   * False while at least one asset's `frame_count` is still the upload-time estimate from
   * container metadata rather than a decoded count. An estimate that runs high offers
   * frames the media does not contain, which an annotator meets as missing media — so this
   * is worth showing rather than trusting.
   *
   * Optional because a server older than this field simply omits it; absent reads as true,
   * which is what that server meant.
   */
  frame_count_exact?: boolean;
  /** Names of the estimated assets, up to a server-side sample size. */
  estimated_assets?: string[];
  /** How many are estimated in total, which may exceed `estimated_assets.length`. */
  estimated_asset_count?: number;
}

/** A queued background job, as much of it as a caller that only wants to poll needs. */
export interface BackgroundTaskBrief {
  id: string;
  kind: string;
  state: string;
  progress: number;
  message: string | null;
  error: string | null;
}

export interface Asset {
  id: string;
  task_id: string;
  name: string;
  position: number;
  start_frame: number;
  frame_count: number;
  /** See `TaskMediaMeta.frame_count_exact`. Always true for an image. */
  frame_count_exact?: boolean;
  created_at: string;
}

export interface LocalImportResult {
  task_id: string;
  imported: Asset[];
  /**
   * Files that were found but could not be attached, each with its reason. The server
   * imports what it can rather than failing the whole folder, so this is routinely
   * non-empty on a real photo library and has to be shown, not swallowed.
   */
  skipped: string[];
  frame_count: number;
}

/** The state of one resumable upload: how much of the declared file has actually landed. */
export interface UploadSession {
  id: string;
  task_id: string;
  filename: string;
  declared_size: number;
  received_bytes: number;
  completed: boolean;
}

export interface Job {
  id: string;
  task_id: string;
  index: number;
  kind: 'annotation' | 'ground_truth';
  state: JobState;
  start_frame: number;
  stop_frame: number;
  annotation_version: number;
  shape_count: number;
  track_count: number;
  tag_count: number;
  locked: boolean;
  created_at: string;
  updated_at: string;
  assignee: UserBrief | null;
  reviewer: UserBrief | null;
}

/**
 * A job as the cross-project listing (`GET /jobs`) returns it.
 *
 * The extra two fields are what make a queue spanning every project readable — see the
 * server's `JobListing`, which carries them only here because `Job.task` is a lazy
 * relationship every other job route would have to start loading.
 */
export interface JobListing extends Job {
  task_name: string;
  project_id: string;
}

export interface FrameInfo {
  frame: number;
  asset_id: string;
  name: string;
  width: number | null;
  height: number | null;
  offset: number;
  media_url: string;
  thumbnail_url: string | null;
}

export interface ApiShape {
  id: string;
  client_id: string | null;
  label_id: string;
  frame: number;
  shape_type: string;
  points: number[];
  rotation: number;
  occluded: boolean;
  outside: boolean;
  z_order: number;
  group: number | null;
  source: AnnotationSourceApi;
  confidence: number | null;
  attributes: Record<string, unknown>;
  mask: Record<string, unknown> | null;
  /** Skeleton joints, in the parent label's declared child order. Empty for every other
   * shape type. */
  elements: ApiSkeletonElement[];
}

/** One joint of a skeleton, as the API sends and accepts it. */
export interface ApiSkeletonElement {
  label_id: string;
  /** Exactly two numbers. `[0, 0]` with `outside` set is a joint nobody could see. */
  points: number[];
  occluded: boolean;
  outside: boolean;
}

export interface ApiTrack {
  id: string;
  client_id: string | null;
  label_id: string;
  shape_type: string;
  group: number | null;
  object_id: number | null;
  source: AnnotationSourceApi;
  confidence: number | null;
  attributes: Record<string, unknown>;
  shapes: {
    frame: number;
    points: number[];
    rotation: number;
    occluded: boolean;
    outside: boolean;
    keyframe: boolean;
    z_order: number;
    attributes: Record<string, unknown>;
  }[];
}

export interface ApiTag {
  id: string;
  client_id: string | null;
  label_id: string;
  frame: number | null;
  source: AnnotationSourceApi;
  confidence: number | null;
  attributes: Record<string, unknown>;
}

export interface AnnotationDocument {
  job_id: string;
  annotation_version: number;
  shapes: ApiShape[];
  tracks: ApiTrack[];
  tags: ApiTag[];
}

export interface AnnotationWriteResult {
  job_id: string;
  annotation_version: number;
  created: Record<string, number>;
  updated: Record<string, number>;
  deleted: Record<string, number>;
  id_map: Record<string, string>;
}

export interface Issue {
  id: string;
  job_id: string;
  frame: number;
  position: number[];
  shape_id: string | null;
  track_id: string | null;
  state: 'open' | 'resolved';
  created_at: string;
  resolved_at: string | null;
  comments: { id: string; body: string; created_at: string; author: UserBrief | null }[];
}

export interface DatasetFormat {
  id: string;
  name: string;
  version: string;
  extension: string;
  supports_import: boolean;
  supports_export: boolean;
  shape_types: string[];
  supports_tracks: boolean;
  supports_tags: boolean;
  supports_attributes: boolean;
  notes: string | null;
}

export interface ModelRegistration {
  id: string;
  organization_id: string | null;
  slug: string;
  name: string;
  description: string | null;
  provider: string;
  kind: 'detector' | 'segmenter' | 'interactor' | 'tracker' | 'classifier' | 'ocr';
  output_labels: string[];
  /**
   * The model takes the classes to look for as text, at inference time — YOLO-World,
   * Grounding DINO, OWL-ViT. `output_labels` is then a default rather than the limit.
   *
   * Optional because a server older than the field omits it; absent reads as false, which
   * is what that server meant.
   */
  open_vocabulary?: boolean;
  is_active: boolean;
  created_at: string;
}

export interface InferenceResult {
  background_task_id: string | null;
  shapes: Record<string, unknown>[];
  frames_processed: number;
  created_shapes: number;
  created_tags: number;
  annotation_version: number | null;
  warnings: string[];
}

/** RFC 9457 problem document, as returned by every error path. */
export interface ProblemDocument {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  errors?: { location: (string | number)[]; message: string; type: string }[];
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------- quality

/** What went wrong at one place, as `services/comparison.py` classifies it. */
export type ConflictKind = 'missing' | 'extra' | 'wrong_label' | 'poor_overlap';

export interface QualityConflict {
  kind: ConflictKind;
  frame: number;
  /** The annotated shape's label. Null on a `missing` conflict: nothing was annotated. */
  label_id: string | null;
  /** The ground truth's label. Null on an `extra` conflict: the ground truth has nothing. */
  expected_label_id: string | null;
  shape_id: string | null;
  ground_truth_shape_id: string | null;
  iou: number | null;
}

export interface QualityLabelScore {
  matched: number;
  missing: number;
  extra: number;
  precision: number;
  recall: number;
  f1: number;
  mean_iou: number;
}

/**
 * The report's `details` blob.
 *
 * The server stores this as free-form JSON, so this interface describes what
 * `ComparisonResult.as_details()` writes rather than anything the API enforces. Read it
 * through `features/editor/quality.ts`, which tolerates a report missing any of it.
 */
export interface QualityDetails {
  compared_frames?: number;
  matched?: number;
  missing?: number;
  extra?: number;
  mean_iou?: number;
  per_label?: Record<string, QualityLabelScore>;
  conflicts?: QualityConflict[];
}

export interface QualityReport {
  id: string;
  task_id: string;
  job_id: string | null;
  ground_truth_job_id: string | null;
  iou_threshold: number;
  precision: number;
  recall: number;
  f1: number;
  /**
   * The job's `annotation_version` when it was scored, or null on a report written before
   * the server recorded it. Compare against the job's version now to tell whether the
   * score still describes the work.
   */
  annotation_version: number | null;
  details: QualityDetails;
  created_at: string;
}
