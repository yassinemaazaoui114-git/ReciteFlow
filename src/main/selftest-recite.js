import { app } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

// Reciting screen check (RECITEFLOW_SELFTEST=recite, with RECITEFLOW_WAV + RECITEFLOW_FILE): replays the recording
// with Ctrl+Enter and watches the page until the end. Per frame: the time from the frame reaching Electron to the
// page's next drawn frame, skipped frames (nothing changed), hide mode ("…", no unsaid word drawn), the first unsaid
// word kept in view, one tone per beep frame, and the longest gap between two drawn frames (a stall).
// RECITEFLOW_HIDE=0 turns hide off first; RECITEFLOW_EXPECT=<n> = the number of correct words expected at the end.
// Then the result screen: the whole text, the counters and the done line (written the old window's way from the
// final words), Recite again (stopped after a few seconds: "(n said)"), and Edit text back to the box.
// Pictures and a log go to test_out/recite/.
const outDir = join(app.getAppPath(), 'test_out', 'recite')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

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

// The old window's done line from the final words and counts (an unsaved replay: no correction hint)
function oldLine(done) {
  const s = done.counts
  const extras = done.tokens.filter((t) => t.kind === 'extra' && t.state === 'extra').map((t) => t.text.slice(1, -1))
  const fixed = done.tokens.filter((t) => t.underline).length
  const total = done.result.total   // the summary's word count
  return `${s.correct} of ${total} words correct` + (s.pending ? ` (${total - s.pending} said)` : '')
    + (extras.length ? '. Extra words: ' + extras.join(', ') : '.')
    + (fixed ? ` ${fixed} fixed by re-reciting (underlined).` : '')
}

const pct = (xs, p) => {
  const s = [...xs].sort((a, b) => a - b)
  return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : NaN
}

