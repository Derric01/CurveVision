/**
 * Autosave with a crash-resilient buffer.
 *
 * Two properties matter more than anything else here:
 *
 * 1. **Nothing is lost.** Everything the server has not confirmed is mirrored into
 *    IndexedDB (`drafts.ts`), so a browser crash, a closed laptop or a dropped connection
 *    leaves the work recoverable, and opening the job again offers it back. Losing an hour
 *    of annotation to a refresh is the single worst failure this class of tool has.
 * 2. **Concurrent edits are detected, not merged.** The batch carries the version the
 *    client last read; a 409 means someone else edited the job and the user is told, rather
 *    than one of them silently winning.
 * 3. **One refused object does not hold the rest hostage.** The server names the entry it
 *    refused; that object is set aside with the reason, the rest is saved at once, and the
 *    object goes back in the queue the next time it is edited.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import type { Annotation } from '@/canvas/types';
import { toApiShape, toApiTrackUpdate, type UnsavedFrame } from './adapters';
import {
  draftOf,
  isEmptyDraft,
  loadDraft,
  planRestore,
  restoreSize,
  trackKey,
  writeDraft,
  type Draft,
  type LocalObject,
} from './drafts';
import { applyCanvasEdits, type CanvasEdit } from './keyframes';
import { refusedEntries } from './refusals';

const FLUSH_INTERVAL_MS = 4_000;
/** How long an edit waits before the copy in the browser is rewritten: a drag is many edits. */
const COPY_DELAY_MS = 250;

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

/**
 * An object the server refused, held out of the queue until it is edited or deleted.
 *
 * Sending it again unchanged can only be refused again, and it would take the rest of the
 * batch with it; the edit that fixes it is what puts it back.
 */
interface Refused {
  reason: string;
  labelId: string;
  frame: number;
  /** Never saved: it goes back to `created`, since the server has no id for it. */
  created?: Annotation;
  updated?: Annotation;
  /** A tracked object's edits, every frame of them. */
  tracks?: CanvasEdit[];
}

