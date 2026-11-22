// Draws the engine's tokens ({pos, kind, text, state, sep, corrected, underline, labels}) and the counters.
// One plain DOM rebuild per frame: a word's colour shows the moment its frame arrives.

// The counters are also the legend 
export const TALLY = [['correct', 'Correct'], ['misspelled', 'Misspelled'], ['wrong', 'Wrong'], ['missed', '[Missed]'],
  ['extra', '{Extra}'], ['pending', 'Not yet said']]

// Correction menu labels 
export const LABELS = {
  correct: ['Correct', 'said right'], misspelled: ['Misspelled', 'close, not exact'],
  wrong: ['Wrong', 'said another word'], missed: ['Missed', 'not said'],
  extra: ['Really said', 'extra word'], not_said: ['Not said', 'the app invented it']
}

// Hide mode: words not yet said are left out and a "…" follows the last said word. A word the engine has heard
// but not checked yet (heard) is drawn plain, so it appears as it is said and takes its colour a moment later.
export function drawWords(el, tokens, { hide = false } = {}) {
  const shown = hide ? tokens.filter((t) => !(t.kind === 'word' && t.state === 'pending' && !t.heard)) : tokens
  const out = document.createDocumentFragment()
  for (const t of shown) {
    const w = document.createElement('span')
    w.className = 'w'
    w.dataset.state = t.state
    w.dataset.pos = t.pos
    if (t.underline) w.dataset.underline = ''
    if (t.corrected) w.dataset.corrected = ''
    if (t.heard) w.dataset.heard = ''
    w.textContent = t.text
    out.append(w, t.sep)
  }
  if (shown.length < tokens.length) {
    const more = document.createElement('span')
    more.className = 'more'
    more.textContent = '…'
    out.append(more)
  }
  el.replaceChildren(out)
}

export function drawTally(el, counts) {
  el.replaceChildren(...TALLY.map(([state, label]) => {
    const li = document.createElement('li')
    const n = counts[state] ?? 0
    li.dataset.state = state
    const b = document.createElement('b')
    b.textContent = n
    li.append(b, ' ' + label)
    return li
  }))
}
