// The window: the text screen (paste a text, pick the mic and options, Start), the engine's status, and the
// recitation's phases: edit -> listening -> finishing -> done. While reciting, the page shows the live words; when
// it is done, the whole text, the result line, Recite again and Edit text; after a saved recitation a click on a word
// opens the menu to correct the app. The settings button (top right) opens Settings (the theme) in the text box's
// place, when not reciting. "Open a file" fills the text box from a .txt, a PDF or a photo, through a popup.
import { LABELS, drawTally, drawWords } from './design/words.js'

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
const edit = $('edit')
const menu = $('menu')
const settings = $('settings')
const themes = [...document.querySelectorAll('[data-theme-pick]')]

let phase = 'edit'
let engine = { status: 'starting' }
let dev = { text: null, wav: null }    // dev replay (RECITEFLOW_FILE / RECITEFLOW_WAV)
let settingsRead = false               // keep is only offered once its remembered value is known
let settingsOpen = false

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
  const idle = phase === 'edit' || phase === 'done'
  go.className = 'btn ' + (idle ? 'btn-go' : 'btn-stop')
  go.innerHTML = {
    edit: '<svg class="icon"><use href="#i-play" /></svg>Start',
    done: '<svg class="icon"><use href="#i-again" /></svg>Recite again',
    listening: '<svg class="icon"><use href="#i-stop" /></svg>Stop',
    finishing: 'Finishing <span class="dots"><i></i><i></i><i></i></span>'
  }[phase]
  go.disabled = idle ? !loaded() : phase === 'finishing'
  box.readOnly = phase !== 'edit'
  if (!idle) settingsOpen = false   // an engine restart or a start never leaves Settings over a recitation
  box.hidden = phase !== 'edit' || settingsOpen
  page.hidden = phase === 'edit' || settingsOpen
  settings.hidden = !settingsOpen
  $('options').hidden = $('actions').hidden = settingsOpen
  $('settings-bar').hidden = !settingsOpen
  $('open-settings').disabled = !idle
  $('open-settings').toggleAttribute('aria-pressed', settingsOpen)
  page.dataset.phase = phase
  edit.hidden = phase !== 'done'
  $('open-file').hidden = phase !== 'edit'
  if (phase !== 'done') closeMenu()
  words.toggleAttribute('data-clickable', canCorrect())
  wordStops()
  mic.disabled = !idle || !!dev.wav
  keep.disabled = !idle || !!dev.wav || !settingsRead || engine.history === null
  hide.disabled = !idle   // the beep can be switched any time
  if (settingsOpen) return void ($('hint').innerHTML = `Changes apply at once. ${kbd('Esc')} to go back.`)
  if (phase === 'done') return showResult()
  $('hint').innerHTML = {
    edit: `Paste the text you want to recite or open a file ${kbd('Ctrl+O')}, then press Start ${kbd('Ctrl+Enter')}`,
    listening: (hide.checked
      ? 'Recite from memory: each word appears as you say it and takes its colour a moment later. '
      : 'Recite the text from memory. Each word darkens as you say it and takes its colour a moment later. ')
      + (beep.checked ? 'A beep means a mistake. ' : '') + `${kbd('Esc')} to stop.`,
    finishing: 'Checking the last words...'
  }[phase]
}

// The done line: "x of y words correct (z said). Extra words: a, b n fixed by re-reciting (underlined)." + the
// correction hint when the recitation was saved
let finished = null   // the last done event
function showResult() {
  const r = finished.result
  const hint = $('hint')
  const b = document.createElement('b')
  b.textContent = `${r.correct} of ${r.total}`
  hint.replaceChildren(b, ' words correct' + (r.said !== null ? ` (${r.said} said)` : '')
    + (r.extras.length ? '. Extra words: ' + r.extras.join(', ') : '.')
    + (r.fixed ? ` ${r.fixed} fixed by re-reciting (underlined).` : '')
    + (finished.rec_id !== null ? ' Click a word the app got wrong to correct it.' : ''))
}

