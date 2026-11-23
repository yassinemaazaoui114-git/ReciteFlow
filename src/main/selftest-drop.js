import { app, nativeTheme } from 'electron'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Drag and drop check (RECITEFLOW_SELFTEST=drop, with RECITEFLOW_WAV + RECITEFLOW_FILE for the replay). Drags are
// sent to the page as drag events carrying a file; the file's path (which a drag from Explorer would give) comes from
// RECITEFLOW_PICK. On the text screen: the drop zone shows over the text box while a file is over the window and
// hides when it leaves; a dropped .txt, photo and PDF each open the pick popup with their text (a long PDF asks for
// the pages); a wrong type and two files at once give a message; dragged text (not a file) shows no zone. While
// reciting and on the result screen: no zone, a drop opens nothing. Pictures of the zone in both themes and at
// 640x460. Log and pictures in test_out/drop/. Uses the generated test files (engine/check_ocr.py make,
// engine/check_pdf.py make). Loads the speech model once: the GPU must be free.
const outDir = join(app.getAppPath(), 'test_out', 'drop')
const gen = join(app.getAppPath(), 'engine', 'clips', 'ocr', 'generated')
const pdfs = join(app.getAppPath(), 'engine', 'clips', 'ocr', 'pdf')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const words = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}' ]+/gu, ' ').split(/\s+/).filter(Boolean)

