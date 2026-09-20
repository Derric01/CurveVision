/**
 * React's window onto the annotation engine.
 *
 * This component owns three canvases and a resize observer, and forwards DOM events to the
 * engine. It deliberately holds almost no state: everything that changes at pointer speed
 * lives in the engine, so React re-renders only when something a human would notice
 * changes.
 */

import { useEffect, useImperativeHandle, useRef, forwardRef, useCallback } from 'react';
import { AnnotationEngine } from '@/canvas/engine';
import type { Annotation, AnnotationChange, LabelStyle, Point, ToolName } from '@/canvas/types';
import type { OverlayPin } from '@/canvas/renderer';

export interface CanvasHandle {
  engine: AnnotationEngine | null;
}

interface Props {
  annotations: Annotation[];
  labels: LabelStyle[];
  imageUrl: string | null;
  activeLabelId: string | null;
  tool: ToolName;
  /** The frame being viewed, so a newly drawn shape is stamped with it rather than 0. */
  currentFrame: number;
  onChange: (change: AnnotationChange) => void;
  onSelectionChange: (ids: string[]) => void;
  onViewportChange?: (scale: number) => void;
  /** Review issue pins for the frame on screen. */
  pins?: OverlayPin[];
  /** When true, the next click reports a point instead of drawing. */
  picking?: boolean;
  onPointPicked?: (point: Point) => void;
  /** What the active tool is waiting for, for tools with a multi-step interaction. */
  onToolStatus?: (status: string | null) => void;
  /** Global visibility toggles and fill opacity. See `Scene`'s getter/setter pair for each
   *  — controlled props, the same shape as `activeLabelId` and `tool`, rather than a direct
   *  `engine.scene.setX()` call from a click handler, so the engine stays in sync with
   *  whatever set these even before it exists on the very first render. */
  showLabels?: boolean;
  showSuggestions?: boolean;
  fillOpacity?: number;
  /**
   * The engine's tool changed. Fires for a keyboard shortcut exactly as it does for the
   * `tool` prop causing `setTool` below -- the parent should treat this as the source of
   * truth for which tool is active, not only its own `tool` state, or a shortcut key leaves
   * the toolbar highlighted on whatever was active before it was pressed.
   */
  onToolChange?: (tool: ToolName) => void;
}

