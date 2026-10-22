import { copyFileSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, 'renderer'), { recursive: true });

const common = { bundle: true, sourcemap: false, logLevel: 'info', target: 'node20' };
// The sandboxed preload may require only electron, so everything else is bundled in.
await build({
  ...common,
  entryPoints: [join(root, 'src/main/main.ts')],
  outfile: join(dist, 'main.js'),
  platform: 'node',
  format: 'cjs',
  external: ['electron', 'xmllint-wasm'],
});
await build({
  ...common,
  entryPoints: [join(root, 'src/preload/preload.ts')],
  outfile: join(dist, 'preload.js'),
  platform: 'node',
  format: 'cjs',
  external: ['electron'],
});
await build({
  ...common,
  entryPoints: [join(root, 'src/renderer/app.ts')],
  outfile: join(dist, 'renderer', 'app.js'),
  platform: 'browser',
  format: 'iife',
  target: 'chrome140',
});
for (const f of ['index.html', 'styles.css'])
  copyFileSync(join(root, 'src/renderer', f), join(dist, 'renderer', f));
