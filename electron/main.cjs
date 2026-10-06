const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8765';
let ownedBackend = null;
let quitting = false;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function probe() {
  try {
    const response = await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(1200) });
    if (!response.ok) throw new Error('Port 8765 is occupied by an unrelated service.');
    const health = await response.json();
    if (health.service !== 'kanban-agent-board' || health.version !== '0.1.0') {
      throw new Error('Port 8765 is occupied by an unrelated or incompatible service.');
    }
    return true;
  } catch (error) {
    if (error.cause?.code === 'ECONNREFUSED') return false;
    throw error;
  }
}

async function backend() {
  if (await probe()) return;
  const python = process.env.KANBAN_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!fs.existsSync(python)) throw new Error('Project Python was not found. Run uv sync or set KANBAN_PYTHON to an interpreter with the project installed.');
  if (!fs.existsSync(path.join(root, 'frontend/out/index.html'))) throw new Error('The frontend is not built. Run npm run build first.');
  const args = ['-m', 'kanban'];
  if (process.env.KANBAN_DATA_DIR) args.push('--data-dir', path.resolve(process.env.KANBAN_DATA_DIR));
  ownedBackend = spawn(python, args, { cwd: root, windowsHide: true, stdio: 'ignore', env: process.env });
  let spawnError = false;
  ownedBackend.once('error', () => { spawnError = true; });
  for (let attempt = 0; attempt < 100; attempt++) {
    await sleep(200);
    if (spawnError || ownedBackend.exitCode !== null || ownedBackend.signalCode !== null) throw new Error('The Python backend could not start. Check the interpreter, data directory, and port.');
    if (await probe()) return;
  }
  throw new Error('The Python backend did not become ready within 20 seconds.');
}

async function stopOwnedBackend() {
  const child = ownedBackend;
  ownedBackend = null;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  for (let i = 0; i < 50 && child.exitCode === null && child.signalCode === null; i++) await sleep(200);
  // An interrupted journal entry makes restart stop for inspection.
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
}

function createWindow() {
  const window = new BrowserWindow({ width: 1500, height: 960, minWidth: 900, minHeight: 650, title: 'Kanban Agent Board',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (new URL(url).origin !== origin) event.preventDefault(); });
  window.loadURL(origin);
}

ipcMain.handle('select-repository', async event => {
  if (!event.senderFrame || new URL(event.senderFrame.url).origin !== origin) throw new Error('Untrusted frame');
  const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), { title: 'Select a Git repository with an initial commit', properties: ['openDirectory'] });
  return result.canceled ? null : result.filePaths[0];
});

app.on('before-quit', event => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  stopOwnedBackend().finally(() => app.quit());
});
app.on('window-all-closed', () => app.quit());
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

app.whenReady().then(async () => {
  try { await backend(); createWindow(); }
  catch (error) { dialog.showErrorBox('Kanban could not start', error.message); app.quit(); }
});
