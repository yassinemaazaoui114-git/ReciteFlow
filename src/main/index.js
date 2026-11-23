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
// ...and never touch the window's own settings either
if (process.env.RECITEFLOW_SELFTEST) app.setPath('userData', join(tmpdir(), 'reciteflow-selftest', 'userdata'))
// The corrections and polish checks save their replay (into that temporary history), so it can be corrected
if (['correct', 'polish', 'shots'].includes(process.env.RECITEFLOW_SELFTEST)) process.env.RECITEFLOW_SAVE_REPLAY = '1'
// The stuck engine check starts the engine with a tiny limit, so its first speech pass counts as stuck
if (process.env.RECITEFLOW_SELFTEST === 'stall') process.env.RECITEFLOW_STALL_S = '0.2'
// A high-DPI run of a check: RECITEFLOW_SCALE=1.5 draws the window as on a 150% Windows display
if (process.env.RECITEFLOW_SELFTEST && process.env.RECITEFLOW_SCALE) {
  app.commandLine.appendSwitch('force-device-scale-factor', process.env.RECITEFLOW_SCALE)
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

// The window's own settings (the theme), in the app's user folder. The engine keeps its own (keep_audio).
const THEMES = ['system', 'light', 'dark']
const windowSettingsFile = () => join(app.getPath('userData'), 'window-settings.json')
function readTheme() {
  try {
    const theme = JSON.parse(readFileSync(windowSettingsFile(), 'utf8')).theme
    return THEMES.includes(theme) ? theme : 'system'
  } catch { return 'system' }
}
// The page follows prefers-color-scheme, which Electron sets from themeSource: one switch for the whole window
function applyTheme(theme) {
  nativeTheme.themeSource = theme
  for (const w of BrowserWindow.getAllWindows()) w.setBackgroundColor(nativeTheme.shouldUseDarkColors ? '#0D1424' : '#F8FAFC')
}
function saveTheme(theme) {
  if (!THEMES.includes(theme)) return { error: 'Unknown theme.' }
  applyTheme(theme)
  try {
    mkdirSync(app.getPath('userData'), { recursive: true })
    writeFileSync(windowSettingsFile(), JSON.stringify({ theme }), 'utf8')
  } catch {
    return { error: 'Could not save the theme.' }
  }
  return { theme }
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
  applyTheme(readTheme())
  ipcMain.handle('theme-get', () => ({ theme: readTheme() }))
  ipcMain.handle('theme-set', (_e, theme) => saveTheme(theme))
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
  // the settings check needs no engine (and no GPU)
  if (process.env.RECITEFLOW_SELFTEST?.startsWith('settings')) return (await import('./selftest-settings')).run(win)
  // RECITEFLOW_SELFTEST=speed: start-up, memory and VRAM marks for engine/check_speed.py (src/main/selftest-speed.js)
  const speed = process.env.RECITEFLOW_SELFTEST === 'speed' && (await import('./selftest-speed')).run(win, engine)
  engine.start()
  console.log('[reciteflow] system info:', await info)
  if (speed) return
  // RECITEFLOW_SELFTEST=engine: the engine check (src/main/selftest.js)
  if (process.env.RECITEFLOW_SELFTEST === 'engine') return (await import('./selftest')).run(win, engine)
  // RECITEFLOW_SELFTEST=text: the text screen check (src/main/selftest-text.js)
  if (process.env.RECITEFLOW_SELFTEST === 'text') return (await import('./selftest-text')).run(win, engine)
  // RECITEFLOW_SELFTEST=recite: the reciting screen check (src/main/selftest-recite.js)
  if (process.env.RECITEFLOW_SELFTEST === 'recite') return (await import('./selftest-recite')).run(win, engine)
  // RECITEFLOW_SELFTEST=correct: the corrections check (src/main/selftest-correct.js)
  if (process.env.RECITEFLOW_SELFTEST === 'correct') return (await import('./selftest-correct')).run(win, engine)
  // RECITEFLOW_SELFTEST=polish: sizes, themes, motion and keyboard (src/main/selftest-polish.js)
  if (process.env.RECITEFLOW_SELFTEST === 'polish') return (await import('./selftest-polish')).run(win, engine)
  // RECITEFLOW_SELFTEST=stall: a stuck recitation and a hung engine end with a message (src/main/selftest-stall.js)
  if (process.env.RECITEFLOW_SELFTEST === 'stall') return (await import('./selftest-stall')).run(win, engine)
  // RECITEFLOW_SELFTEST=shots: the README pictures (src/main/selftest-shots.js)
  if (process.env.RECITEFLOW_SELFTEST === 'shots') return (await import('./selftest-shots')).run(win, engine)
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
