// The small slice of a file system the git import needs, with paths relative to the repository's root ("/" separated). The browser version sits on a folder the person chose
// (File System Access API); the tests use the real disk. Nothing in here knows what git is.
import type { FsDirectoryHandle, FsFileHandle } from '../localWorkspace.ts';

export interface GitFs {
  /** The file's bytes, or null when there is no such file. */
  read(path: string): Promise<Uint8Array | null>;
  /** Writes the file, making its folders first. */
  write(path: string, data: Uint8Array): Promise<void>;
  exists(path: string): Promise<boolean>;
  /** Deletes the file; nothing happens when it is not there. */
  remove(path: string): Promise<void>;
  stat(path: string): Promise<{ size: number; mtimeMs: number } | null>;
  /** The names in a folder, or [] when there is no such folder. */
  list(path: string): Promise<string[]>;
}

const parts = (path: string) => path.split('/').filter(Boolean);

async function walkTo(root: FsDirectoryHandle, folders: string[]): Promise<FsDirectoryHandle | null> {
  let current = root;
  for (const name of folders) {
    try {
      current = await current.getDirectoryHandle(name);
    } catch {
      return null;
    }
  }
  return current;
}

export function folderGitFs(root: FsDirectoryHandle): GitFs {
  const fileAt = async (path: string): Promise<FsFileHandle | null> => {
    const names = parts(path);
    const dir = await walkTo(root, names.slice(0, -1));
    if (!dir) return null;
    try {
      return await dir.getFileHandle(names[names.length - 1]);
    } catch {
      return null;
    }
  };
  return {
    async read(path) {
      const handle = await fileAt(path);
      return handle ? new Uint8Array(await (await handle.getFile()).arrayBuffer()) : null;
    },
    async write(path, data) {
      const names = parts(path);
      let dir = root;
      for (const name of names.slice(0, -1)) dir = await dir.getDirectoryHandle(name, { create: true });
      const handle = await dir.getFileHandle(names[names.length - 1], { create: true });
      const writable = await handle.createWritable();
      await writable.write(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer);
      await writable.close();
    },
    async exists(path) {
      if (await fileAt(path)) return true;
      return (await walkTo(root, parts(path))) !== null;
    },
    async remove(path) {
      const names = parts(path);
      const dir = await walkTo(root, names.slice(0, -1));
      if (!dir) return;
      try {
        await dir.removeEntry(names[names.length - 1]);
      } catch {
        /* not there */
      }
    },
    async stat(path) {
      const handle = await fileAt(path);
      if (!handle) return null;
      const file = await handle.getFile();
      return { size: file.size, mtimeMs: file.lastModified };
    },
    async list(path) {
      const dir = await walkTo(root, parts(path));
      if (!dir) return [];
      const names: string[] = [];
      for await (const entry of dir.values()) names.push(entry.name);
      return names;
    },
  };
}
