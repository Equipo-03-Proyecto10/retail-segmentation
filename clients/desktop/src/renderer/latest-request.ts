interface LatestRequest {
  <T>(request: () => Promise<T>, render: (result: T) => void): Promise<void>;
  /** Start an action, or invalidate pending work when selecting a local form. */
  begin(): () => boolean;
}

/** One instance per panel; bridge success and error results use the same guard. */
export function latestRequest(): LatestRequest {
  let newest = 0;
  const begin = (): (() => boolean) => {
    const current = ++newest;
    return () => current === newest;
  };
  const run = async <T>(request: () => Promise<T>, render: (result: T) => void): Promise<void> => {
    const isCurrent = begin();
    let result: T;
    try {
      result = await request();
    } catch (error) {
      if (isCurrent()) throw error;
      return;
    }
    if (isCurrent()) render(result);
  };
  return Object.assign(run, { begin });
}
