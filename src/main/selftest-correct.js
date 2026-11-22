import { app, ipcMain } from 'electron'
import { execFile } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

// Corrections check (RECITEFLOW_SELFTEST=correct, with RECITEFLOW_WAV + RECITEFLOW_FILE): replays the recording into
// the temporary history (saved, so it can be corrected), then clicks words and menu items with the mouse, as a person
// would: a word the app got right made wrong, then missed, then undone; a mistake made correct; a word made
// misspelled and then given back the app's own label; an extra word marked not said; the last word left wrong;
// Esc and a click outside close the menu without saving; a refused save shows its message. After each click: the
// menu (what the app said, the check mark, Undo), the tint, the counters, the done line and the status. At the end
// engine/check_corrections.py applies the same clicks with the old window's own History.correct to a copy of the
// history taken before the first click and compares every row. Pictures and a log go to test_out/t2.4/.
const outDir = join(app.getAppPath(), 'test_out', 't2.4')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const COUNTED = ['correct', 'misspelled', 'wrong', 'missed', 'pending']

function waitFor(engine, status, ms) {
  return new Promise((resolve, reject) => {
    if (engine.status.status === status) return resolve()
    const timer = setTimeout(() => reject(new Error(`no "${status}" within ${ms} ms`)), ms)
    const on = (s) => { if (s.status === status) { clearTimeout(timer); engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
}

async function until(win, fn, ms, what) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await win.webContents.executeJavaScript(fn)) return
    await sleep(50)
  }
  throw new Error(`${what}: not seen within ${ms} ms`)
}

function py(args) {
  return new Promise((resolve) => {
    execFile('py', ['-3.12', join(app.getAppPath(), 'engine', 'check_corrections.py'), ...args],
      { timeout: 60000, windowsHide: true }, (err, stdout, stderr) => resolve({ ok: !err, out: (stdout + stderr).trim() }))
  })
}

// The old window's counts and done line from the tokens and their labels (corrected_counts and _done_hint)
function oldCounts(done, labels) {
  const out = Object.fromEntries([...COUNTED, 'extra'].map((k) => [k, done.counts[k] ?? 0]))
  for (const t of done.tokens) {
    const user = labels.get(t.pos)
    if (!user) continue
    const old = t.kind === 'extra' ? 'extra' : t.app_state
    const now = user !== 'not_said' ? user : null
    out[old] = Math.max(0, out[old] - 1)
    if (now in out) out[now] += 1
  }
  return out
}
function oldLine(done, labels) {
  const s = oldCounts(done, labels)
  const total = done.result.total
  const extras = done.tokens.filter((t) => t.kind === 'extra' && (labels.get(t.pos) ?? t.app_state) === 'extra')
    .map((t) => t.text.slice(1, -1))
  const fixed = done.tokens.filter((t) => t.underline).length   // recovered (no correction yet when done)
  return `${s.correct} of ${total} words correct` + (s.pending ? ` (${total - s.pending} said)` : '')
    + (extras.length ? '. Extra words: ' + extras.join(', ') : '.')
    + (fixed ? ` ${fixed} fixed by re-reciting (underlined).` : '')
    + ' Click a word the app got wrong to correct it.'
}
const tallyOf = (c) => ['correct', 'misspelled', 'wrong', 'missed', 'extra', 'pending']
  .map((k, i) => `${c[k] ?? 0} ${['Correct', 'Misspelled', 'Wrong', '[Missed]', '{Extra}', 'Not yet said'][i]}`).join('')

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const wav = process.env.RECITEFLOW_WAV
  const tag = basename(wav || 'none', '.wav')
  const log = []
  const t0 = Date.now()
  const note = (text) => { log.push(`${((Date.now() - t0) / 1000).toFixed(2)} s  ${text}`); console.log('[selftest]', text) }
  const js = (code) => win.webContents.executeJavaScript(code)
  const shot = async (name) => writeFileSync(join(outDir, `${tag}-${name}.png`), (await win.webContents.capturePage()).toPNG())
  win.webContents.setBackgroundThrottling(false)
  let pass = true
  const fail = (text) => { pass = false; note(`FAIL: ${text}`) }
  let done = null
  const onEvent = (event, data) => { if (event === 'done') done = data }
  engine.on('event', onEvent)

  const mouse = (x, y) => {
    for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) {
      win.webContents.sendInputEvent({ type, x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 })
    }
  }
  const center = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect()
    return [r.left + r.width / 2, r.top + r.height / 2] })()`)
  const menuOpen = () => js("!document.getElementById('menu').hidden")
  const readMenu = () => js(`(() => { const m = document.getElementById('menu')
    return { said: m.querySelector('.menu-said')?.textContent,
      items: [...m.querySelectorAll('.menu-item')].map((b) => [b.dataset.label, b.getAttribute('aria-checked') === 'true', !!b.querySelector('.tick svg')]),
      focused: document.activeElement?.dataset.label ?? null } })()`)
  const screen = () => js(`(() => { const $ = (id) => document.getElementById(id)
    return { hint: $('hint').textContent, status: $('status').textContent, tally: $('tally').textContent,
      tinted: [...$('words').querySelectorAll('.w[data-corrected]')].map((w) => +w.dataset.pos),
      clickable: $('words').hasAttribute('data-clickable'),
      cursor: getComputedStyle($('words').querySelector('.w')).cursor } })()`)
  async function openWord(pos) {
    await js(`document.querySelector('#words .w[data-pos="${pos}"]').scrollIntoView({ block: 'center' })`)
    await sleep(150)
    const [x, y] = await center(`#words .w[data-pos="${pos}"]`)
    mouse(x, y)
    await until(win, "!document.getElementById('menu').hidden", 2000, `menu of word ${pos}`)
  }

  const labels = new Map()   // pos -> the label the user gave (the history's user_state)
  const clicks = []          // [pos, state or null], as History.correct is called
  async function pick(pos, label, why) {
    const t = done.tokens[pos]
    await openWord(pos)
    const m = await readMenu()
    const current = labels.get(pos) ?? t.app_state
    const wantItems = t.labels.map((l) => [l, l === current, l === current])
    if (labels.has(pos)) wantItems.push(['undo', false, false])
    const said = 'The app said: ' + { correct: 'Correct', misspelled: 'Misspelled', wrong: 'Wrong', missed: 'Missed',
      extra: 'Really said' }[t.app_state]
    if (m.said !== said) fail(`word ${pos}: menu says "${m.said}", "${said}" expected`)
    if (JSON.stringify(m.items) !== JSON.stringify(wantItems)) fail(`word ${pos}: menu ${JSON.stringify(m.items)}`)
    if (m.focused !== t.labels[0]) fail(`word ${pos}: the first label should have the focus`)
    const before = await js('window.__redraws')
    const [x, y] = await center(`#menu .menu-item[data-label="${label}"]`)
    mouse(x, y)
    const state = label === 'undo' ? null : label
    clicks.push([pos, state])
    if (state === null || state === t.app_state) labels.delete(pos)
    else labels.set(pos, state)
    await until(win, `window.__redraws > ${before}`, 5000, `saved after word ${pos}`)
    const s = await screen()
    const tinted = [...labels.keys()].sort((a, b) => a - b)
    note(`${why}: word ${pos} "${t.text}" ${t.app_state} -> ${label}; status "${s.status}", counters "${s.tally}", ` +
      `tinted [${s.tinted}], menu closed ${!(await menuOpen())}`)
    if (s.status !== `Saved #${done.rec_id}: ${labels.size} correction(s)`) fail('status')
    if (JSON.stringify(s.tinted) !== JSON.stringify(tinted)) fail(`tinted words: [${tinted}] expected`)
    if (s.tally !== tallyOf(oldCounts(done, labels))) fail(`counters: "${tallyOf(oldCounts(done, labels))}" expected`)
    if (s.hint !== oldLine(done, labels)) fail(`done line "${s.hint}", the old window would say "${oldLine(done, labels)}"`)
    if (await menuOpen()) fail('the menu stayed open')
  }

  try {
    if (!wav) throw new Error('RECITEFLOW_WAV is not set')
    await waitFor(engine, 'ready', 120000)
    await until(win, "!document.getElementById('go').disabled", 10000, 'Start enabled')
    note(`replay ${basename(wav)} into the temporary history ${process.env.RECITEFLOW_DB}`)
    await js("document.getElementById('text').focus()")
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter', modifiers: ['control'] })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter', modifiers: ['control'] })
    while (!done) {
      await sleep(200)
      if (Date.now() - t0 > 400000) throw new Error('the replay did not end')
    }
    await until(win, "document.getElementById('go').textContent === 'Recite again'", 5000, 'the result screen')
    note(`done: ${done.result.correct} of ${done.result.total} correct, note "${done.note}", rec_id ${done.rec_id}`)
    if (done.rec_id == null) throw new Error('the replay was not saved, nothing to correct')
    // the words are redrawn once a correction is saved
    await js(`window.__redraws = 0; new MutationObserver(() => window.__redraws++)
      .observe(document.getElementById('words'), { childList: true }); true`)
    const snap = join(outDir, `${tag}-before.db`)
    const s1 = await py(['snapshot', process.env.RECITEFLOW_DB, snap])
    if (!s1.ok) throw new Error(`snapshot: ${s1.out}`)
    const s0 = await screen()
    note(`result: "${s0.hint}", words clickable ${s0.clickable}, cursor ${s0.cursor}`)
    if (!s0.clickable || s0.cursor !== 'pointer') fail('the words should be clickable')
    if (s0.hint !== oldLine(done, labels)) fail(`done line: the old window would say "${oldLine(done, labels)}"`)

    const words = done.tokens.filter((t) => t.kind === 'word')
    const right = words.filter((t) => t.app_state === 'correct')
    const wrong = words.find((t) => t.app_state !== 'correct' && t.app_state !== 'pending')
    const extra = done.tokens.find((t) => t.kind === 'extra')
    const last = words.at(-1)

    // Esc and a click outside close the menu, nothing saved
    await openWord(right[0].pos)
    await shot('menu-light')
    await js("document.documentElement.dataset.theme = 'dark'")
    await sleep(150)
    await shot('menu-dark')
    await js('delete document.documentElement.dataset.theme')
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' })
    await sleep(150)
    if (await menuOpen()) fail('Esc did not close the menu')
    await openWord(right[0].pos)
    const [hx, hy] = await center('#hint')
    mouse(hx, hy)
    await sleep(150)
    if (await menuOpen()) fail('a click outside did not close the menu')
    note(`Esc and a click outside close the menu; status still "${(await screen()).status}"`)

    await pick(right[0].pos, 'wrong', 'right made wrong')
    await pick(right[0].pos, 'missed', 'changed to missed')
    await shot('corrected')
    await pick(right[0].pos, 'undo', 'undone')
    if (wrong) await pick(wrong.pos, 'correct', 'mistake made correct')
    else note('no mistake in this clip')
    await pick(right[1].pos, 'misspelled', 'right made misspelled')
    await pick(right[1].pos, 'correct', "given back the app's label")
    if (extra) await pick(extra.pos, 'not_said', 'extra marked not said')
    else note('no extra word in this clip')
    if (last.pos !== right[0].pos && last.pos !== right[1].pos) await pick(last.pos, last.app_state === 'wrong' ? 'correct' : 'wrong', 'last word, left corrected')

    // A refused save: the message shows, nothing changes
    const keep = await screen()
    ipcMain.removeHandler('correct')
    ipcMain.handle('correct', (_e, recId, pos, state) => engine.call('/corrections', { rec_id: recId + 1000, pos, state }))
    await openWord(right[2].pos)
    const [x, y] = await center('#menu .menu-item[data-label="wrong"]')
    mouse(x, y)
    await until(win, "document.getElementById('status').textContent.startsWith('Could not')", 5000, 'the refusal')
    const bad = await screen()
    note(`refused save: status "${bad.status}", counters unchanged ${bad.tally === keep.tally}, tint unchanged ` +
      `${JSON.stringify(bad.tinted) === JSON.stringify(keep.tinted)}`)
    if (!bad.status.startsWith('Could not save the correction:') || bad.tally !== keep.tally
      || JSON.stringify(bad.tinted) !== JSON.stringify(keep.tinted)) fail('a refused save should only show its message')
    await shot('refused')
    ipcMain.removeHandler('correct')
    ipcMain.handle('correct', (_e, recId, pos, state) => engine.call('/corrections', { rec_id: recId, pos, state }))

    // Same rows as the old window's History.correct for the same clicks
    const file = join(outDir, `${tag}-clicks.json`)
    writeFileSync(file, JSON.stringify({ rec_id: done.rec_id, clicks }))
    const cmp = await py(['compare', snap, process.env.RECITEFLOW_DB, file])
    for (const line of cmp.out.split(/\r?\n/)) note(`compare: ${line}`)
    if (!cmp.ok) fail('the history rows differ from the old window\'s')
    if (pass) note('PASS')
  } catch (e) {
    fail(e.message)
  }
  engine.off('event', onEvent)
  writeFileSync(join(outDir, `log-${tag}.txt`), log.join('\n') + '\n')
  app.quit()
}
