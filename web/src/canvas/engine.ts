/**
 * The annotation engine facade.
 *
 * React mounts this and talks to it through the methods below; the engine imports nothing
 * from React. Input is throttled to `requestAnimationFrame`, so a pointer emitting 500
 * events a second still produces at most one repaint per frame.
 */

import { CommandStack, createAddCommand, createDeleteCommand, createUpdateCommand } from './commands';
import { annotationBounds } from './geometry';
import {
  EMPTY_OVERLAY,
  Renderer,
  type OverlayPin,
  type OverlayState,
  type RendererLayers,
} from './renderer';
import { Scene } from './scene';
import { createTool, PathTool, TOOL_SHORTCUTS, type Tool, type ToolContext } from './tools';
import {
  createViewport,
  fitToImage,
  focusOn,
  panBy,
  screenToImage,
  screenTolerance,
  zoomAt,
} from './viewport';
import type {
  Annotation,
  EngineEvents,
  LabelStyle,
  Point,
  PointerInput,
  SceneMedia,
  ToolName,
  ViewportState,
} from './types';

export interface EngineOptions {
  layers: RendererLayers;
  listeners?: Partial<EngineEvents>;
}

type DirtyFlags = { shapes: boolean; media: boolean; overlay: boolean };

export class AnnotationEngine {
  readonly scene: Scene;
  readonly commands: CommandStack;
  private readonly renderer: Renderer;
  private readonly listeners: Partial<EngineEvents>;

  private viewportState: ViewportState = createViewport();
  private media: SceneMedia = { width: 0, height: 0, image: null };
  /**
   * The frame number newly created shapes are stamped with.
   *
   * The engine otherwise has no notion of "frame" at all -- `Scene` holds whatever
   * `setAnnotations` last loaded, which the caller has already filtered to one frame, and
   * every tool builds its draft through `draftAnnotation`, which sets `frame: 0` as a
   * placeholder. Without this, drawing anything on any frame but a job's first silently
   * saved it to frame 0 instead: the shape did not error, did not warn, and simply was not
   * there the next time that frame was viewed. `setFrame` is how `AnnotationCanvas` tells
   * the engine which frame that placeholder should become, and `applyResult` is the one
   * place every tool's `created` result passes through, so the correction lives once
   * rather than once per tool.
   */
  private currentFrame = 0;
  /** Frame pixels, rasterised lazily for tools that read the image. See `frameImageData`. */
  private cachedImageData: ImageData | null = null;
  private cachedImageDataSource: CanvasImageSource | null = null;
  private tool: Tool = createTool('select');
  private overlay: OverlayState = { ...EMPTY_OVERLAY };
  private pickingPoint = false;

  private dirty: DirtyFlags = { shapes: true, media: true, overlay: true };
  private frameHandle: number | null = null;
  private disposed = false;

  private activeLabel: string | null = null;
  private panning = false;
  private lastPanPoint: Point | null = null;
  /** Space held: temporarily pan with any tool active, then return to it. */
  private spacePan = false;

  snapEnabled = true;

  constructor(options: EngineOptions) {
    this.listeners = options.listeners ?? {};
    this.scene = new Scene({
      onChange: () => this.invalidate('shapes'),
      onSelectionChange: (ids) => this.listeners.selectionChanged?.(ids),
    });
    this.commands = new CommandStack({ onChange: () => this.invalidate('shapes') });
    this.renderer = new Renderer(options.layers, this.scene);
  }

  // ------------------------------------------------------------------ lifecycle

  dispose(): void {
    this.disposed = true;
    if (this.frameHandle !== null) cancelAnimationFrame(this.frameHandle);
    this.frameHandle = null;
  }

  resize(width: number, height: number, dpr = 1): void {
    this.viewportState = { ...this.viewportState, width, height };
    this.renderer.resize(width, height, dpr);
    this.invalidate('media', 'shapes', 'overlay');
    this.listeners.viewportChanged?.(this.viewportState);
  }

  // --------------------------------------------------------------------- content

