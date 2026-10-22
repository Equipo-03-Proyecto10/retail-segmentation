export interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
}

/** A deterministic gate for fake responses that a test delivers out of order. */
export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