// ---- Corrections (after a saved recitation): click a word, pick what was really said ----
const canCorrect = () => phase === 'done' && finished?.rec_id != null
let openPos = null   // the word whose menu is open
let saving = false

// back: the focus goes back to the word (closed with Esc, Tab or a choice)
function closeMenu(back = false) {
  const w = words.querySelector('[data-open]')
  menu.hidden = true
  w?.removeAttribute('data-open')
  openPos = null
  if (back && w) w.focus()
}

// Keyboard: after a saved recitation one word is in the Tab order; the arrows move along the words, Enter or Space
// opens its menu
let wordAt = 0   // the word that takes the focus
function wordStops() {
  const ws = [...words.querySelectorAll('.w')]
  if (!canCorrect()) return ws.forEach((w) => w.removeAttribute('tabindex'))
  if (!ws.some((w) => +w.dataset.pos === wordAt)) wordAt = +(ws[0]?.dataset.pos ?? 0)
  for (const w of ws) w.tabIndex = +w.dataset.pos === wordAt ? 0 : -1
}
function focusWord(w) {
  wordAt = +w.dataset.pos
  wordStops()
  w.focus()
}
words.addEventListener('keydown', (e) => {
  const w = e.target.closest('.w')
  if (!w || !canCorrect() || e.ctrlKey || e.altKey) return
  const ws = [...words.querySelectorAll('.w')]
  const i = ws.indexOf(w)
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: ws.length - 1 }[e.key]
  if (to !== undefined) {
    e.preventDefault()
    focusWord(ws[Math.max(0, Math.min(ws.length - 1, to))])
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    if (!saving) openMenu(w)
  }
})

// The menu: what the app said, the allowed labels (a check mark on the current one), Undo when corrected
const shade = (state) => (state === 'extra' || state === 'missed' ? 'wrong' : state)
function openMenu(w) {
  const t = finished.tokens[+w.dataset.pos]
  closeMenu()
  openPos = t.pos
  w.dataset.open = ''
  const said = document.createElement('div')
  said.className = 'menu-said'
  const b = document.createElement('b')
  b.style.color = `var(--w-${shade(t.app_state)})`
  b.textContent = LABELS[t.app_state]?.[0] ?? t.app_state
  said.append('The app said: ', b)
  const item = (label, text, why, on) => {
    const el = document.createElement('button')
    el.className = 'menu-item'
    el.dataset.label = label
    el.setAttribute('role', label === 'undo' ? 'menuitem' : 'menuitemradio')
    if (label !== 'undo') el.setAttribute('aria-checked', String(on))
    el.innerHTML = `<span class="tick">${on ? '<svg class="icon"><use href="#i-check" /></svg>' : ''}</span>`
    el.append(text)
    if (why) {
      const q = document.createElement('span')
      q.className = 'why'
      q.textContent = `(${why})`
      el.append(' ', q)
    }
    return el
  }
  const rows = [said, ...t.labels.map((l) => item(l, LABELS[l][0], LABELS[l][1], t.state === l))]
  if (t.corrected) {
    const sep = document.createElement('div')
    sep.className = 'menu-sep'
    rows.push(sep, item('undo', 'Undo my correction', null, false))
  }
  menu.replaceChildren(...rows)
  // under the word, kept inside the page
  const box = w.getBoundingClientRect()
  const wrap = page.getBoundingClientRect()
  menu.hidden = false
  const m = menu.getBoundingClientRect()
  const below = box.bottom - wrap.top + 6
  menu.style.left = `${Math.max(8, Math.min(box.left - wrap.left, wrap.width - m.width - 8))}px`
  menu.style.top = `${below + m.height > wrap.height - 8 ? Math.max(8, box.top - wrap.top - m.height - 6) : below}px`
  menu.querySelector('.menu-item')?.focus()
}