  setMedia(media: SceneMedia, { fit = true }: { fit?: boolean } = {}): void {
    this.media = media;
    // A new frame means new shapes; the mask bitmaps cached for the old one are dead.
    this.renderer.forgetMasks();
    if (media.image !== this.cachedImageDataSource) {
      this.cachedImageData = null;
      this.cachedImageDataSource = null;
    }
    if (fit) {
      this.viewportState = fitToImage(this.viewportState, media.width, media.height);
      this.listeners.viewportChanged?.(this.viewportState);
    }
    this.invalidate('media', 'shapes', 'overlay');
  }

  setAnnotations(annotations: readonly Annotation[]): void {
    this.scene.load(annotations);
    this.commands.clear();
    this.invalidate('shapes', 'overlay');
  }

  /** Which frame a shape drawn from now on belongs to. See `currentFrame`. */
  setFrame(frame: number): void {
    this.currentFrame = frame;
  }

  setLabels(labels: readonly LabelStyle[]): void {
    this.scene.setLabels(labels);
    if (this.activeLabel === null && labels.length > 0) {
      this.activeLabel = labels[0]!.id;
    }
    this.invalidate('shapes');
  }

  setActiveLabel(labelId: string | null): void {
    this.activeLabel = labelId;
  }

  get activeLabelId(): string | null {
    return this.activeLabel;
  }

  get viewport(): ViewportState {
    return this.viewportState;
  }

  get activeTool(): ToolName {
    return this.tool.name;
  }

  get cursor(): string {
    if (this.spacePan || this.panning) return 'grabbing';
    return this.tool.cursor;
  }

  /** How many shapes the last paint drew. Surfaced by the editor's performance readout. */
  get renderedShapeCount(): number {
    return this.renderer.lastShapeCount;
  }

  // ------------------------------------------------------------------------ tools

  setTool(name: ToolName): void {
    if (this.tool.name === name) return;
    this.applyResult(this.tool.cancel());
    this.tool = createTool(name);
    this.invalidate('overlay');
    // Fired unconditionally on every real switch, whichever of the two paths caused it
    // (a toolbar click's React state, or `handleKey`'s direct call) -- see `EngineEvents`.
    this.listeners.toolChanged?.(name);
  }

  private context(): ToolContext {
    return {
      scene: this.scene,
      viewport: () => this.viewportState,
      imageSize: () => ({ width: this.media.width, height: this.media.height }),
      activeLabelId: () => this.activeLabel,
      snapEnabled: () => this.snapEnabled,
      imageData: () => this.frameImageData(),
    };
  }

