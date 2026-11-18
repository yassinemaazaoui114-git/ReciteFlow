# Task 01 report: Matcher (DONE)
Result: pytest 17 passed in 0.43 s. 500-word feed ~0.18 s (random 1-8 word chunks and single utterance). No audio/torch imports in matcher/. app.py untouched.

Added: matcher/normalize.py, matcher/align.py, matcher/__init__.py, matcher/README.md, tests/test_matcher.py (11 required tests + extras), requirements.txt (rapidfuzz, num2words, pytest).

Rules changed / interpreted:
1. Window = len(spoken) + lookahead (fixed 15 cannot align a 40+ word utterance).
2. Tie-break on equal cost: more exact matches, then fewer skipped target words, then further ahead. Costs x10 internally.
3. "Missed only if a later word matched": any aligned later word counts, including a wrong one.
4. Near-miss: similarity >= 0.75 OR shared stem after stripping s/es/ed/ing/ly. No real phonetics.
5. Normalization: only 4-digit 1100-2099 read as years; 's -> "is" only after it/that/he/there etc., else possessive; num2words keeps "and".
6. Restart (test 10): no rewind, no crash; repeated words listed as extras.

Known weakness: unknown trailing word (e.g. hallucinated "thank you") marks following target words wrong. Suggested fix: hold back unmatched tail of each utterance (needs extra API, e.g. feed(words, final=False)). Planned for Task 03.
