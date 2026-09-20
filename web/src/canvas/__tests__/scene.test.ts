/**
 * Scene-level display settings: whether they read back what was set, and whether setting
 * one actually asks for a repaint.
 *
 * `showLabels`, `showSuggestions` and `fillOpacity` used to be plain public fields. Reading
 * one worked fine; writing one directly (`scene.fillOpacity = 0.3`) silently changed nothing
 * on screen, because nothing called `invalidate()` for a raw field write -- and for a long
 * time nothing called these at all, since no UI control existed to call them from. The
 * getter/setter pair is what makes a write actually repaint, and this is what proves it does.
 */

import { describe, expect, it, vi } from 'vitest';
import { Scene } from '../scene';

describe('display settings default to visible, full stated opacity', () => {
  it('starts with labels and suggestions shown, at the documented default opacity', () => {
    const scene = new Scene({});
    expect(scene.showLabels).toBe(true);
    expect(scene.showSuggestions).toBe(true);
    expect(scene.fillOpacity).toBe(0.18);
  });
});

describe('setShowLabels', () => {
  it('is read back by the getter', () => {
    const scene = new Scene({});
    scene.setShowLabels(false);
    expect(scene.showLabels).toBe(false);
  });

  it('asks for a repaint when it actually changes something', () => {
    const onChange = vi.fn();
    const scene = new Scene({ onChange });
    scene.setShowLabels(false);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('does not ask for a repaint when the value does not change', () => {
    const onChange = vi.fn();
    const scene = new Scene({ onChange });
    scene.setShowLabels(true); // already true by default
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('setShowSuggestions', () => {
  it('is read back by the getter and triggers a repaint on a real change', () => {
    const onChange = vi.fn();
    const scene = new Scene({ onChange });
    scene.setShowSuggestions(false);
    expect(scene.showSuggestions).toBe(false);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  // The pairing that makes this setting worth having at all: it hides only what is still
  // *unreviewed*, not everything a model ever produced. `isVisible`'s own tests would catch
  // a regression in the predicate itself; this just confirms the setter feeds it correctly.
  it('is what Scene.isVisible reads to decide whether an unreviewed suggestion shows', () => {
    const scene = new Scene({});
    const suggestion = {
      id: 's1',
      labelId: 'l1',
      frame: 0,
      shapeType: 'rectangle' as const,
      points: [0, 0, 10, 10],
      rotation: 0,
      occluded: false,
      outside: false,
      zOrder: 0,
      source: 'model' as const,
      confidence: 0.8,
      attributes: {},
    };
    expect(scene.isVisible(suggestion)).toBe(true);
    scene.setShowSuggestions(false);
    expect(scene.isVisible(suggestion)).toBe(false);
  });
});

describe('setFillOpacity', () => {
  it('is read back by the getter', () => {
    const scene = new Scene({});
    scene.setFillOpacity(0.5);
    expect(scene.fillOpacity).toBe(0.5);
  });

  it('clamps rather than storing an out-of-range value', () => {
    const scene = new Scene({});
    scene.setFillOpacity(5);
    expect(scene.fillOpacity).toBe(1);
    scene.setFillOpacity(-2);
    expect(scene.fillOpacity).toBe(0);
  });

  it('asks for a repaint only when the clamped value actually changes', () => {
    const onChange = vi.fn();
    const scene = new Scene({ onChange });
    scene.setFillOpacity(0.18); // the default -- nothing to change
    expect(onChange).not.toHaveBeenCalled();
    scene.setFillOpacity(0.4);
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
