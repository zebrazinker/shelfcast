#!/bin/sh
# Convert every corpus EPUB with our converter into tests/out_mine/ and run verify.py --check on each.
cd "$(dirname "$0")" || exit 1
mkdir -p out_mine
rc=0
for e in corpus/*.epub; do
  n=$(basename "$e" .epub)
  python3 ../opub.py "$e" "out_mine/$n.opub" >/dev/null || { echo "CONVERT FAILED: $n"; rc=1; continue; }
  python3 ../verify.py --check "out_mine/$n.opub" || rc=1
done
exit $rc
