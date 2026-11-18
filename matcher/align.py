"""Semi-global edit-distance alignment of spoken words against the target."""

from dataclasses import dataclass

from rapidfuzz import fuzz

from .normalize import normalize

PENDING = "pending"
CORRECT = "correct"
MISSPELLED = "misspelled"
WRONG = "wrong"
MISSED = "missed"
EXTRA = "extra"
RECOVERED = "recovered"  # Event only: a mistaken word re-recited correctly (R14); its state stays

# Costs are scaled by 10 so the DP stays in integers.
COST_MATCH = 0
COST_NEAR = 3
COST_SUB = 10
COST_GAP = 10

NEAR_THRESHOLD = 75.0
RESTART_MAX = 8  # a restart may repeat up to this many target words before the pointer
RESTART_PENALTY = 5  # (scaled cost) a restart must save more than half a word to be chosen
JUMP_BACK = 25  # re-recite: the speaker may go back up to this many target words (e.g. sentence start)
JUMP_PENALTY = 15  # (scaled cost) a jump back must save more than 1.5 words to be chosen
_STEM_SUFFIXES = ("es", "s", "ed", "ing", "ly")

# Cell scores pack (cost, -exact_matches, target_skips) into one int so ties
# on cost prefer more exact matches, then fewer skipped target words.
_COST_UNIT = 10**8
_MATCH_UNIT = 10**4

_DIAG, _UP, _LEFT = 0, 1, 2  # UP = extra spoken word, LEFT = skipped target word


@dataclass(frozen=True)
class Event:
    """A change a UI should redraw. For state == "extra", index is the target
    position before which the word was said and word is the spoken word."""

    index: int
    state: str
    word: str | None = None


