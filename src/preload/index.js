import { contextBridge, ipcRenderer } from 'electron'

// The page has no Node access; it only gets these few named calls.
contextBridge.exposeInMainWorld('reciteflow', {
  systemInfo: () => ipcRenderer.invoke('system-info'),
  // Dev replay (RECITEFLOW_FILE / RECITEFLOW_WAV): { text: for the box, or null; wav: replay path, or null }
  devOptions: () => ipcRenderer.invoke('dev-options'),
  // Engine status: { status: 'starting' | 'loading' | 'ready' | 'error' | 'restarting' | 'stopped', gpu, loadSeconds,
  //   message, history: its path, or null when it could not be opened }
  engineStatus: () => ipcRenderer.invoke('engine-status'),
  restartEngine: () => ipcRenderer.invoke('engine-restart'),
  onEngineStatus: (fn) => ipcRenderer.on('engine-status', (_event, status) => fn(status)),
  // Recitation. Each call answers the engine's JSON, or { error: 'plain words' }.
  devices: () => ipcRenderer.invoke('devices'), // { devices: [{ id, name }] }
  // { text, device (mic id or null), wav (replay path, dev only), keep_audio }
  start: (options) => ipcRenderer.invoke('recite-start', options),
  stop: () => ipcRenderer.invoke('recite-stop'),
  correct: (recId, pos, state) => ipcRenderer.invoke('correct', recId, pos, state), // state null = undo
  getSettings: () => ipcRenderer.invoke('settings-get'), // { keep_audio }
  setSettings: (settings) => ipcRenderer.invoke('settings-set', settings),
  // frame { tokens, counts, beep }; done { finished, rec_id, note, tokens, counts, result }; error { message }
  onFrame: (fn) => ipcRenderer.on('recite-frame', (_event, frame) => fn(frame)),
  onDone: (fn) => ipcRenderer.on('recite-done', (_event, done) => fn(done)),
  onError: (fn) => ipcRenderer.on('recite-error', (_event, error) => fn(error))
})
