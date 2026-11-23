import { app, nativeTheme } from 'electron'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Open-a-file check (no engine): the file the Windows dialog would give comes from RECITEFLOW_PICK.
// A photo of a page -> the popup shows "Reading", then the text (timed from the click, compared with the true text)
// -> select a sentence, "Use this part" puts only it in the text box (Ctrl+Z gives the old text back). A .txt ->
// "Take it all". A broken picture -> a plain-words status, no popup. Cancel while a photo is read -> the box is
// unchanged. Ctrl+O opens, Esc closes. Pictures of the popup in both themes and at 640x460. Log and pictures in
// test_out/open/. PDFs: 2 typed pages read at once; a 12-page one asks for the pages (3 pages and a page past the
// end refused, then 2 pages read); a scanned one read with the OCR; locked and broken ones give a message. Uses the
// generated test photos and PDFs (engine/check_ocr.py make, engine/check_pdf.py make).
const outDir = join(app.getAppPath(), 'test_out', 'open')
const gen = join(app.getAppPath(), 'engine', 'clips', 'ocr', 'generated')
const pdfs = join(app.getAppPath(), 'engine', 'clips', 'ocr', 'pdf')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const words = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}' ]+/gu, ' ').split(/\s+/).filter(Boolean)

export async function run(win) {
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
  const key = (keyCode, modifiers = []) => {
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers })
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers })
  }
  const click = async (selector) => {
    const r = await js(`(() => { const b = document.querySelector('${selector}').getBoundingClientRect();
      return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) } })()`)
    for (const type of ['mouseDown', 'mouseUp']) win.webContents.sendInputEvent({ type, x: r.x, y: r.y, button: 'left', clickCount: 1 })
  }
  const until = async (code, ms = 30000) => {
    const t = Date.now()
    while (Date.now() - t < ms) {
      if (await js(code)) return (Date.now() - t) / 1000
      await sleep(50)
    }
    return -1
  }
  const box = () => js("document.getElementById('text').value")
  const isOpen = () => js("document.getElementById('pick').open")
  const filled = "document.getElementById('pick').open && !document.getElementById('pick-text').disabled"

  try {
    await new Promise((r) => (win.webContents.isLoading() ? win.webContents.once('did-finish-load', r) : r()))
    await sleep(800)
    check(await js("!document.getElementById('open-file').hidden"), 'Open a file is on the text screen')
    await js("document.getElementById('text').focus(); document.execCommand('insertText', false, 'my old text')")

    // a photo: reading state, then the text found
    const photo = join(gen, 'daily_bakery-photo.jpg')
    const truth = readFileSync(join(gen, 'daily_bakery-photo.txt'), 'utf8').trim()
    process.env.RECITEFLOW_PICK = photo
    await click('#open-file')
    check(await until("document.getElementById('pick').open", 3000) >= 0 &&
      await js("document.getElementById('pick-text').disabled && /Reading/.test(document.getElementById('pick-hint').textContent)"),
    'a photo opens the popup at once, with "Reading the words in the photo"')
    await shot('reading.png')
    const s = await until(filled)
    const got = await js("document.getElementById('pick-text').value")
    const t = words(truth)
    const same = t.length === words(got).length && t.every((w, i) => w === words(got)[i])
    check(s >= 0 && same, `photo read in ${s.toFixed(1)} s (from the click, popup already open), text the same as the true one: ${same}`)
    check(await js("document.activeElement.id === 'pick-text'") && await js("document.getElementById('pick-count').textContent") === `${t.length} words`,
      'the text has the focus, the count says the whole text')
    await shot('photo-light.png')

    // select the second sentence -> Use this part
    const part = 'At first she sold only bread, but now she makes cakes, cookies and coffee too.'
    await js(`(() => { const f = document.getElementById('pick-text'); const i = f.value.indexOf(${JSON.stringify(part)});
      f.focus(); f.setSelectionRange(i, i + ${part.length}); f.dispatchEvent(new Event('select')) })()`)
    await sleep(100)
    check(await js("!document.getElementById('pick-part').disabled") &&
      await js("document.getElementById('pick-count').textContent") === `${words(part).length} of ${t.length} words selected`,
    'selecting a sentence enables Use this part and counts its words')
    await shot('photo-selected.png')
    await click('#pick-part')
    await sleep(200)
    check(!(await isOpen()) && await box() === part, 'Use this part puts only the selection in the text box')
    key('z', ['control'])
    await sleep(100)
    check(await box() === 'my old text', 'Ctrl+Z gives the old text back')

    // a .txt -> Take it all, opened with Ctrl+O
    const txt = join(app.getAppPath(), 'engine', 'clips', 'practice_tts', 'daily_walk_ok.txt')
    process.env.RECITEFLOW_PICK = txt
    await js("document.getElementById('text').focus()")
    key('o', ['control'])
    const ts = await until(filled, 5000)
    check(ts >= 0, `Ctrl+O opens a .txt: text shown in ${ts.toFixed(2)} s`)
    await click('#pick-all')
    await sleep(200)
    check(await box() === readFileSync(txt, 'utf8').trim(), 'Take it all puts the whole file in the text box')

    // a broken picture -> a plain-words message
    const broken = join(outDir, 'broken.jpg')
    writeFileSync(broken, 'not a picture')
    process.env.RECITEFLOW_PICK = broken
    const before = await box()
    await click('#open-file')
    await until("!document.getElementById('pick').open && document.getElementById('status').dataset.tone === 'bad'", 10000)
    check(!(await isOpen()) && /Could not open this picture/.test(await js("document.getElementById('status').textContent")) &&
      await box() === before, `a broken picture: "${await js("document.getElementById('status').textContent")}", box unchanged`)

    // cancel while a photo is being read -> nothing changes, even when the text arrives later
    process.env.RECITEFLOW_PICK = join(gen, 'daily_violin-photo.jpg')
    await click('#open-file')
    await until("document.getElementById('pick').open", 3000)
    await click('#pick-cancel')
    await sleep(5000)
    check(!(await isOpen()) && await box() === before, 'Cancel while reading: popup closed, box unchanged after the reading ends')

    // Esc closes; a cancelled Windows dialog opens nothing
    process.env.RECITEFLOW_PICK = txt
    await click('#open-file')
    await until(filled, 5000)
    key('Escape')
    await sleep(200)
    check(!(await isOpen()) && await box() === before, 'Esc closes the popup, box unchanged')
    process.env.RECITEFLOW_PICK = ''
    await click('#open-file')
    await sleep(500)
    check(!(await isOpen()), 'no file picked: no popup')

    // PDFs (engine/check_pdf.py make): 2 typed pages straight to the text; a long one asks for the pages first
    const sameText = (a, b) => words(a).join(' ') === words(b).join(' ')
    const truthOf = (name) => readFileSync(join(pdfs, name.replace(/\.pdf$/, '.txt')), 'utf8').trim()
    process.env.RECITEFLOW_PICK = join(pdfs, 'text.pdf')
    await click('#open-file')
    const ps = await until(filled, 10000)
    check(ps >= 0 && sameText(await js("document.getElementById('pick-text').value"), truthOf('text.pdf')),
      `a 2-page text PDF: both pages shown in ${ps.toFixed(2)} s, same words as the true text`)
    key('Escape')
    await sleep(200)

    process.env.RECITEFLOW_PICK = join(pdfs, 'long.pdf')
    await click('#open-file')
    const asked = "document.getElementById('pick').open && !document.getElementById('pick-pages').hidden"
    check(await until(asked, 10000) >= 0 && await js("document.getElementById('pick-text').hidden") &&
      /12 pages/.test(await js("document.getElementById('pick-hint').textContent")) &&
      await js("document.activeElement.id === 'pick-range' && document.getElementById('pick-range').value === '1-2'"),
    'a 12-page PDF asks which pages first (says 12 pages, box focused, 1-2 offered), text hidden')
    await shot('pdf-pages-light.png')
    const typePages = async (spec) => {
      await js(`(() => { const r = document.getElementById('pick-range'); r.value = ${JSON.stringify(spec)};
        r.dispatchEvent(new Event('input')) })()`)
      // Enter as typed: a form is sent on the key's character, not on the key itself
      win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return' })
      win.webContents.sendInputEvent({ type: 'char', keyCode: '\r' })
      win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return' })
      await sleep(150)
    }
    await typePages('3-5')
    check(await js(asked) && /at most 2/.test(await js("document.getElementById('pick-range-note').textContent")) &&
      await js("document.getElementById('pick-range').getAttribute('aria-invalid')") === 'true',
    `3 pages refused at once: "${await js("document.getElementById('pick-range-note').textContent")}"`)
    await shot('pdf-pages-refused.png')
    await typePages('13')
    check(/pages 1 to 12/.test(await js("document.getElementById('pick-range-note').textContent")), 'a page past the end refused')
    await typePages('4, 9')
    const ls = await until(filled, 10000)
    check(ls >= 0 && sameText(await js("document.getElementById('pick-text').value"), truthOf('long.pdf')),
      `pages 4, 9 read in ${ls.toFixed(2)} s: only those two pages, same words as the true text`)
    await click('#pick-all')
    await sleep(200)
    check(sameText(await box(), truthOf('long.pdf')), 'Take it all puts those pages in the text box')

    process.env.RECITEFLOW_PICK = join(pdfs, 'scanned.pdf')
    await click('#open-file')
    const ss = await until(filled, 60000)
    const scanned = await js("document.getElementById('pick-text').value")
    const tw = words(truthOf('scanned.pdf'))
    const gw = words(scanned)
    const wrong = tw.filter((w, i) => w !== gw[i]).length + Math.max(0, gw.length - tw.length)
    check(ss >= 0 && wrong / tw.length <= 0.05 && /can be misread/.test(await js("document.getElementById('pick-hint').textContent")),
      `a scanned PDF (2 pages, OCR): read in ${ss.toFixed(1)} s, ${wrong} of ${tw.length} words different, "check the words" hint`)
    await shot('pdf-scanned.png')
    key('Escape')
    await sleep(200)

    for (const [name, says] of [['locked.pdf', 'locked with a password'], ['broken.pdf', 'Could not open this PDF']]) {
      process.env.RECITEFLOW_PICK = join(pdfs, name)
      const kept = await box()
      await click('#open-file')
      await until(`!document.getElementById('pick').open && /${says}/.test(document.getElementById('status').textContent)`, 10000)
      check(!(await isOpen()) && new RegExp(says).test(await js("document.getElementById('status').textContent")) &&
        await box() === kept, `${name}: "${await js("document.getElementById('status').textContent")}", box unchanged`)
    }

    // pictures: dark, and the smallest window
    process.env.RECITEFLOW_PICK = join(gen, 'long_clean_7176-88083-0000_14u-photo.jpg')
    nativeTheme.themeSource = 'dark'
    await click('#open-file')
    await until(filled)
    await shot('photo-dark.png')
    win.setContentSize(640, 460)
    await sleep(300)
    check(await js(`(() => { const b = document.getElementById('pick-all').getBoundingClientRect();
      return b.right <= innerWidth && b.bottom <= innerHeight })()`), 'at 640x460 the popup and its buttons fit')
    await shot('photo-small-dark.png')
    key('Escape')
    await sleep(200)
    process.env.RECITEFLOW_PICK = join(pdfs, 'long.pdf')
    await click('#open-file')
    await until("!document.getElementById('pick-pages').hidden", 10000)
    check(await js(`(() => { const b = document.querySelector('#pick-pages .btn').getBoundingClientRect();
      return b.right <= innerWidth && b.bottom <= innerHeight })()`), 'at 640x460 the pages box fits')
    await shot('pdf-pages-small-dark.png')
    nativeTheme.themeSource = 'system'
  } catch (err) {
    check(false, `error: ${err.message}`)
  }
  log.push(failed ? `${failed} FAILED` : 'ALL PASS')
  writeFileSync(join(outDir, 'log.txt'), log.join('\n') + '\n', 'utf8')
  console.log('[selftest]', failed ? `${failed} FAILED` : 'ALL PASS')
  app.quit()
}
