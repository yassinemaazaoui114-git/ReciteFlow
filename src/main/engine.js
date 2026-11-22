import { execFile, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { join } from 'node:path'

const RESTARTS = 3          // restarts allowed within RESTART_WINDOW_MS, then the page shows "Start again"
const RESTART_WINDOW_MS = 60000
const RESTART_DELAY_MS = 1000
const STOP_WAIT_MS = 3000   // on quit, the engine gets this long to stop after /shutdown before it is killed

// The real python.exe behind "py -3.12", so that stopping it stops the engine (not just the py launcher).
function findPython() {
  return new Promise((resolve, reject) => {
    execFile('py', ['-3.12', '-c', 'import sys; print(sys.executable)'], { windowsHide: true, timeout: 15000 },
      (err, stdout) => (err ? reject(new Error('Python 3.12 not found (py -3.12)')) : resolve(stdout.trim())))
  })
}

// Runs engine/server.py, keeps it running, and reports its status:
// { status: 'starting' | 'loading' | 'ready' | 'error' | 'restarting' | 'stopped', gpu, loadSeconds, message }
export class Engine extends EventEmitter {
  constructor(engineDir) {
    super()
    this.dir = engineDir
    this.status = { status: 'starting', gpu: null, loadSeconds: null, message: null }
    this.child = null
    this.port = null
    this.token = null
    this.restarts = []
    this.quitting = false
  }

  _set(patch) {
    this.status = { ...this.status, ...patch }
    this.emit('status', this.status)
  }

  async start() {
    this._set({ status: 'starting', loadSeconds: null, message: null })
    let python
    try {
      python = this.python || (this.python = await findPython())
    } catch (e) {
      return this._set({ status: 'stopped', message: e.message })
    }
    if (this.quitting) return
    this.token = randomBytes(32).toString('hex')
    const child = spawn(python, ['-u', join(this.dir, 'server.py')], {
      cwd: this.dir,
      env: { ...process.env, RECITEFLOW_TOKEN: this.token, RECITEFLOW_PARENT_PID: String(process.pid),
        PYTHONIOENCODING: 'utf-8' },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    this.child = child
    let stderr = ''
    let out = ''
    child.stderr.on('data', (d) => { stderr = (stderr + d).slice(-2000) })
    child.stdout.on('data', (d) => {
      if (this.port && child === this.child) return
      out += d
      const line = out.split(/\r?\n/)[0]
      if (out.includes('\n')) {
        try {
          this.port = JSON.parse(line).port
          this._listen(child)
        } catch { /* not the port line; the exit handler reports it */ }
      }
    })
    child.on('error', (e) => { stderr += e.message })
    child.on('exit', (code) => {
      if (child !== this.child) return
      this.child = null
      this.port = null
      if (this.quitting) return
      const last = stderr.trim().split(/\r?\n/).slice(-3).join(' ')
      this._died(`The engine stopped (exit code ${code})${last ? ': ' + last : ''}`)
    })
  }

  _died(message) {
    const now = Date.now()
    this.restarts = this.restarts.filter((t) => now - t < RESTART_WINDOW_MS)
    if (this.restarts.length >= RESTARTS) {
      return this._set({ status: 'stopped', message: `${message}. It stopped ${RESTARTS + 1} times in a minute, so it was not started again.` })
    }
    this.restarts.push(now)
    this._set({ status: 'restarting', message })
    setTimeout(() => { if (!this.quitting && !this.child) this.start() }, RESTART_DELAY_MS)
  }

  // "Start again" in the page, after it gave up.
  restart() {
    if (this.child) return
    this.restarts = []
    this.start()
  }

  request(path, options = {}) {
    if (!this.port) return Promise.reject(new Error('engine not running'))
    return fetch(`http://127.0.0.1:${this.port}${path}`, {
      ...options,
      headers: { ...options.headers, Authorization: `Bearer ${this.token}` }
    })
  }

  // A JSON call for the page: the engine's answer, or { error } in plain words (never throws).
  async call(path, body) {
    try {
      const res = await this.request(path, body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      })
      return await res.json()
    } catch {
      return { error: 'The engine is not running.' }
    }
  }

  // Read the event stream and turn engine events into status updates.
  async _listen(child) {
    try {
      const res = await this.request('/events')
      const decoder = new TextDecoder()
      let buf = ''
      for await (const part of res.body) {
        buf += decoder.decode(part, { stream: true })
        let end
        while ((end = buf.indexOf('\n\n')) >= 0) {
          this._event(buf.slice(0, end))
          buf = buf.slice(end + 2)
        }
      }
    } catch { /* the engine stopped; its exit handler reports it */ }
    // The stream closed while the engine still runs: listen again.
    if (child === this.child && !this.quitting) setTimeout(() => { if (child === this.child) this._listen(child) }, 500)
  }

  _event(block) {
    let event = 'message'
    let data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data += line.slice(6)
    }
    if (!data) return
    const value = JSON.parse(data)
    if (event === 'state') {
      this._set({ status: value.state, gpu: value.gpu, loadSeconds: value.load_s,
        message: value.state === 'error' ? value.error : null })
    }
    this.emit('event', event, value)
  }

  // Stop the engine on quit: ask it to stop, kill it if it does not.
  stop() {
    this.quitting = true
    const child = this.child
    if (!child) return Promise.resolve()
    return new Promise((resolve) => {
      const timer = setTimeout(() => { child.kill(); resolve() }, STOP_WAIT_MS)
      child.once('exit', () => { clearTimeout(timer); resolve() })
      this.request('/shutdown', { method: 'POST' }).catch(() => child.kill())
    })
  }
}
