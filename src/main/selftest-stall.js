import { app } from 'electron'
import { execFile } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Stuck engine check (RECITEFLOW_SELFTEST=stall, with RECITEFLOW_WAV + RECITEFLOW_FILE). Two replays that never end
// by themselves, each must end with a plain-words message and a fresh engine:
// A. the recitation does not move: the engine starts with RECITEFLOW_STALL_S=0.2 (set in index.js), so its own
//    check sees the first speech pass as stuck and ends the engine;
// B. the whole engine hangs: its process is suspended part way through a replay, so the event stream goes silent.
// Log and pictures go to test_out/t3.2b/.
const outDir = join(app.getAppPath(), 'test_out', 't3.2b')
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

const suspend = (pid) => new Promise((resolve, reject) => execFile('py',
  ['-3.12', '-c', `import psutil; psutil.Process(${pid}).suspend()`], { windowsHide: true },
  (err) => (err ? reject(err) : resolve())))

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const log = []
  const t0 = Date.now()
  const note = (text) => { log.push(`${((Date.now() - t0) / 1000).toFixed(2)} s  ${text}`); console.log('[selftest]', text) }
  const js = (code) => win.webContents.executeJavaScript(code)
  const shot = async (name) => writeFileSync(join(outDir, `${name}.png`), (await win.webContents.capturePage()).toPNG())
  win.webContents.setBackgroundThrottling(false)
  const screen = () => js(`(() => { const $ = (id) => document.getElementById(id)
    return { message: $('message').hidden ? null : $('message-text').textContent, status: $('status').textContent,
      go: $('go').textContent.trim(), box: !$('text').hidden } })()`)
  const start = async () => {
    await until(win, "!document.getElementById('go').disabled", 20000, 'Start enabled')
    await js("document.getElementById('text').focus()")
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter', modifiers: ['control'] })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter', modifiers: ['control'] })
    await until(win, "document.getElementById('status').textContent === 'Replaying the recording'", 15000, 'replaying')
    return Date.now()
  }
  let pass = true
  const fail = (text) => { pass = false; note(`FAIL: ${text}`) }
  const check = (r, words, what) => {
    note(`${what}: message "${r.message}", status "${r.status}", button "${r.go}", text box shown ${r.box}`)
    if (!r.message || !words.every((w) => r.message.includes(w))) fail(`${what}: the message should say ${words.join(', ')}`)
    if (!r.box || r.go !== 'Start') fail(`${what}: not back on the text screen`)
  }
  try {
    // A. the recitation does not move
    await waitFor(engine, 'ready', 120000)
    note(`engine ready (stall limit ${process.env.RECITEFLOW_STALL_S} s)`)
    process.env.RECITEFLOW_STALL_S = '30'   // the next engine gets the normal limit
    const a = await start()
    await waitFor(engine, 'restarting', 20000)
    note(`A: engine ended ${((Date.now() - a) / 1000).toFixed(2)} s after Start`)
    await sleep(300)
    await shot('a-message')
    check(await screen(), ['stopped responding', 'not saved', 'Starting it again'], 'A')
    await waitFor(engine, 'ready', 120000)
    note(`A: fresh engine ready ${((Date.now() - a) / 1000).toFixed(1)} s after Start; message now "${(await screen()).message}"`)

    // B. the whole engine hangs part way through a replay
    const b = await start()
    await sleep(4000)
    const pid = engine.child.pid
    await suspend(pid)
    const frozen = Date.now()
    note(`B: engine process ${pid} suspended ${((frozen - b) / 1000).toFixed(1)} s after Start`)
    await waitFor(engine, 'restarting', 90000)
    note(`B: noticed ${((Date.now() - frozen) / 1000).toFixed(1)} s after the engine froze`)
    await sleep(300)
    await shot('b-message')
    check(await screen(), ['stopped answering', 'not saved', 'Starting it again'], 'B')
    await waitFor(engine, 'ready', 120000)
    note(`B: fresh engine ready ${((Date.now() - frozen) / 1000).toFixed(1)} s after the freeze`)

    // The fresh engine works: a replay runs again
    await start()
    note('a new replay starts on the fresh engine')
    if (pass) note('PASS')
  } catch (e) {
    fail(e.message)
  }
  writeFileSync(join(outDir, 'log.txt'), log.join('\n') + '\n')
  app.quit()
}
