/**
 * The copy of unsaved work the editor keeps in the browser, and getting it back.
 *
 * Autosave mirrors everything the server has not confirmed into IndexedDB, so that a crash,
 * a closed laptop, or a dropped connection followed by a closed tab loses nothing. Until
 * iteration 56 the copy was written on every edit and never read — nothing called the
 * loader — so it recovered nothing, while the roadmap called crash recovery done. This file
 * holds both halves: what goes into the copy, and what of it can safely be put back when the
 * job is opened again.
 *
 * Storage is per origin. In the desktop shape the page's origin is `http://127.0.0.1:<port>`,
 * which is why the local server keeps its port from one launch to the next
 * (`desktop.port_for`): a new port was a new, empty origin.
 */

import type { AnnotationDocument } from '@/api/types';
import type { Annotation } from '@/canvas/types';
import type { CanvasEdit } from './keyframes';

const DB_NAME = 'curvevision-drafts';
const STORE = 'pending';

/** Everything for one job the server has not confirmed, as the browser keeps it. */
export interface Draft {
  format: 2;
  savedAt: number;
  /** The job's `annotation_version` these edits were made against; `null` when unknown. */
  annotationVersion: number | null;
  /** Drawn here and never confirmed saved. */
  created: Annotation[];
  /** Saved objects, changed since. */
  updated: Annotation[];
  /** Server ids of shapes to delete. */
  deleted: string[];
  /**
   * Objects deleted while their first save was in flight, by the client id that save
   * carried: whether it reached the server is exactly what a crash leaves unknown.
   */
  deletedClientIds: string[];
  tracks: CanvasEdit[];
  deletedTracks: string[];
}

/** One of autosave's queues: the batch in flight, or the one filling behind it. */
export interface Queue {
  created: ReadonlyMap<string, Annotation>;
  updated: ReadonlyMap<string, Annotation>;
  deleted: ReadonlySet<string>;
  tracks: ReadonlyMap<string, CanvasEdit>;
  deletedTracks: ReadonlySet<string>;
}

/** An object the server refused, as autosave holds it. */
export interface HeldBack {
  created?: Annotation;
  updated?: Annotation;
  tracks?: CanvasEdit[];
}

/** What autosave knows about an object drawn in this session. */
export interface LocalObject {
  clientId: string;
  /** The server id its first save returned; `null` until one has. */
  saved: string | null;
}

export function trackKey(edit: Pick<CanvasEdit, 'trackId' | 'frame'>): string {
  return `${edit.trackId}@${edit.frame}`;
}

/**
 * The copy to keep: what is in flight, what is queued behind it, and what the server
 * refused — all of it unsaved, and the in-flight part unconfirmed until its answer arrives.
 */
export function draftOf(input: {
  /** Oldest first, so a later queue's copy of an object wins. */
  queues: readonly Queue[];
  held: Iterable<HeldBack>;
  local: ReadonlyMap<string, LocalObject>;
  annotationVersion: number;
  savedAt: number;
}): Draft {
  const created = new Map<string, Annotation>();
  const updated = new Map<string, Annotation>();
  const deletions = new Set<string>();
  const tracks = new Map<string, CanvasEdit>();
  const deletedTracks = new Set<string>();
  for (const held of input.held) {
    if (held.created) created.set(held.created.id, held.created);
    if (held.updated) updated.set(held.updated.id, held.updated);
    for (const edit of held.tracks ?? []) tracks.set(trackKey(edit), edit);
  }
  for (const queue of input.queues) {
    for (const [id, annotation] of queue.created) created.set(id, annotation);
    for (const [id, annotation] of queue.updated) updated.set(id, annotation);
    for (const id of queue.deleted) deletions.add(id);
    for (const [key, edit] of queue.tracks) tracks.set(key, edit);
    for (const id of queue.deletedTracks) deletedTracks.add(id);
  }

  const deleted: string[] = [];
  const deletedClientIds: string[] = [];
  for (const id of deletions) {
    const object = input.local.get(id);
    if (!object) deleted.push(id);
    else if (object.saved) deleted.push(object.saved);
    else {
      // Deleted while its first save was in flight. Whether that save landed is unknown,
      // so it is not kept as something to create, and its deletion names the client id.
      created.delete(id);
      deletedClientIds.push(object.clientId);
    }
  }

  return {
    format: 2,
    savedAt: input.savedAt,
    annotationVersion: input.annotationVersion,
    created: [...created.values()],
    updated: [...updated.values()],
    deleted,
    deletedClientIds,
    tracks: [...tracks.values()],
    deletedTracks: [...deletedTracks],
  };
}

export function isEmptyDraft(draft: Draft): boolean {
  return (
    draft.created.length +
      draft.updated.length +
      draft.deleted.length +
      draft.deletedClientIds.length +
      draft.tracks.length +
      draft.deletedTracks.length ===
    0
  );
}

function isAnnotation(value: unknown): value is Annotation {
  const candidate = value as Partial<Annotation> | null;
  return (
    typeof candidate?.id === 'string' &&
    typeof candidate.labelId === 'string' &&
    typeof candidate.frame === 'number' &&
    Array.isArray(candidate.points)
  );
}

