/**
 * Translation between the API's snake_case payloads and the engine's camelCase model.
 *
 * Isolated in one file on purpose: the engine must not know the wire format, and the API
 * client must not know about the canvas. When the API grows a field, this is the only
 * place that changes.
 */

import type { AnnotationDocument, ApiShape, Label } from '@/api/types';
import type { Annotation, LabelStyle, ShapeType } from '@/canvas/types';
import { interpolateTrack, type Keyframe } from './interpolate';

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
  };
}

export function toLabelStyles(labels: Label[]): LabelStyle[] {
  return labels.map((label) => ({
    id: label.id,
    name: label.name,
    color: label.color,
    visible: true,
    locked: false,
  }));
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
      attributes: track.attributes,
    });
  }

  return result;
}

/** Count every annotation in a job, tracks included, for the editor's status bar. */
export function totalObjects(document: AnnotationDocument | undefined): number {
  if (!document) return 0;
  return document.shapes.length + document.tracks.length + document.tags.length;
}
