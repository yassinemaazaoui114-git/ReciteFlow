// Start page: shows the versions and the GPU, proves the page has no Node access, and shows the
// engine's status with a "Start again" button when it gave up restarting.
document.getElementById('node').textContent =
  `Node in the page: ${typeof require === 'undefined' && typeof process === 'undefined' ? 'none (safe)' : 'present!'}`

const engineLine = document.getElementById('engine')
const again = document.getElementById('again')
again.addEventListener('click', () => window.reciteflow.restartEngine())

function showEngine(s) {
  const text = {
    starting: 'Starting the engine...',
    loading: 'Loading the speech model...',
    ready: `Engine ready, GPU: ${s.gpu ?? 'none found'}`,
    restarting: `${s.message}. Starting it again...`,
    error: s.message,
    stopped: s.message
  }[s.status]
  engineLine.textContent = text ?? s.status
  engineLine.dataset.status = s.status
  again.hidden = s.status !== 'stopped'
}
window.reciteflow.onEngineStatus(showEngine)
showEngine(await window.reciteflow.engineStatus())

const info = await window.reciteflow.systemInfo()
const rows = [['Electron', info.electron], ['Node', info.node], ['Chrome', info.chrome],
  ['Python', info.python ?? 'not found'], ['GPU', info.gpu ?? 'not found']]
for (const [name, value] of rows) {
  const dt = document.createElement('dt')
  const dd = document.createElement('dd')
  dt.textContent = name
  dd.textContent = value
  document.getElementById('info').append(dt, dd)
}