function waitFor(engine, status, ms) {
  return new Promise((resolve, reject) => {
    if (engine.status.status === status) return resolve()
    const timer = setTimeout(() => reject(new Error(`no "${status}" within ${ms} ms`)), ms)
    const on = (s) => { if (s.status === status) { clearTimeout(timer); engine.off('status', on); resolve() } }
    engine.on('status', on)
  })
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
  const js = (code) => win.webContents.executeJavaScript(code)
  const shot = async (name) => {
    await sleep(300)
    writeFileSync(join(outDir, name), (await win.webContents.capturePage()).toPNG())
  }
  const until = async (code, ms = 30000) => {
    const t = Date.now()
    while (Date.now() - t < ms) {
      if (await js(code)) return (Date.now() - t) / 1000
      await sleep(50)
    }
    return -1
  }
  // A drag event on the element under the middle of the text box (or the page), carrying files or text.
  // Answers whether the page took the drag (default prevented).
  const drag = (type, names = ['file.txt'], text = false) => js(`(() => {
    const dt = new DataTransfer()
    ${text ? "dt.setData('text/plain', 'a few words')" : `for (const n of ${JSON.stringify(names)}) dt.items.add(new File(['x'], n))`}
    const r = document.getElementById('text').getBoundingClientRect()
    const el = document.elementFromPoint(innerWidth / 2, r.height ? r.top + r.height / 2 : innerHeight / 2)
    const e = new DragEvent('${type}', { dataTransfer: dt, bubbles: true, cancelable: true })
    el.dispatchEvent(e)
    return e.defaultPrevented
  })()`)
  const dropFile = async (path, names) => {
    process.env.RECITEFLOW_PICK = path
    const name = names || [path.split(/[\\/]/).pop()]
    await drag('dragenter', name)
    await drag('dragover', name)
    return drag('drop', name)
  }
  const zone = () => js("!document.getElementById('drop').hidden")
  const isOpen = () => js("document.getElementById('pick').open")
  const status = () => js("document.getElementById('status').textContent")
  const box = () => js("document.getElementById('text').value")
  const filled = "document.getElementById('pick').open && !document.getElementById('pick-text').disabled"
  const closePick = async () => { await js("document.getElementById('pick').close()"); await sleep(150) }

  try {
    await new Promise((r) => (win.webContents.isLoading() ? win.webContents.once('did-finish-load', r) : r()))
    await sleep(800)

    // the zone: shows while a file is over the window, follows the text box, hides when it leaves
    await drag('dragenter')
    await drag('dragover')
    check(await zone() && await js(`(() => { const a = document.getElementById('drop').getBoundingClientRect();
      const b = document.getElementById('text').getBoundingClientRect();
      return Math.abs(a.left - b.left) < 1 && Math.abs(a.top - b.top) < 1 && Math.abs(a.width - b.width) < 1 && Math.abs(a.height - b.height) < 1 })()`),
    'a file dragged over the window shows the drop zone, exactly over the text box')
    await shot('zone-light.png')
    await drag('dragenter')   // into a child: still shown
    await drag('dragleave')
    check(await zone(), 'moving over the page keeps the zone')
    await drag('dragleave')
    check(!(await zone()), 'leaving the window hides the zone')
    await drag('dragenter', [], true)
    check(!(await zone()), 'dragged text (not a file) shows no zone')
    await drag('dragleave', [], true)

    // a .txt: the popup with its text
    const txt = join(app.getAppPath(), 'engine', 'clips', 'practice_tts', 'daily_walk_ok.txt')
    const tookIt = await dropFile(txt)
    const ts = await until(filled, 5000)
    check(tookIt && !(await zone()) && ts >= 0 &&
      await js("document.getElementById('pick-text').value") === readFileSync(txt, 'utf8').trim(),
    `a dropped .txt opens the popup with its text in ${ts.toFixed(2)} s, zone gone`)
    await js("document.getElementById('pick-all').click()")
    await sleep(200)
    check(await box() === readFileSync(txt, 'utf8').trim(), 'Take it all puts it in the text box')

    // a photo: "Reading the words in the photo", then its text
    const photo = join(gen, 'daily_bakery-photo.jpg')
    const truth = words(readFileSync(join(gen, 'daily_bakery-photo.txt'), 'utf8'))
    await dropFile(photo)
    check(await until("document.getElementById('pick').open", 3000) >= 0 &&
      /Reading the words in the photo/.test(await js("document.getElementById('pick-hint').textContent")),
    'a dropped photo opens the popup at once, "Reading the words in the photo"')
    const ps = await until(filled)
    const got = words(await js("document.getElementById('pick-text').value"))
    check(ps >= 0 && got.join(' ') === truth.join(' '), `photo read in ${ps.toFixed(1)} s, same words as the true text`)
    await closePick()

    // PDFs: 2 pages straight to the text; a 12-page one asks for the pages
    await dropFile(join(pdfs, 'text.pdf'))
    const ds = await until(filled, 10000)
    check(ds >= 0 && words(await js("document.getElementById('pick-text').value")).join(' ') ===
      words(readFileSync(join(pdfs, 'text.txt'), 'utf8')).join(' '), `a dropped 2-page PDF: its text in ${ds.toFixed(2)} s`)
    await closePick()
    await dropFile(join(pdfs, 'long.pdf'))
    check(await until("document.getElementById('pick').open && !document.getElementById('pick-pages').hidden", 10000) >= 0 &&
      /12 pages/.test(await js("document.getElementById('pick-hint').textContent")), 'a dropped 12-page PDF asks which pages')
    await drag('dragenter')
    check(!(await zone()), 'no zone while the popup is open')
    await drag('dragleave')
    await closePick()

    // wrong type, a folder, two files: a message, no popup, box unchanged
    const before = await box()
    for (const [what, path, names, says] of [
      ['a .docx', join(outDir, 'notes.docx'), null, /opens a text file \(\.txt\), a PDF or a photo/],
      ['a folder', outDir, ['drop'], /opens a text file \(\.txt\), a PDF or a photo/],
      ['two files', txt, ['a.txt', 'b.txt'], /one file at a time/]]) {
      if (what === 'a .docx') writeFileSync(path, 'not opened')
      await js("document.getElementById('status').textContent = ''")
      await dropFile(path, names)
      await sleep(300)
      check(!(await isOpen()) && says.test(await status()) && await box() === before,
        `${what}: "${await status()}", no popup, box unchanged`)
    }

    // pictures: dark, and the smallest window
    nativeTheme.themeSource = 'dark'
    await drag('dragenter')
    await shot('zone-dark.png')
    await drag('dragleave')
    win.setContentSize(640, 460)
    await sleep(300)
    await drag('dragenter')
    check(await js(`(() => { const a = document.getElementById('drop').getBoundingClientRect();
      const t = document.querySelector('#drop .hint').getBoundingClientRect();
      return a.right <= innerWidth && a.bottom <= innerHeight && t.bottom <= a.bottom && t.right <= a.right })()`),
    'at 640x460 the zone and its words fit')
    await shot('zone-small-dark.png')
    await drag('dragleave')
    nativeTheme.themeSource = 'system'
    win.setContentSize(980, 680)

    // while reciting (a replay) and on the result screen: no zone, nothing opens
    await waitFor(engine, 'ready', 120000)
    await until("!document.getElementById('go').disabled", 10000)
    await js(`(() => { const b = document.getElementById('text'); b.value = ${JSON.stringify(readFileSync(process.env.RECITEFLOW_FILE, 'utf8').trim())} })()`)
    await js("document.getElementById('go').click()")
    await until("document.getElementById('page').dataset.phase === 'listening' && document.getElementById('words').childNodes.length", 30000)
    const shown = await status()
    const took = await dropFile(txt)
    await sleep(500)
    check(took && !(await zone()) && !(await isOpen()) && await status() === shown &&
      await js("document.getElementById('page').dataset.phase") === 'listening',
    'while reciting: no zone, a dropped file opens nothing, the recitation goes on')
    await js("document.getElementById('go').click()")   // Stop
    await until("document.getElementById('go').textContent === 'Recite again'", 30000)
    await dropFile(txt)
    await sleep(500)
    check(!(await zone()) && !(await isOpen()), 'on the result screen: a dropped file opens nothing')
  } catch (err) {
    check(false, `error: ${err.message}`)
  }
  log.push(failed ? `${failed} FAILED` : 'ALL PASS')
  writeFileSync(join(outDir, 'log.txt'), log.join('\n') + '\n', 'utf8')
  console.log('[selftest]', failed ? `${failed} FAILED` : 'ALL PASS')
  app.quit()
}
