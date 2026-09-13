/**
 * API contract types.
 *
 * Hand-written to mirror the server's Pydantic models. They are checked against the live
 * OpenAPI schema in CI (`scripts/check-api-types.mjs`), so drift fails the build rather
 * than surfacing as a runtime surprise.
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
}

export interface Asset {
  id: string;
  task_id: string;
  name: string;
  position: number;
  start_frame: number;
  frame_count: number;
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
  elements: Record<string, unknown>[];
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
