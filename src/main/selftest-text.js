import { app } from 'electron'
import { execFile, execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Text screen check (RECITEFLOW_SELFTEST=text): Start is disabled while the model loads -> the mic list is the
// engine's -> hide and beep start on -> an empty text is refused -> "keep" is saved and still set after an engine
// restart -> a text typed in the box and Ctrl+Enter start a recitation (the mic, or the replay with
// RECITEFLOW_WAV) -> Esc stops it -> Edit text goes back to the box. Pictures and a log go to test_out/text/ (suffix "-replay" with RECITEFLOW_WAV).
const outDir = join(app.getAppPath(), 'test_out', 'text')
const ENGINE_DIR = join(app.getAppPath(), 'engine')

function waitFor(engine, status, ms) {
  return new Promise((resolve, reject) => {
    if (engine.status.status === status) return resolve()
    const timer = setTimeout(() => reject(new Error(`no "${status}" within ${ms} ms`)), ms)
    const on = (s) => { if (s.status === status) { clearTimeout(timer); engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Wait until fn() (run in the page) is true
async function until(win, fn, ms, what) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await win.webContents.executeJavaScript(fn)) return
    await sleep(50)
  }
  throw new Error(`${what}: not seen within ${ms} ms`)
}

// The microphones as recite.input_devices lists them, asked directly (no .pyc written)
function engineMics() {
  return new Promise((resolve, reject) => {
    execFile('py', ['-3.12', '-B', '-c', 'import json, recite; print(json.dumps([n for _, n in recite.input_devices()]))'],
      { cwd: ENGINE_DIR, windowsHide: true, timeout: 60000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } },
      (err, stdout) => (err ? reject(err) : resolve(JSON.parse(stdout.trim().split(/\r?\n/).pop()))))
  })
}

function key(win, keyCode, modifiers = []) {
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers })
  win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers })
}

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const replay = !!process.env.RECITEFLOW_WAV
  const tag = replay ? '-replay' : ''
  const log = []
  const t0 = Date.now()
  const note = (text) => { log.push(`${((Date.now() - t0) / 1000).toFixed(2)} s  ${text}`); console.log('[selftest]', text) }
  const js = (code) => win.webContents.executeJavaScript(code)
  const status = () => js("document.getElementById('status').textContent")
  const shot = async (name) => {
    await sleep(300)
    writeFileSync(join(outDir, name), (await win.webContents.capturePage()).toPNG())
  }
  const frames = []
  const onEvent = (event) => { if (event === 'frame') frames.push(Date.now()) }
  engine.on('event', onEvent)
  try {
    await waitFor(engine, 'loading', 30000)
    await sleep(300)
    const early = await js("[document.getElementById('go').disabled, document.getElementById('status').textContent]")
    note(`while loading: Start disabled = ${early[0]}, status "${early[1]}"`)
    if (!early[0]) throw new Error('Start was enabled before the model was ready')
    await shot(`1-loading${tag}.png`)
    await waitFor(engine, 'ready', 120000)
    await until(win, "!document.getElementById('go').disabled && document.getElementById('mic').options.length > 0", 10000, 'Start enabled')
    await sleep(500)
    note(`ready: status "${await status()}", engine light "${await js("document.getElementById('engine').textContent")}"`)
    for (const theme of ['light', 'dark']) {
      await js(`document.documentElement.dataset.theme = '${theme}'`)
      await shot(`2-edit-${theme}${tag}.png`)
    }
    await js('delete document.documentElement.dataset.theme')

    if (replay) {
      const box = await js("document.getElementById('text').value")
      note(`replay: box has the RECITEFLOW_FILE text (${box.split(/\s+/).filter(Boolean).length} words), ` +
        `mic picker disabled = ${await js("document.getElementById('mic').disabled")}, ` +
        `keep disabled = ${await js("document.getElementById('keep').disabled")}`)
    } else {
      // Mic list, against recite.input_devices
      const page = await js("[...document.getElementById('mic').options].map((o) => o.textContent)")
      const mics = await engineMics()
      const same = JSON.stringify(page) === JSON.stringify(['Default microphone', ...mics])
      note(`mic list (${page.length}): ${JSON.stringify(page)}`)
      note(`input_devices: ${JSON.stringify(['Default microphone', ...mics])} -> ${same ? 'same' : 'DIFFERENT'}`)
      if (!same) throw new Error('the mic list differs from input_devices')

      // Defaults, as the old window: hide and beep on at every start
      const defaults = await js("[document.getElementById('hide').checked, document.getElementById('beep').checked]")
      note(`hide on = ${defaults[0]}, beep on = ${defaults[1]}`)
      if (!defaults[0] || !defaults[1]) throw new Error('hide and beep must start on')

      // Empty text
      await js("document.getElementById('text').value = '   '; document.getElementById('go').click()")
      await sleep(300)
      const refused = await status()
      note(`Start with an empty text -> status "${refused}", button "${await js("document.getElementById('go').textContent")}"`)
      if (refused !== 'Paste a text first.' || frames.length) throw new Error('an empty text was not refused')
      await shot('3-empty.png')

      // Keep: saved in the history settings, still set after the engine restarts
      const before = (await engine.call('/settings')).keep_audio
      await js("document.getElementById('keep').click()")
      await sleep(500)
      const saved = (await engine.call('/settings')).keep_audio
      note(`keep was ${before}, clicked -> engine says ${saved}`)
      if (saved === before) throw new Error('keep was not saved')
      execFileSync('taskkill', ['/PID', String(engine.child.pid), '/F'])
      await waitFor(engine, 'restarting', 5000)
      await waitFor(engine, 'ready', 120000)
      await until(win, "!document.getElementById('keep').disabled", 10000, 'keep offered again')
      const after = await js("document.getElementById('keep').checked")
      note(`engine restarted -> keep in the window = ${after}`)
      if (after !== saved) throw new Error('keep was not remembered')
      await js("document.getElementById('keep').click()")
      await sleep(500)
      note(`clicked back -> engine says ${(await engine.call('/settings')).keep_audio}`)
    }

    // Type a text and press Ctrl+Enter
    if (!replay) {
      await js("document.getElementById('text').value = ''; document.getElementById('text').focus()")
      win.webContents.insertText('Say it back slowly, word by word.')
      await sleep(200)
      note(`typed: "${await js("document.getElementById('text').value")}"`)
    } else {
      await js("document.getElementById('text').focus()")
    }
    const startAt = Date.now()
    key(win, 'Enter', ['control'])
    await until(win, "document.getElementById('go').textContent === 'Stop'", 3000, 'Stop button')
    note(`Ctrl+Enter -> status "${await status()}", text box read-only = ${await js("document.getElementById('text').readOnly")}`)
    await until(win, `document.getElementById('status').textContent === '${replay ? 'Replaying the recording' : 'Listening'}'`,
      15000, 'listening status')
    note(`-> status "${await status()}" ${Date.now() - startAt} ms after Ctrl+Enter, first frame ${frames[0] - startAt} ms`)
    await shot(`4-listening${tag}.png`)
    await sleep(replay ? 6000 : 2000)
    key(win, 'Escape')
    await sleep(100)
    note(`Esc -> status "${await status()}", button "${await js("document.getElementById('go').textContent.trim()")}"`)
    await until(win, "document.getElementById('go').textContent === 'Recite again'", 20000, 'the result')
    note(`done: status "${await status()}", ${frames.length} frames`)
    await shot(`5-stopped${tag}.png`)
    await js("document.getElementById('edit').click()")
    await until(win, "document.getElementById('go').textContent === 'Start'", 2000, 'back to the text screen')
    note(`Edit text -> text box editable = ${!(await js("document.getElementById('text').readOnly"))}`)
    note('PASS')
  } catch (e) {
    note(`FAIL: ${e.message}`)
  }
  engine.off('event', onEvent)
  writeFileSync(join(outDir, `log${tag}.txt`), log.join('\n') + '\n')
  app.quit()
}
