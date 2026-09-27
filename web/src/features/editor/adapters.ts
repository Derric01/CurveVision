/**
 * Translation between the API's snake_case payloads and the engine's camelCase model.
 *
 * Isolated in one file on purpose: the engine must not know the wire format, and the API
 * client must not know about the canvas. When the API grows a field, this is the only
 * place that changes.
 */

import type {
  AnnotationDocument,
  ApiShape,
  ApiSkeletonElement,
  ApiTrack,
  Label,
} from '@/api/types';
import { skeletonSchema } from '@/canvas/skeleton';
import type {
  Annotation,
  LabelStyle,
  MaskData,
  ShapeType,
  SkeletonElement,
} from '@/canvas/types';
import { initialAttributes } from './attributeValues';
import { interpolateTrack, type Keyframe } from './interpolate';
import { attributesAt, type CanvasEdit } from './keyframes';

export function toAnnotation(shape: ApiShape): Annotation {
  return {
    id: shape.id,
    clientId: shape.client_id ?? undefined,
    labelId: shape.label_id,
    frame: shape.frame,
    shapeType: shape.shape_type as ShapeType,
    points: shape.points,
    rotation: shape.rotation,
    occluded: shape.occluded,
    outside: shape.outside,
    zOrder: shape.z_order,
    group: shape.group,
    source: shape.source,
    confidence: shape.confidence,
    attributes: shape.attributes,
    ...(shape.elements && shape.elements.length > 0
      ? { elements: shape.elements.map(toSkeletonElement) }
      : {}),
    ...(shape.mask ? { mask: shape.mask as unknown as MaskData } : {}),
  };
}

function toSkeletonElement(element: ApiSkeletonElement): SkeletonElement {
  return {
    labelId: element.label_id,
    points: element.points,
    occluded: element.occluded,
    outside: element.outside,
  };
}

export function toApiShape(annotation: Annotation): Record<string, unknown> {
  return {
    ...(annotation.pending ? {} : { id: annotation.id }),
    client_id: annotation.clientId ?? annotation.id,
    label_id: annotation.labelId,
    frame: annotation.frame,
    shape_type: annotation.shapeType,
    points: annotation.points,
    rotation: annotation.rotation,
    occluded: annotation.occluded,
    outside: annotation.outside,
    z_order: annotation.zOrder,
    group: annotation.group ?? null,
    source: annotation.source === 'interpolated' ? 'manual' : annotation.source,
    attributes: annotation.attributes,
    // Sent only when there are joints. The server rejects a skeleton with no elements, and
    // sending an empty array on every rectangle would be noise on the hottest write path.
    ...(annotation.elements && annotation.elements.length > 0
      ? {
          elements: annotation.elements.map((element) => ({
            label_id: element.labelId,
            points: element.points,
            occluded: element.occluded,
            outside: element.outside,
          })),
        }
      : {}),
    // Same shape on both sides, so it travels as-is rather than through a per-field mapping
    // that would be one more place for the encoding to drift.
    ...(annotation.mask ? { mask: annotation.mask } : {}),
  };
}

/**
 * A whole track as `updated_tracks` takes it.
 *
 * `updated_tracks` replaces a track's shapes outright, so every stored shape travels, not
 * only the one that changed — and each carries its own per-frame attributes, which are the
 * values of the label's mutable attributes at that keyframe.
 */
export function toApiTrackUpdate(track: ApiTrack): Record<string, unknown> {
  return {
    id: track.id,
    label_id: track.label_id,
    shape_type: track.shape_type,
    group: track.group,
    object_id: track.object_id,
    source: track.source,
    attributes: track.attributes,
    shapes: track.shapes.map((shape) => ({
      frame: shape.frame,
      shape_type: track.shape_type,
      points: shape.points,
      rotation: shape.rotation,
      occluded: shape.occluded,
      outside: shape.outside,
      keyframe: shape.keyframe,
      z_order: shape.z_order ?? 0,
      attributes: shape.attributes ?? {},
    })),
  };
}

/**
 * Label styles for the canvas, including each skeleton's joint order and bones.
 *
 * Child (keypoint) labels are flattened in alongside their parents rather than dropped:
 * without them the canvas cannot name or colour a joint, and a skeleton drawn by somebody
 * else would render as anonymous dots.
 */
export function toLabelStyles(labels: Label[]): LabelStyle[] {
  const styles: LabelStyle[] = [];
  for (const label of labels) {
    const schema = skeletonSchema({
      id: label.id,
      name: label.name,
      children: label.children,
      skeletonEdges: label.skeleton_edges,
    });
    const initial = initialAttributes(label.attributes);
    styles.push({
      id: label.id,
      name: label.name,
      color: label.color,
      visible: true,
      locked: false,
      ...(schema ? { skeleton: schema } : {}),
      ...(Object.keys(initial).length > 0 ? { initialAttributes: initial } : {}),
    });
    for (const child of label.children ?? []) {
      styles.push({
        id: child.id,
        name: child.name,
        // A joint with no colour of its own inherits its skeleton's, so one pose reads as
        // one object rather than as a scatter of unrelated points.
        color: child.color || label.color,
        visible: true,
        locked: false,
        parentId: label.id,
      });
    }
  }
  return styles;
}

