// Git objects, built byte for byte the way git builds them, so an object made here has the same id git would give it. Pure and dependency-free: only Web Crypto and
// CompressionStream, which browsers and Node both have. The spike that uses it is described in docs/local-git-spike.md.

const encoder = new TextEncoder();

export function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (const byte of bytes) out += byte.toString(16).padStart(2, '0');
  return out;
}

export function hexToBytes(hex: string): Uint8Array {
  if (!/^[0-9a-f]{40}$/.test(hex)) throw new Error('not a 40-character object id');
  const out = new Uint8Array(20);
  for (let i = 0; i < 20; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

export async function sha1Hex(data: Uint8Array): Promise<string> {
  const copy = new Uint8Array(data);                          // a plain ArrayBuffer-backed copy, whatever the input was a view of
  return bytesToHex(new Uint8Array(await crypto.subtle.digest('SHA-1', copy)));
}

export type ObjectType = 'blob' | 'tree' | 'commit';

/** The bytes git hashes and stores for an object: "<type> <size>\0<body>". */
export function objectBytes(type: ObjectType, body: Uint8Array): Uint8Array {
  return concat([encoder.encode(`${type} ${body.length}\0`), body]);
}

export function objectId(type: ObjectType, body: Uint8Array): Promise<string> {
  return sha1Hex(objectBytes(type, body));
}

/** zlib (RFC 1950) compression, the format of a loose object file. */
export async function deflateZlib(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([new Uint8Array(data)]).stream().pipeThrough(new CompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export interface TreeEntry {
  mode: string;      // as GitHub reports it: "100644", "100755", "120000", "160000", "040000"
  name: string;
  sha: string;
}

/** A tree object's body: "<mode> <name>\0<20-byte id>" for each entry, in the order given (GitHub lists them in git's order). A directory's mode has no leading zero in the object. */
export function serializeTree(entries: TreeEntry[]): Uint8Array {
  return concat(entries.map(entry => concat([encoder.encode(`${entry.mode.replace(/^0+/, '')} ${entry.name}\0`), hexToBytes(entry.sha)])));
}

export interface Person {
  name: string;
  email: string;
  timestamp: number;     // seconds since 1970
  offset: string;        // "+0000"
}

export interface CommitFields {
  tree: string;
  parents: string[];
  author: Person;
  committer: Person;
  message: string;
  signature?: string | null;
}

const person = (p: Person) => `${p.name} <${p.email}> ${p.timestamp} ${p.offset}`;

/** A commit object's body: the headers, a blank line, then the message exactly as given (no newline is added). */
export function serializeCommit(commit: CommitFields): Uint8Array {
  const lines = [`tree ${commit.tree}`, ...commit.parents.map(parent => `parent ${parent}`), `author ${person(commit.author)}`, `committer ${person(commit.committer)}`];
  if (commit.signature) lines.push('gpgsig ' + commit.signature.split('\n').map((line, index) => (index ? ` ${line}` : line)).join('\n'));
  return encoder.encode(`${lines.join('\n')}\n\n${commit.message}`);
}

/** An ISO 8601 date as GitHub's API gives it ("2026-10-25T20:10:21Z" or with "+05:30") as git's seconds and "+hhmm" offset. */
export function parseIsoDate(iso: string): { timestamp: number; offset: string } {
  const match = /(Z|([+-])(\d{2}):?(\d{2}))$/.exec(iso);
  const ms = Date.parse(iso);
  if (!match || Number.isNaN(ms)) throw new Error('unreadable commit date');
  return { timestamp: Math.floor(ms / 1000), offset: match[1] === 'Z' ? '+0000' : `${match[2]}${match[3]}${match[4]}` };
}