function isCanvasEdit(value: unknown): value is CanvasEdit {
  const candidate = value as Partial<CanvasEdit> | null;
  return (
    typeof candidate?.trackId === 'string' &&
    typeof candidate.frame === 'number' &&
    Array.isArray(candidate.points)
  );
}

/**
 * A stored draft as something safe to use, or `null`.
 *
 * Whatever is in IndexedDB was written by some earlier version of this code, or damaged, so
 * nothing is trusted to be the right shape: an entry that is not is dropped rather than
 * handed to the canvas. A draft from before the version was recorded has no version, and is
 * treated as made against a job that has moved on since.
 */
export function readDraft(raw: unknown): Draft | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const list = (key: string): unknown[] => (Array.isArray(value[key]) ? value[key] : []);
  const strings = (key: string) => list(key).filter((id): id is string => typeof id === 'string');
  const draft: Draft = {
    format: 2,
    savedAt: typeof value.savedAt === 'number' ? value.savedAt : 0,
    annotationVersion:
      value.format === 2 && typeof value.annotationVersion === 'number'
        ? value.annotationVersion
        : null,
    created: list('created').filter(isAnnotation),
    updated: list('updated').filter(isAnnotation),
    deleted: strings('deleted'),
    deletedClientIds: strings('deletedClientIds'),
    tracks: list('tracks').filter(isCanvasEdit),
    deletedTracks: strings('deletedTracks'),
  };
  return isEmptyDraft(draft) ? null : draft;
}

/** What of a draft can be put back, against the job as the server has it now. */
export interface RestorePlan {
  created: Annotation[];
  updated: Annotation[];
  deleted: string[];
  tracks: CanvasEdit[];
  deletedTracks: string[];
}

export function restoreSize(plan: RestorePlan): number {
  return (
    plan.created.length +
    plan.updated.length +
    plan.deleted.length +
    new Set(plan.tracks.map((edit) => edit.trackId)).size +
    plan.deletedTracks.length
  );
}

/**
 * Put back only what the server does not already have, and nothing this session has
 * changed since — the session's copy is the newer one.
 *
 * `taken` holds the ids and track keys (`trackKey`) this session has queued, saved or
 * held, and the ids of tracks it deleted. A drawn object is recognised on the server by its
 * client id, which the server keeps: a crash between the server storing it and the browser
 * hearing so would otherwise put a second copy on the canvas.
 */
export function planRestore(
  draft: Draft,
  document: Pick<AnnotationDocument, 'shapes' | 'tracks'>,
  taken: ReadonlySet<string>,
): RestorePlan {
  const shapes = new Set(document.shapes.map((shape) => shape.id));
  const byClientId = new Map<string, string>();
  for (const shape of document.shapes) if (shape.client_id) byClientId.set(shape.client_id, shape.id);
  const tracks = new Set(document.tracks.map((track) => track.id));
  const deleted = new Set([
    ...draft.deleted.filter((id) => shapes.has(id)),
    ...draft.deletedClientIds.flatMap((clientId) => byClientId.get(clientId) ?? []),
  ]);

  return {
    created: draft.created.filter(
      (annotation) =>
        !byClientId.has(annotation.clientId ?? annotation.id) && !taken.has(annotation.id),
    ),
    updated: draft.updated.filter(
      (annotation) => shapes.has(annotation.id) && !taken.has(annotation.id),
    ),
    deleted: [...deleted].filter((id) => !taken.has(id)),
    tracks: draft.tracks.filter(
      (edit) => tracks.has(edit.trackId) && !taken.has(trackKey(edit)) && !taken.has(edit.trackId),
    ),
    deletedTracks: draft.deletedTracks.filter((id) => tracks.has(id) && !taken.has(id)),
  };
}

// ------------------------------------------------------------------------- storage

let database: Promise<IDBDatabase | null> | null = null;

/**
 * One connection, opened once. A connection per write let two writes in quick succession
 * open in either order, and the older copy land last.
 */
function openDatabase(): Promise<IDBDatabase | null> {
  database ??= new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') {
      resolve(null);
      return;
    }
    try {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return database;
}

/** Keep `draft` for a job, or forget the job's draft when there is nothing left to keep. */
export async function writeDraft(jobId: string, draft: Draft | null): Promise<void> {
  const connection = await openDatabase();
  if (!connection) return;
  try {
    const store = connection.transaction(STORE, 'readwrite').objectStore(STORE);
    if (draft && !isEmptyDraft(draft)) store.put(draft, jobId);
    else store.delete(jobId);
  } catch {
    /* A failed copy must never block the actual save. */
  }
}

export async function loadDraft(jobId: string): Promise<Draft | null> {
  const connection = await openDatabase();
  if (!connection) return null;
  return new Promise((resolve) => {
    try {
      const request = connection.transaction(STORE, 'readonly').objectStore(STORE).get(jobId);
      request.onsuccess = () => resolve(readDraft(request.result));
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}