async function correct(pos, state) {
  saving = true
  const res = await api.correct(finished.rec_id, pos, state)
  saving = false
  if (phase !== 'done') return
  if (res.error) return showStatus('bad', res.error)
  finished = { ...finished, tokens: res.tokens, counts: res.counts, result: res.result }
  drawWords(words, res.tokens)
  drawTally(tally, res.counts)
  wordStops()
  words.querySelector(`.w[data-pos="${pos}"]`)?.focus()
  showResult()
  showStatus('ok', `Saved #${finished.rec_id}: ${res.result.corrections} correction(s)`)
}

words.addEventListener('click', (e) => {
  const w = e.target.closest('.w')
  if (!w || !canCorrect() || saving) return
  e.stopPropagation()
  wordAt = +w.dataset.pos
  wordStops()
  if (openPos === +w.dataset.pos) return closeMenu()
  openMenu(w)
})
menu.addEventListener('click', (e) => {
  e.stopPropagation()
  const el = e.target.closest('.menu-item')
  if (!el || saving) return
  const pos = openPos
  closeMenu(true)
  correct(pos, el.dataset.label === 'undo' ? null : el.dataset.label)
})
menu.addEventListener('keydown', (e) => {
  const items = [...menu.querySelectorAll('.menu-item')]
  const i = items.indexOf(document.activeElement)
  if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus() }
  if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus() }
  if (e.key === 'Tab') { e.preventDefault(); closeMenu(true) }
})
document.addEventListener('click', () => { if (!menu.hidden) closeMenu() })
scroll.addEventListener('scroll', () => { if (!menu.hidden) closeMenu() })
window.addEventListener('resize', () => { if (!menu.hidden) closeMenu() })

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

// ---- Settings: the theme, applied by the main process at once and kept for the next start ----
function showTheme(theme) {
  for (const b of themes) {
    b.setAttribute('aria-checked', String(b.dataset.themePick === theme))
    b.tabIndex = b.dataset.themePick === theme ? 0 : -1
  }
}
async function pickTheme(theme) {
  showTheme(theme)
  const res = await api.setTheme(theme)
  if (res.error) {
    showStatus('bad', res.error)
    showTheme((await api.getTheme()).theme)
  }
}
function openSettings() {
  if (phase !== 'edit' && phase !== 'done') return
  closeMenu()
  settingsOpen = true
  update()
  themes.find((b) => b.tabIndex === 0)?.focus()
}
function closeSettings() {
  if (!settingsOpen) return
  settingsOpen = false
  update()
  if (phase === 'edit') box.focus()
  else $('open-settings').focus()
}
for (const b of themes) b.addEventListener('click', () => pickTheme(b.dataset.themePick))
// A radio group: the arrows move the choice, Tab leaves the group
$('theme').addEventListener('keydown', (e) => {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
  if (!step) return
  e.preventDefault()
  const next = themes[(themes.indexOf(document.activeElement) + step + themes.length) % themes.length]
  next.focus()
  pickTheme(next.dataset.themePick)
})
$('open-settings').addEventListener('click', (e) => {
  e.stopPropagation()
  settingsOpen ? closeSettings() : openSettings()
})
$('close-settings').addEventListener('click', closeSettings)

// ---- Open a file (Ctrl+O): a .txt, a PDF or a photo of a page. A popup shows the text found (for a PDF of more
// than 2 pages, after asking which pages), to fix it and pick the part to recite ("Use this part") or "Take it all";
// the choice replaces the text box's text (Ctrl+Z brings it back) ----
const pick = $('pick')
const pickText = $('pick-text')
let reading = 0   // which opening is in progress; the text of a cancelled one is dropped when it arrives
const wordCount = (s) => s.split(/\s+/).filter(Boolean).length
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
const pickPart = () => pickText.value.slice(pickText.selectionStart, pickText.selectionEnd).trim()

