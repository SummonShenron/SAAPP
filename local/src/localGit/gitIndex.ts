// Reading and writing git's index (".git/index"), the part of a repository that records what is staged. Versions 2 and 3 only: version 4, a split index, or an index with a conflict in it is refused
// (the caller then falls back to telling the person the commands). Extensions (the tree cache, resolve-undo, untracked cache) are dropped on writing: git rebuilds them on its own.
import { bytesToHex, concat, hexToBytes, sha1Hex } from './objects.ts';

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8');

export interface IndexEntry {
  ctimeSec: number;
  ctimeNsec: number;
  mtimeSec: number;
  mtimeNsec: number;
  dev: number;
  ino: number;
  mode: number;        // 0o100644 and so on
  uid: number;
  gid: number;
  size: number;
  sha: string;
  flags: number;       // 12 bits of name length, 2 of stage, assume-valid, extended
  extendedFlags: number | null;
  path: string;
}

export interface ParsedIndex {
  version: number;
  entries: IndexEntry[];
}

export class IndexRefused extends Error {}

const u32 = (view: DataView, offset: number) => view.getUint32(offset, false);

export function parseIndex(bytes: Uint8Array): ParsedIndex {
  if (bytes.length < 32) throw new IndexRefused('the index is too short to read');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (decoder.decode(bytes.subarray(0, 4)) !== 'DIRC') throw new IndexRefused('the index is not in a format this can read');
  const version = u32(view, 4);
  if (version !== 2 && version !== 3) throw new IndexRefused(`the index is version ${version}; only versions 2 and 3 are handled`);
  const count = u32(view, 8);
  const entries: IndexEntry[] = [];
  let offset = 12;
  for (let i = 0; i < count; i++) {
    if (offset + 62 > bytes.length - 20) throw new IndexRefused('the index ends in the middle of an entry');
    const flags = view.getUint16(offset + 60, false);
    const extended = (flags & 0x4000) !== 0;
    if (extended && version < 3) throw new IndexRefused('the index has extended flags in a version 2 file');
    if (((flags >> 12) & 3) !== 0) throw new IndexRefused('the index records a merge conflict, so it is left alone');
    const fixed = extended ? 64 : 62;
    const extendedFlags = extended ? view.getUint16(offset + 62, false) : null;
    let end = offset + fixed;
    while (end < bytes.length && bytes[end] !== 0) end++;
    const path = decoder.decode(bytes.subarray(offset + fixed, end));
    entries.push({
      ctimeSec: u32(view, offset), ctimeNsec: u32(view, offset + 4), mtimeSec: u32(view, offset + 8), mtimeNsec: u32(view, offset + 12), dev: u32(view, offset + 16), ino: u32(view, offset + 20),
      mode: u32(view, offset + 24), uid: u32(view, offset + 28), gid: u32(view, offset + 32), size: u32(view, offset + 36), sha: bytesToHex(bytes.subarray(offset + 40, offset + 60)),
      flags, extendedFlags, path,
    });
    offset += entrySize(extended, end - offset - fixed);      // the entry (header, name, at least one NUL) padded to a multiple of 8 bytes in size
  }
  // Extensions follow the entries, then the 20-byte checksum. A split index points at another file this cannot follow.
  while (offset + 8 <= bytes.length - 20) {
    const signature = decoder.decode(bytes.subarray(offset, offset + 4));
    if (signature === 'link') throw new IndexRefused('the repository uses a split index');
    offset += 8 + u32(view, offset + 4);
  }
  return { version, entries };
}

/** The padded size of an entry on disk: header, name and at least one NUL, rounded up to a multiple of 8. */
function entrySize(extended: boolean, nameBytes: number): number {
  return ((extended ? 64 : 62) + nameBytes + 8) & ~7;
}

function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

export async function serializeIndex(index: ParsedIndex): Promise<Uint8Array> {
  const named = index.entries.map(entry => ({ entry, name: encoder.encode(entry.path) }));
  named.sort((a, b) => compareBytes(a.name, b.name));
  const version = named.some(({ entry }) => entry.extendedFlags !== null) ? 3 : 2;
  const head = new Uint8Array(12);
  const headView = new DataView(head.buffer);
  head.set(encoder.encode('DIRC'), 0);
  headView.setUint32(4, version, false);
  headView.setUint32(8, named.length, false);
  const parts: Uint8Array[] = [head];
  for (const { entry, name } of named) {
    const extended = entry.extendedFlags !== null;
    const size = entrySize(extended, name.length);
    const buffer = new Uint8Array(size);
    const view = new DataView(buffer.buffer);
    [entry.ctimeSec, entry.ctimeNsec, entry.mtimeSec, entry.mtimeNsec, entry.dev, entry.ino, entry.mode, entry.uid, entry.gid, entry.size].forEach((value, i) => view.setUint32(i * 4, value >>> 0, false));
    buffer.set(hexToBytes(entry.sha), 40);
    const flags = (entry.flags & 0xf000) | Math.min(name.length, 0xfff);
    view.setUint16(60, flags, false);
    if (extended) view.setUint16(62, entry.extendedFlags as number, false);
    buffer.set(name, extended ? 64 : 62);                      // the rest of the entry is already zero: the NUL padding
    parts.push(buffer);
  }
  const body = concat(parts);
  const checksum = new Uint8Array(20);
  checksum.set(hexToBytes(await sha1Hex(body)));
  return concat([body, checksum]);
}

/** A new entry for a file whose content is the blob `sha`. When the file on disk is known to hold exactly that content, its size and times are recorded so git sees it as unchanged without reading it;
 *  otherwise the times are zero, which makes git read the file and compare, and show a difference if there is one. */
export function newEntry(path: string, mode: number, sha: string, blobSize: number, onDisk: { size: number; mtimeMs: number } | null): IndexEntry {
  const seconds = onDisk ? Math.floor(onDisk.mtimeMs / 1000) : 0;
  const nanos = onDisk ? Math.floor((onDisk.mtimeMs % 1000) * 1_000_000) : 0;
  return { ctimeSec: seconds, ctimeNsec: nanos, mtimeSec: seconds, mtimeNsec: nanos, dev: 0, ino: 0, mode, uid: 0, gid: 0, size: onDisk ? onDisk.size : blobSize, sha, flags: 0, extendedFlags: null, path };
}

/** The index with these entries added or replaced (matched by path). Entries for other paths are kept exactly as they were. */
export function withEntries(index: ParsedIndex, entries: IndexEntry[]): ParsedIndex {
  const replaced = new Set(entries.map(entry => entry.path));
  return { version: index.version, entries: [...index.entries.filter(entry => !replaced.has(entry.path)), ...entries] };
}
