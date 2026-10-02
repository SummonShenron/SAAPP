// Minimal IndexedDB key-value helpers. Every function swallows IndexedDB failures (private
// windows, blocked storage, quota) and resolves null/[] instead, because everything stored here is
// a convenience — the folder handle, the undo history — never state the app can't work without.

function openDb(dbName: string, stores: string[]): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => {
      for (const store of stores) {
        if (!request.result.objectStoreNames.contains(store)) request.result.createObjectStore(store);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(
  dbName: string,
  stores: string[],
  store: string,
  mode: IDBTransactionMode,
  op: (objectStore: IDBObjectStore) => IDBRequest<T>,
): Promise<T | null> {
  try {
    const db = await openDb(dbName, stores);
    return await new Promise<T | null>((resolve) => {
      const request = op(db.transaction(store, mode).objectStore(store));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
    });
  } catch {
    return null;
  }
}

export interface KeyValueStore {
  get<T>(key: string): Promise<T | null>;
  put(key: string, value: unknown): Promise<void>;
  remove(key: string): Promise<void>;
  /** Every value in the store. */
  all<T>(): Promise<T[]>;
}

export function keyValueStore(dbName: string, store: string): KeyValueStore {
  const stores = [store];
  return {
    async get<T>(key: string) {
      const value = await run<T | undefined>(dbName, stores, store, 'readonly', s => s.get(key));
      return value ?? null;
    },
    async put(key: string, value: unknown) {
      await run(dbName, stores, store, 'readwrite', s => s.put(value, key));
    },
    async remove(key: string) {
      await run(dbName, stores, store, 'readwrite', s => s.delete(key));
    },
    async all<T>() {
      return (await run<T[]>(dbName, stores, store, 'readonly', s => s.getAll())) ?? [];
    },
  };
}
