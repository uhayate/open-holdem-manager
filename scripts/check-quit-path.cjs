// Drives the *real* electron/main.js quit path with stubbed Electron APIs.
//
// Why this exists: the shutdown work (commit db583e7) could not be verified
// through the GUI from this sandbox, so it shipped with three undeclared
// identifiers and the user's first close of the window popped
// "A JavaScript error occurred in the main process". A static check
// (scripts/check-electron.mjs) now covers the undeclared-name half; this covers
// the sequencing half: does closing the window actually ask the backend to
// exit, and does it avoid child.kill() on the way out?
//
// It stubs electron / electron-updater / http / child_process, loads main.js
// for real, then emits 'window-all-closed' and asserts on the outcome.
'use strict';

const Module = require('module');
const path = require('path');
const { EventEmitter } = require('events');

const ROOT = path.resolve(__dirname, '..');

const log = {
  shutdownUrls: [],
  kills: 0,
  quits: 0,
  quitCompleted: 0,
  prevented: 0,
  dialogs: [],
  thrown: [],
};

const failures = [];
function check(ok, label) {
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}`);
  if (!ok) failures.push(label);
}

// ---------------------------------------------------------------- fake backend
let fakeChild = null;

function makeFakeChild() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {
    log.kills++;
    child.emit('exit', null);
  };
  return child;
}

// ---------------------------------------------------------------- http stubs
function fakeHttpGet(url, cb) {
  const req = new EventEmitter();
  req.setTimeout = () => {};
  req.destroy = () => {};
  setImmediate(() => {
    const res = new EventEmitter();
    res.statusCode = 200;
    res.resume = () => {};
    cb(res);
  });
  return req;
}

function fakeHttpRequest(url, options, cb) {
  log.shutdownUrls.push(url);
  const req = new EventEmitter();
  req.setTimeout = () => {};
  req.destroy = () => {};
  req.end = () => {
    // The backend acknowledges and then exits on its own, like uvicorn would.
    setImmediate(() => {
      const res = new EventEmitter();
      res.resume = () => {};
      cb(res);
      res.emit('end');
      setImmediate(() => fakeChild && fakeChild.emit('exit', 0));
    });
  };
  return req;
}

// ---------------------------------------------------------------- electron stubs
const app = new EventEmitter();
app.isPackaged = true;
app.getVersion = () => '0.0.0-test';
app.getPath = () => path.join(ROOT, '.tmp-quit-test');
app.relaunch = () => {};
app.exit = () => {};

let readyResolve;
const readyPromise = new Promise((resolve) => { readyResolve = resolve; });
app.whenReady = () => readyPromise;

// Model Electron's real contract: app.quit() emits before-quit, and a
// preventDefault() there aborts the quit (the caller must call quit() again).
app.quit = () => {
  log.quits++;
  const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  app.emit('before-quit', event);
  if (event.defaultPrevented) {
    log.prevented++;
    return;
  }
  log.quitCompleted++;
};

class BrowserWindow {
  constructor() {
    this.webContents = { send() {}, openDevTools() {} };
    this.handlers = {};
  }
  loadURL() {}
  on(name, fn) {
    (this.handlers[name] ||= []).push(fn);
  }
  static getAllWindows() {
    return [];
  }
}

const electronStub = {
  app,
  BrowserWindow,
  dialog: { showErrorBox: (title, message) => log.dialogs.push(`${title}: ${message}`) },
  ipcMain: { handle: () => {} },
  shell: { openExternal() {} },
};

const updaterStub = {
  autoUpdater: {
    on() {},
    checkForUpdates: () => Promise.resolve(),
    quitAndInstall() {},
  },
};

// main.js reads process.resourcesPath (Electron-only) to locate the backend.
process.resourcesPath = path.join(ROOT, '.tmp-quit-test', 'resources');

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === 'electron') return electronStub;
  if (request === 'electron-updater') return updaterStub;
  if (request === 'http') return { get: fakeHttpGet, request: fakeHttpRequest };
  if (request === 'https') return { get: () => new EventEmitter() };
  if (request === 'child_process') {
    return {
      ...originalLoad.call(this, request, parent, isMain),
      spawn: () => (fakeChild = makeFakeChild()),
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

// ---------------------------------------------------------------- run
// Optional argv[2] lets you point it at a different copy of main.js (used to
// prove the harness actually catches the regression).
const target = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(ROOT, 'electron', 'main.js');
console.log(`main.js under test: ${target}\n`);
require(target);

function finish() {
  console.log('\n--- quit path report ---');
  console.log(`shutdown requests : ${JSON.stringify(log.shutdownUrls)}`);
  console.log(`child.kill() calls: ${log.kills}`);
  console.log(`app.quit() calls  : ${log.quits} (prevented ${log.prevented}, completed ${log.quitCompleted})`);
  if (log.dialogs.length) console.log(`error dialogs     : ${JSON.stringify(log.dialogs)}`);
  if (log.thrown.length) console.log(`thrown            : ${JSON.stringify(log.thrown)}`);
  console.log('');

  check(log.thrown.length === 0, 'closing the window throws nothing (no ReferenceError)');
  check(log.dialogs.length === 0, 'startup shows no error dialog');
  check(
    log.shutdownUrls.length === 1 && /^http:\/\/127\.0\.0\.1:\d+\/api\/shutdown$/.test(log.shutdownUrls[0]),
    'exactly one POST to /api/shutdown on the backend URL'
  );
  check(log.kills === 0, 'the graceful path is taken (child.kill() is not called)');
  check(log.prevented === 1, 'before-quit is deferred exactly once');
  check(log.quitCompleted === 1, 'the app actually quits');

  if (failures.length) {
    console.error(`\n${failures.length} quit-path check(s) failed`);
    process.exit(1);
  }
  console.log('\nquit path OK');
  // setupUpdateSystem() leaves a 4-hour setInterval behind, which would keep the
  // event loop alive forever. Nothing is pending at this point.
  process.exit(0);
}

setImmediate(() => {
  readyResolve();
  // Let whenReady's async body finish (findFreePort + startBackend + window).
  setTimeout(() => {
    try {
      // This is what the user does: close the window.
      app.emit('window-all-closed');
    } catch (err) {
      log.thrown.push(String(err));
    }
    setTimeout(finish, 500);
  }, 600);
});
