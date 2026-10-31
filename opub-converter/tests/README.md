# OPUB test corpus and tools

* `make_corpus.py` writes 10 small deterministic EPUB 3 files (with NCX) to `corpus/` (stdlib only; PNG images generated in-script, no JPEG).
  `06_fonts` embeds `Georgia.ttf`, `Georgia Bold.ttf`, `Georgia Italic.ttf` copied from `/System/Library/Fonts/Supplemental/`
  (macOS system fonts, ~0.5 MB; if missing the book is generated without @font-face). Re-run to regenerate; output is byte-identical.
* `run_mine.sh` converts the corpus with `../opub.py` into `out_mine/` and runs `../verify.py --check` on every result.
* Put the official Boox OPUBs (same basenames, `NN_name.opub`) in `ref/` (gitignored, proprietary engine output), then:

      python3 compare.py ref/01_basic.opub out_mine/01_basic.opub [--chapter N] [--verbose]
      python3 compare.py --all ref out_mine

* `realbooks.sh book.epub reference.opub [...]` converts real books (copyrighted, kept outside the repo) and prints the compare summary against their Boox references.
* **Licensing:** `corpus/06_fonts.epub` embeds Apple's proprietary Georgia TTFs and is therefore listed in `.gitignore` (never commit it). Regenerate it locally with `python3 make_corpus.py`.
  The other nine corpus EPUBs contain no third-party material. `ref/` (Boox output) and `out_mine/` are ignored as well.
