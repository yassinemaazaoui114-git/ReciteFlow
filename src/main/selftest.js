import { app } from 'electron'
import { execFileSync, spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Engine check (run with RECITEFLOW_SELFTEST=engine): engine ready -> request without token refused -> kill it
// -> it restarts -> ready -> killed 3 more times -> gives up, "Start again" -> clicked -> ready -> an engine whose
// parent process ends exits by itself -> quit. Screenshots and a log go to test_out/engine/.
const outDir = join(app.getAppPath(), 'test_out', 'engine')

function waitFor(engine, status, ms) {
  return new Promise((resolve, reject) => {
    if (engine.status.status === status) return resolve()
    const timer = setTimeout(() => reject(new Error(`no "${status}" within ${ms} ms`)), ms)
    const on = (s) => { if (s.status === status) { clearTimeout(timer); engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
}

const pageText = (win) => win.webContents.executeJavaScript("document.getElementById('engine').textContent")

async function shot(win, name) {
  await new Promise((r) => setTimeout(r, 300))   // let the page repaint
  writeFileSync(join(outDir, name), (await win.webContents.capturePage()).toPNG())
}

const kill = (pid) => execFileSync('taskkill', ['/PID', String(pid), '/F'])

// An engine started with a short-lived parent must exit by itself when that parent ends.
async function orphanCheck(engine, note) {
  const parent = spawn('ping', ['-n', '60', '127.0.0.1'], { windowsHide: true, stdio: 'ignore' })
  const child = spawn(engine.python, ['-u', join(engine.dir, 'server.py')], {
    cwd: engine.dir,
    // a model that cannot load (offline, no such name): this check needs no GPU
    env: { ...process.env, RECITEFLOW_TOKEN: 'x', RECITEFLOW_PARENT_PID: String(parent.pid),
      RECITEFLOW_MODEL: 'no-such-model', HF_HUB_OFFLINE: '1' },
    stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true
  })
  await new Promise((r) => child.stdout.once('data', r))
  const exited = new Promise((r) => child.once('exit', () => r(Date.now())))
  const t = Date.now()
  parent.kill()
  const gone = await Promise.race([exited, new Promise((r) => setTimeout(() => r(null), 5000))])
  if (gone === null) { child.kill(); throw new Error('the engine did not exit when its parent ended') }
  note(`parent ended -> engine exited by itself after ${gone - t} ms`)
}

export async function run(win, engine) {
  mkdirSync(outDir, { recursive: true })
  const log = []
  const t0 = Date.now()
  const note = (text) => { log.push(`${((Date.now() - t0) / 1000).toFixed(2)} s  ${text}`); console.log('[selftest]', text) }
  try {
    await waitFor(engine, 'loading', 30000)
    note(`page: ${await pageText(win)}`)
    await shot(win, '1-loading.png')
    await waitFor(engine, 'ready', 120000)
    note(`page: ${await pageText(win)}`)
    note(`model loaded in ${engine.status.loadSeconds} s (load + GPU warm-up)`)
    await shot(win, '2-ready.png')
    const bad = await fetch(`http://127.0.0.1:${engine.port}/health`)
    const wrong = await fetch(`http://127.0.0.1:${engine.port}/health`, { headers: { Authorization: 'Bearer wrong' } })
    note(`request without token -> HTTP ${bad.status}, wrong token -> HTTP ${wrong.status}`)
    if (bad.status !== 403 || wrong.status !== 403) throw new Error('a request without the token was answered')
    const pid = engine.child.pid
    kill(pid)
    note(`killed engine pid ${pid}`)
    await waitFor(engine, 'restarting', 5000)
    note(`page: ${await pageText(win)}`)
    await shot(win, '3-restarting.png')
    const t1 = Date.now()
    await waitFor(engine, 'ready', 120000)
    note(`ready again, new pid ${engine.child.pid}, ${((Date.now() - t1) / 1000).toFixed(1)} s after the restart ` +
      `message, model loaded in ${engine.status.loadSeconds} s`)
    for (let i = 0; i < 3; i++) {
      kill(engine.child.pid)
      note(`killed again (${i + 2})`)
      if (i < 2) { await waitFor(engine, 'starting', 5000); await waitFor(engine, 'loading', 30000) }
    }
    await waitFor(engine, 'stopped', 5000)
    const shown = await win.webContents.executeJavaScript("!document.getElementById('again').hidden")
    note(`gave up: "${engine.status.message}"; Start again shown: ${shown}`)
    await shot(win, '4-stopped.png')
    if (!shown) throw new Error('no Start again button')
    await win.webContents.executeJavaScript("document.getElementById('again').click()")
    await waitFor(engine, 'ready', 120000)
    note(`Start again clicked -> ready, model loaded in ${engine.status.loadSeconds} s`)
    await shot(win, '5-ready-again.png')
    await orphanCheck(engine, note)
    note(`quitting, engine pid ${engine.child.pid} must stop`)
    note('PASS')
  } catch (e) {
    note(`FAIL: ${e.message}`)
  }
  writeFileSync(join(outDir, 'log.txt'), log.join('\n') + '\n')
  app.quit()
}
