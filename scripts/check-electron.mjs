// Static check for electron/*.js.
//
// Why this exists: commit db583e7 shipped a quit path that referenced three
// identifiers it never declared (`quitting`, `backendUrl`, `SHUTDOWN_TIMEOUT_MS`).
// Nothing caught it -- `node --check` only parses, the Python tests never touch
// Electron, and the path only fires when the window is actually closed, so the
// first sign was a "A JavaScript error occurred in the main process" dialog for
// the user. tsc with checkJs reports those as TS2304 ("Cannot find name ..."),
// which is exactly the class of bug we want gated.
//
// Runs before every build (see package.json). Requires frontend/node_modules.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const tsc = path.join(root, 'frontend', 'node_modules', 'typescript', 'bin', 'tsc');

if (!existsSync(tsc)) {
  console.error(`typescript not found at ${tsc} -- run "cd frontend && npm install" first`);
  process.exit(1);
}

const files = ['electron/main.js', 'electron/preload.js'].map((f) => path.join(root, f));

const res = spawnSync(
  process.execPath,
  [
    tsc,
    '--noEmit',
    '--allowJs',
    '--checkJs',
    '--target', 'es2022',
    '--module', 'commonjs',
    '--moduleResolution', 'node',
    '--skipLibCheck',
    '--types', 'node',
    '--lib', 'es2022',
    ...files,
  ],
  { encoding: 'utf8' }
);

const output = `${res.stdout ?? ''}${res.stderr ?? ''}`.trim();
if (output) {
  console.error(output);
  console.error('\nelectron/ failed type checking');
  process.exit(1);
}

console.log('electron/ OK');
