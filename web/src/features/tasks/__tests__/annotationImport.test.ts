import { describe, expect, it } from 'vitest';
import type { AnnotationImportResult, DatasetFormat } from '@/api/types';
import { canImport, canImportInto, describeImport, importableFormats } from '../annotationImport';

function format(id: string, supportsImport: boolean): DatasetFormat {
  return {
    id,
    name: id,
    version: '1',
    extension: 'zip',
    supports_import: supportsImport,
    supports_export: true,
    shape_types: [],
    supports_tracks: false,
    supports_tags: false,
    supports_attributes: false,
    notes: null,
  };
}

function result(overrides: Partial<AnnotationImportResult> = {}): AnnotationImportResult {
  return {
    shapes_imported: 0,
    tracks_imported: 0,
    tags_imported: 0,
    frames_matched: 1,
    labels_created: [],
    warnings: [],
    ...overrides,
  };
}

const FILE = new File(['x'], 'export.zip');

describe('which formats are offered', () => {
  it('is the ones that read, in the server’s order', () => {
    const formats = [format('coco', true), format('yolo_pose', false), format('cvat_xml', true)];
    expect(importableFormats(formats).map((f) => f.id)).toEqual(['coco', 'cvat_xml']);
    expect(importableFormats(undefined)).toEqual([]);
  });
});

describe('sending', () => {
  it('needs a format and a file', () => {
    const base = { format: 'coco', file: FILE, policy: 'append' as const, replaceConfirmed: false };
    expect(canImport(base)).toBe(true);
    expect(canImport({ ...base, format: '' })).toBe(false);
    expect(canImport({ ...base, file: null })).toBe(false);
  });

  it('will not replace without an explicit yes', () => {
    const replace = { format: 'coco', file: FILE, policy: 'replace' as const };
    expect(canImport({ ...replace, replaceConfirmed: false })).toBe(false);
    expect(canImport({ ...replace, replaceConfirmed: true })).toBe(true);
  });
});

describe('what the import says', () => {
  it('names what it stored, tracks and tags only when there are any', () => {
    expect(describeImport(result({ shapes_imported: 1, frames_matched: 1 }))).toBe(
      'Imported 1 shape across 1 frame.',
    );
    expect(
      describeImport(
        result({ shapes_imported: 12, tracks_imported: 2, tags_imported: 3, frames_matched: 40 }),
      ),
    ).toBe('Imported 12 shapes, 2 tracks and 3 tags across 40 frames.');
  });

  it('names the labels it added to the project', () => {
    expect(
      describeImport(result({ shapes_imported: 2, labels_created: ['person', 'bike'] })),
    ).toMatch(/New labels added to the project: person, bike\./);
  });

  it('says why nothing came in when no frame matched, rather than "0 shapes"', () => {
    expect(describeImport(result({ frames_matched: 0 }))).toMatch(
      /no frame of this task matched.*by image file name/,
    );
  });
});

describe('who is offered the panel', () => {
  it('is whoever the server lets import', () => {
    expect(canImportInto('maintainer')).toBe(true);
    expect(canImportInto('owner')).toBe(true);
    expect(canImportInto('annotator')).toBe(false);
    expect(canImportInto('reviewer')).toBe(false);
    expect(canImportInto(undefined)).toBe(true);
  });
});
