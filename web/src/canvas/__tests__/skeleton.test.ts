import { describe, expect, it } from 'vitest';
import type { SkeletonElement } from '../types';
import {
  bones,
  buildElements,
  canCommit,
  describeProgress,
  drawableJoints,
  isComplete,
  nextJoint,
  placedCount,
  skeletonPoints,
  skeletonSchema,
  type Placement,
  type SkeletonSchema,
} from '../skeleton';

/** A three-joint arm: shoulder — elbow — wrist. Small enough to reason about by hand. */
function arm(edges: number[][] = [[0, 1], [1, 2]]) {
  const schema = skeletonSchema({
    id: 'arm',
    name: 'arm',
    children: [
      { id: 'j-shoulder', name: 'shoulder' },
      { id: 'j-elbow', name: 'elbow' },
      { id: 'j-wrist', name: 'wrist' },
    ],
    skeletonEdges: edges,
  });
  if (!schema) throw new Error('expected a skeleton schema');
  return schema;
}

function at(x: number, y: number, occluded = false) {
  return { point: { x, y }, occluded };
}

describe('skeletonSchema', () => {
  it('is null for a label with no children', () => {
    expect(skeletonSchema({ id: 'car', name: 'car' })).toBeNull();
    expect(skeletonSchema({ id: 'car', name: 'car', children: [] })).toBeNull();
  });

  it('keeps the children in declared order, because that order is the file format', () => {
    expect(arm().joints.map((joint) => joint.name)).toEqual(['shoulder', 'elbow', 'wrist']);
  });

  // Plenty of keypoint datasets declare no connectivity at all. Refusing to draw one would
  // be inventing a requirement the format does not have.
  it('is still a skeleton when no bones are declared', () => {
    const schema = arm([]);
    expect(schema.joints).toHaveLength(3);
    expect(schema.edges).toEqual([]);
  });

  it('drops an edge naming a joint that does not exist', () => {
    expect(arm([[0, 1], [1, 9]]).edges).toEqual([[0, 1]]);
    expect(arm([[-1, 0]]).edges).toEqual([]);
  });

  it('drops a self-edge and a non-integer edge', () => {
    expect(arm([[1, 1]]).edges).toEqual([]);
    expect(arm([[0, 1.5]]).edges).toEqual([]);
  });

  it('drops a malformed pair rather than reading undefined as joint 0', () => {
    expect(arm([[0]]).edges).toEqual([]);
  });
});

describe('walking the joints', () => {
  const schema = arm();

  it('asks for the joints strictly in order', () => {
    expect(nextJoint(schema, [])?.name).toBe('shoulder');
    expect(nextJoint(schema, [at(1, 1)])?.name).toBe('elbow');
    expect(nextJoint(schema, [at(1, 1), null])?.name).toBe('wrist');
  });

  it('has nothing left to ask once every joint is decided', () => {
    const placement: Placement = [at(1, 1), null, at(3, 3)];
    expect(nextJoint(schema, placement)).toBeNull();
    expect(isComplete(schema, placement)).toBe(true);
  });

  // A skipped joint is a decision, not a gap: it still occupies its slot.
  it('counts a skip as decided but not as placed', () => {
    expect(placedCount([at(1, 1), null])).toBe(1);
    expect(isComplete(schema, [at(1, 1), null])).toBe(false);
  });

  it('will not commit a skeleton where every joint was skipped', () => {
    expect(canCommit([null, null, null])).toBe(false);
    expect(canCommit([null, at(5, 5), null])).toBe(true);
    expect(canCommit([])).toBe(false);
  });
});

describe('describeProgress', () => {
  const schema = arm();

  it('names the joint and where it falls, counting from one', () => {
    expect(describeProgress(schema, [])).toBe('shoulder — joint 1 of 3');
    expect(describeProgress(schema, [at(1, 1)])).toBe('elbow — joint 2 of 3');
  });

  it('says so when everything is placed', () => {
    expect(describeProgress(schema, [at(1, 1), at(2, 2), at(3, 3)])).toBe(
      'All 3 joints placed — press Enter to finish',
    );
  });

  it('reports the skipped ones rather than claiming a full pose', () => {
    expect(describeProgress(schema, [at(1, 1), null, at(3, 3)])).toBe(
      '2 of 3 joints placed, 1 skipped — Enter to finish',
    );
  });
});

