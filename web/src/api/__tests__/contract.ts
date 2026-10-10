/**
 * The hand-written API types held to what the server declares, at compile time.
 *
 * `../schema.ts` is generated from the server's OpenAPI document (`scripts/api_types.py`,
 * kept current by `./scripts/check.sh`). For each response the client reads, every field
 * the hand-written interface declares must exist on the server's schema, with a type the
 * client's accepts. A field the server renamed or dropped used to surface as a value that
 * was quietly `undefined`; here it is a `tsc` error naming the field.
 *
 * Nothing runs: this file is only type-checked (`tsc -b`, in `npm run build` and
 * `./scripts/check.sh`). The hand-written file stays because it carries the comments and the
 * narrower unions the editor relies on; this is what stops it drifting.
 */

import type { components } from '../schema';
import type * as Api from '../types';

type Schemas = components['schemas'];

/**
 * `true`, or the names of the client's fields the server does not send as declared:
 * `missing` when the server has no such field, `differs` when its type is not one the
 * client's accepts.
 */
type Reads<Server, Client> = Compare<Sent<Server>, Client>;

/**
 * A response model serialises every field, defaults included, so a field the schema marks
 * optional — because it has a default — is always sent. Nested objects are sent the same way.
 */
type Sent<T> = T extends readonly (infer Item)[]
  ? Sent<Item>[]
  : T extends object
    ? { [K in keyof T]-?: Sent<Exclude<T[K], undefined>> }
    : T;

type Compare<Server, Client> = {
  [K in keyof Client]-?: K extends keyof Server
    ? Server[K] extends Client[K]
      ? true
      : ['differs', K]
    : ['missing', K];
}[keyof Client];

type Holds<T extends true> = T;

/**
 * The one known gap, held open by name rather than by loosening the check. The server
 * declares a shape's skeleton joints as untyped objects (`list[dict]` on `ShapeOut`), and it
 * is right to: a COCO import stores each joint as the reader produced it — by `name`, with no
 * `label_id` — so what the client's `ApiSkeletonElement` promises is true only of joints drawn
 * here. Typing it on the server would turn every such job into a 500 on read. Fixing the
 * import to resolve joint names to label ids is `handoff.md`'s next best action; when it is
 * done, type `elements` on the server and delete this.
 */
type WithoutJoints<T> = Omit<T, 'elements'>;
type DocumentWithoutJoints = Omit<Api.AnnotationDocument, 'shapes'> & {
  shapes: WithoutJoints<Api.ApiShape>[];
};

export type Contract = [
  Holds<Reads<Schemas['UserBrief'], Api.UserBrief>>,
  Holds<Reads<Schemas['UserOut'], Api.User>>,
  Holds<Reads<Schemas['TokenPair'], Api.TokenPair>>,
  Holds<Reads<Schemas['OrganizationWithRole'], Api.Organization>>,
  Holds<Reads<Schemas['AttributeOut'], Api.AttributeDefinition>>,
  Holds<Reads<Schemas['LabelOut'], Api.Label>>,
  Holds<Reads<Schemas['ProjectDetail'], Api.Project>>,
  Holds<Reads<Schemas['ProjectStatistics'], Api.ProjectStatistics>>,
  Holds<Reads<Schemas['TaskProgress'], Api.TaskProgress>>,
  Holds<Reads<Schemas['TaskDetail'], Api.Task>>,
  Holds<Reads<Schemas['TaskMediaMeta'], Api.TaskMediaMeta>>,
  Holds<Reads<Schemas['BackgroundTaskOut'], Api.BackgroundTaskBrief>>,
  Holds<Reads<Schemas['AssetOut'], Api.Asset>>,
  Holds<Reads<Schemas['LocalImportResult'], Api.LocalImportResult>>,
  Holds<Reads<Schemas['UploadSessionOut'], Api.UploadSession>>,
  Holds<Reads<Schemas['JobOut'], Api.Job>>,
  Holds<Reads<Schemas['JobListing'], Api.JobListing>>,
  Holds<Reads<Schemas['FrameInfo'], Api.FrameInfo>>,
  Holds<Reads<Schemas['ShapeOut'], WithoutJoints<Api.ApiShape>>>,
  Holds<Reads<Schemas['SkeletonElement'], Api.ApiSkeletonElement>>,
  Holds<Reads<Schemas['TrackOut'], Api.ApiTrack>>,
  Holds<Reads<Schemas['TagOut'], Api.ApiTag>>,
  Holds<Reads<Schemas['AnnotationDocument'], DocumentWithoutJoints>>,
  Holds<Reads<Schemas['AnnotationWriteResult'], Api.AnnotationWriteResult>>,
  Holds<Reads<Schemas['IssueOut'], Api.Issue>>,
  Holds<Reads<Schemas['FormatCapabilitiesOut'], Api.DatasetFormat>>,
  Holds<Reads<Schemas['ModelRegistrationOut'], Api.ModelRegistration>>,
  Holds<Reads<Schemas['InferenceRunResult'], Api.InferenceResult>>,
  Holds<Reads<Schemas['QualityReportOut'], Api.QualityReport>>,
];
