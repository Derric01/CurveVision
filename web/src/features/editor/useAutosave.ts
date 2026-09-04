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
import { toApiShape } from './adapters';

const FLUSH_INTERVAL_MS = 4_000;
const DB_NAME = 'curvevision-drafts';
const STORE = 'pending';

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error' | 'conflict';

interface PendingBatch {
  created: Map<string, Annotation>;
  updated: Map<string, Annotation>;
  deleted: Set<string>;
}

function emptyBatch(): PendingBatch {
  return { created: new Map(), updated: new Map(), deleted: new Set() };
}

function isEmpty(batch: PendingBatch): boolean {
  return batch.created.size === 0 && batch.updated.size === 0 && batch.deleted.size === 0;
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
  }) => void;
  /** Flush now; awaited by the "save" button and by job submission. */
  flush: () => Promise<void>;
}

export function useAutosave(
  jobId: string,
  version: number,
  onSaved: (result: { annotation_version: number; id_map: Record<string, string> }) => void,
): AutosaveApi {
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
    const current = batch.current;
    setPendingCount(current.created.size + current.updated.size + current.deleted.size);
  }, []);

  const record: AutosaveApi['record'] = useCallback(
    (change) => {
      const current = batch.current;

      for (const annotation of change.created ?? []) {
        current.created.set(annotation.id, annotation);
      }
      for (const annotation of change.updated ?? []) {
        // An object created but not yet saved stays in `created`: sending it as an update
        // would reference a server id that does not exist.
        if (current.created.has(annotation.id)) current.created.set(annotation.id, annotation);
        else if (!annotation.pending) current.updated.set(annotation.id, annotation);
      }
      for (const id of change.deletedIds ?? []) {
        if (current.created.delete(id)) continue; // never saved; nothing to delete server-side
        current.updated.delete(id);
        current.deleted.add(id);
      }

      updateCount();
      if (!isEmpty(current)) {
        setStatus('pending');
        void persistDraft(jobId, {
          savedAt: Date.now(),
          created: [...current.created.values()],
          updated: [...current.updated.values()],
          deleted: [...current.deleted],
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
      updateCount();

      if (caught instanceof ApiError && caught.status === 409) {
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
