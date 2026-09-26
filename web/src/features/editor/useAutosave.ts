/**
 * Autosave with a crash-resilient buffer.
 *
 * Two properties matter more than anything else here:
 *
 * 1. **Nothing is lost.** Every pending change is mirrored into IndexedDB before the
 *    network is touched, so a browser crash, a closed laptop or a dropped connection
 *    leaves the work recoverable. Losing an hour of annotation to a refresh is the single
 *    worst failure this class of tool has.
 * 2. **Concurrent edits are detected, not merged.** The batch carries the version the
 *    client last read; a 409 means someone else edited the job and the user is told, rather
 *    than one of them silently winning.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import type { Annotation } from '@/canvas/types';
import { toApiShape, toApiTrackUpdate } from './adapters';
import { applyCanvasEdits, type CanvasEdit } from './keyframes';

const FLUSH_INTERVAL_MS = 4_000;
const DB_NAME = 'curvevision-drafts';
const STORE = 'pending';

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error' | 'conflict';

interface PendingBatch {
  created: Map<string, Annotation>;
  updated: Map<string, Annotation>;
  deleted: Set<string>;
  /**
   * Tracked objects moved on the canvas, keyed by track and frame: a track moved on two
   * frames before a save is two keyframes, not one.
   *
   * These cannot travel with the shapes. A tracked object is drawn under its *track's* id,
   * so sent as an `updated_shapes` entry it names a shape that does not exist — a 404 that
   * failed the whole batch, which autosave then put back and retried, so every edit made
   * after touching a tracked object was lost with it.
   */
  tracks: Map<string, CanvasEdit>;
  deletedTracks: Set<string>;
  /**
   * Objects created and then deleted before any save, so nothing was ever sent for them.
   * Kept only so that undoing the deletion can put them back into `created`: an undo
   * arrives as an *update*, and an update for an object the server has never seen is
   * otherwise ignored.
   */
  discarded: Set<string>;
}

function emptyBatch(): PendingBatch {
  return {
    created: new Map(),
    updated: new Map(),
    deleted: new Set(),
    tracks: new Map(),
    deletedTracks: new Set(),
    discarded: new Set(),
  };
}

function sizeOf(batch: PendingBatch): number {
  return (
    batch.created.size +
    batch.updated.size +
    batch.deleted.size +
    batch.tracks.size +
    batch.deletedTracks.size
  );
}

function isEmpty(batch: PendingBatch): boolean {
  return sizeOf(batch) === 0;
}

function asCanvasEdit(annotation: Annotation & { trackId: string }): CanvasEdit {
  return {
    trackId: annotation.trackId,
    frame: annotation.frame,
    labelId: annotation.labelId,
    points: annotation.points,
    rotation: annotation.rotation,
    occluded: annotation.occluded,
    attributes: annotation.attributes,
  };
}

// --------------------------------------------------------------- write-ahead buffer

async function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return null;
  return new Promise((resolve) => {
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
}

async function persistDraft(jobId: string, payload: unknown): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  try {
    const transaction = database.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).put(payload, jobId);
  } catch {
    /* A failed buffer write must never block the actual save. */
  }
}

async function clearDraft(jobId: string): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  try {
    const transaction = database.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).delete(jobId);
  } catch {
    /* ignore */
  }
}

