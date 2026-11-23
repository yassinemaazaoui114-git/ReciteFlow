// Speed and memory check (RECITEFLOW_SELFTEST=speed, with RECITEFLOW_WAV + RECITEFLOW_FILE), driven by
// engine/check_speed.py, which measures from outside. Prints "[speed] <mark> <epoch ms>" lines: the main process
// start, the window shown, the engine ready, the replay started (after RECITEFLOW_IDLE_S seconds idle, default 20)
// and done (with the result), then quits.
import { app } from 'electron'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const mark = (name, extra = '') => console.log(`[speed] ${name} ${Date.now()}${extra ? ' ' + extra : ''}`)

export async function run(win, engine) {
  mark('main', `${Math.round(Date.now() - process.uptime() * 1000)}`)
  win.once('show', () => mark('window'))
  win.webContents.setBackgroundThrottling(false)
  let done = null
  engine.on('event', (event, data) => { if (event === 'done') done = data })
  await new Promise((resolve) => {
    const on = (s) => { if (s.status === 'ready') { engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
  mark('ready', `load_s=${engine.status.loadSeconds}`)
  const js = (code) => win.webContents.executeJavaScript(code)
  while (await js("document.getElementById('go').disabled")) await sleep(50)
  await sleep(1000 * Number(process.env.RECITEFLOW_IDLE_S || 20))
  await js("document.getElementById('text').focus()")
  mark('start')
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter', modifiers: ['control'] })
  win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter', modifiers: ['control'] })
  while (!done) await sleep(100)
  mark('done', `${done.result.correct}/${done.result.total}`)
  await sleep(3000)
  app.quit()
}
