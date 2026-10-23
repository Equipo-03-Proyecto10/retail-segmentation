import { describe, expect, it, vi } from 'vitest';
import { latestRequest } from '../../src/renderer/latest-request';
import { deferred } from '../support/deferred';

describe('latest renderer request', () => {
  it('drops an old answer while the newest request is still pending', async () => {
    const run = latestRequest();
    const render = vi.fn();
    const older = deferred<string>();
    const newer = deferred<string>();
    const oldRun = run(() => older.promise, render);
    const newRun = run(() => newer.promise, render);
    older.resolve('old');
    await oldRun;
    expect(render).not.toHaveBeenCalled();
    newer.resolve('new');
    await newRun;
    expect(render).toHaveBeenCalledExactlyOnceWith('new');
  });

  it('invalidates a pending detail when a local form is selected', async () => {
    const run = latestRequest();
    const render = vi.fn();
    const response = deferred<string>();
    const pending = run(() => response.promise, render);
    const isCurrent = run.begin();
    response.resolve('old detail');
    await pending;
    expect(render).not.toHaveBeenCalled();
    expect(isCurrent()).toBe(true);
    run.begin();
    expect(isCurrent()).toBe(false);
  });

  it('keeps separate panels independent', async () => {
    const list = latestRequest();
    const detail = latestRequest();
    const render = vi.fn();
    const response = deferred<string>();
    const pending = list(() => response.promise, render);
    await detail(() => Promise.resolve('detail'), render);
    response.resolve('list');
    await pending;
    expect(render.mock.calls).toEqual([['detail'], ['list']]);
  });

  it('drops stale promise rejections but propagates a current rejection', async () => {
    const run = latestRequest();
    const render = vi.fn();
    let reject!: (reason: Error) => void;
    const response = new Promise<string>((_, fail) => {
      reject = fail;
    });
    const pending = run(() => response, render);
    await run(() => Promise.resolve('new'), render);
    reject(new Error('stale'));
    await expect(pending).resolves.toBeUndefined();
    await expect(run(() => Promise.reject(new Error('current')), render)).rejects.toThrow('current');
    expect(render).toHaveBeenCalledExactlyOnceWith('new');
  });

  it('renders the newest request', async () => {
    const run = latestRequest();
    const render = vi.fn();

    await run(() => Promise.resolve('first'), render);
    await run(() => Promise.resolve('second'), render);

    expect(render).toHaveBeenLastCalledWith('second');
  });

  it('drops an older request that answers late', async () => {
    const run = latestRequest();
    const render = vi.fn();
    const older = deferred<string>();
    const newer = deferred<string>();

    const olderRun = run(() => older.promise, render);
    const newerRun = run(() => newer.promise, render);
    newer.resolve('newer');
    await newerRun;
    older.resolve('older');
    await olderRun;

    expect(render).toHaveBeenCalledOnce();
    expect(render).toHaveBeenCalledWith('newer');
  });

  it('applies the same ordering rule to error results', async () => {
    const run = latestRequest();
    const render = vi.fn();
    const older = deferred<{ ok: false; error: string }>();

    const olderRun = run(() => older.promise, render);
    await run(() => Promise.resolve({ ok: false as const, error: 'new error' }), render);
    older.resolve({ ok: false, error: 'stale error' });
    await olderRun;

    expect(render).toHaveBeenCalledOnce();
    expect(render).toHaveBeenCalledWith({ ok: false, error: 'new error' });
  });
});