function pickHint(name, ...rest) {
  const b = document.createElement('b')
  b.textContent = name
  $('pick-hint').replaceChildren('From ', b, ...rest)
}
function showPickCount() {
  if (pickText.disabled) return
  const part = pickPart()
  const all = wordCount(pickText.value)
  $('pick-count').textContent = part ? `${wordCount(part)} of ${plural(all, 'word')} selected` : plural(all, 'word')
  $('pick-part').disabled = !part
  $('pick-all').disabled = !all
}

// The popup while a file is read: the hint with moving dots, an empty text, no choice yet
function showReading(file, what) {
  const dots = document.createElement('span')
  dots.className = 'dots'
  dots.innerHTML = '<i></i><i></i><i></i>'
  pickHint(file.name, what, dots)
  $('pick-pages').hidden = true
  pickText.hidden = false
  pickText.value = ''
  pickText.disabled = true
  $('pick-count').textContent = ''
  $('pick-part').disabled = $('pick-all').disabled = true
}

async function openFile() {
  if (phase !== 'edit' || settingsOpen || pick.open) return
  const file = await api.pickFile()
  if (file.canceled || phase !== 'edit' || pick.open) return
  showReading(file, file.photo ? '. Reading the words in the photo ' : '. Reading it ')
  $('pick-range').value = ''
  pick.showModal()
  readInto(file)
}

// A PDF of more than 2 pages: which pages to read ('3', '3-4' or '3, 7'), at most 2, checked here at once (and
// again by the reader)
const MAX_PAGES = 2
let pdf = null   // { file, pages } while its pages are being chosen
function pagesProblem(spec, count) {
  const pages = new Set()
  for (const part of spec.replace(/\s+/g, '').split(',')) {
    const m = /^(\d+)(?:-(\d+))?$/.exec(part)
    if (!m) return 'Write the pages as 3, or 3-4, or 3, 7.'
    const [a, b] = [+m[1], +(m[2] ?? m[1])].sort((x, y) => x - y)
    if (b - a + 1 > MAX_PAGES) return `Pick at most ${MAX_PAGES} pages.`
    for (let p = a; p <= b; p++) pages.add(p)
  }
  if (pages.size > MAX_PAGES) return `Pick at most ${MAX_PAGES} pages.`
  if ([...pages].some((p) => p < 1 || p > count)) return `This PDF has pages 1 to ${count}.`
  return null
}
function askPages(file, count, problem) {
  pdf = { file, pages: count }
  pickHint(file.name, `. This PDF has ${count} pages: pick up to ${MAX_PAGES} to read.`)
  pickText.hidden = true
  $('pick-pages').hidden = false
  const range = $('pick-range')
  if (!range.value) range.value = `1-${MAX_PAGES}`
  showPagesNote(problem)
  range.focus()
  range.select()
}
function showPagesNote(problem) {
  $('pick-range').setAttribute('aria-invalid', String(!!problem))
  const note = $('pick-range-note')
  note.dataset.tone = problem ? 'bad' : ''
  note.textContent = problem || 'One page or two: 3, or 3-4, or 3, 7.'
}
$('pick-pages').addEventListener('submit', (e) => {
  e.preventDefault()
  if (!pdf) return
  const spec = $('pick-range').value.trim()
  const problem = pagesProblem(spec, pdf.pages)
  if (problem) return askPages(pdf.file, pdf.pages, problem)
  const file = pdf.file
  showReading(file, `. Reading ${/[,-]/.test(spec) ? 'pages' : 'page'} ${spec} `)
  readInto(file, spec)
})
$('pick-range').addEventListener('input', () => showPagesNote(null))