export const AnnotationCanvas = forwardRef<CanvasHandle, Props>(function AnnotationCanvas(
  {
    annotations,
    labels,
    imageUrl,
    activeLabelId,
    tool,
    currentFrame,
    onChange,
    onSelectionChange,
    onViewportChange,
    pins,
    picking = false,
    onPointPicked,
    onToolStatus,
    onToolChange,
    showLabels = true,
    showSuggestions = true,
    fillOpacity = 0.18,
  },
  ref,
) {
  const container = useRef<HTMLDivElement>(null);
  const mediaCanvas = useRef<HTMLCanvasElement>(null);
  const shapeCanvas = useRef<HTMLCanvasElement>(null);
  const overlayCanvas = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<AnnotationEngine | null>(null);

  // Callbacks are read through a ref so the engine is built once, not rebuilt whenever a
  // parent re-render produces new function identities.
  const callbacks = useRef({
    onChange,
    onSelectionChange,
    onViewportChange,
    onPointPicked,
    onToolStatus,
    onToolChange,
  });
  callbacks.current = {
    onChange,
    onSelectionChange,
    onViewportChange,
    onPointPicked,
    onToolStatus,
    onToolChange,
  };

  // A getter, not a snapshot. `useImperativeHandle` runs as a layout effect and is declared
  // above the effect that constructs the engine, so a plain `{ engine: engineRef.current }`
  // captures `null` — and with an empty dependency list it stays null for the life of the
  // component. Every control the parent drives through this handle (undo, redo, delete, fit
  // to frame, the label visibility and lock toggles, and focusing an object from the object
  // list) then silently does nothing: no error, no console warning, just a button that is
  // not connected to anything.
  useImperativeHandle(
    ref,
    () => ({
      get engine() {
        return engineRef.current;
      },
    }),
    [],
  );

  useEffect(() => {
    const media = mediaCanvas.current?.getContext('2d');
    const shapes = shapeCanvas.current?.getContext('2d');
    const overlay = overlayCanvas.current?.getContext('2d');
    if (!media || !shapes || !overlay) return;

    const engine = new AnnotationEngine({
      layers: { media, shapes, overlay },
      listeners: {
        annotationsChanged: (change) => callbacks.current.onChange(change),
        selectionChanged: (ids) => callbacks.current.onSelectionChange(ids),
        viewportChanged: (viewport) => callbacks.current.onViewportChange?.(viewport.scale),
        pointPicked: (point) => callbacks.current.onPointPicked?.(point),
        toolStatusChanged: (status) => callbacks.current.onToolStatus?.(status),
        toolChanged: (nextTool) => callbacks.current.onToolChange?.(nextTool),
      },
    });
    engineRef.current = engine;

    const element = container.current;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const { width, height } = entry.contentRect;
      engine.resize(width, height, window.devicePixelRatio || 1);
    });
    if (element) observer.observe(element);

    return () => {
      observer.disconnect();
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  // Load the frame image. Decoding off the main thread keeps the first paint from janking.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (!imageUrl) {
      engine.setMedia({ width: 0, height: 0, image: null });
      return;
    }

    let cancelled = false;
    const image = new Image();
    // `imageUrl` is an object URL created from bytes the API client already fetched with
    // its credentials, so there is no cross-origin request to configure here.
    image.src = imageUrl;
    void image
      .decode()
      .then(() => {
        if (cancelled) return;
        engine.setMedia({
          width: image.naturalWidth,
          height: image.naturalHeight,
          image,
        });
      })
      .catch(() => {
        if (cancelled) return;
        // A failed decode leaves a *broken* HTMLImageElement, and handing that to the
        // renderer makes `drawImage` throw. Catching the rejection and then carrying on
        // into the success path -- which this did -- turns a load failure into a page
        // error, which is how scrubbing quickly through a video used to break the editor.
        //
        // Clearing rather than keeping the previous frame is deliberate: a frame number
        // identifies one picture, permanently. Leaving the old image under a new frame
        // number would let an annotator draw a box on the wrong picture, and nothing
        // downstream could tell.
        engine.setMedia({ width: 0, height: 0, image: null }, { fit: false });
      });

    return () => {
      cancelled = true;
    };
  }, [imageUrl]);

  useEffect(() => {
    engineRef.current?.setLabels(labels);
  }, [labels]);

  useEffect(() => {
    engineRef.current?.setAnnotations(annotations);
  }, [annotations]);

  // The engine has no other way to know which frame a newly drawn shape belongs to --
  // `annotations` is already filtered to one frame by the time it arrives here, but a
  // *new* shape starts with a placeholder frame that only this tells it how to correct.
  useEffect(() => {
    engineRef.current?.setFrame(currentFrame);
  }, [currentFrame]);

  useEffect(() => {
    engineRef.current?.setActiveLabel(activeLabelId);
  }, [activeLabelId]);

  useEffect(() => {
    engineRef.current?.setPins(pins ?? []);
  }, [pins]);

  useEffect(() => {
    engineRef.current?.setPointPicking(picking);
  }, [picking]);

  useEffect(() => {
    engineRef.current?.setTool(tool);
  }, [tool]);

  useEffect(() => {
    engineRef.current?.scene.setShowLabels(showLabels);
  }, [showLabels]);

  useEffect(() => {
    engineRef.current?.scene.setShowSuggestions(showSuggestions);
  }, [showSuggestions]);

  useEffect(() => {
    engineRef.current?.scene.setFillOpacity(fillOpacity);
  }, [fillOpacity]);

  // Keyboard handling is bound to the window rather than the canvas: annotators expect
  // shortcuts to work while their focus is on the object list or the label picker.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (engineRef.current?.handleKey(event.key, {
        ctrl: event.ctrlKey,
        meta: event.metaKey,
        shift: event.shiftKey,
      })) {
        event.preventDefault();
      }
    }
    function onKeyUp(event: KeyboardEvent) {
      engineRef.current?.releaseKey(event.key);
    }

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, []);

  const localPoint = useCallback((event: React.PointerEvent | React.WheelEvent) => {
    const bounds = container.current?.getBoundingClientRect();
    return {
      x: event.clientX - (bounds?.left ?? 0),
      y: event.clientY - (bounds?.top ?? 0),
    };
  }, []);

  return (
    <div
      ref={container}
      className="relative h-full w-full overflow-hidden bg-ink-950"
      // Picking overrides the tool's cursor: the click is about to mean something other
      // than what the active tool says it means, and a crosshair is the only warning.
      style={{ cursor: picking ? 'crosshair' : (engineRef.current?.cursor ?? 'default') }}
      onPointerDown={(event) => {
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
        engineRef.current?.pointerDown(localPoint(event), {
          button: event.button,
          shiftKey: event.shiftKey,
          ctrlKey: event.ctrlKey,
          altKey: event.altKey,
        });
      }}
      onPointerMove={(event) =>
        engineRef.current?.pointerMove(localPoint(event), {
          shiftKey: event.shiftKey,
          ctrlKey: event.ctrlKey,
          altKey: event.altKey,
        })
      }
      onPointerUp={(event) =>
        engineRef.current?.pointerUp(localPoint(event), { button: event.button })
      }
      onDoubleClick={() => engineRef.current?.doubleClick()}
      onWheel={(event) => engineRef.current?.wheel(localPoint(event), event.deltaY)}
      onContextMenu={(event) => event.preventDefault()}
    >
      <canvas ref={mediaCanvas} className="absolute inset-0" />
      <canvas ref={shapeCanvas} className="absolute inset-0" />
      <canvas ref={overlayCanvas} className="absolute inset-0" />
    </div>
  );
});