describe('buildElements', () => {
  const schema = arm();

  it('writes one element per declared joint, in declared order', () => {
    const elements = buildElements(schema, [at(1, 2), at(3, 4), at(5, 6)]);
    expect(elements.map((element) => element.labelId)).toEqual([
      'j-shoulder',
      'j-elbow',
      'j-wrist',
    ]);
    expect(elements.map((element) => element.points)).toEqual([
      [1, 2],
      [3, 4],
      [5, 6],
    ]);
    expect(elements.every((element) => !element.outside)).toBe(true);
  });

  // The whole reason this module exists. `yolo_pose` writes triples positionally, so a
  // shortened list moves every later joint one place left.
  it('pads a skipped joint in place rather than omitting it', () => {
    const elements = buildElements(schema, [at(1, 2), null, at(5, 6)]);
    expect(elements).toHaveLength(3);
    expect(elements[1]).toEqual({
      labelId: 'j-elbow',
      points: [0, 0],
      occluded: false,
      outside: true,
    });
    expect(elements[2]?.points).toEqual([5, 6]);
  });

  it('pads the joints never reached, so an early finish is still full width', () => {
    const elements = buildElements(schema, [at(1, 2)]);
    expect(elements).toHaveLength(3);
    expect(elements.slice(1).every((element) => element.outside)).toBe(true);
  });

  it('carries occlusion through, which is visibility 1 rather than 2', () => {
    const elements = buildElements(schema, [at(1, 2, true), at(3, 4), at(5, 6)]);
    expect(elements[0]?.occluded).toBe(true);
    expect(elements[0]?.outside).toBe(false);
    expect(elements[1]?.occluded).toBe(false);
  });
});

describe('skeletonPoints', () => {
  const schema = arm();

  it('flattens the joints that exist', () => {
    const elements = buildElements(schema, [at(1, 2), at(3, 4), at(5, 6)]);
    expect(skeletonPoints(elements)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  // Including a skipped joint's (0, 0) would stretch every skeleton's bounding box to the
  // top-left corner of the image, which moves its label chip there too.
  it('leaves out a skipped joint rather than putting it at the origin', () => {
    const elements = buildElements(schema, [at(10, 20), null, at(30, 40)]);
    expect(skeletonPoints(elements)).toEqual([10, 20, 30, 40]);
  });

  it('is empty when nothing was placed', () => {
    expect(skeletonPoints(buildElements(schema, [null, null, null]))).toEqual([]);
  });
});

describe('drawing', () => {
  const schema = arm();

  it('reports each present joint with the index its bones refer to', () => {
    const elements = buildElements(schema, [at(1, 2), null, at(5, 6)]);
    expect(drawableJoints(elements).map((joint) => joint.index)).toEqual([0, 2]);
    expect(drawableJoints(elements)[1]?.point).toEqual({ x: 5, y: 6 });
  });

  it('draws both bones of a complete arm', () => {
    const elements = buildElements(schema, [at(0, 0), at(10, 0), at(20, 0)]);
    expect(bones(schema.edges, elements)).toEqual([
      [{ x: 0, y: 0 }, { x: 10, y: 0 }],
      [{ x: 10, y: 0 }, { x: 20, y: 0 }],
    ]);
  });

  // Drawing a line to where a hidden joint "would have been" is indistinguishable on screen
  // from one somebody annotated. It is the one thing the tool must never produce.
  it('draws no bone whose endpoint was skipped', () => {
    const elements = buildElements(schema, [at(0, 0), null, at(20, 0)]);
    expect(bones(schema.edges, elements)).toEqual([]);
  });

  it('draws the bones that survive when only one endpoint is missing', () => {
    const wide = arm([[0, 1], [1, 2], [0, 2]]);
    const elements = buildElements(wide, [at(0, 0), null, at(20, 0)]);
    expect(bones(wide.edges, elements)).toEqual([
      [{ x: 0, y: 0 }, { x: 20, y: 0 }],
    ]);
  });

  it('draws nothing for a label that declares no bones', () => {
    const elements = buildElements(schema, [at(0, 0), at(10, 0), at(20, 0)]);
    expect(bones([], elements)).toEqual([]);
  });

  it('survives an element whose points are malformed', () => {
    const broken: SkeletonElement[] = [
      { labelId: 'j-shoulder', points: [], occluded: false, outside: false },
      { labelId: 'j-elbow', points: [10, 0], occluded: false, outside: false },
      { labelId: 'j-wrist', points: [20, 0], occluded: false, outside: false },
    ];
    expect(drawableJoints(broken).map((joint) => joint.index)).toEqual([1, 2]);
    expect(bones(schema.edges, broken)).toEqual([
      [{ x: 10, y: 0 }, { x: 20, y: 0 }],
    ]);
  });
});

describe('the shape of what a schema promises', () => {
  it('holds: elements are always as long as the joint list, whatever was placed', () => {
    const schema: SkeletonSchema = arm();
    for (const placement of [
      [],
      [at(1, 1)],
      [null],
      [at(1, 1), null],
      [at(1, 1), at(2, 2), at(3, 3)],
      [null, null, at(3, 3)],
    ] as Placement[]) {
      expect(buildElements(schema, placement)).toHaveLength(schema.joints.length);
    }
  });

  it('holds: element order always matches the declared joint order', () => {
    const schema = arm();
    const expected = schema.joints.map((joint) => joint.labelId);
    for (const placement of [[], [at(1, 1), null], [null, at(2, 2), at(3, 3)]] as Placement[]) {
      expect(buildElements(schema, placement).map((element) => element.labelId)).toEqual(expected);
    }
  });
});
