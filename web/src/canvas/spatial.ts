/**
 * Spatial index over the annotations on the current frame.
 *
 * This is the single decision that decouples editor performance from dataset size. With
 * an R-tree, rendering costs what is *on screen* and picking costs O(log n); without one,
 * both are O(n) and a 100k-annotation job is unusable.
 *
 * `rbush` (MIT) is used rather than a hand-rolled quadtree: it is small, battle-tested and
 * supports dynamic insert/remove, which shapes need because they are edited constantly.
 * `flatbush` is faster but static, so it cannot back an editor -- it remains a candidate
 * for a future read-only review mode, which is why this wrapper exists at all.
 */

import RBush from 'rbush';
import { annotationBounds, areaOf, boxesIntersect, hitTest } from './geometry';
import type { Annotation, Box, Point } from './types';

interface IndexEntry extends Box {
  id: string;
}

export class SpatialIndex {
  private tree = new RBush<IndexEntry>();
  private entries = new Map<string, IndexEntry>();
  private annotations = new Map<string, Annotation>();

  get size(): number {
    return this.annotations.size;
  }

  /** Replace the whole index. Bulk loading builds a far better tree than N inserts. */
  reset(annotations: readonly Annotation[]): void {
    this.tree.clear();
    this.entries.clear();
    this.annotations.clear();

    const bulk: IndexEntry[] = [];
    for (const annotation of annotations) {
      const entry = { ...annotationBounds(annotation), id: annotation.id };
      bulk.push(entry);
      this.entries.set(annotation.id, entry);
      this.annotations.set(annotation.id, annotation);
    }
    this.tree.load(bulk);
  }

  insert(annotation: Annotation): void {
    this.remove(annotation.id);
    const entry = { ...annotationBounds(annotation), id: annotation.id };
    this.tree.insert(entry);
    this.entries.set(annotation.id, entry);
    this.annotations.set(annotation.id, annotation);
  }

  update(annotation: Annotation): void {
    this.insert(annotation);
  }

  remove(id: string): void {
    const entry = this.entries.get(id);
    if (entry) {
      this.tree.remove(entry, (a, b) => a.id === b.id);
      this.entries.delete(id);
    }
    this.annotations.delete(id);
  }

  clear(): void {
    this.tree.clear();
    this.entries.clear();
    this.annotations.clear();
  }

  get(id: string): Annotation | undefined {
    return this.annotations.get(id);
  }

  all(): Annotation[] {
    return [...this.annotations.values()];
  }

  /** Annotations whose bounds intersect `box`. The renderer's culling primitive. */
  search(box: Box): Annotation[] {
    const found: Annotation[] = [];
    for (const entry of this.tree.search(box)) {
      const annotation = this.annotations.get(entry.id);
      if (annotation) found.push(annotation);
    }
    return found;
  }

  /**
   * The topmost annotation under `point`, or null.
   *
   * Candidates come from the R-tree, then get a precise geometric test. Ties break by
   * z-order and then by *smallest area*: when a small object sits inside a large one, the
   * annotator almost always means the small one, and picking the large one is the single
   * most irritating bug in this class of tool.
   */
  pick(point: Point, tolerance: number, filter?: (a: Annotation) => boolean): Annotation | null {
    const probe: Box = {
      minX: point.x - tolerance,
      minY: point.y - tolerance,
      maxX: point.x + tolerance,
      maxY: point.y + tolerance,
    };

    let best: Annotation | null = null;
    let bestArea = Infinity;
    for (const entry of this.tree.search(probe)) {
      const annotation = this.annotations.get(entry.id);
      if (!annotation) continue;
      if (filter && !filter(annotation)) continue;
      if (!hitTest(annotation, point, tolerance)) continue;

      if (best === null) {
        best = annotation;
        bestArea = areaOf(annotation);
        continue;
      }
      const area = areaOf(annotation);
      if (
        annotation.zOrder > best.zOrder ||
        (annotation.zOrder === best.zOrder && area < bestArea)
      ) {
        best = annotation;
        bestArea = area;
      }
    }
    return best;
  }

  /** Every annotation fully or partly inside `box` -- rubber-band selection. */
  pickInBox(box: Box, filter?: (a: Annotation) => boolean): Annotation[] {
    const found: Annotation[] = [];
    for (const entry of this.tree.search(box)) {
      const annotation = this.annotations.get(entry.id);
      if (!annotation) continue;
      if (filter && !filter(annotation)) continue;
      if (boxesIntersect(annotationBounds(annotation), box)) found.push(annotation);
    }
    return found;
  }
}
