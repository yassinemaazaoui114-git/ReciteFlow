// Design sheet: shows every part of the design system with made-up recitations. No engine needed.
import { LABELS, drawTally, drawWords } from './design/words.js'

const $ = (id) => document.getElementById(id)

// ---- Theme picker (sheet only): Windows / Light / Dark ----
function pickTheme(mode) {
  if (mode === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = mode
  for (const b of document.querySelectorAll('[data-theme-pick]')) b.setAttribute('aria-pressed', b.dataset.themePick === mode)
  showSwatches()
}
for (const b of document.querySelectorAll('[data-theme-pick]')) b.addEventListener('click', () => pickTheme(b.dataset.themePick))
window.pickTheme = pickTheme // used by the self-test screenshots

// ---- Swatches with their contrast on the page ----
const hex = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
function lum(h) {
  const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }

function swatches(el, list, against) {
  el.replaceChildren(...list.map(([name, label]) => {
    const value = hex(name)
    const d = document.createElement('div')
    d.className = 'swatch'
    const ratio = against ? ` · ${contrast(value, hex(against)).toFixed(1)}:1` : ''
    d.innerHTML = `<i style="background:${value}"></i><b>${label}</b><span>${value.toUpperCase()}${ratio}</span>`
    return d
  }))
}
function showSwatches() {
  swatches($('sw-brand'), [['--brand-navy', 'Navy'], ['--brand-green', 'Green'], ['--brand-red', 'Red'],
    ['--brand-amber', 'Amber'], ['--brand-grey', 'Grey'], ['--brand-paper', 'Paper']])
  swatches($('sw-ui'), [['--bg', 'Background'], ['--surface', 'Page'], ['--raised', 'Menu'], ['--line', 'Line'],
    ['--w-corrected-bg', 'Corrected tint'],
    ['--ink', 'Text'], ['--muted', 'Quiet text']])
  swatches($('sw-words'), [['--w-correct', 'Correct'], ['--w-misspelled', 'Misspelled'],
    ['--w-wrong', 'Wrong, [missed], {extra}'], ['--w-pending', 'Not yet said']],
  '--surface')
}

// ---- Made-up recitations, shaped like the engine's frames ----
const TEXT = 'Learn the lines once, then close the book.\nSay them back slowly, word by word,\nand let the page show you where you slipped.'

function tokensOf(text) {
  const lines = text.split('\n').map((l) => l.split(' '))
  const toks = []
  lines.forEach((words, li) => words.forEach((w, wi) => toks.push({
    kind: 'word', text: w, state: 'pending', app_state: 'pending',
    sep: wi < words.length - 1 ? ' ' : li < lines.length - 1 ? '\n' : '', labels: ['correct', 'misspelled', 'wrong', 'missed']
  })))
  return toks
}
const extra = (text) => ({ kind: 'extra', text: `{${text}}`, state: 'extra', app_state: 'extra', sep: ' ',
  labels: ['extra', 'not_said'] })
const renumber = (toks) => toks.forEach((t, i) => { t.pos = i })

// How the made-up recitation ends: index -> state (others correct); "really" said before word 9; word 6 recovered
const ENDING = { 3: 'misspelled', 12: 'wrong', 17: 'missed', 6: 'wrong' }
function finished() {
  const toks = tokensOf(TEXT)
  toks.forEach((t, i) => {
    t.state = t.app_state = ENDING[i] ?? 'correct'
    if (t.state === 'missed') t.text = `[${t.text}]`
  })
  toks[6].underline = true
  toks.splice(9, 0, extra('really'))
  renumber(toks)
  return toks
}
function countsOf(toks) {
  const c = { correct: 0, misspelled: 0, wrong: 0, missed: 0, extra: 0, pending: 0 }
  for (const t of toks) if (t.state in c) c[t.state]++
  return c
}

// Every state at once
const all = [
  { text: 'Correct', state: 'correct', sep: ' ' }, { text: 'misspelled', state: 'misspelled', sep: ' ' },
  { text: 'wrong', state: 'wrong', sep: ' ' }, { text: '[missed]', state: 'missed', sep: ' ' },
  { text: '{extra}', state: 'extra', kind: 'extra', sep: ' ' }, { text: 'recovered', state: 'wrong', underline: true, sep: ' ' },
  { text: 'corrected', state: 'correct', corrected: true, sep: ' ' }, { text: '{invented}', state: 'not_said', kind: 'extra', corrected: true, sep: ' ' },
  { text: 'not yet said', state: 'pending', sep: '' }]
all.forEach((t, i) => { t.pos = i; t.kind ??= 'word' })
drawWords($('states'), all)

// Hide mode mid-recitation
const half = finished().map((t, i) => (i > 10 && t.kind === 'word' ? { ...t, state: 'pending', underline: false,
  text: t.text.replace(/^\[|\]$/g, '') } : t)).filter((t, i) => !(i > 10 && t.kind === 'extra'))
drawWords($('hidden'), half, { hide: true })

// ---- Live demo: frames every 0.6 s, then finishing, then done ----
const demo = { toks: tokensOf(TEXT), timer: null }
renumber(demo.toks)
function demoIdle() {
  $('demo-page').dataset.phase = 'edit'
  drawWords($('demo-words'), demo.toks)
  drawTally($('demo-tally'), countsOf(demo.toks))
}
function setButton(kind) {
  const b = $('demo-play')
  b.disabled = kind === 'finishing'
  b.className = 'btn ' + (kind === 'go' || kind === 'again' ? 'btn-go' : 'btn-stop')
  b.innerHTML = {
    go: '<svg class="icon"><use href="#i-play" /></svg>Play',
    stop: '<svg class="icon"><use href="#i-stop" /></svg>Stop',
    finishing: 'Finishing <span class="dots"><i></i><i></i><i></i></span>',
    again: '<svg class="icon"><use href="#i-again" /></svg>Play again'
  }[kind]
}
function status(tone, text) {
  const s = $('demo-status')
  s.dataset.tone = tone
  const mark = { live: '<span class="light" data-state="ready"></span>', wait: '<span class="mark mark-wait">!</span>',
    ok: '<span class="mark mark-ok"><svg class="icon"><use href="#i-check" /></svg></span>' }[tone]
  s.innerHTML = mark + text
}
function play() {
  const end = finished()
  const page = $('demo-page')
  let said = 0
  page.dataset.phase = 'listening'
  status('live', 'Listening')
  $('demo-hint').innerHTML = 'Recite from memory: each word appears, coloured, about a second after you say it. Esc to stop.'
  setButton('stop')
  const frame = () => {
    said = Math.min(said + 2, end.length)
    const toks = end.map((t, i) => (i < said ? t : { ...t, state: 'pending', underline: false, text: t.text.replace(/^\[|\]$/g, '') }))
      .filter((t, i) => i < said || t.kind === 'word')
    drawWords($('demo-words'), toks, { hide: $('demo-hide').checked })
    drawTally($('demo-tally'), countsOf(toks))
    if (said >= end.length) finish(end)
  }
  demo.timer = setInterval(frame, 600)
  frame()
}
function finish(end) {
  clearInterval(demo.timer)
  demo.timer = null
  $('demo-page').dataset.phase = 'finishing'
  status('wait', 'Finishing...')
  $('demo-hint').textContent = 'Checking the last words...'
  setButton('finishing')
  setTimeout(() => {
    $('demo-page').dataset.phase = 'done'
    drawWords($('demo-words'), end)
    drawTally($('demo-tally'), countsOf(end))
    status('ok', 'Finished <span class="note-quiet">(saved #214 with the recording)</span>')
    $('demo-hint').textContent = '20 of 24 words correct. Extra words: really. 1 fixed by re-reciting (underlined).'
    setButton('again')
  }, 900)
}
$('demo-play').addEventListener('click', () => {
  if (demo.timer) finish(finished())
  else play()
})
demoIdle()

// ---- Done: click a word, correct it ----
const done = finished()
const menu = $('menu')
let openPos = null
function drawDone() {
  drawWords($('done'), done)
  drawTally($('done-tally'), countsOf(done))
}
function closeMenu() {
  menu.hidden = true
  if (openPos != null) $('done').querySelector(`[data-pos="${openPos}"]`)?.removeAttribute('data-open')
  openPos = null
}
function openMenu(w) {
  const t = done[+w.dataset.pos]
  closeMenu()
  openPos = t.pos
  w.dataset.open = ''
  const [said] = LABELS[t.app_state]
  const rows = [`<div class="menu-said">The app said: <b style="color:var(--w-${t.app_state === 'extra' ? 'wrong' : t.app_state === 'missed' ? 'wrong' : t.app_state})">${said}</b></div>`]
  for (const label of t.labels) {
    const [name, why] = LABELS[label]
    const on = t.state === label
    rows.push(`<button class="menu-item" role="menuitemradio" aria-checked="${on}" data-label="${label}">
      <span class="tick">${on ? '<svg class="icon"><use href="#i-check" /></svg>' : ''}</span>${name} <span class="why">(${why})</span></button>`)
  }
  if (t.corrected) rows.push('<div class="menu-sep"></div><button class="menu-item" role="menuitem" data-label="undo"><span class="tick"></span>Undo my correction</button>')
  menu.innerHTML = rows.join('')
  const box = w.getBoundingClientRect()
  const wrap = menu.parentElement.getBoundingClientRect()
  menu.style.left = `${Math.min(box.left - wrap.left, wrap.width - 270)}px`
  menu.style.top = `${box.bottom - wrap.top + 6}px`
  menu.hidden = false
  menu.querySelector('.menu-item')?.focus()
}
$('done').addEventListener('click', (e) => {
  const w = e.target.closest('.w')
  if (w) { e.stopPropagation(); openMenu(w) }
})
menu.addEventListener('click', (e) => {
  const item = e.target.closest('.menu-item')
  if (!item) return
  e.stopPropagation()
  const t = done[openPos]
  const label = item.dataset.label === 'undo' ? t.app_state : item.dataset.label
  t.state = label
  t.corrected = label !== t.app_state
  const base = t.text.replace(/^[[{]|[\]}]$/g, '')
  t.text = t.kind === 'extra' ? `{${base}}` : label === 'missed' ? `[${base}]` : base
  closeMenu()
  drawDone()
})
menu.addEventListener('keydown', (e) => {
  const items = [...menu.querySelectorAll('.menu-item')]
  const i = items.indexOf(document.activeElement)
  if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus() }
  if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus() }
})
document.addEventListener('click', () => { if (!menu.hidden) closeMenu() })
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) closeMenu() })
drawDone()

pickTheme('system')
