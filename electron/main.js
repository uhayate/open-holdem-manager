const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const net = require('net');
const http = require('http');

let backendProcess = null;
let mainWindow = null;
// Where the backend is listening (production). Needed to ask it to exit before we
// quit -- that is what lets DuckDB checkpoint and drop its WAL.
let backendUrl = null;
// before-quit guard: the first pass preventDefault()s and stops the backend, the
// second (once stopBackend has resolved) is allowed to go through.
let quitting = false;

const SHUTDOWN_TIMEOUT_MS = 15000;

const isDev = !app.isPackaged;

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      // address() is typed `string | AddressInfo`; for a TCP server it is always
      // the object form. The cast keeps `npm run check:electron` clean.
      const port = /** @type {import('net').AddressInfo} */ (server.address()).port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

function getResourcePath(...parts) {
  if (isDev) {
    return path.join(__dirname, '..', ...parts);
  }
  return path.join(process.resourcesPath, ...parts);
}

function startBackend(port) {
  if (isDev) {
    // In dev mode, backend is started separately (make dev / make backend)
    return Promise.resolve();
  }

  const backendExe = process.platform === 'win32'
    ? getResourcePath('backend', 'ohm-backend.exe')
    : getResourcePath('backend', 'ohm-backend');

  const dataDir = path.join(app.getPath('userData'), 'data');
  const staticDir = getResourcePath('frontend');

  const env = {
    ...process.env,
    OHM_DATA_DIR: dataDir,
    OHM_STATIC_DIR: staticDir,
  };

  return new Promise((resolve, reject) => {
    backendProcess = spawn(backendExe, ['--port', String(port)], {
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    backendProcess.stdout.on('data', (data) => {
      console.log(`[backend] ${data.toString().trim()}`);
    });

    backendProcess.stderr.on('data', (data) => {
      console.error(`[backend] ${data.toString().trim()}`);
    });

    backendProcess.on('error', (err) => {
      reject(new Error(`Failed to start backend: ${err.message}`));
    });

    backendProcess.on('exit', (code) => {
      if (code !== 0 && code !== null) {
        console.error(`Backend exited with code ${code}`);
      }
      backendProcess = null;
    });

    // Give the process a moment to fail on startup errors
    setTimeout(resolve, 200);
  });
}

function waitForBackend(url, maxRetries = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    function tryConnect() {
      attempts++;
      const req = http.get(`${url}/api/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else if (attempts < maxRetries) {
          setTimeout(tryConnect, 500);
        } else {
          reject(new Error(`Backend returned status ${res.statusCode}`));
        }
        res.resume(); // consume response
      });

      req.on('error', () => {
        if (attempts < maxRetries) {
          setTimeout(tryConnect, 500);
        } else {
          reject(new Error('Backend did not start in time'));
        }
      });

      req.setTimeout(2000, () => {
        req.destroy();
        if (attempts < maxRetries) {
          setTimeout(tryConnect, 500);
        } else {
          reject(new Error('Backend health check timed out'));
        }
      });
    }

    tryConnect();
  });
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    title: 'Open Holdem Manager',
    backgroundColor: '#0c0a09',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadURL(url);

  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function killBackend() {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

// Ask the backend to exit on its own. Uvicorn then finishes the request, runs
// its ASGI shutdown handler (close_db()), and exits — DuckDB checkpoints and
// removes its WAL on the way out.
function requestBackendShutdown() {
  return new Promise((resolve) => {
    if (!backendUrl) return resolve();
    const req = http.request(
      `${backendUrl}/api/shutdown`,
      {
        method: 'POST',
        // Don't reuse a socket we are about to lose, and don't let a lingering
        // keep-alive connection hold the server open.
        agent: false,
        headers: { 'Content-Length': 0, Connection: 'close' },
        timeout: 2000,
      },
      (res) => {
        res.resume();
        res.on('end', resolve);
      }
    );
    req.on('error', resolve);  // already gone — nothing to wait for
    req.on('timeout', () => { req.destroy(); resolve(); });
    req.end();
  });
}

// Stop the backend cleanly, falling back to a kill only if it will not go.
async function stopBackend() {
  if (!backendProcess) return;

  const child = backendProcess;
  const exited = new Promise((resolve) => child.once('exit', resolve));

  await requestBackendShutdown();

  let timer;
  const timedOut = await Promise.race([
    exited.then(() => false),
    new Promise((resolve) => { timer = setTimeout(() => resolve(true), SHUTDOWN_TIMEOUT_MS); }),
  ]);
  clearTimeout(timer);

  if (timedOut && backendProcess) {
    console.warn('Backend did not exit in time; killing it (WAL may be left behind)');
    killBackend();
  }
}

app.whenReady().then(async () => {
  try {
    let url;

    if (isDev) {
      // Dev: Vite dev server should already be running
      url = 'http://localhost:4242';
    } else {
      // Production: start backend on a random free port
      const port = await findFreePort();
      await startBackend(port);
      backendUrl = `http://127.0.0.1:${port}`;
      url = backendUrl;
      await waitForBackend(url);
    }

    createWindow(url);
  } catch (err) {
    console.error('Startup error:', err);
    dialog.showErrorBox(
      'Open Holdem Manager - Startup Error',
      `Failed to start the application.\n\n${err.message}`
    );
    killBackend();
    app.quit();
  }
});

app.on('window-all-closed', () => {
  // Deliberately no killBackend() here: before-quit owns the shutdown, and it
  // gives the backend a chance to checkpoint DuckDB on the way out.
  app.quit();
});

app.on('before-quit', (event) => {
  if (isDev || quitting) {
    // Second pass (or dev, where the backend is managed by whoever started it).
    killBackend();
    return;
  }
  event.preventDefault();
  quitting = true;
  // Always reach app.quit() -- a throw or rejection in here must not wedge the
  // app in a state where the window is gone but the process never exits.
  stopBackend()
    .catch((err) => console.error('stopBackend failed:', err))
    .finally(() => app.quit());
});

// macOS: re-create window when dock icon clicked
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && !isDev) {
    // Would need to track the port — for now just quit+relaunch
  }
});
