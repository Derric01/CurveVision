/**
 * Reading the frame archives the server builds.
 *
 * A chunk is a ZIP of JPEGs written with `ZIP_STORED` — no compression, because JPEG is
 * already compressed and deflating it spends CPU to grow the file. That makes reading one
 * pure container parsing: every entry's bytes sit verbatim in the archive, so there is
 * nothing to inflate and no decompression library to ship.
 *
 * We control both ends of this format, which is what makes a partial reader honest rather
 * than lazy. It still refuses what it does not understand: an entry with any compression
 * method other than "stored", or an archive it cannot find a central directory in, throws,
 * and the caller falls back to fetching frames one at a time. Being wrong about a frame's
 * pixels is far worse than being slow.
 *
 * Format reference: PKWARE APPNOTE, sections 4.3.12 (central directory) and 4.3.7 (local
 * file header). Only the fields this needs are read.
 */

const END_OF_CENTRAL_DIRECTORY = 0x06054b50;
const CENTRAL_FILE_HEADER = 0x02014b50;
const LOCAL_FILE_HEADER = 0x04034b50;
const STORED = 0;

/** The largest a ZIP end-of-central-directory record can be: 22 fixed bytes + 64 KB comment. */
const MAX_END_RECORD = 22 + 0xffff;

export class UnsupportedArchiveError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UnsupportedArchiveError';
  }
}

/** Entry name → its bytes. Names are exactly as the server wrote them. */
export type ArchiveEntries = Map<string, Uint8Array>;

function findEndRecord(view: DataView): number {
  // The record is at the end, but a trailing comment can push it back by up to 64 KB, so
  // it has to be searched for rather than assumed.
  const earliest = Math.max(0, view.byteLength - MAX_END_RECORD);
  for (let offset = view.byteLength - 22; offset >= earliest; offset -= 1) {
    if (view.getUint32(offset, true) === END_OF_CENTRAL_DIRECTORY) return offset;
  }
  throw new UnsupportedArchiveError('not a ZIP archive: no end-of-central-directory record');
}

/**
 * Every entry in a stored ZIP, as a map from name to bytes.
 *
 * The returned arrays are **views onto `archive`**, not copies: a chunk is a few megabytes
 * and copying every frame out of it would double that for no reason. They stay valid as
 * long as the buffer does, which for a cached chunk is exactly as long as it is wanted.
 */
export function readStoredZip(archive: ArrayBuffer): ArchiveEntries {
  const view = new DataView(archive);
  const bytes = new Uint8Array(archive);
  const end = findEndRecord(view);

  const count = view.getUint16(end + 10, true);
  let offset = view.getUint32(end + 16, true);

  const entries: ArchiveEntries = new Map();
  for (let index = 0; index < count; index += 1) {
    if (view.getUint32(offset, true) !== CENTRAL_FILE_HEADER) {
      throw new UnsupportedArchiveError(`corrupt central directory at entry ${index}`);
    }
    const method = view.getUint16(offset + 10, true);
    const size = view.getUint32(offset + 20, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const localOffset = view.getUint32(offset + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(offset + 46, offset + 46 + nameLength));

    if (method !== STORED) {
      throw new UnsupportedArchiveError(`${name}: compression method ${method} is not supported`);
    }
    if (view.getUint32(localOffset, true) !== LOCAL_FILE_HEADER) {
      throw new UnsupportedArchiveError(`${name}: corrupt local header`);
    }

    // The local header repeats the name and extra-field lengths, and they can differ from
    // the central directory's — the data starts after the *local* ones.
    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    if (start + size > bytes.byteLength) {
      throw new UnsupportedArchiveError(`${name}: entry runs past the end of the archive`);
    }

    entries.set(name, bytes.subarray(start, start + size));
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}
