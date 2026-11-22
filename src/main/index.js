import { app, BrowserWindow, ipcMain } from 'electron'
import { execFile } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { Engine } from './engine'

// Self-tests never touch the real history: the engine gets a temporary one
if (process.env.RECITEFLOW_SELFTEST && !process.env.RECITEFLOW_DB) {
  process.env.RECITEFLOW_DB = join(tmpdir(), 'reciteflow-selftest', 'history.db')
}
const engine = new Engine(join(app.getAppPath(), 'engine'))

// Run a command and return its first output line, or null when it is missing.
function firstLine(cmd, args) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: 10000, windowsHide: true }, (err, stdout, stderr) => {
      if (err) return resolve(null)
      const text = (stdout || stderr || '').trim()
      resolve(text.split(/\r?\n/)[0] || null)
    })
  })
}

async function systemInfo() {
  const [python, gpu] = await Promise.all([
    firstLine('py', ['-3.12', '--version']),
    firstLine('nvidia-smi', ['--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'])
  ])
  return {
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
    python: python ? python.replace(/^Python\s+/, '') : null,
    gpu
  }
}

// Window: 980x680, smallest 640x460.
function createWindow() {
  const win = new BrowserWindow({
    width: 980,
    height: 680,
    minWidth: 640,
    minHeight: 460,
    title: 'ReciteFlow',
    icon: join(app.getAppPath(), 'build', 'icon.ico'),
    show: false,
    backgroundColor: '#0B1220',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  })
  win.once('ready-to-show', () => win.show())
  if (process.env.ELECTRON_RENDERER_URL) win.loadURL(process.env.ELECTRON_RENDERER_URL)
  else win.loadFile(join(__dirname, '../renderer/index.html'))
  return win
}

app.whenReady().then(async () => {
  const info = systemInfo()
  ipcMain.handle('system-info', () => info)
  ipcMain.handle('engine-status', () => engine.status)
  ipcMain.handle('engine-restart', () => engine.restart())
  // Recitation: each call answers the engine's JSON or { error }
  ipcMain.handle('devices', () => engine.call('/devices'))
  ipcMain.handle('recite-start', (_e, options) => engine.call('/recite', options))
  ipcMain.handle('recite-stop', () => engine.call('/recite/stop', {}))
  ipcMain.handle('correct', (_e, recId, pos, state) => engine.call('/corrections', { rec_id: recId, pos, state }))
  ipcMain.handle('settings-get', () => engine.call('/settings'))
  ipcMain.handle('settings-set', (_e, settings) => engine.call('/settings', settings))
  const win = createWindow()
  const send = (status) => { if (!win.isDestroyed()) win.webContents.send('engine-status', status) }
  const forward = (event, data) => {
    if (['frame', 'done', 'error'].includes(event) && !win.isDestroyed()) win.webContents.send('recite-' + event, data)
  }
  engine.on('status', send)
  engine.on('event', forward)
  win.on('closed', () => { engine.off('status', send); engine.off('event', forward) })
  engine.on('status', (s) => console.log('[reciteflow] engine:', s.status, s.gpu || '',
    s.loadSeconds != null ? `model loaded in ${s.loadSeconds} s` : '', s.message || ''))
  engine.start()
  console.log('[reciteflow] system info:', await info)
  // RECITEFLOW_SELFTEST=engine: the engine check (src/main/selftest.js)
  if (process.env.RECITEFLOW_SELFTEST === 'engine') return (await import('./selftest')).run(win, engine)
  // RECITEFLOW_SELFTEST=1: print what the page shows, save a picture of it in test_out/, then quit
  if (process.env.RECITEFLOW_SELFTEST) {
    win.webContents.once('did-finish-load', () => setTimeout(async () => {
      console.log('[reciteflow] page:', await win.webContents.executeJavaScript('document.body.innerText'))
      const shot = join(app.getAppPath(), 'test_out', 't01_window.png')
      mkdirSync(dirname(shot), { recursive: true })
      writeFileSync(shot, (await win.webContents.capturePage()).toPNG())
      console.log('[reciteflow] picture:', shot)
      app.quit()
    }, 1000))
  }
})

app.on('window-all-closed', () => app.quit())

// Stop the engine before the app ends, so no python.exe is left behind.
let stopped = false
app.on('before-quit', (event) => {
  if (stopped) return
  event.preventDefault()
  engine.stop().then(() => { stopped = true; app.quit() })
})
