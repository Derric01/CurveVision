/**
 * The scene: the annotations on the current frame, their spatial index, and selection.
 *
 * Deliberately not a React store. The renderer and the tools read it imperatively at 60fps;
 * routing that through a reactive store would mean a re-render per pointer-move.
 */

import { annotationBounds } from './geometry';
import { SpatialIndex } from './spatial';
import type { Annotation, Box, LabelStyle, Point } from './types';

export interface SceneOptions {
  onChange?: () => void;
  onSelectionChange?: (ids: string[]) => void;
}

export class Scene {
  readonly index = new SpatialIndex();
  private selection = new Set<string>();
  private labels = new Map<string, LabelStyle>();
  private hovered: string | null = null;
  private readonly onChange?: () => void;
  private readonly onSelectionChange?: (ids: string[]) => void;

  /** Global visibility toggles, applied on top of per-label ones. */
  showLabels = true;
  showSuggestions = true;
  fillOpacity = 0.18;

  constructor(options: SceneOptions = {}) {
    this.onChange = options.onChange;
    this.onSelectionChange = options.onSelectionChange;
  }

  // ------------------------------------------------------------------ annotations

  load(annotations: readonly Annotation[]): void {
    this.index.reset(annotations);
    // Drop selections for objects that are no longer present rather than leaving dangling
    // ids that make the sidebar and the canvas disagree.
    const present = new Set(annotations.map((a) => a.id));
    let changed = false;
    for (const id of [...this.selection]) {
      if (!present.has(id)) {
        this.selection.delete(id);
        changed = true;
      }
    }
    if (changed) this.emitSelection();
    this.onChange?.();
  }

  add(annotation: Annotation): void {
    this.index.insert(annotation);
    this.onChange?.();
  }

  update(annotation: Annotation): void {
    this.index.update(annotation);
    this.onChange?.();
  }

  remove(id: string): void {
    this.index.remove(id);
    if (this.selection.delete(id)) this.emitSelection();
    if (this.hovered === id) this.hovered = null;
    this.onChange?.();
  }

  get(id: string): Annotation | undefined {
    return this.index.get(id);
  }

  all(): Annotation[] {
    return this.index.all();
  }

  get size(): number {
    return this.index.size;
  }

  // ---------------------------------------------------------------------- labels

  setLabels(labels: readonly LabelStyle[]): void {
    this.labels = new Map(labels.map((label) => [label.id, label]));
    this.onChange?.();
  }

  label(id: string): LabelStyle | undefined {
    return this.labels.get(id);
  }

  labelColor(id: string): string {
    return this.labels.get(id)?.color ?? '#38bdf8';
  }

  labelName(id: string): string {
    return this.labels.get(id)?.name ?? 'unlabelled';
  }

  setLabelVisible(id: string, visible: boolean): void {
    const label = this.labels.get(id);
    if (label) {
      this.labels.set(id, { ...label, visible });
      this.onChange?.();
    }
  }

  setLabelLocked(id: string, locked: boolean): void {
    const label = this.labels.get(id);
    if (label) {
      this.labels.set(id, { ...label, locked });
      // A newly locked label must not keep objects selected: the next drag would silently
      // do nothing, which reads as a broken editor.
      if (locked) {
        let changed = false;
        for (const selectedId of [...this.selection]) {
          if (this.get(selectedId)?.labelId === id) {
            this.selection.delete(selectedId);
            changed = true;
          }
        }
        if (changed) this.emitSelection();
      }
      this.onChange?.();
    }
  }

  isVisible(annotation: Annotation): boolean {
    if (!this.showSuggestions && annotation.source === 'model') return false;
    const label = this.labels.get(annotation.labelId);
    return label ? label.visible : true;
  }

  isEditable(annotation: Annotation): boolean {
    const label = this.labels.get(annotation.labelId);
    return this.isVisible(annotation) && !(label?.locked ?? false);
  }

  // ------------------------------------------------------------------- selection

  get selectedIds(): string[] {
    return [...this.selection];
  }

  get selected(): Annotation[] {
    return this.selectedIds
      .map((id) => this.get(id))
      .filter((a): a is Annotation => a !== undefined);
  }

  isSelected(id: string): boolean {
    return this.selection.has(id);
  }

  select(ids: readonly string[], additive = false): void {
    if (!additive) this.selection.clear();
    for (const id of ids) {
      if (this.get(id)) this.selection.add(id);
    }
    this.emitSelection();
    this.onChange?.();
  }

  toggleSelection(id: string): void {
    if (this.selection.has(id)) this.selection.delete(id);
    else if (this.get(id)) this.selection.add(id);
    this.emitSelection();
    this.onChange?.();
  }

  clearSelection(): void {
    if (this.selection.size === 0) return;
    this.selection.clear();
    this.emitSelection();
    this.onChange?.();
  }

  selectAll(): void {
    this.selection = new Set(
      this.all()
        .filter((annotation) => this.isEditable(annotation))
        .map((annotation) => annotation.id),
    );
    this.emitSelection();
    this.onChange?.();
  }

  private emitSelection(): void {
    this.onSelectionChange?.(this.selectedIds);
  }

  // ----------------------------------------------------------------------- hover

  get hoveredId(): string | null {
    return this.hovered;
  }

  setHovered(id: string | null): boolean {
    if (this.hovered === id) return false;
    this.hovered = id;
    return true;
  }

  // ---------------------------------------------------------------------- picking

  pick(point: Point, tolerance: number): Annotation | null {
    return this.index.pick(point, tolerance, (a) => this.isEditable(a));
  }

  pickInBox(box: Box): Annotation[] {
    return this.index.pickInBox(box, (a) => this.isEditable(a));
  }

  /** Annotations intersecting the viewport, sorted back-to-front for painting. */
  visible(box: Box): Annotation[] {
    return this.index
      .search(box)
      .filter((annotation) => this.isVisible(annotation))
      .sort((a, b) => a.zOrder - b.zOrder);
  }

  /** Bounding box enclosing the selection, or null when nothing is selected. */
  selectionBounds(): Box | null {
    const selected = this.selected;
    if (selected.length === 0) return null;
    let box = annotationBounds(selected[0]!);
    for (let i = 1; i < selected.length; i++) {
      const next = annotationBounds(selected[i]!);
      box = {
        minX: Math.min(box.minX, next.minX),
        minY: Math.min(box.minY, next.minY),
        maxX: Math.max(box.maxX, next.maxX),
        maxY: Math.max(box.maxY, next.maxY),
      };
    }
    return box;
  }
}