def _stem(word: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _is_near(spoken: str, target: str) -> bool:
    if fuzz.ratio(spoken, target) >= NEAR_THRESHOLD:
        return True
    return _stem(spoken) == _stem(target)


def _ops_cost(ops) -> int:
    """Scaled alignment cost of ops (pair costs + gaps; trailing unspoken target words are free)."""
    return sum(cost if op == _DIAG else COST_GAP for op, _, cost in ops)


class Matcher:
    def __init__(self, target_text: str, lookahead: int = 15):
        self.target = normalize(target_text)
        self.lookahead = lookahead
        self.pointer = 0
        self._states = [PENDING] * len(self.target)
        self._extras: list[tuple[int, str]] = []
        self._near_cache: dict[tuple[str, str], bool] = {}
        self._held: list[str] = []
        self._recovered: set[int] = set()

    def states(self) -> list[str]:
        return list(self._states)

    def extras(self) -> list[tuple[int, str]]:
        return list(self._extras)

    def recovered(self) -> set[int]:
        """Target indices said wrong first, then re-recited correctly (R14). Their state keeps
        the first attempt (the mistake was made); the UI underlines them."""
        return set(self._recovered)

    def finished(self) -> bool:
        return self.pointer >= len(self.target)

    def _pair_cost(self, spoken: str, target: str) -> int:
        if spoken == target:
            return COST_MATCH
        key = (spoken, target)
        near = self._near_cache.get(key)
        if near is None:
            near = self._near_cache[key] = _is_near(spoken, target)
        return COST_NEAR if near else COST_SUB

    def snapshot(self):
        """The alignment state (pointer, states, extras, held words, recovered), for restore()."""
        return self.pointer, list(self._states), list(self._extras), list(self._held), set(self._recovered)

    def restore(self, snap) -> None:
        """Go back to a snapshot(): everything fed since then is forgotten (live re-align)."""
        self.pointer, states, extras, held, recovered = snap
        self._states, self._extras, self._held = list(states), list(extras), list(held)
        self._recovered = set(recovered)

    def held(self) -> list[str]:
        """Spoken words held back by feed(final=False), not yet committed."""
        return list(self._held)

    def feed(self, spoken_words: list[str], final: bool = True) -> list[Event]:
        """Align one finished utterance and return the resulting state changes.

        final=False (streaming): trailing spoken words that do not align as correct or
        misspelled are held back and prepended to the next feed, so a trailing hallucination
        or an utterance cut right after a deleted word does not mark the next target words
        wrong. If the next utterance starts with a restart (its first word aligns as extra),
        the held words are committed as they were first aligned. flush() commits the rest.

        Re-recite: an utterance may go back (see _restart); the pointer then moves back and
        the re-said words are aligned again. A re-said word keeps its first state; a mistaken
        word said correctly the second time is marked recovered.
        """
        new = normalize(" ".join(spoken_words))
        held, self._held = self._held, []
        events: list[Event] = []
        if held and new:
            spoken = held + new
            ops = self._align(spoken, self._window(len(spoken)))
            split = self._first_op_of(ops, len(held)) == _UP
            if not split:
                # A restart right after held words ("at last" held, then "at first she ..."):
                # committing the held words first, then the restart, may be cheaper.
                saved = self.snapshot()
                h_ops = self._align(held, self._window(len(held)))
                self._commit(held, h_ops, final=True)
                r_start, r_ops, penalty = self._restart(new)
                split = r_start < self.pointer and (_ops_cost(h_ops) + _ops_cost(r_ops) + penalty < _ops_cost(ops))
                self.restore(saved)
                self._held = []
            start = None
            if split:
                events += self._commit(held, self._align(held, self._window(len(held))), final=True)
                spoken = new
                start, ops, _ = self._restart(new)
        else:
            spoken = held + new
            if not spoken:
                return []
            if held:
                start, ops = None, self._align(spoken, self._window(len(spoken)))
            else:
                start, ops, _ = self._restart(spoken)
        events += self._commit(spoken, ops, final, start)
        return events

    def _mistake_between(self, lo: int, hi: int) -> bool:
        """A wrong / missed / misspelled word or an extra word in target range [lo, hi)."""
        if any(s not in (CORRECT, PENDING) for s in self._states[lo:hi]):
            return True
        return any(lo <= b < hi for b, _ in self._extras)

    def _restart(self, spoken):
        """Align `spoken` from the pointer, or from an earlier target position q when the
        speaker went back (re-recite) and that is cheaper. Two kinds of going back:
          repeat: `spoken` starts with the last k <= RESTART_MAX target words ("at last, at first
                  she ..."); k = 1 only when that word is not correct (re-saying it right).
          jump:   `spoken` starts with 2 target words at q, up to JUMP_BACK words back, and a
                  mistake was made in [q, pointer) (the reason to go back, e.g. "at nine ...",
                  then the whole sentence again). Stricter penalty: a wrong phrase that happens
                  to equal earlier words must not hide a mistake.
        Returns (start, ops, penalty); start = pointer when there is no restart."""
        p = self.pointer
        ops = self._align(spoken, self._window(len(spoken)))
        best, best_q, best_ops, best_pen = _ops_cost(ops), p, ops, 0
        for q in range(p - 1, max(0, p - max(RESTART_MAX, JUMP_BACK)) - 1, -1):  # nearest first
            k = p - q
            if k <= RESTART_MAX and spoken[:k] == self.target[q:p] and not (k == 1 and self._states[q] == CORRECT):
                penalty = RESTART_PENALTY
            elif len(spoken) >= 2 and spoken[:2] == self.target[q:q + 2] and self._mistake_between(q, p):
                penalty = JUMP_PENALTY
            else:
                continue
            r_ops = self._align(spoken, self.target[q: q + len(spoken) + self.lookahead])
            cost = _ops_cost(r_ops) + penalty
            if cost < best:
                best, best_q, best_ops, best_pen = cost, q, r_ops, penalty
        return best_q, best_ops, best_pen

    def flush(self) -> list[Event]:
        """Commit any words held back by feed(final=False) (call at stop / end of text)."""
        held, self._held = self._held, []
        if not held:
            return []
        return self._commit(held, self._align(held, self._window(len(held))), final=True)

    def _window(self, n_spoken: int) -> list[str]:
        # The window grows with the utterance so a long utterance (a whole
        # paragraph in one go) can still be aligned in full.
        return self.target[self.pointer : self.pointer + n_spoken + self.lookahead]

    @staticmethod
    def _first_op_of(ops, k: int) -> int | None:
        """Kind of the op that consumes spoken word k (0-based)."""
        seen = 0
        for op, _, _ in ops:
            if op == _LEFT:
                continue
            if seen == k:
                return op
            seen += 1
        return None

    def _commit(self, spoken: list[str], ops, final: bool, start: int | None = None) -> list[Event]:
        """Apply ops from target index `start` (default: the pointer; lower after a restart)."""
        t0 = self.pointer if start is None else start
        if not final:
            good = [k for k, (op, _, cost) in enumerate(ops) if op == _DIAG and cost != COST_SUB]
            cut = good[-1] + 1 if good else 0
            reaches_end = t0 + sum(op != _UP for op, _, _ in ops) >= len(self.target)
            if cut < len(ops) and not reaches_end:
                self._held = [w for op, w, _ in ops[cut:] if op != _LEFT]
                ops = ops[:cut]

        events: list[Event] = []
        t = t0
        for op, s_word, cost in ops:
            if op == _UP:
                self._extras.append((t, s_word))
                events.append(Event(t, EXTRA, s_word))
                continue
            if op == _DIAG:
                state = CORRECT if cost == COST_MATCH else MISSPELLED if cost == COST_NEAR else WRONG
            else:
                state = MISSED
            old = self._states[t]
            if old == PENDING:
                self._states[t] = state
                events.append(Event(t, state))
            elif old != CORRECT and state == CORRECT and t not in self._recovered:  # re-recited right
                self._recovered.add(t)
                events.append(Event(t, RECOVERED))
            t += 1
        self.pointer = t
        return events

    def _align(self, spoken: list[str], window: list[str]) -> list[tuple[int, str | None, int]]:
        """Return ops (kind, spoken word, pair cost) in order. Leading skipped
        target words cost a gap; trailing unspoken target words are free."""
        m, n = len(spoken), len(window)
        gap = COST_GAP * _COST_UNIT
        score = [[0] * (n + 1) for _ in range(m + 1)]
        back = [[0] * (n + 1) for _ in range(m + 1)]
        pair = [[0] * (n + 1) for _ in range(m + 1)]
        for j in range(1, n + 1):
            score[0][j] = score[0][j - 1] + gap + 1
            back[0][j] = _LEFT
        for i in range(1, m + 1):
            score[i][0] = score[i - 1][0] + gap
            back[i][0] = _UP
            s_word = spoken[i - 1]
            row, prev = score[i], score[i - 1]
            for j in range(1, n + 1):
                c = self._pair_cost(s_word, window[j - 1])
                best = prev[j - 1] + c * _COST_UNIT - (_MATCH_UNIT if c == COST_MATCH else 0)
                move = _DIAG
                up = prev[j] + gap
                if up < best:
                    best, move = up, _UP
                left = row[j - 1] + gap + 1
                if left < best:
                    best, move = left, _LEFT
                row[j] = best
                back[i][j] = move
                pair[i][j] = c

        # Free end on the target side; on ties advance further (prefer
        # marking a substitution over leaving the word pending).
        last = score[m]
        j = min(range(n + 1), key=lambda k: (last[k], -k))

        ops: list[tuple[int, str | None, int]] = []
        i = m
        while i > 0 or j > 0:
            move = back[i][j]
            if move == _DIAG:
                ops.append((_DIAG, spoken[i - 1], pair[i][j]))
                i, j = i - 1, j - 1
            elif move == _UP:
                ops.append((_UP, spoken[i - 1], 0))
                i -= 1
            else:
                ops.append((_LEFT, None, 0))
                j -= 1
        ops.reverse()
        return ops
