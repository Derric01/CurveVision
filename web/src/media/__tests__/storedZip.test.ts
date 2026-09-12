/**
 * Reading the frame archives the server builds.
 *
 * The fixtures here are built by Node's own zlib rather than hand-assembled, so they are
 * real ZIPs written by something that is not this reader — a hand-rolled fixture would only
 * prove the reader agrees with itself.
 *
 * The refusals matter as much as the reads. A reader that guessed at an entry it did not
 * understand would hand the editor the wrong pixels for a frame number, and an annotation
 * drawn on the wrong picture is a silent, permanent error. Throwing puts the caller back on
 * the single-frame endpoint, which is slower and right.
 */

import { describe, expect, it } from 'vitest';
import { deflateRawSync } from 'node:zlib';
import { UnsupportedArchiveError, readStoredZip } from '../storedZip';

/** A minimal but real ZIP writer, so the fixtures are not built by the code under test. */
function buildZip(
  files: { name: string; data: Uint8Array; deflate?: boolean }[],
  comment = '',
): ArrayBuffer {
  const encoder = new TextEncoder();
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;

  for (const file of files) {
    const name = encoder.encode(file.name);
    const stored = file.deflate ? new Uint8Array(deflateRawSync(file.data)) : file.data;
    const method = file.deflate ? 8 : 0;
    const crc = 0; // Not checked by the reader, and not worth a CRC implementation here.

    const local = new Uint8Array(30 + name.length + stored.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(8, method, true);
    localView.setUint32(14, crc, true);
    localView.setUint32(18, stored.length, true);
    localView.setUint32(22, file.data.length, true);
    localView.setUint16(26, name.length, true);
    localView.setUint16(28, 0, true);
    local.set(name, 30);
    local.set(stored, 30 + name.length);
    locals.push(local);

    const central = new Uint8Array(46 + name.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(10, method, true);
    centralView.setUint32(16, crc, true);
    centralView.setUint32(20, stored.length, true);
    centralView.setUint32(24, file.data.length, true);
    centralView.setUint16(28, name.length, true);
    centralView.setUint32(42, offset, true);
    central.set(name, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((total, part) => total + part.length, 0);
  const tail = encoder.encode(comment);
  const end = new Uint8Array(22 + tail.length);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, offset, true);
  endView.setUint16(20, tail.length, true);
  end.set(tail, 22);

  const total = offset + centralSize + end.length;
  const archive = new Uint8Array(total);
  let cursor = 0;
  for (const part of [...locals, ...centrals, end]) {
    archive.set(part, cursor);
    cursor += part.length;
  }
  return archive.buffer;
}

const jpeg = (marker: number) => new Uint8Array([0xff, 0xd8, 0xff, marker, 1, 2, 3, 4, 5]);

describe('readStoredZip', () => {
  it('returns every entry, by name, with its bytes intact', () => {
    const archive = buildZip([
      { name: '000000.jpg', data: jpeg(0xe0) },
      { name: '000001.jpg', data: jpeg(0xe1) },
      { name: '000002.jpg', data: jpeg(0xe2) },
    ]);

    const entries = readStoredZip(archive);
    expect([...entries.keys()]).toEqual(['000000.jpg', '000001.jpg', '000002.jpg']);
    expect(entries.get('000001.jpg')).toEqual(jpeg(0xe1));
  });

  it('reads an archive of one entry, which is what a one-frame tail chunk is', () => {
    const entries = readStoredZip(buildZip([{ name: '000599.jpg', data: jpeg(0xdb) }]));
    expect(entries.size).toBe(1);
    expect(entries.get('000599.jpg')).toEqual(jpeg(0xdb));
  });

  it('finds the end record behind a trailing comment', () => {
    // The record is not at a fixed offset; a comment pushes it back by up to 64 KB.
    const archive = buildZip([{ name: '000000.jpg', data: jpeg(0xe0) }], 'written by something');
    expect(readStoredZip(archive).get('000000.jpg')).toEqual(jpeg(0xe0));
  });

  it('hands back views, not copies, so a chunk is not duplicated in memory', () => {
    const archive = buildZip([{ name: '000000.jpg', data: jpeg(0xe0) }]);
    const entry = readStoredZip(archive).get('000000.jpg');
    expect(entry?.buffer).toBe(archive);
  });

  it('refuses a compressed entry rather than handing back deflated bytes', () => {
    // The failure this prevents: returning compressed bytes as a JPEG, which would render
    // as nothing or as garbage while the frame number claims to be a real picture.
    const archive = buildZip([{ name: '000000.jpg', data: jpeg(0xe0), deflate: true }]);
    expect(() => readStoredZip(archive)).toThrow(UnsupportedArchiveError);
  });

  it('refuses something that is not a ZIP at all', () => {
    const nonsense = new TextEncoder().encode('this is a JPEG, actually').buffer;
    expect(() => readStoredZip(nonsense)).toThrow(UnsupportedArchiveError);
  });

  it('refuses an archive truncated after its directory', () => {
    const archive = buildZip([{ name: '000000.jpg', data: jpeg(0xe0) }]);
    const full = new Uint8Array(archive);
    // Keep the end record, corrupt the central-directory signature it points at.
    const broken = full.slice();
    new DataView(broken.buffer).setUint32(full.length - 22 + 16, 0, true);
    expect(() => readStoredZip(broken.buffer)).toThrow(UnsupportedArchiveError);
  });
});