async function readInto(file, pages) {
  const id = ++reading
  const res = await api.readPicked(pages)
  if (id !== reading || !pick.open) return
  if (res.pages) return askPages(file, res.pages)
  // chosen pages that give nothing: choose others
  if (res.error && pages != null && pdf) return askPages(pdf.file, pdf.pages, res.error)
  if (res.error) {
    pick.close()
    return showStatus('bad', res.error)
  }
  pdf = null
  pickHint(file.name, '. Select the part to recite, or take it all. '
    + (file.photo ? 'Check the words first: a photo can be misread, and you can fix them here.'
      : res.ocr ? 'Check the words first: its pictures of text can be misread, and you can fix them here.'
        : 'You can change it here first.'))
  pickText.disabled = false
  pickText.value = res.text
  pickText.setSelectionRange(0, 0)
  pickText.scrollTop = 0
  pickText.focus()
  showPickCount()
}

function usePick(text) {
  pick.close()
  box.focus()
  box.select()
  if (!document.execCommand('insertText', false, text)) box.value = text   // insertText keeps Ctrl+Z
  box.setSelectionRange(0, 0)
  box.scrollTop = 0
}

for (const ev of ['select', 'selectionchange', 'input', 'keyup', 'mouseup']) pickText.addEventListener(ev, showPickCount)
pick.addEventListener('close', () => { reading++; pdf = null })   // Esc, Cancel or a choice: a reading still going is dropped
$('pick-cancel').addEventListener('click', () => pick.close())
$('pick-part').addEventListener('click', () => { if (pickPart()) usePick(pickPart()) })
$('pick-all').addEventListener('click', () => { if (pickText.value.trim()) usePick(pickText.value.trim()) })
$('open-file').addEventListener('click', openFile)

// ---- Start / Stop ----
async function start() {
  if ((phase !== 'edit' && phase !== 'done') || !loaded() || settingsOpen || pick.open) return
  const text = box.value.trim()
  if (!text) return showStatus('bad', 'Paste a text first.')
  phase = 'listening'
  drawn = null
  wordAt = 0
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

// Back to the text box, with the text that was recited
function editText() {
  if (phase !== 'done') return
  phase = 'edit'
  showStatus(loaded() ? 'ok' : 'wait', loaded() ? 'Ready' : 'Loading the speech model...')
  update()
  box.focus()
}

go.addEventListener('click', () => (phase === 'listening' ? stop() : start()))
edit.addEventListener('click', editText)
$('again').addEventListener('click', () => api.restartEngine())
document.addEventListener('keydown', (e) => {
  if (pick.open) return   // the popup has its own keys (Esc closes it)
  if (e.ctrlKey && e.key.toLowerCase() === 'o') { e.preventDefault(); openFile() }
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); start() }
  if (e.key === 'Escape') {
    if (!menu.hidden) return closeMenu(true)
    if (settingsOpen) return closeSettings()
    stop()
  }
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
  const next = words.querySelector('.w[data-state="pending"]:not([data-heard])')
  if (next) next.scrollIntoView({ block: 'nearest', behavior })
  else scroll.scrollTo({ top: scroll.scrollHeight, behavior })
}

// ---- Recitation events ----
api.onFrame((frame) => {
  if (phase !== 'listening' && phase !== 'finishing') return
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
  if (phase !== 'listening' && phase !== 'finishing') return
  phase = 'done'
  finished = done
  drawn = null
  drawWords(words, done.tokens)   // the whole text, hidden or not while reciting
  drawTally(tally, done.counts)
  const note = done.note || ''
  showStatus(note.startsWith('not saved') && !dev.wav ? 'bad' : done.finished ? 'ok' : 'idle',
    done.finished ? 'Finished' : 'Stopped', note)
  update()
})
api.onError((err) => {
  if (phase === 'listening' || phase === 'finishing') phase = 'edit'
  showStatus('bad', err.message)
  update()
})

dev = await api.devOptions()
showTheme((await api.getTheme()).theme)
if (dev.text) box.value = dev.text
api.onEngineStatus(showEngine)
showEngine(await api.engineStatus())
box.focus()