/** What the editor is told about a refused object: enough to name it and go to it. */
export interface Refusal {
  reason: string;
  labelId: string;
  frame: number;
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

// ------------------------------------------------------------------ tracked objects

/** Which of a label's attributes may change from frame to frame. */
export type MutableAttributes = (labelId: string) => ReadonlySet<string>;

/** The job changed under a pending track edit: the same answer a 409 would have given. */
class StaleTracks extends Error {}

interface TrackWrites {
  updated_tracks?: Record<string, unknown>[];
  deleted_tracks?: string[];
}

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
): Promise<TrackWrites> {
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

/** Unsaved work found in the browser when the job was opened, waiting to be put back. */
export interface Recovered {
  savedAt: number;
  /** Objects it would add, change or delete. */
  changes: number;
  /** Made against an older version of the job than the one open now, or an unknown one. */
  changedSince: boolean;
}

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
  /** Objects the server refused, by canvas id, held until they are edited again. */
  refused: ReadonlyMap<string, Refusal>;
  /**
   * What is held for one frame and not on the server yet — queued, or refused.
   *
   * Read at the moment it is called rather than kept as state: the editor asks when the
   * server's document or the frame changes, and a change here on every edit would reload
   * the canvas under the annotator's pointer.
   */
  unsavedOn: (frame: number) => UnsavedFrame;
  /**
   * What an earlier session left unsaved, when there is anything the server does not
   * already have. Until it is restored or discarded, the copy it came from is kept as it is.
   */
  recovered: Recovered | null;
  /** Queue what was recovered for saving, and save it. */
  restore: () => Promise<void>;
  discard: () => void;
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
  /** The batch a request is carrying: unsaved until its answer arrives, so kept too. */
  const inFlight = useRef<PendingBatch | null>(null);
  const held = useRef(new Map<string, Refused>());
  /**
   * Every object drawn in this session, by the local id the canvas knows it by, and the
   * server id its first save gave it — `null` until then.
   *
   * A save is followed by a reload that swaps the server id in, and anything done to the
   * object before that reload lands still names the local one. A deletion did: the server
   * cannot even parse a local id, so the request was a 422 naming no object, and every
   * save after it failed the same way.
   */
  const local = useRef(new Map<string, LocalObject>());
  /**
   * Everything this session has changed, by id and track key (`trackKey`), and the ids of
   * tracks it deleted. A restore leaves these alone: the session's copy is the newer one.
   */
  const touched = useRef(new Set<string>());
  /**
   * The draft found on opening the job, until somebody restores or discards it; `loading`
   * until the browser has been asked. The copy is not rewritten meanwhile, which would
   * replace what was found with this session's work.
   */
  const found = useRef<Draft | 'loading' | null>('loading');
  const copyTimer = useRef<number | null>(null);
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [pendingCount, setPendingCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState<ReadonlyMap<string, Refusal>>(new Map());
  const [recovered, setRecovered] = useState<Recovered | null>(null);
  const [restores, setRestores] = useState(0);

  useEffect(() => {
    versionRef.current = version;
  }, [version]);

  const updateCount = useCallback(() => {
    setPendingCount(sizeOf(batch.current));
  }, []);

  /** Rewrite the browser's copy now: after a save's answer, and when the page goes. */
  const copyNow = useCallback(() => {
    if (copyTimer.current !== null) {
      window.clearTimeout(copyTimer.current);
      copyTimer.current = null;
    }
    if (found.current !== null) return;
    const draft = draftOf({
      queues: inFlight.current ? [inFlight.current, batch.current] : [batch.current],
      held: held.current.values(),
      local: local.current,
      annotationVersion: versionRef.current,
      savedAt: Date.now(),
    });
    void writeDraft(jobId, isEmptyDraft(draft) ? null : draft);
  }, [jobId]);

  /** Rewrite it shortly: an edit arrives on every pointer move of a drag. */
  const copySoon = useCallback(() => {
    if (copyTimer.current === null) copyTimer.current = window.setTimeout(copyNow, COPY_DELAY_MS);
  }, [copyNow]);

  const publishRefused = useCallback(() => {
    setRefused(
      new Map(
        [...held.current].map(([id, { reason, labelId, frame }]) => [id, { reason, labelId, frame }]),
      ),
    );
  }, []);

  /**
   * Take an object out of `held` because it was edited or deleted, restoring its other
   * edits. The edit being recorded is presumably the fix; if it is not, the server says so
   * again and it is set aside again.
   */
  const release = useCallback((id: string): Refused | undefined => {
    const refusal = held.current.get(id);
    if (!refusal) return undefined;
    held.current.delete(id);
    const current = batch.current;
    for (const edit of refusal.tracks ?? []) {
      const key = `${edit.trackId}@${edit.frame}`;
      if (!current.tracks.has(key)) current.tracks.set(key, edit);
    }
    return refusal;
  }, []);

  const record: AutosaveApi['record'] = useCallback(
    (change) => {
      const current = batch.current;
      const releasedAny = [
        ...(change.updated ?? []).map((annotation) => annotation.id),
        ...(change.deletedIds ?? []),
        ...(change.deletedTrackIds ?? []),
      ].some((id) => held.current.has(id));

      for (const annotation of change.created ?? []) {
        current.created.set(annotation.id, annotation);
        touched.current.add(annotation.id);
        if (!local.current.has(annotation.id)) {
          local.current.set(annotation.id, {
            clientId: annotation.clientId ?? annotation.id,
            saved: null,
          });
        }
      }
      for (const annotation of change.updated ?? []) {
        touched.current.add(
          annotation.trackId
            ? trackKey({ trackId: annotation.trackId, frame: annotation.frame })
            : annotation.id,
        );
        // Never saved, so the server has no id for it: it can only go back as a creation.
        if (release(annotation.id)?.created) {
          current.created.set(annotation.id, annotation);
          continue;
        }
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
        touched.current.add(id);
        const refusal = release(id);
        if (current.created.delete(id) || refusal?.created) {
          current.discarded.add(id); // never saved; nothing to delete server-side
          continue;
        }
        current.updated.delete(id);
        current.deleted.add(id);
      }
      for (const id of change.deletedTrackIds ?? []) {
        touched.current.add(id);
        release(id);
        for (const [key, edit] of current.tracks) {
          if (edit.trackId === id) current.tracks.delete(key);
        }
        current.deletedTracks.add(id);
      }

      if (releasedAny) publishRefused();
      updateCount();
      if (!isEmpty(current)) setStatus('pending');
      // Also when the queue has just emptied: a copy still holding a box drawn and then
      // deleted would put it back after a crash.
      copySoon();
    },
    [updateCount, release, publishRefused, copySoon],
  );

  /**
   * Take the entries a refusal names out of `current` and hold them. True when there was
   * at least one, which means what is left of `current` has not been tried on its own yet.
   */
  const setAside = useCallback(
    (
      caught: unknown,
      current: PendingBatch,
      sent: { created: Annotation[]; updated: Annotation[]; tracks: TrackWrites },
    ): boolean => {
      if (!(caught instanceof ApiError) || caught.status === 409) return false;
      let any = false;
      for (const { list, index, reason } of refusedEntries(caught.problem)) {
        if (list === 'updated_tracks') {
          const trackId = sent.tracks.updated_tracks?.[index]?.id;
          if (typeof trackId !== 'string') continue;
          const edits = [...current.tracks].filter(([, edit]) => edit.trackId === trackId);
          if (edits.length === 0) continue;
          for (const [key] of edits) current.tracks.delete(key);
          const first = edits[0]![1];
          held.current.set(trackId, {
            reason,
            labelId: first.labelId,
            frame: first.frame,
            tracks: edits.map(([, edit]) => edit),
          });
        } else {
          const kind = list === 'created_shapes' ? 'created' : 'updated';
          const annotation = sent[kind][index];
          if (!annotation) continue;
          current[kind].delete(annotation.id);
          // Deleted while its refused save was in flight: there is nothing left to fix, and
          // a notice nobody could clear would hold Submit back for good.
          if (kind === 'created' && batch.current.deleted.delete(annotation.id)) {
            batch.current.discarded.add(annotation.id);
          } else {
            held.current.set(annotation.id, {
              reason,
              labelId: annotation.labelId,
              frame: annotation.frame,
              [kind]: annotation,
            });
          }
        }
        any = true;
      }
      if (any) publishRefused();
      return any;
    },
    [publishRefused],
  );

  /** One request. True when entries were set aside and the rest should be tried now. */
  const flushOnce = useCallback(async (): Promise<boolean> => {
    const current = batch.current;
    batch.current = emptyBatch();
    inFlight.current = current;
    updateCount();
    setStatus('saving');

    const sent = {
      created: [...current.created.values()],
      updated: [...current.updated.values()],
      tracks: {} as TrackWrites,
    };
    try {
      sent.tracks = await trackWrites(jobId, current, versionRef.current, mutableRef.current);
      const result = await api.writeAnnotations(jobId, {
        annotation_version: versionRef.current,
        created_shapes: sent.created.map(toApiShape),
        updated_shapes: sent.updated.map(toApiShape),
        // Under the id its save gave it; a local id with none was never saved, so there
        // is nothing on the server to delete.
        deleted_shapes: [...current.deleted].flatMap((id) => {
          const object = local.current.get(id);
          if (!object) return [id];
          return object.saved ? [object.saved] : [];
        }),
        ...sent.tracks,
      });
      for (const annotation of sent.created) {
        const saved = result.id_map[annotation.clientId ?? annotation.id];
        if (saved) local.current.set(annotation.id, { clientId: annotation.clientId ?? annotation.id, saved });
      }
      versionRef.current = result.annotation_version;
      inFlight.current = null;
      // At once rather than soon: a tab closed straight after its last save would otherwise
      // leave a copy of work the server has, offered back on the next open.
      copyNow();
      onSaved(result);
      setStatus('saved');
      setError(null);
      return false;
    } catch (caught) {
      const again = setAside(caught, current, sent);
      // Put the work back so the next flush retries it: dropping it here is how an
      // autosave silently loses an annotator's afternoon.
      const restored = batch.current;
      for (const [id, annotation] of current.created) {
        // Deleted while this, its first save, was in flight: it never reached the server,
        // so the creation and the deletion cancel out.
        if (restored.deleted.delete(id)) {
          restored.discarded.add(id);
          continue;
        }
        restored.created.set(id, annotation);
      }
      for (const [id, annotation] of current.updated) restored.updated.set(id, annotation);
      for (const id of current.deleted) restored.deleted.add(id);
      // A newer edit to the same track and frame, made while this flush was in flight, wins.
      for (const [key, edit] of current.tracks) {
        if (!restored.tracks.has(key)) restored.tracks.set(key, edit);
      }
      for (const id of current.deletedTracks) restored.deletedTracks.add(id);
      inFlight.current = null;
      copyNow();
      updateCount();

      if (again) {
        // With nothing else queued there is no next pass to report a status of its own.
        if (isEmpty(restored)) setStatus('idle');
        return true;
      }
      if (caught instanceof StaleTracks || (caught instanceof ApiError && caught.status === 409)) {
        setStatus('conflict');
        setError('These annotations changed elsewhere. Reload the job before saving.');
      } else {
        setStatus('error');
        setError(caught instanceof Error ? caught.message : 'Could not save');
      }
      return false;
    }
  }, [jobId, onSaved, updateCount, setAside, copyNow]);

  const flush = useCallback(async () => {
    if (flushing.current) return;
    flushing.current = true;
    try {
      // Each pass that goes round again has held back at least one entry, so this ends.
      while (!isEmpty(batch.current) && (await flushOnce())) {
        /* the rest of the batch, without what was refused */
      }
    } finally {
      flushing.current = false;
    }
  }, [flushOnce]);

  const unsavedOn = useCallback((frame: number): UnsavedFrame => {
    const current = batch.current;
    // The batch in flight too: a restore saves at once, so by the time the editor asks,
    // what it restored is in a request — which may yet fail.
    const queues = inFlight.current ? [inFlight.current, current] : [current];
    const annotations = queues.flatMap((queue) => [
      ...queue.created.values(),
      ...queue.updated.values(),
    ]);
    const tracks = queues.flatMap((queue) => [...queue.tracks.values()]);
    for (const refusal of held.current.values()) {
      if (refusal.created) annotations.push(refusal.created);
      if (refusal.updated) annotations.push(refusal.updated);
      tracks.push(...(refusal.tracks ?? []));
    }
    // A deletion queued under a local id is of the object the reload shows under its server
    // id; hidden only by the local one, a deleted box came back until the next save.
    const deleted = queues.flatMap((queue) =>
      [...queue.deleted].map((id) => local.current.get(id)?.saved ?? id),
    );
    return {
      annotations: annotations.filter((annotation) => annotation.frame === frame),
      tracks: tracks.filter((edit) => edit.frame === frame),
      deletedIds: new Set([...deleted, ...queues.flatMap((queue) => [...queue.deletedTracks])]),
    };
    // `restores` is not read. A new identity is how the editor learns that what this returns
    // changed without the canvas changing it — a restore — and lays that work on the canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restores]);

  // What an earlier session left, looked for once per job, and offered only when the server
  // does not already have it: a copy of work that did reach it is just forgotten.
  useEffect(() => {
    let cancelled = false;
    found.current = 'loading';
    setRecovered(null);
    void (async () => {
      const draft = await loadDraft(jobId);
      if (cancelled) return;
      let offer: Recovered | null = null;
      if (draft) {
        try {
          const document = await api.annotations(jobId);
          if (cancelled) return;
          const changes = restoreSize(planRestore(draft, document, touched.current));
          if (changes > 0) {
            offer = {
              savedAt: draft.savedAt,
              changes,
              changedSince: draft.annotationVersion !== document.annotation_version,
            };
          }
        } catch {
          // Cannot tell what the server has, so offer all of it: restoring checks again.
          const everything = {
            ...draft,
            deleted: [...draft.deleted, ...draft.deletedClientIds],
          };
          offer = { savedAt: draft.savedAt, changes: restoreSize(everything), changedSince: true };
        }
      }
      found.current = offer && draft ? draft : null;
      setRecovered(offer);
      if (!offer) copyNow();
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, copyNow]);

  const restore = useCallback(async () => {
    const draft = found.current;
    if (!draft || draft === 'loading') return;
    let document;
    try {
      document = await api.annotations(jobId);
    } catch {
      // Without knowing what the server already has, restoring could send a drawn object a
      // second time. The offer stays, to be taken up when the server can be reached.
      setError('Could not reach the server to check what it already has. Try restoring again.');
      return;
    }
    const taken = new Set(touched.current);
    for (const [id, refusal] of held.current) {
      taken.add(id);
      for (const edit of refusal.tracks ?? []) taken.add(trackKey(edit));
    }
    const plan = planRestore(draft, document, taken);
    const current = batch.current;
    for (const annotation of plan.created) {
      current.created.set(annotation.id, annotation);
      local.current.set(annotation.id, {
        clientId: annotation.clientId ?? annotation.id,
        saved: null,
      });
    }
    for (const annotation of plan.updated) current.updated.set(annotation.id, annotation);
    for (const id of plan.deleted) current.deleted.add(id);
    for (const edit of plan.tracks) current.tracks.set(trackKey(edit), edit);
    for (const id of plan.deletedTracks) current.deletedTracks.add(id);
    found.current = null;
    setRecovered(null);
    setRestores((count) => count + 1);
    updateCount();
    if (!isEmpty(current)) setStatus('pending');
    copyNow();
    await flush();
  }, [jobId, updateCount, copyNow, flush]);

  const discard = useCallback(() => {
    found.current = null;
    setRecovered(null);
    // Replaces what was found with this session's own copy, or with nothing.
    copyNow();
  }, [copyNow]);

  // A copy waiting on its timer is written before the page or the job goes.
  useEffect(() => () => copyNow(), [copyNow]);

  // Periodic flush.
  useEffect(() => {
    const timer = window.setInterval(() => void flush(), FLUSH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [flush]);

  // Warn before leaving with unsaved work, and try one last flush. A refused object is
  // unsaved work too, and leaving is the one way it is lost for good.
  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      copyNow();
      if (pendingCount > 0 || refused.size > 0) {
        void flush();
        event.preventDefault();
        event.returnValue = '';
      }
    }
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [flush, pendingCount, refused, copyNow]);

  return {
    status,
    pendingCount,
    error,
    record,
    flush,
    refused,
    unsavedOn,
    recovered,
    restore,
    discard,
  };
}
