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

export type Contract = [
  Holds<Reads<Schemas['UserBrief'], Api.UserBrief>>,
  Holds<Reads<Schemas['UserOut'], Api.User>>,
  Holds<Reads<Schemas['TokenPair'], Api.TokenPair>>,
  Holds<Reads<Schemas['SignInOptions'], Api.SignInOptions>>,
  Holds<Reads<Schemas['SsoStarted'], Api.SsoStarted>>,
  Holds<Reads<Schemas['ApiTokenOut'], Api.ApiToken>>,
  Holds<Reads<Schemas['ApiTokenCreated'], Api.ApiTokenCreated>>,
  Holds<Reads<Schemas['MembershipOut'], Api.Membership>>,
  Holds<Reads<Schemas['DatasetVersionOut'], Api.DatasetVersion>>,
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
  Holds<Reads<Schemas['TeamServerCheckOut'], Api.TeamServerCheck>>,
  Holds<Reads<Schemas['UploadSessionOut'], Api.UploadSession>>,
  Holds<Reads<Schemas['JobOut'], Api.Job>>,
  Holds<Reads<Schemas['JobListing'], Api.JobListing>>,
  Holds<Reads<Schemas['FrameInfo'], Api.FrameInfo>>,
  Holds<Reads<Schemas['ShapeOut'], Api.ApiShape>>,
  Holds<Reads<Schemas['SkeletonElement'], Api.ApiSkeletonElement>>,
  Holds<Reads<Schemas['SkeletonElementOut'], Api.ApiSkeletonElement>>,
  Holds<Reads<Schemas['TrackOut'], Api.ApiTrack>>,
  Holds<Reads<Schemas['TagOut'], Api.ApiTag>>,
  Holds<Reads<Schemas['AnnotationDocument'], Api.AnnotationDocument>>,
  Holds<Reads<Schemas['AnnotationWriteResult'], Api.AnnotationWriteResult>>,
  Holds<Reads<Schemas['IssueOut'], Api.Issue>>,
  Holds<Reads<Schemas['FormatCapabilitiesOut'], Api.DatasetFormat>>,
  Holds<Reads<Schemas['ImportResultOut'], Api.AnnotationImportResult>>,
  Holds<Reads<Schemas['ModelRegistrationOut'], Api.ModelRegistration>>,
  Holds<Reads<Schemas['DiscoveredModel'], Api.DiscoveredModel>>,
  Holds<Reads<Schemas['ModelDiscoveryOut'], Api.ModelDiscovery>>,
  Holds<Reads<Schemas['InferenceRunResult'], Api.InferenceResult>>,
  Holds<Reads<Schemas['QualityReportOut'], Api.QualityReport>>,
];