// Watches the page from inside it: every redraw of the words, the drawn frames and the tones
const WATCH = `(() => {
  const words = document.getElementById('words')
  const scroll = document.getElementById('scroll')
  const w = window.__watch = { paints: [], tones: 0, maxGap: 0, lastFrame: 0, follow: [], gaps: [], heardAt: {}, colorAt: {} }
  const shown = (el) => {
    if (!el) return true
    const a = el.getBoundingClientRect(), b = scroll.getBoundingClientRect()
    return a.top >= b.top - 1 && a.bottom <= b.bottom + 1
  }
  let seq = 0
  const check = (n) => {
    if (n !== seq || document.getElementById('page').dataset.phase !== 'listening') return   // redrawn again since: that one is checked
    const next = words.querySelector('.w[data-state="pending"]:not([data-heard])')
    const end = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 2
    w.follow.push(next ? shown(next) : end)
  }
  new MutationObserver(() => {
    if (!words.childNodes.length || document.getElementById('page').dataset.phase === 'done') return
    const n = ++seq
    const ws = [...words.querySelectorAll('.w')]
    const heard = ws.filter((e) => e.dataset.heard !== undefined)
    const paint = { colored: ws.filter((e) => e.dataset.state !== 'pending').length,
      pendingDrawn: ws.filter((e) => e.dataset.state === 'pending').length - heard.length, heard: heard.length,
      more: !!words.querySelector('.more') }
    const colored = ws.filter((e) => e.dataset.state !== 'pending')
    requestAnimationFrame(() => {
      paint.at = performance.timeOrigin + performance.now()
      for (const e of heard) w.heardAt[e.dataset.pos] ??= paint.at
      for (const e of colored) w.colorAt[e.dataset.pos] ??= [paint.at, e.dataset.state]
      paint.tally = document.getElementById('tally').textContent
      w.paints.push(paint)
    })
    setTimeout(() => check(n), 700)   // after the follow scroll has settled
  }).observe(words, { childList: true })
  const tick = (t) => {
    if (w.lastFrame && !['Start', 'Recite again'].includes(document.getElementById('go').textContent)) {
      w.maxGap = Math.max(w.maxGap, t - w.lastFrame)
      if (t - w.lastFrame > 50) w.gaps.push([Math.round(performance.timeOrigin + t), Math.round(t - w.lastFrame)])
    }
    w.lastFrame = t
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  const make = AudioContext.prototype.createOscillator
  AudioContext.prototype.createOscillator = function () { w.tones++; return make.call(this) }
  return true
})()`

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const wav = process.env.RECITEFLOW_WAV
  const hideOff = process.env.RECITEFLOW_HIDE === '0'
  const expect = process.env.RECITEFLOW_EXPECT ? Number(process.env.RECITEFLOW_EXPECT) : null
  const tag = basename(wav || 'none', '.wav') + (hideOff ? '-shown' : '-hidden')
  const log = []
  const t0 = Date.now()
  const note = (text) => { log.push(`${((Date.now() - t0) / 1000).toFixed(2)} s  ${text}`); console.log('[selftest]', text) }
  const js = (code) => win.webContents.executeJavaScript(code)
  const shotsAt = []
  const shot = async (name) => shotsAt.push(Date.now()) && writeFileSync(join(outDir, `${tag}-${name}.png`), (await win.webContents.capturePage()).toPNG())
  win.webContents.setBackgroundThrottling(false)   // the window may be behind others while the check runs

  // Frames as Electron receives them: when, whether they change the words, whether they beep
  const frames = []
  let last = null
  let done = null
  const onEvent = (event, data) => {
    if (event === 'frame') {
      const key = JSON.stringify(data.tokens)
      frames.push({ at: Date.now(), changed: key !== last, beep: data.beep })
      last = key
    } else if (event === 'done') done = data
  }
  engine.on('event', onEvent)
  let pass = true
  const fail = (text) => { pass = false; note(`FAIL: ${text}`) }
  try {
    if (!wav) throw new Error('RECITEFLOW_WAV is not set')
    await waitFor(engine, 'ready', 120000)
    await until(win, "!document.getElementById('go').disabled", 10000, 'Start enabled')
    if (hideOff) await js("document.getElementById('hide').click()")
    note(`replay ${basename(wav)}, hide ${hideOff ? 'off' : 'on'}, beep on`)
    await js(WATCH)
    await js("window.__text = document.getElementById('text').value.trim()")
    await js("document.getElementById('text').focus()")
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter', modifiers: ['control'] })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter', modifiers: ['control'] })
    await until(win, "document.getElementById('status').textContent === 'Replaying the recording'", 15000, 'replaying')
    note(`page shown = ${await js("!document.getElementById('page').hidden")}, text box hidden = ${await js("document.getElementById('text').hidden")}`)

    // Pictures part way through, in both themes
    let shots = 0
    while (!done) {
      await sleep(250)
      const colored = await js('window.__watch.paints.at(-1)?.colored ?? 0')
      if (shots === 0 && colored >= 12) {
        for (const theme of ['light', 'dark']) {
          await js(`document.documentElement.dataset.theme = '${theme}'`)
          await sleep(120)
          await shot(`live-${theme}`)
        }
        await js('delete document.documentElement.dataset.theme')
        shots = 1
      }
      if (Date.now() - t0 > 400000) throw new Error('the replay did not end')
    }
    await until(win, "document.getElementById('go').textContent === 'Recite again'", 5000, 'the result screen')
    const w = await js('window.__watch')
    const changed = frames.filter((f) => f.changed)
    note(`${frames.length} frames, ${changed.length} change the words, ${w.paints.length} redraws on the page`)
    if (w.paints.length !== changed.length) fail('a redraw for each changed frame expected')

    // Frame (in Electron) -> the page's next drawn frame, pairing the n-th changed frame with the n-th redraw
    const lags = w.paints.map((p, i) => p.at - (changed[i]?.at ?? NaN)).filter((x) => !Number.isNaN(x))
    note(`frame to screen: median ${pct(lags, 0.5).toFixed(1)} ms, p90 ${pct(lags, 0.9).toFixed(1)} ms, ` +
      `max ${Math.max(...lags).toFixed(1)} ms`)
    if (Math.max(...lags) > 100) fail('a frame took over 100 ms to reach the screen')
    note(`longest gap between two drawn frames while reciting: ${w.maxGap.toFixed(0)} ms; gaps over 50 ms ` +
      `(seconds from start, ms): ${w.gaps.map(([at, g]) => `${((at - t0) / 1000).toFixed(1)}:${g}`).join(' ') || 'none'}; ` +
      `pictures taken at ${shotsAt.map((at) => ((at - t0) / 1000).toFixed(1)).join(', ')} s`)
    if (w.maxGap > 250) fail('the window stalled')

    // Live colours: the coloured words grow during the replay
    const colored = w.paints.map((p) => p.colored)
    note(`coloured words per redraw: ${colored.join(' ')}`)
    if (!(colored.length > 3 && colored[0] < colored.at(-1))) fail('the words were not coloured live')
    // Hide mode
    if (hideOff) {
      if (!w.paints.some((p) => p.pendingDrawn > 0)) fail('unsaid words should be drawn with hide off')
    } else {
      if (w.paints.some((p) => p.pendingDrawn > 0)) fail('an unsaid word was drawn in hide mode')
      if (!w.paints.slice(0, -1).some((p) => p.more)) fail('no "…" while words were left')
    }
    // Heard words: shown plain before their colour. How much sooner a word appears than its colour
    const early = Object.entries(w.colorAt).filter(([pos]) => w.heardAt[pos] !== undefined)
      .map(([pos, [at, state]]) => ({ gain: (at - w.heardAt[pos]) / 1000, state }))
    const lagsC = done.stats.display_lags
    const gains = early.map((e) => e.gain)
    note(`heard early: ${early.length} of ${Object.keys(w.colorAt).length} coloured words, sooner by median ` +
      `${pct(gains, 0.5).toFixed(2)} s, p90 ${pct(gains, 0.9).toFixed(2)} s; then coloured as mistakes: ` +
      `${early.filter((e) => e.state !== 'correct').length}; colour after the word: median ${pct(lagsC, 0.5).toFixed(2)} s, ` +
      `p90 ${pct(lagsC, 0.9).toFixed(2)} s`)
    if (!early.length) fail('no word was shown as heard before its colour')
    if (gains.some((g) => g < 0)) fail('a heard mark came after the colour')
    const inView = w.follow.filter(Boolean).length
    note(`first unsaid word (or the end) in view after a redraw: ${inView} of ${w.follow.length}`)
    if (inView < w.follow.length) fail('the page did not follow the reading')
    const beeps = frames.filter((f) => f.beep).length
    note(`beep frames ${beeps}, tones played ${w.tones}`)
    if (beeps !== w.tones) fail('one tone per beep frame expected')

    note(`last counters: ${w.paints.at(-1)?.tally}`)
    const r = done.result
    note(`done: ${r.correct} of ${r.total} words correct, finished = ${done.finished}, note "${done.note}"`)
    if (expect !== null && r.correct !== expect) fail(`${expect} correct expected`)
    if (!String(w.paints.at(-1)?.tally).startsWith(`${done.counts.correct} Correct`)) fail('the counters differ from the result')
    await shot('after')

    // The result screen
    const screen = () => js(`(() => { const $ = (id) => document.getElementById(id)
      return { hint: $('hint').textContent, status: $('status').textContent, words: $('words').querySelectorAll('.w').length,
        more: !!$('words').querySelector('.more'), tally: $('tally').textContent, edit: !$('edit').hidden,
        hide: !$('hide').disabled, box: $('text').hidden, page: !$('page').hidden } })()`)
    const tallyOf = (c) => ['correct', 'misspelled', 'wrong', 'missed', 'extra', 'pending']
      .map((k, i) => `${c[k] ?? 0} ${['Correct', 'Misspelled', 'Wrong', '[Missed]', '{Extra}', 'Not yet said'][i]}`).join('')
    const checkResult = async (d, what) => {
      const r = await screen()
      note(`${what}: status "${r.status}", result line "${r.hint}"`)
      note(`${what}: ${r.words} words drawn of ${d.tokens.length}, "…" ${r.more}, counters "${r.tally}", ` +
        `Edit text shown ${r.edit}, hide switch enabled ${r.hide}, page shown ${r.page}, text box hidden ${r.box}`)
      if (r.hint !== oldLine(d)) fail(`${what}: the old window would say "${oldLine(d)}"`)
      if (r.words !== d.tokens.length || r.more) fail(`${what}: the whole text is not shown`)
      if (r.tally !== tallyOf(d.counts)) fail(`${what}: the counters differ from the result`)
      if (!r.edit || !r.hide || !r.page || !r.box) fail(`${what}: wrong controls on the result screen`)
      if (r.status !== (d.finished ? 'Finished' : 'Stopped')) fail(`${what}: status`)
    }
    await checkResult(done, 'result')

    // Recite again, stopped after a few seconds
    done = null
    await js("document.getElementById('go').click()")
    await until(win, "document.getElementById('status').textContent === 'Replaying the recording'", 15000, 'replaying again')
    await sleep(6000)
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' })
    while (!done) await sleep(100)
    await until(win, "document.getElementById('go').textContent === 'Recite again'", 5000, 'the result screen again')
    await shot('stopped')
    await checkResult(done, 'Recite again, Esc after 6 s')
    if (!done.counts.pending) fail('the stopped recitation should have words not said')

    // Edit text: back to the box with the same text
    await js("document.getElementById('edit').click()")
    await until(win, "document.getElementById('go').textContent === 'Start'", 2000, 'back to the text screen')
    const back = await js("[document.getElementById('text').hidden, document.getElementById('text').readOnly, " +
      "document.getElementById('text').value.trim() === (window.__text ?? ''), document.getElementById('page').hidden]")
    note(`Edit text: text box shown ${!back[0]}, editable ${!back[1]}, same text ${back[2]}, page hidden ${back[3]}`)
    if (back[0] || back[1] || !back[2] || !back[3]) fail('Edit text did not go back to the box')
    if (pass) note('PASS')
  } catch (e) {
    fail(e.message)
  }
  engine.off('event', onEvent)
  writeFileSync(join(outDir, `log-${tag}.txt`), log.join('\n') + '\n')
  app.quit()
}
