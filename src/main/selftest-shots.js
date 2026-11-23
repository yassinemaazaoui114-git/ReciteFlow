import { app } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// README pictures (RECITEFLOW_SELFTEST=shots, with RECITEFLOW_WAV + RECITEFLOW_FILE, best with RECITEFLOW_SCALE=1.5):
// the text screen, a recitation part way through, the result and the word menu, saved in docs/images/. The words and
// colours are the engine's own; the replay-only labels (mic picker, status) show the microphone wording instead.
const outDir = join(app.getAppPath(), 'docs', 'images')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function until(win, fn, ms, what) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await win.webContents.executeJavaScript(fn)) return
    await sleep(50)
  }
  throw new Error(`${what}: not seen within ${ms} ms`)
}

const MIC_WORDING = `(() => {
  const opt = document.getElementById('mic').selectedOptions[0]
  if (opt) opt.textContent = 'Default microphone'
  const s = document.getElementById('status')
  if (s.textContent === 'Replaying the recording') s.textContent = 'Listening'
})()`

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const js = (code) => win.webContents.executeJavaScript(code)
  const theme = (t) => js(`document.documentElement.dataset.theme = '${t}'`).then(() => sleep(250))
  const shot = async (name) => {
    await js(MIC_WORDING)
    await sleep(80)
    writeFileSync(join(outDir, `${name}.png`), (await win.webContents.capturePage()).toPNG())
    console.log('[selftest] picture', name)
  }
  win.webContents.setBackgroundThrottling(false)
  let done = false
  engine.on('event', (event) => { if (event === 'done') done = true })
  try {
    await until(win, "!document.getElementById('go').disabled", 120000, 'Start enabled')
    await js("document.getElementById('text').blur()")
    await theme('dark')
    await shot('text-dark')
    await theme('light')
    await js("document.getElementById('text').focus()")
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter', modifiers: ['control'] })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter', modifiers: ['control'] })
    // part way through, once mistakes are on the page
    await until(win, "document.querySelectorAll('#words .w:not([data-state=\"pending\"])').length >= 34", 90000, 'words coloured')
    await shot('reciting-light')
    while (!done) await sleep(100)
    await until(win, "document.getElementById('go').textContent.includes('Recite again')", 5000, 'the result screen')
    await sleep(400)
    await shot('result-light')
    await theme('dark')
    await shot('result-dark')
    // the word menu on the first mistake
    const [x, y] = await js(`(() => { const r = document.querySelector('#words .w[data-state="misspelled"], #words .w[data-state="wrong"]').getBoundingClientRect()
      return [r.left + r.width / 2, r.top + r.height / 2] })()`)
    for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) {
      win.webContents.sendInputEvent({ type, x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 })
    }
    await until(win, "!document.getElementById('menu').hidden", 3000, 'the word menu')
    await sleep(300)
    await shot('menu-dark')
    console.log('[selftest] PASS')
  } catch (e) {
    console.log('[selftest] FAIL:', e.message)
  }
  app.quit()
}
