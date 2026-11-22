// The window: the text screen (paste a text, pick the mic and options, Start), the engine's status, and the
// recitation's phases: edit -> listening -> finishing -> edit. While reciting, the page shows the live words.
import { drawTally, drawWords } from './design/words.js'

const api = window.reciteflow
const $ = (id) => document.getElementById(id)
const box = $('text')
const go = $('go')
const mic = $('mic')
const keep = $('keep')
const hide = $('hide')
const beep = $('beep')
const page = $('page')
const scroll = $('scroll')
const words = $('words')
const tally = $('tally')

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
  box.hidden = !idle
  page.hidden = idle
  page.dataset.phase = phase
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
  drawn = null
  words.replaceChildren()
  drawTally(tally, {})
  audio.resume()   // on a key press or click, so the tone is allowed to play
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

// ---- The live words ----
let drawn = null   // the tokens on screen, to skip a frame that changes nothing
const audio = new AudioContext()   // made once at start-up: making it on Start held the page ~0.1 s

// The tone for a new mistake (880 Hz, 160 ms), with a 5 ms fade at each end so it does not click
function tone() {
  const t = audio.currentTime
  const osc = audio.createOscillator()
  const gain = audio.createGain()
  osc.frequency.value = 880
  gain.gain.setValueAtTime(0, t)
  gain.gain.linearRampToValueAtTime(0.3, t + 0.005)
  gain.gain.setValueAtTime(0.3, t + 0.155)
  gain.gain.linearRampToValueAtTime(0, t + 0.16)
  osc.connect(gain).connect(audio.destination)
  osc.start(t)
  osc.stop(t + 0.16)
}

// Keep the first word not yet said in view (in hide mode it is not drawn: keep the end in view)
function follow() {
  const behavior = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  const next = words.querySelector('.w[data-state="pending"]')
  if (next) next.scrollIntoView({ block: 'nearest', behavior })
  else scroll.scrollTo({ top: scroll.scrollHeight, behavior })
}

// ---- Recitation events ----
api.onFrame((frame) => {
  if (phase === 'edit') return
  const key = JSON.stringify(frame.tokens)
  if (key !== drawn) {
    drawn = key
    drawWords(words, frame.tokens, { hide: hide.checked })
    follow()
  }
  drawTally(tally, frame.counts)
  if (frame.beep && beep.checked && phase === 'listening') tone()
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