  /**
   * Pixels of the current frame, rasterised on demand and cached until the frame changes.
   *
   * Only the scissors tool needs this, and it needs it once per frame rather than once per
   * event, so the work is done lazily and the result held. Returning the *same object* while
   * the frame is unchanged is load-bearing: the tool uses identity to decide whether its
   * precomputed edge features are still valid.
   */
  private frameImageData(): ImageData | null {
    const { image, width, height } = this.media;
    if (!image || width <= 0 || height <= 0) return null;
    if (this.cachedImageData && this.cachedImageDataSource === image) {
      return this.cachedImageData;
    }
    try {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) return null;
      context.drawImage(image, 0, 0, width, height);
      this.cachedImageData = context.getImageData(0, 0, width, height);
      this.cachedImageDataSource = image;
      return this.cachedImageData;
    } catch {
      // A cross-origin frame taints the canvas and `getImageData` throws. Frames are fetched
      // through the API client and drawn from object URLs, so this should not happen -- but
      // a tool that cannot read pixels must decline, not take down the editor.
      this.cachedImageData = null;
      this.cachedImageDataSource = null;
      return null;
    }
  }

  // ------------------------------------------------------------------ pointer input

  /**
   * Issue pins to draw on the frame currently on screen.
   *
   * Overlay state rather than scene objects: a pin is not an annotation. It must not be
   * selectable, draggable, deletable with a marquee, or exportable, and keeping it out of
   * the scene is what guarantees all four rather than remembering to special-case them.
   */
  setPins(pins: readonly OverlayPin[]): void {
    this.overlay = { ...this.overlay, pins: [...pins] };
    this.invalidate('overlay');
  }

  /**
   * Take the next click as a point rather than as a drawing action.
   *
   * Armed from the issues panel: the click reports an image-space point and disarms itself,
   * so picking is always exactly one click and never leaves the canvas in a mode the
   * annotator has to find their way out of.
   */
  setPointPicking(enabled: boolean): void {
    this.pickingPoint = enabled;
    this.invalidate('overlay');
  }

  get isPickingPoint(): boolean {
    return this.pickingPoint;
  }

  pointerDown(screen: Point, modifiers: Partial<PointerInput> = {}): void {
    const input = this.toInput(screen, modifiers);

    // Picking wins over the active tool, and over nothing else: panning still works, so a
    // reviewer can reach the part of the frame they mean before placing the pin.
    if (this.pickingPoint && input.button === 0 && !this.spacePan) {
      this.pickingPoint = false;
      this.listeners.pointPicked?.(input.image);
      this.invalidate('overlay');
      return;
    }

    // Middle button or space always pans, whatever tool is active. Losing an in-progress
    // polygon because you needed to scroll is a genuinely infuriating failure.
    if (input.button === 1 || this.spacePan || this.tool.name === 'pan') {
      this.panning = true;
      this.lastPanPoint = screen;
      return;
    }
    this.applyResult(this.tool.onPointerDown(input, this.context()));
  }

  pointerMove(screen: Point, modifiers: Partial<PointerInput> = {}): void {
    if (this.panning && this.lastPanPoint) {
      this.viewportState = panBy(
        this.viewportState,
        screen.x - this.lastPanPoint.x,
        screen.y - this.lastPanPoint.y,
      );
      this.lastPanPoint = screen;
      this.listeners.viewportChanged?.(this.viewportState);
      this.invalidate('media', 'shapes', 'overlay');
      return;
    }

    const input = this.toInput(screen, modifiers);
    this.overlay = { ...this.overlay, cursor: input.image };
    const before = this.scene.hoveredId;
    const result = this.tool.onPointerMove(input, this.context());
    if (this.scene.hoveredId !== before) {
      this.listeners.hoverChanged?.(this.scene.hoveredId);
    }
    this.applyResult(result);
    this.invalidate('overlay');
  }

  pointerUp(screen: Point, modifiers: Partial<PointerInput> = {}): void {
    if (this.panning) {
      this.panning = false;
      this.lastPanPoint = null;
      return;
    }
    this.applyResult(this.tool.onPointerUp(this.toInput(screen, modifiers), this.context()));
  }

  /** Double-click finishes a multi-vertex shape, matching every other tool in this space. */
  doubleClick(): void {
    if (this.tool instanceof PathTool) {
      this.applyResult(this.tool.finish());
    }
  }

  wheel(screen: Point, deltaY: number): void {
    // Exponential in the wheel delta so a trackpad's small deltas and a mouse's large ones
    // both feel proportional rather than one being unusably fast.
    const factor = Math.exp(-deltaY * 0.0015);
    this.viewportState = zoomAt(this.viewportState, screen, factor);
    this.listeners.viewportChanged?.(this.viewportState);
    this.invalidate('media', 'shapes', 'overlay');
  }

  private toInput(screen: Point, modifiers: Partial<PointerInput>): PointerInput {
    return {
      screen,
      image: screenToImage(this.viewportState, screen),
      shiftKey: modifiers.shiftKey ?? false,
      ctrlKey: modifiers.ctrlKey ?? false,
      altKey: modifiers.altKey ?? false,
      button: modifiers.button ?? 0,
    };
  }

  // -------------------------------------------------------------------- keyboard

  /** Returns true when the engine consumed the key. */
  handleKey(
    key: string,
    modifiers: { ctrl?: boolean; meta?: boolean; shift?: boolean } = {},
  ): boolean {
    const accel = modifiers.ctrl || modifiers.meta;

    if (accel && key.toLowerCase() === 'z') {
      if (modifiers.shift) this.commands.redo();
      else this.commands.undo();
      this.emitAnnotationsChanged();
      this.invalidate('shapes', 'overlay');
      return true;
    }
    if (accel && key.toLowerCase() === 'y') {
      this.commands.redo();
      this.emitAnnotationsChanged();
      this.invalidate('shapes', 'overlay');
      return true;
    }
    if (accel && key.toLowerCase() === 'a') {
      this.scene.selectAll();
      this.invalidate('shapes', 'overlay');
      return true;
    }
    if (accel && key.toLowerCase() === 'd') {
      this.duplicateSelection();
      return true;
    }

    const toolResult = this.tool.onKey?.(key, this.context());
    if (toolResult) {
      this.applyResult(toolResult);
      return true;
    }

    if (key === 'Delete' || key === 'Backspace') {
      this.deleteSelection();
      return true;
    }
    if (key === 'Escape') {
      this.applyResult(this.tool.cancel());
      this.scene.clearSelection();
      this.invalidate('shapes', 'overlay');
      return true;
    }
    if (key === ' ') {
      this.spacePan = true;
      return true;
    }

    const tool = TOOL_SHORTCUTS[key.toLowerCase()];
    if (tool && !accel) {
      this.setTool(tool);
      return true;
    }
    return false;
  }

  releaseKey(key: string): void {
    if (key === ' ') {
      this.spacePan = false;
      this.panning = false;
      this.lastPanPoint = null;
    }
  }

  // ------------------------------------------------------------------- operations

  deleteSelection(): void {
    const selected = this.scene.selected;
    if (selected.length === 0) return;
    const editable = selected.filter((a) => this.scene.isEditable(a));
    if (editable.length === 0) return;

    this.commands.execute(createDeleteCommand(this.commandTarget(), editable));
    this.scene.clearSelection();
    this.listeners.annotationsChanged?.({
      created: [],
      updated: [],
      deletedIds: editable.map((a) => a.id),
    });
    this.invalidate('shapes', 'overlay');
  }

  duplicateSelection(offset = 12): void {
    const selected = this.scene.selected;
    if (selected.length === 0) return;

    const copies: Annotation[] = selected.map((annotation) => ({
      ...annotation,
      id: `${annotation.id}-copy-${Math.random().toString(36).slice(2, 8)}`,
      clientId: undefined,
      pending: true,
      points: annotation.points.map((value, index) => value + (index % 2 === 0 ? offset : offset)),
    }));

    for (const copy of copies) {
      this.commands.execute(createAddCommand(this.commandTarget(), copy));
    }
    this.scene.select(copies.map((c) => c.id));
    this.listeners.annotationsChanged?.({ created: copies, updated: [], deletedIds: [] });
    this.invalidate('shapes', 'overlay');
  }

  /** Change the label of every selected object. */
  relabelSelection(labelId: string): void {
    const before = this.scene.selected.map((a) => ({ ...a }));
    if (before.length === 0) return;
    const after = before.map((a) => ({ ...a, labelId }));
    this.commands.execute(
      createUpdateCommand(this.commandTarget(), before, after, 'Change label'),
    );
    this.listeners.annotationsChanged?.({ created: [], updated: after, deletedIds: [] });
    this.invalidate('shapes', 'overlay');
  }

  toggleOccluded(): void {
    const before = this.scene.selected.map((a) => ({ ...a }));
    if (before.length === 0) return;
    const target = !before.every((a) => a.occluded);
    const after = before.map((a) => ({ ...a, occluded: target }));
    this.commands.execute(
      createUpdateCommand(this.commandTarget(), before, after, 'Toggle occluded'),
    );
    this.listeners.annotationsChanged?.({ created: [], updated: after, deletedIds: [] });
    this.invalidate('shapes', 'overlay');
  }

  fitView(): void {
    this.viewportState = fitToImage(this.viewportState, this.media.width, this.media.height);
    this.listeners.viewportChanged?.(this.viewportState);
    this.invalidate('media', 'shapes', 'overlay');
  }

  focusSelection(): void {
    const box = this.scene.selectionBounds();
    if (!box) return;
    this.viewportState = focusOn(this.viewportState, box);
    this.listeners.viewportChanged?.(this.viewportState);
    this.invalidate('media', 'shapes', 'overlay');
  }

  focusAnnotation(id: string): void {
    const annotation = this.scene.get(id);
    if (!annotation) return;
    this.scene.select([id]);
    this.viewportState = focusOn(this.viewportState, annotationBounds(annotation));
    this.listeners.viewportChanged?.(this.viewportState);
    this.invalidate('media', 'shapes', 'overlay');
  }

  zoomBy(factor: number): void {
    const centre = { x: this.viewportState.width / 2, y: this.viewportState.height / 2 };
    this.viewportState = zoomAt(this.viewportState, centre, factor);
    this.listeners.viewportChanged?.(this.viewportState);
    this.invalidate('media', 'shapes', 'overlay');
  }

  /** Image-space tolerance matching the current zoom; the UI uses it for hit areas. */
  get pickTolerance(): number {
    return screenTolerance(this.viewportState);
  }

  // ------------------------------------------------------------------- internals

  private commandTarget() {
    return {
      add: (annotation: Annotation) => this.scene.add(annotation),
      update: (annotation: Annotation) => this.scene.update(annotation),
      remove: (id: string) => this.scene.remove(id),
      get: (id: string) => this.scene.get(id),
    };
  }

  private applyResult(result: ReturnType<Tool['onPointerDown']>): void {
    if (result.created) {
      // Every tool builds its draft with `frame: 0` as a placeholder (see `currentFrame`);
      // this is the one place that placeholder becomes the frame actually being viewed,
      // so no tool has to know the current frame to get this right.
      const created = { ...result.created, frame: this.currentFrame };
      this.commands.execute(createAddCommand(this.commandTarget(), created));
      this.scene.select([created.id]);
      this.listeners.created?.(created);
      this.listeners.annotationsChanged?.({
        created: [created],
        updated: [],
        deletedIds: [],
      });
      this.invalidate('shapes');
    }

    if (result.updated) {
      this.commands.execute(
        createUpdateCommand(this.commandTarget(), result.updated.before, result.updated.after),
      );
      this.listeners.annotationsChanged?.({
        created: [],
        updated: result.updated.after,
        deletedIds: [],
      });
    }

    if (result.deleted && result.deleted.length > 0) {
      this.commands.execute(createDeleteCommand(this.commandTarget(), result.deleted));
      this.scene.clearSelection();
      this.listeners.annotationsChanged?.({
        created: [],
        updated: [],
        deletedIds: result.deleted.map((annotation) => annotation.id),
      });
      this.invalidate('shapes');
    }

    if (result.draft !== undefined) {
      this.overlay = { ...this.overlay, draft: result.draft };
      this.listeners.draftChanged?.(result.draft);
      this.invalidate('overlay');
    }
    if (result.marquee !== undefined) {
      this.overlay = { ...this.overlay, marquee: result.marquee };
      this.invalidate('overlay');
    }
    if (result.snap !== undefined) {
      this.overlay = { ...this.overlay, snap: result.snap };
      this.invalidate('overlay');
    }
    if (result.status !== undefined) this.listeners.toolStatusChanged?.(result.status);
    if (result.invalidateShapes) this.invalidate('shapes');
  }

  private emitAnnotationsChanged(): void {
    // After an undo the caller cannot know what moved, so it is handed the whole frame and
    // diffs it against what it last persisted. Precision here would cost more than it saves.
    this.listeners.annotationsChanged?.({
      created: [],
      updated: this.scene.all(),
      deletedIds: [],
    });
  }

  private invalidate(...layers: (keyof DirtyFlags)[]): void {
    for (const layer of layers) this.dirty[layer] = true;
    this.scheduleFrame();
  }

  private scheduleFrame(): void {
    if (this.disposed || this.frameHandle !== null) return;
    this.frameHandle = requestAnimationFrame(() => {
      this.frameHandle = null;
      this.render();
    });
  }

  /** Paint whichever layers are dirty. Public so tests and benches can drive it. */
  render(): void {
    if (this.dirty.media) {
      this.renderer.drawMedia(this.viewportState, this.media);
      this.dirty.media = false;
    }
    if (this.dirty.shapes) {
      this.renderer.drawShapes(this.viewportState);
      this.dirty.shapes = false;
    }
    if (this.dirty.overlay) {
      this.renderer.drawOverlay(this.viewportState, this.overlay);
      this.dirty.overlay = false;
    }
  }
}
