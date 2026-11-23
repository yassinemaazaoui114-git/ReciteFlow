import { app, nativeTheme } from 'electron'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Settings check, in two runs (the self-test settings folder is a temporary one, kept between them):
// RECITEFLOW_SELFTEST=settings: starts from no saved theme (Windows) -> the settings button opens Settings -> Light,
// then Dark, each timed from the click to the first drawn frame in the new colours -> Esc goes back to the text box.
// RECITEFLOW_SELFTEST=settings-restart: the window opens dark, Dark is checked -> Windows again, saved. No engine.
// Pictures and a log go to test_out/settings/.
const outDir = join(app.getAppPath(), 'test_out', 'settings')
const file = () => join(app.getPath('userData'), 'window-settings.json')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const saved = () => { try { return JSON.parse(readFileSync(file(), 'utf8')).theme } catch { return null } }

// Click a theme and time it to the first frame drawn with the new page colour
const timedPick = (theme) => `new Promise((done) => {
  const bg = () => getComputedStyle(document.body).backgroundColor
  const before = bg()
  const t = performance.now()
  document.querySelector('[data-theme-pick="${theme}"]').click()
  const tick = () => {
    if (bg() !== before) done(performance.now() - t)
    else if (performance.now() - t > 3000) done(-1)
    else requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
})`

export async function run(win) {
  const restart = process.env.RECITEFLOW_SELFTEST === 'settings-restart'
  mkdirSync(outDir, { recursive: true })
  const log = []
  let failed = 0
  const check = (ok, text) => {
    if (!ok) failed++
    log.push(`${ok ? 'PASS' : 'FAIL'}  ${text}`)
    console.log('[selftest]', ok ? 'PASS' : 'FAIL', text)
  }
  const js = (code) => win.webContents.executeJavaScript(code)
  const dark = () => js("matchMedia('(prefers-color-scheme: dark)').matches")
  const checked = () => js("document.querySelector('[data-theme-pick][aria-checked=\"true\"]')?.dataset.themePick")
  const shot = async (name) => {
    await sleep(300)
    writeFileSync(join(outDir, name), (await win.webContents.capturePage()).toPNG())
  }
  const key = (keyCode) => {
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode })
  }
  // A real mouse click in the middle of an element
  const click = async (selector) => {
    const r = await js(`(() => { const b = document.querySelector('${selector}').getBoundingClientRect();
      return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) } })()`)
    for (const type of ['mouseDown', 'mouseUp']) win.webContents.sendInputEvent({ type, x: r.x, y: r.y, button: 'left', clickCount: 1 })
  }

  try {
    if (!restart) rmSync(file(), { force: true })
    await new Promise((r) => (win.webContents.isLoading() ? win.webContents.once('did-finish-load', r) : r()))
    await sleep(800)
    if (!restart) {
      check(saved() === null, 'no theme saved yet')
      check(await checked() === 'system', 'Windows is the starting choice')
      check(await dark() === nativeTheme.shouldUseDarkColors, 'the window follows Windows')
      await click('#open-settings')
      await sleep(200)
      check(await js("!document.getElementById('settings').hidden && document.getElementById('text').hidden"),
        'the settings button opens Settings in the text box\'s place')
      check(await js("document.activeElement.dataset.themePick === 'system'"), 'the current choice has the focus')
      // dark then light: one of them always differs from what Windows shows, and the second differs from the first
      for (const theme of ['dark', 'light']) {
        const ms = await js(timedPick(theme))
        await sleep(100)
        check(ms >= 0 && await dark() === (theme === 'dark') && await checked() === theme && saved() === theme,
          `${theme}: window changed in ${ms.toFixed(1)} ms (first drawn frame), checked and saved`)
        await shot(`settings-${theme}.png`)
      }
      await js("document.querySelector('[data-theme-pick=\"dark\"]').focus()")
      key('Left')
      await sleep(200)
      check(await checked() === 'light' && saved() === 'light' && !(await dark()), 'arrow left picks Light')
      key('Right')
      await sleep(200)
      check(await checked() === 'dark' && saved() === 'dark' && await dark(), 'arrow right picks Dark')
      key('Escape')
      await sleep(200)
      check(await js("document.getElementById('settings').hidden && !document.getElementById('text').hidden && document.activeElement.id === 'text'"),
        'Esc goes back to the text box')
      await shot('text-dark.png')
    } else {
      check(saved() === 'dark', 'Dark was saved by the first run')
      check(await dark() && await checked() === 'dark', 'after a restart the window opens dark, Dark checked')
      const bg = win.getBackgroundColor().toUpperCase()
      check(bg.startsWith('#0D1424') || bg.endsWith('0D1424'), `window background before the page draws: ${bg}`)
      await shot('restart-dark.png')
      await js("document.getElementById('open-settings').click()")
      await sleep(200)
      await js("document.querySelector('[data-theme-pick=\"system\"]').click()")
      await sleep(300)
      check(saved() === 'system' && await checked() === 'system' && await dark() === nativeTheme.shouldUseDarkColors,
        'Windows picked again: follows Windows, saved')
    }
  } catch (err) {
    check(false, `error: ${err.message}`)
  }
  log.push(failed ? `${failed} FAILED` : 'ALL PASS')
  writeFileSync(join(outDir, restart ? 'log-restart.txt' : 'log.txt'), log.join('\n') + '\n', 'utf8')
  console.log('[selftest]', failed ? `${failed} FAILED` : 'ALL PASS')
  app.quit()
}