/**
 * The labels somebody can actually draw with.
 *
 * A joint is annotated as part of its skeleton, never on its own, so listing joints
 * alongside their parents would offer seventeen choices that do nothing and push the real
 * labels off the panel — and eat the number shortcuts on the way.
 */
export function drawableLabels(labels: readonly LabelStyle[]): LabelStyle[] {
  return labels.filter((label) => label.parentId == null);
}

/**
 * Everything visible on one frame: the frame's own shapes, plus each track materialised
 * at that frame.
 *
 * Interpolating client-side rather than fetching per frame is what makes scrubbing
 * through a video feel instant -- the alternative is a round trip per frame change.
 */
export function frameAnnotations(
  document: AnnotationDocument | undefined,
  frame: number,
): Annotation[] {
  if (!document) return [];

  const result: Annotation[] = document.shapes
    .filter((shape) => shape.frame === frame)
    .map(toAnnotation);

  for (const track of document.tracks) {
    const keyframes: Keyframe[] = track.shapes.map((shape) => ({
      frame: shape.frame,
      points: shape.points,
      rotation: shape.rotation,
      occluded: shape.occluded,
      outside: shape.outside,
      keyframe: shape.keyframe,
    }));

    const position = interpolateTrack(keyframes, track.shape_type as ShapeType, frame);
    if (!position) continue;

    result.push({
      id: track.id,
      trackId: track.id,
      labelId: track.label_id,
      frame,
      shapeType: track.shape_type as ShapeType,
      points: position.points,
      rotation: position.rotation,
      occluded: position.occluded,
      outside: false,
      zOrder: 0,
      group: track.group,
      source: position.keyframe ? track.source : 'interpolated',
      confidence: track.confidence,
      // The track's values and this frame's, as the server merges them for an export:
      // showing the track's alone hid every value that changes from frame to frame.
      attributes: attributesAt(track, frame),
    });
  }

  return result;
}

/** What autosave holds for one frame that the server does not have yet. */
export interface UnsavedFrame {
  /** Objects drawn or changed here: queued, or refused and waiting to be fixed. */
  annotations: readonly Annotation[];
  /** Tracked objects moved or changed on this frame. */
  tracks: readonly CanvasEdit[];
  /** Shapes and tracks whose deletion has not been sent. */
  deletedIds: ReadonlySet<string>;
}

/**
 * One frame as the server has it, with what autosave has not saved yet laid back on top.
 *
 * A save reloads the frame from the server, and the canvas shows only what it is given — so
 * an object the server refused, which is on no server, vanished with the reload that
 * followed the rest of its batch being saved, and nobody could select it to fix it. An
 * object drawn while a save was in flight vanished the same way until the next one.
 */
export function withUnsaved(
  onServer: readonly Annotation[],
  unsaved: UnsavedFrame,
): Annotation[] {
  const local = new Map(unsaved.annotations.map((annotation) => [annotation.id, annotation]));
  const moved = new Map(unsaved.tracks.map((edit) => [edit.trackId, edit]));
  const result: Annotation[] = [];
  for (const annotation of onServer) {
    if (unsaved.deletedIds.has(annotation.id)) continue;
    const changed = local.get(annotation.id);
    local.delete(annotation.id);
    const edit = annotation.trackId ? moved.get(annotation.trackId) : undefined;
    if (changed) result.push(changed);
    else if (edit) {
      result.push({
        ...annotation,
        labelId: edit.labelId,
        points: edit.points,
        rotation: edit.rotation,
        occluded: edit.occluded,
        attributes: edit.attributes,
      });
    } else result.push(annotation);
  }
  // Only what the server has never had. A change to something it no longer has is not
  // drawn back: that object was deleted elsewhere, and drawing it would be a ghost. Nor is
  // a drawn object whose save the server already holds, by its client id — a save in
  // flight when this frame was read can have landed in what was read.
  const saved = new Set(onServer.map((annotation) => annotation.clientId));
  for (const annotation of local.values()) {
    if (annotation.pending && !saved.has(annotation.clientId ?? annotation.id)) {
      result.push(annotation);
    }
  }
  return result;
}

/** Count every annotation in a job, tracks included, for the editor's status bar. */
export function totalObjects(document: AnnotationDocument | undefined): number {
  if (!document) return 0;
  return document.shapes.length + document.tracks.length + document.tags.length;
}
