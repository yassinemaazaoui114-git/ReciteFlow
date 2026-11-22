import { app, BrowserWindow, ipcMain, nativeTheme } from 'electron'
import { execFile } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { Engine } from './engine'

// Self-tests never touch the real history: the engine gets a temporary one
if (process.env.RECITEFLOW_SELFTEST && !process.env.RECITEFLOW_DB) {
  process.env.RECITEFLOW_DB = join(tmpdir(), 'reciteflow-selftest', 'history.db')
}
// The corrections check saves its replay (into that temporary history), so it can be corrected
if (process.env.RECITEFLOW_SELFTEST === 'correct') process.env.RECITEFLOW_SAVE_REPLAY = '1'
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

// Dev replay, as the old --file / --wav: RECITEFLOW_FILE = a UTF-8 text put in the box, RECITEFLOW_WAV = a recording
// replayed in real time instead of the microphone (saved only with RECITEFLOW_SAVE_REPLAY=1).
function devOptions() {
  const file = process.env.RECITEFLOW_FILE
  const wav = process.env.RECITEFLOW_WAV
  let text = null
  if (file) {
    try { text = readFileSync(resolve(file), 'utf8').replace(/^﻿/, '') } catch { console.log('[reciteflow] cannot read', file) }
  }
  return { text, wav: wav ? resolve(wav) : null }
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
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0D1424' : '#F8FAFC',
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

// RECITEFLOW_PAGE=design: the design sheet, no engine. With RECITEFLOW_SELFTEST=design it saves a full-page
// picture of each theme in test_out/t1.1/ and quits.
function openDesignSheet() {
  const win = new BrowserWindow({
    width: 1120, height: 820, title: 'ReciteFlow design sheet', icon: join(app.getAppPath(), 'build', 'icon.ico'),
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0D1424' : '#F8FAFC', autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false }
  })
  if (process.env.ELECTRON_RENDERER_URL) win.loadURL(process.env.ELECTRON_RENDERER_URL + '/design.html')
  else win.loadFile(join(__dirname, '../renderer/design.html'))
  if (process.env.RECITEFLOW_SELFTEST !== 'design') return
  win.webContents.once('did-finish-load', () => setTimeout(async () => {
    const outDir = join(app.getAppPath(), 'test_out', 't1.1')
    mkdirSync(outDir, { recursive: true })
    const height = await win.webContents.executeJavaScript('document.documentElement.scrollHeight')
    win.setContentSize(1120, Math.min(height, 6000))
    for (const theme of ['light', 'dark']) {
      await win.webContents.executeJavaScript(`pickTheme('${theme}')`)
      await new Promise((r) => setTimeout(r, 500))
      writeFileSync(join(outDir, `sheet-${theme}.png`), (await win.webContents.capturePage()).toPNG())
    }
    console.log('[reciteflow] design sheet pictures in', outDir)
    app.quit()
  }, 1000))
}

app.whenReady().then(async () => {
  if (process.env.RECITEFLOW_PAGE === 'design') return openDesignSheet()
  const info = systemInfo()
  ipcMain.handle('system-info', () => info)
  ipcMain.handle('dev-options', () => devOptions())
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
  // RECITEFLOW_SELFTEST=text: the text screen check (src/main/selftest-text.js)
  if (process.env.RECITEFLOW_SELFTEST === 'text') return (await import('./selftest-text')).run(win, engine)
  // RECITEFLOW_SELFTEST=recite: the reciting screen check (src/main/selftest-recite.js)
  if (process.env.RECITEFLOW_SELFTEST === 'recite') return (await import('./selftest-recite')).run(win, engine)
  // RECITEFLOW_SELFTEST=correct: the corrections check (src/main/selftest-correct.js)
  if (process.env.RECITEFLOW_SELFTEST === 'correct') return (await import('./selftest-correct')).run(win, engine)
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
