#!/bin/sh
# Regression check on real books (copyrighted, NOT in the repo): convert each EPUB and compare against
# the official Boox output for the same book.
# Usage: realbooks.sh book1.epub ref1.opub [book2.epub ref2.opub ...]
cd "$(dirname "$0")" || exit 1
[ $# -ge 2 ] || { echo "usage: $0 book.epub reference.opub [...]"; exit 2; }
T=$(mktemp -d); rc=0; n=0
while [ $# -ge 2 ]; do
  e=$1; r=$2; shift 2; n=$((n + 1))
  if [ ! -f "$e" ] || [ ! -f "$r" ]; then echo "== $n: skipped (epub or reference missing)"; continue; fi
  echo "== $n: $(basename "$e")"
  python3 ../opub.py "$e" "$T/$n.opub" >/dev/null || { echo "CONVERT FAILED"; rc=1; continue; }
  python3 ../verify.py --check "$T/$n.opub" || rc=1
  python3 compare.py "$r" "$T/$n.opub" | sed -n '/== SUMMARY/,/overall/p'
done
rm -rf "$T"; exit $rc
