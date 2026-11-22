// The window: the text screen (paste a text, pick the mic and options, Start), the engine's status, and the
// recitation's phases: edit -> listening -> finishing -> edit.
const api = window.reciteflow
const $ = (id) => document.getElementById(id)
const box = $('text')
const go = $('go')
const mic = $('mic')
const keep = $('keep')
const hide = $('hide')
const beep = $('beep')

let phase = 'edit'
let engine = { status: 'starting' }
let dev = { text: null, wav: null }    // dev replay (RECITEFLOW_FILE / RECITEFLOW_WAV)
let settingsRead = false               // keep is only offered once its remembered value is known

const loaded = () => engine.status === 'ready'

// ---- Status line (top right): a mark + plain words + an optional quiet note ----
const MARKS = {
  wait: '<span class="mark mark-wait">!</span>',
  ok: '<span class="mark mark-ok"><svg class="icon"><use href="#i-check" /></svg></span>',
  bad: '<span class="mark mark-bad"><svg class="icon"><use href="#i-cross" /></svg></span>',
  live: '<span class="light" data-state="ready"></span>',
  idle: '<span class="mark mark-idle"></span>'
}
function showStatus(tone, text, note) {
  const s = $('status')
  s.dataset.tone = tone
  s.innerHTML = MARKS[tone]
  s.append(text)
  if (note) {
    const q = document.createElement('span')
    q.className = 'note-quiet'
    q.textContent = `(${note})`
    s.append(' ', q)
  }
}

function showMessage(text, again = false) {
  $('message').hidden = !text
  $('message-text').textContent = text || ''
  $('again').hidden = !again
}

// ---- Buttons, options and hint for the current phase ----
const kbd = (k) => `<span class="kbd">${k}</span>`
function update() {
  const idle = phase === 'edit'
  go.className = 'btn ' + (idle ? 'btn-go' : 'btn-stop')
  go.innerHTML = {
    edit: '<svg class="icon"><use href="#i-play" /></svg>Start',
    listening: '<svg class="icon"><use href="#i-stop" /></svg>Stop',
    finishing: 'Finishing <span class="dots"><i></i><i></i><i></i></span>'
  }[phase]
  go.disabled = idle ? !loaded() : phase === 'finishing'
  box.readOnly = !idle
  mic.disabled = !idle || !!dev.wav
  keep.disabled = !idle || !!dev.wav || !settingsRead || engine.history === null
  hide.disabled = !idle   // the beep can be switched any time
  $('hint').innerHTML = {
    edit: `Paste the text you want to recite, then press Start ${kbd('Ctrl+Enter')}`,
    listening: (hide.checked
      ? 'Recite from memory: each word appears, coloured, about a second after you say it. '
      : 'Recite the text from memory. Each word is coloured about a second after you say it. ')
      + (beep.checked ? 'A beep means a mistake. ' : '') + `${kbd('Esc')} to stop.`,
    finishing: 'Checking the last words...'
  }[phase]
}

// ---- Engine status ----
const LIGHT = {
  starting: () => 'Starting the engine...',
  loading: () => 'Loading the speech model...',
  ready: (s) => `Engine ready, GPU: ${s.gpu ?? 'none found'}`,
  restarting: () => 'Engine restarting',
  error: () => 'Engine error',
  stopped: () => 'Engine stopped'
}
function showEngine(s) {
  const was = engine.status
  engine = s
  const light = $('engine')
  light.dataset.state = s.status
  light.textContent = (LIGHT[s.status] ?? (() => s.status))(s)
  $('history-off').hidden = s.history !== null
  if (s.status === 'restarting' || s.status === 'stopped' || s.status === 'error') {
    // a recitation in progress ends with the engine (R6)
    if (phase !== 'edit') phase = 'edit'
    showMessage(s.status === 'restarting' ? `${s.message}. Starting it again...` : s.message, s.status === 'stopped')
    showStatus('bad', s.status === 'error' ? 'Could not load the speech model' : 'The engine stopped')
  } else {
    if (s.status === 'ready') showMessage(null)   // a restart message stays until the engine is back
    if (phase === 'edit') showStatus(loaded() ? 'ok' : 'wait', loaded() ? 'Ready' : 'Loading the speech model...')
  }
  if (s.status === 'ready' && was !== 'ready') readEngine()
  update()
}

// Microphones and the remembered "keep" option, read again whenever the engine (re)starts
async function readEngine() {
  const chosen = mic.selectedOptions[0]?.textContent
  const res = await api.devices()
  const list = res.devices ?? []
  mic.replaceChildren(new Option('Default microphone', ''), ...list.map((d) => new Option(d.name, String(d.id))))
  const again = [...mic.options].find((o) => o.textContent === chosen)
  if (again) mic.value = again.value
  if (dev.wav) mic.options[0].textContent = 'Replaying a recording'
  const set = await api.getSettings()
  settingsRead = !set.error
  keep.checked = !!set.keep_audio
  update()
}

keep.addEventListener('change', async () => {
  const res = await api.setSettings({ keep_audio: keep.checked })
  if (res.error) {
    keep.checked = !keep.checked
    showStatus('bad', res.error)
  }
})
beep.addEventListener('change', update)
hide.addEventListener('change', update)

// ---- Start / Stop ----
async function start() {
  if (phase !== 'edit' || !loaded()) return
  const text = box.value.trim()
  if (!text) return showStatus('bad', 'Paste a text first.')
  phase = 'listening'
  showStatus('wait', dev.wav ? 'Starting the replay...' : 'Starting the microphone...')
  update()
  const res = await api.start({ text, device: dev.wav || mic.value === '' ? null : Number(mic.value),
    wav: dev.wav, keep_audio: keep.checked })
  if (res.error && phase !== 'edit') {
    phase = 'edit'
    showStatus('bad', res.error)
    update()
  }
}

async function stop() {
  if (phase !== 'listening') return
  phase = 'finishing'
  showStatus('wait', 'Finishing...')
  update()
  await api.stop()
}

go.addEventListener('click', () => (phase === 'listening' ? stop() : start()))
$('again').addEventListener('click', () => api.restartEngine())
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); start() }
  if (e.key === 'Escape') stop()
})

// ---- Recitation events ----
api.onFrame(() => {
  if (phase === 'listening' && $('status').dataset.tone !== 'live') {
    showStatus('live', dev.wav ? 'Replaying the recording' : 'Listening')
  }
})
api.onDone((done) => {
  if (phase === 'edit') return
  phase = 'edit'
  const note = done.note || ''
  showStatus(note.startsWith('not saved') && !dev.wav ? 'bad' : done.finished ? 'ok' : 'idle',
    done.finished ? 'Finished' : 'Stopped', note)
  update()
})
api.onError((err) => {
  if (phase !== 'edit') phase = 'edit'
  showStatus('bad', err.message)
  update()
})

dev = await api.devOptions()
if (dev.text) box.value = dev.text
api.onEngineStatus(showEngine)
showEngine(await api.engineStatus())
box.focus()
