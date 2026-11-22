import { app } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Motion and details check (RECITEFLOW_SELFTEST=polish, with RECITEFLOW_WAV + RECITEFLOW_FILE; RECITEFLOW_SCALE=1.5
// for a high-DPI run). At the normal size (980x680) and the smallest window (640x460): every screen (text, Settings,
// an engine message, reciting, result, word menu) in both themes, checked for anything cut off or outside the window.
// Motion: a word's colour has no transition or animation; screen changes come in within 200 ms. Keyboard: one word in
// the Tab order after a saved recitation, the arrows move along the words, Enter opens the menu, Esc closes it back on
// the word, a choice is saved and the focus stays on the word. Pictures and a log go to test_out/t3.1/.
const scale = process.env.RECITEFLOW_SCALE || '1'
const outDir = join(app.getAppPath(), 'test_out', 't3.1')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function waitFor(engine, status, ms) {
  return new Promise((resolve, reject) => {
    if (engine.status.status === status) return resolve()
    const timer = setTimeout(() => reject(new Error(`no "${status}" within ${ms} ms`)), ms)
    const on = (s) => { if (s.status === status) { clearTimeout(timer); engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
}

// Visible parts that are cut off, outside the window, or wider than the window; a reading area too short to read
const LAYOUT = `(() => {
  const bad = []
  const W = innerWidth, H = innerHeight
  if (document.documentElement.scrollWidth > W) bad.push('page wider than the window')
  const parts = document.querySelectorAll('button, select, textarea, .option, .page, .settings, .message, .status, .hint, .light, .title, .tally li')
  for (const el of parts) {
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) continue   // not shown
    const name = el.id || el.textContent.trim().slice(0, 24) || el.className
    if (r.left < -0.5 || r.top < -0.5 || r.right > W + 0.5 || r.bottom > H + 0.5) bad.push(name + ' outside the window')
    if (el.matches('button, .option, .title, .tally li') && el.scrollWidth > el.clientWidth + 1) bad.push(name + ' text cut off')
  }
  // the words (or the text box) keep at least two and a half lines
  const page = document.querySelector('.page:not([hidden]) .scroll, textarea:not([hidden])')
  const line = parseFloat(getComputedStyle(page?.matches('textarea') ? page : document.getElementById('words')).lineHeight)
  if (page && page.clientHeight < 2.5 * line) bad.push('reading area only ' + (page.clientHeight / line).toFixed(1) + ' lines tall')
  return { bad, size: W + 'x' + H, dpr: devicePixelRatio }
})()`

async function until(win, fn, ms, what) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await win.webContents.executeJavaScript(fn)) return
    await sleep(50)
  }
  throw new Error(`${what}: not seen within ${ms} ms`)
}

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const log = []
  let failed = 0
  const check = (ok, text) => {
    if (!ok) failed++
    log.push(`${ok ? 'PASS' : 'FAIL'}  ${text}`)
    console.log('[selftest]', ok ? 'PASS' : 'FAIL', text)
  }
  const note = (text) => { log.push(`      ${text}`); console.log('[selftest]', text) }
  const js = (code) => win.webContents.executeJavaScript(code)
  const key = (keyCode, modifiers = []) => {
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers })
    // a button acts on Enter's character, as from a real keyboard
    if (keyCode === 'Return' && !modifiers.length) win.webContents.sendInputEvent({ type: 'char', keyCode: '\r' })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers })
  }
  const click = async (selector) => {
    const r = await js(`(() => { const b = document.querySelector('${selector}').getBoundingClientRect();
      return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) } })()`)
    for (const type of ['mouseDown', 'mouseUp']) win.webContents.sendInputEvent({ type, x: r.x, y: r.y, button: 'left', clickCount: 1 })
  }
  // A picture in each theme and the layout check, at the current size
  const look = async (name, themes = ['light', 'dark']) => {
    for (const theme of themes) {
      await js(`document.documentElement.dataset.theme = '${theme}'`)
      await sleep(350)
      const l = await js(LAYOUT)
      check(!l.bad.length, `${name} ${theme} ${l.size} @${l.dpr}x: ${l.bad.join('; ') || 'nothing cut off'}`)
      writeFileSync(join(outDir, `${name}-${theme}-${win.getSize().join('x')}@${scale}x.png`), (await win.webContents.capturePage()).toPNG())
    }
    await js('delete document.documentElement.dataset.theme')
  }
  const SIZES = [[980, 680], [640, 460]]
  win.webContents.setBackgroundThrottling(false)
  let done = null
  engine.on('event', (event, data) => { if (event === 'done') done = data })

  try {
    await waitFor(engine, 'ready', 120000)
    await until(win, "!document.getElementById('go').disabled", 10000, 'Start enabled')

    // Text screen and Settings, each size
    for (const [w, h] of SIZES) {
      win.setSize(w, h)
      await sleep(300)
      await look('text')
      await click('#open-settings')
      await sleep(250)
      await look('settings')
      key('Escape')
      await sleep(250)
    }

    // Screen changes: short, and nothing on the words
    const enter = await js("getComputedStyle(document.getElementById('text')).animationDuration")
    check(parseFloat(enter) * 1000 <= 200, `a screen comes in within ${parseFloat(enter) * 1000} ms`)

    // An engine message at the smallest size (as the engine sends it when it dies), then the real status back
    win.webContents.send('engine-status', { ...engine.status, status: 'restarting', message: 'The engine stopped unexpectedly' })
    await sleep(300)
    check(await js("document.getElementById('message').getAttribute('role') === 'alert' && !document.getElementById('message').hidden"),
      'an engine message is shown and announced')
    await look('message')
    win.webContents.send('engine-status', engine.status)
    await until(win, "!document.getElementById('go').disabled", 10000, 'Start enabled again')

    // Reciting at the smallest size
    await js("document.getElementById('text').focus()")
    key('Enter', ['control'])
    await until(win, "document.getElementById('words').querySelectorAll('.w:not([data-state=\"pending\"])').length >= 12", 60000, 'coloured words')
    const still = await js(`(() => { const w = document.querySelector('#words .w'); const s = getComputedStyle(w)
      return { transition: s.transitionDuration, animation: s.animationName } })()`)
    check(still.transition === '0s' && still.animation === 'none',
      `a word's colour changes at once (transition ${still.transition}, animation ${still.animation})`)
    await look('reciting')
    win.setSize(980, 680)
    await sleep(300)
    await look('reciting')
    while (!done) await sleep(200)
    await until(win, "document.getElementById('go').textContent === 'Recite again'", 5000, 'the result screen')
    note(`result: ${done.result.correct} of ${done.result.total} correct, saved #${done.rec_id} (temporary history)`)
    await look('result')
    win.setSize(640, 460)
    await sleep(300)
    await look('result')

    // Keyboard corrections
    const stops = await js("document.querySelectorAll('#words .w[tabindex=\"0\"]').length")
    check(stops === 1, `one word in the Tab order (${stops})`)
    await js("document.getElementById('go').focus()")
    // Tab order from Recite again wraps round to the top; walk until the word takes the focus
    let reached = false
    for (let i = 0; i < 12 && !reached; i++) {
      key('Tab')
      await sleep(40)
      reached = await js("document.activeElement?.classList.contains('w') ?? false")
    }
    check(reached, 'Tab reaches the words')
    for (let i = 0; i < 3; i++) key('Right')
    await sleep(100)
    const at = await js("[...document.querySelectorAll('#words .w')].indexOf(document.activeElement)")
    check(at === 3, `three Right arrows move to the fourth word (${at})`)
    key('Return')
    await sleep(250)
    check(await js("!document.getElementById('menu').hidden && document.activeElement.classList.contains('menu-item')"),
      'Enter opens the menu, the focus is in it')
    await look('menu', ['light', 'dark'])
    key('Escape')
    await sleep(200)
    check(await js("document.getElementById('menu').hidden && [...document.querySelectorAll('#words .w')].indexOf(document.activeElement) === 3"),
      'Esc closes the menu and the focus is back on the word')
    key('Return')
    await sleep(200)
    key('Down')
    const label = await js('document.activeElement.dataset.label')
    key('Return')
    await sleep(100)
    note(`picked "${label}", focus on ${await js("document.activeElement.className + ' ' + document.activeElement.textContent")}`)
    await until(win, "document.getElementById('status').textContent.startsWith('Saved #')", 10000,
      `the correction saved (status "${await js("document.getElementById('status').textContent")}")`)
    check(await js("[...document.querySelectorAll('#words .w')].indexOf(document.activeElement) === 3"),
      `a choice ("${label}") is saved and the focus stays on the word`)
    await look('corrected', ['light'])
  } catch (err) {
    check(false, err.message)
  }
  writeFileSync(join(outDir, `log@${scale}x.txt`), log.join('\n') + '\n', 'utf8')
  console.log('[selftest]', failed ? `${failed} FAILED` : 'ALL PASS', '- log and pictures in', outDir)
  app.quit()
}