/** Recover a draft left behind by a crash, so the editor can offer to restore it. */
export async function loadDraft(jobId: string): Promise<unknown | null> {
  const database = await openDatabase();
  if (!database) return null;
  return new Promise((resolve) => {
    try {
      const request = database.transaction(STORE, 'readonly').objectStore(STORE).get(jobId);
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

// ------------------------------------------------------------------ tracked objects

/** Which of a label's attributes may change from frame to frame. */
export type MutableAttributes = (labelId: string) => ReadonlySet<string>;

/** The job changed under a pending track edit: the same answer a 409 would have given. */
class StaleTracks extends Error {}

/**
 * `updated_tracks` and `deleted_tracks` for the tracked objects in a batch, or nothing.
 *
 * `updated_tracks` replaces a track's shapes outright, so the keyframes are placed on the
 * track as the server holds it *now*, read fresh rather than from the editor's cache —
 * which is a save behind right after a flush, and would drop the keyframe that flush
 * wrote. If the job has moved on from the version this batch was built against, somebody
 * else edited it, and that is a conflict to report, not a track to overwrite.
 */
async function trackWrites(
  jobId: string,
  batch: PendingBatch,
  version: number,
  mutable: MutableAttributes,
): Promise<Record<string, unknown>> {
  if (batch.tracks.size === 0 && batch.deletedTracks.size === 0) return {};
  const document = await api.annotations(jobId);
  if (document.annotation_version !== version) throw new StaleTracks();
  const surviving = document.tracks.filter((track) => !batch.deletedTracks.has(track.id));
  return {
    updated_tracks: applyCanvasEdits(surviving, [...batch.tracks.values()], mutable).map(
      toApiTrackUpdate,
    ),
    deleted_tracks: [...batch.deletedTracks],
  };
}

// -------------------------------------------------------------------------- the hook

export interface AutosaveApi {
  status: SaveStatus;
  pendingCount: number;
  error: string | null;
  /** Queue changes for the next flush. */
  record: (change: {
    created?: Annotation[];
    updated?: Annotation[];
    deletedIds?: string[];
    /** Tracks deleted from the canvas: the whole object, on every frame. */
    deletedTrackIds?: string[];
  }) => void;
  /** Flush now; awaited by the "save" button and by job submission. */
  flush: () => Promise<void>;
}

export function useAutosave(
  jobId: string,
  version: number,
  onSaved: (result: { annotation_version: number; id_map: Record<string, string> }) => void,
  mutableAttributes: MutableAttributes = () => new Set(),
): AutosaveApi {
  // A ref, so the label schema arriving late does not rebuild `flush` and its timer.
  const mutableRef = useRef(mutableAttributes);
  mutableRef.current = mutableAttributes;
  const batch = useRef<PendingBatch>(emptyBatch());
  const versionRef = useRef(version);
  const flushing = useRef(false);
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [pendingCount, setPendingCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    versionRef.current = version;
  }, [version]);

  const updateCount = useCallback(() => {
    setPendingCount(sizeOf(batch.current));
  }, []);

  const record: AutosaveApi['record'] = useCallback(
    (change) => {
      const current = batch.current;

      for (const annotation of change.created ?? []) {
        current.created.set(annotation.id, annotation);
      }
      for (const annotation of change.updated ?? []) {
        // An update for an object whose deletion is still queued is that deletion being
        // undone — the only way a deleted object comes back onto the canvas. Leaving the
        // deletion queued sent both, and the server applies deletions last, so the object
        // the annotator had just restored was deleted on the next save.
        current.deleted.delete(annotation.id);
        if (annotation.trackId) current.deletedTracks.delete(annotation.trackId);
        if (current.discarded.delete(annotation.id)) {
          current.created.set(annotation.id, annotation);
          continue;
        }
        // An object created but not yet saved stays in `created`: sending it as an update
        // would reference a server id that does not exist.
        if (current.created.has(annotation.id)) current.created.set(annotation.id, annotation);
        else if (annotation.pending) continue;
        else if (annotation.trackId) {
          const edit = asCanvasEdit({ ...annotation, trackId: annotation.trackId });
          current.tracks.set(`${edit.trackId}@${edit.frame}`, edit);
        } else current.updated.set(annotation.id, annotation);
      }
      for (const id of change.deletedIds ?? []) {
        if (current.created.delete(id)) {
          current.discarded.add(id); // never saved; nothing to delete server-side
          continue;
        }
        current.updated.delete(id);
        current.deleted.add(id);
      }
      for (const id of change.deletedTrackIds ?? []) {
        for (const [key, edit] of current.tracks) {
          if (edit.trackId === id) current.tracks.delete(key);
        }
        current.deletedTracks.add(id);
      }

      updateCount();
      if (!isEmpty(current)) {
        setStatus('pending');
        void persistDraft(jobId, {
          savedAt: Date.now(),
          created: [...current.created.values()],
          updated: [...current.updated.values()],
          deleted: [...current.deleted],
          tracks: [...current.tracks.values()],
          deletedTracks: [...current.deletedTracks],
        });
      }
    },
    [jobId, updateCount],
  );

  const flush = useCallback(async () => {
    if (flushing.current) return;
    const current = batch.current;
    if (isEmpty(current)) return;

    flushing.current = true;
    batch.current = emptyBatch();
    updateCount();
    setStatus('saving');

    try {
      const result = await api.writeAnnotations(jobId, {
        annotation_version: versionRef.current,
        created_shapes: [...current.created.values()].map(toApiShape),
        updated_shapes: [...current.updated.values()].map(toApiShape),
        deleted_shapes: [...current.deleted],
        ...(await trackWrites(jobId, current, versionRef.current, mutableRef.current)),
      });
      versionRef.current = result.annotation_version;
      onSaved(result);
      setStatus('saved');
      setError(null);
      await clearDraft(jobId);
    } catch (caught) {
      // Put the work back so the next flush retries it: dropping it here is how an
      // autosave silently loses an annotator's afternoon.
      const restored = batch.current;
      for (const [id, annotation] of current.created) restored.created.set(id, annotation);
      for (const [id, annotation] of current.updated) restored.updated.set(id, annotation);
      for (const id of current.deleted) restored.deleted.add(id);
      // A newer edit to the same track and frame, made while this flush was in flight, wins.
      for (const [key, edit] of current.tracks) {
        if (!restored.tracks.has(key)) restored.tracks.set(key, edit);
      }
      for (const id of current.deletedTracks) restored.deletedTracks.add(id);
      updateCount();

      if (caught instanceof StaleTracks || (caught instanceof ApiError && caught.status === 409)) {
        setStatus('conflict');
        setError('These annotations changed elsewhere. Reload the job before saving.');
      } else {
        setStatus('error');
        setError(caught instanceof Error ? caught.message : 'Could not save');
      }
    } finally {
      flushing.current = false;
    }
  }, [jobId, onSaved, updateCount]);

  // Periodic flush.
  useEffect(() => {
    const timer = window.setInterval(() => void flush(), FLUSH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [flush]);

  // Warn before leaving with unsaved work, and try one last flush.
  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      if (pendingCount > 0) {
        void flush();
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [flush, pendingCount]);

  return { status, pendingCount, error, record, flush };
}
