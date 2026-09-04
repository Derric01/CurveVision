import { describe, expect, it } from 'vitest';
import {
  MAX_SCALE,
  MIN_SCALE,
  createViewport,
  fitToImage,
  focusOn,
  imageToScreen,
  panBy,
  screenTolerance,
  screenToImage,
  visibleBox,
  zoomAt,
} from '../viewport';

describe('coordinate conversion', () => {
  it('round-trips a point through both transforms', () => {
    const viewport = { ...createViewport(800, 600), scale: 2.5, offsetX: 120, offsetY: 40 };
    const original = { x: 321.5, y: 87.25 };
    const roundTripped = screenToImage(viewport, imageToScreen(viewport, original));
    expect(roundTripped.x).toBeCloseTo(original.x);
    expect(roundTripped.y).toBeCloseTo(original.y);
  });

  it('places the offset at the canvas origin', () => {
    const viewport = { ...createViewport(800, 600), scale: 2, offsetX: 50, offsetY: 25 };
    expect(imageToScreen(viewport, { x: 50, y: 25 })).toEqual({ x: 0, y: 0 });
  });
});

describe('zoomAt', () => {
  it('holds the point under the cursor still', () => {
    // Getting this wrong makes the tool feel broken within seconds of use.
    const viewport = createViewport(800, 600);
    const cursor = { x: 300, y: 200 };
    const before = screenToImage(viewport, cursor);

    const zoomed = zoomAt(viewport, cursor, 2.5);
    const after = screenToImage(zoomed, cursor);

    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
    expect(zoomed.scale).toBeCloseTo(2.5);
  });

  it('clamps to the scale limits', () => {
    const viewport = createViewport(800, 600);
    expect(zoomAt(viewport, { x: 0, y: 0 }, 1e6).scale).toBe(MAX_SCALE);
    expect(zoomAt(viewport, { x: 0, y: 0 }, 1e-6).scale).toBe(MIN_SCALE);
  });

  it('returns the same object when already clamped', () => {
    const viewport = { ...createViewport(800, 600), scale: MAX_SCALE };
    expect(zoomAt(viewport, { x: 10, y: 10 }, 2)).toBe(viewport);
  });
});

describe('panBy', () => {
  it('moves the image by the screen delta divided by scale', () => {
    const viewport = { ...createViewport(800, 600), scale: 4 };
    const panned = panBy(viewport, 40, -20);
    expect(panned.offsetX).toBeCloseTo(-10);
    expect(panned.offsetY).toBeCloseTo(5);
  });
});

describe('fitToImage', () => {
  it('centres a wide image and scales it to fit', () => {
    const fitted = fitToImage(createViewport(800, 600), 1600, 900);
    expect(fitted.scale).toBeLessThan(0.5);

    const centre = screenToImage(fitted, { x: 400, y: 300 });
    expect(centre.x).toBeCloseTo(800, 0);
    expect(centre.y).toBeCloseTo(450, 0);
  });

  it('leaves the viewport alone when the image has no size', () => {
    const viewport = createViewport(800, 600);
    expect(fitToImage(viewport, 0, 0)).toBe(viewport);
  });
});

describe('visibleBox', () => {
  it('describes exactly the image region on screen', () => {
    const viewport = { ...createViewport(800, 600), scale: 2, offsetX: 100, offsetY: 50 };
    expect(visibleBox(viewport)).toEqual({
      minX: 100,
      minY: 50,
      maxX: 500,
      maxY: 350,
    });
  });
});

describe('focusOn', () => {
  it('centres a box in the viewport', () => {
    const focused = focusOn(createViewport(800, 600), {
      minX: 200,
      minY: 200,
      maxX: 300,
      maxY: 260,
    });
    const centre = screenToImage(focused, { x: 400, y: 300 });
    expect(centre.x).toBeCloseTo(250, 0);
    expect(centre.y).toBeCloseTo(230, 0);
  });
});

describe('screenTolerance', () => {
  it('shrinks in image space as the viewport zooms in', () => {
    expect(screenTolerance({ ...createViewport(), scale: 1 }, 6)).toBe(6);
    expect(screenTolerance({ ...createViewport(), scale: 12 }, 6)).toBe(0.5);
  });
});
