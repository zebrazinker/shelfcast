#!/usr/bin/env python3
"""Field-by-field diff of two .opub files (reference from Boox's engine vs. ours).

    python3 compare.py ref.opub mine.opub [--chapter N] [--verbose]
    python3 compare.py --all refdir mydir [--verbose]

N is the 0-based spine index of a chapter.  Unknown fields/tables in either file are shown (raw hex / generic decode), never fatal.
Decoding lives in ../verify.py (load, fb_gen, style_gen).
"""
import sys, os, struct, difflib, collections, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import verify

CATS = ['header', 'meta', 'toc', 'chapter', 'rows', 'run_text', 'run_style', 'links', 'images', 'style_table', 'containers', 'pagination']
IGNORE_META = ('bookId', 'md5sum')

class Report:
    def __init__(s, verbose=False, quiet=False):
        s.verbose, s.quiet = verbose, quiet
        s.score = {c: [0, 0] for c in CATS}   # matched, total
        s.lines = []; s.slotdiff = collections.Counter(); s.sec = None; s.shown = collections.Counter()
    def p(s, *a):
        t = ' '.join(str(x) for x in a); s.lines.append(t)
        if not s.quiet: print(t)
    def section(s, name): s.sec = name; s.p('\n== %s' % name)
    def count(s, cat, ok, n=1): s.score[cat][0] += ok; s.score[cat][1] += n
    def diff(s, cat, msg, limit=6):
        """Print a difference line, limited per category unless --verbose."""
        s.shown[cat] += 1
        if s.verbose or s.shown[cat] <= limit: s.p('  DIFF %s' % msg)
        elif s.shown[cat] == limit + 1: s.p('  ... more %s differences (use --verbose)' % cat)

def short(x, n=70): x = repr(x); return x if len(x) <= n else x[:n] + '...'

# ---------------------------------------------------------------------------- styles
class Styles:
    def __init__(s, O):
        s.recs = [verify.style_gen(r) for r in O['styles']]
        s.canon = [repr(sorted(g.items(), key=lambda kv: str(kv[0]))) for g in s.recs]
        s.by = collections.defaultdict(list)
        for i, c in enumerate(s.canon): s.by[c].append(i + 1)
    def g(s, sid): return s.recs[sid - 1] if sid and 1 <= sid <= len(s.recs) else None
    def same(s, other, a, b):
        """style id a (this) vs id b (other) equal by content?"""
        if a == 0 and b == 0: return True
        return a and b and 1 <= a <= len(s.canon) and 1 <= b <= len(other.canon) and s.canon[a - 1] == other.canon[b - 1]

def style_slot_diff(ga, gb):
    out = []
    for k in sorted(set(ga) | set(gb), key=str):
        va, vb = ga.get(k), gb.get(k)
        if va != vb:
            nm = verify.STYLE_SLOTS.get(k, 's%s' % k)
            out.append((nm, verify.gen_fmt(va) if va else '-', verify.gen_fmt(vb) if vb else '-'))
    return out

# ---------------------------------------------------------------------------- pixel diff
def unpack(w, h, kind, pix):
    """-> list of 0..255 values w*h or None."""
    if len(pix) == w * h: return list(pix)
    rb = (w + 7) // 8
    if len(pix) == rb * h:
        return [255 if (pix[y * rb + (x >> 3)] >> (7 - (x & 7))) & 1 else 0 for y in range(h) for x in range(w)]
    return None

def pixel_diff(ia, ib):
    wa, ha, ka, _, pa = ia; wb, hb, kb, _, pb = ib
    A, B = unpack(wa, ha, ka, pa), unpack(wb, hb, kb, pb)
    if A is None or B is None: return None
    if (wa, ha) != (wb, hb):   # nearest-neighbour resample mine to ref size
        B = [B[min(hb - 1, y * hb // ha) * wb + min(wb - 1, x * wb // wa)] for y in range(ha) for x in range(wa)]
    return sum(abs(x - y) for x, y in zip(A, B)) / max(1, len(A))

# ---------------------------------------------------------------------------- comparison
def compare(ref, mine, only=None, verbose=False, quiet=False):
    A, B = verify.load(ref), verify.load(mine); R = Report(verbose, quiet)
    SA, SB = Styles(A), Styles(B)
    # --- header
    R.section('header')
    R.p('  file size   ref %d  mine %d  (%.1f%%)   EOF field ok: ref %s mine %s' % (A['size'], B['size'], 100.0 * B['size'] / A['size'], A['eof'] == A['size'], B['eof'] == B['size']))
    R.p('  chapters    ref %d  mine %d   toc %d / %d   styles %d / %d   images %d / %d   pagination pairs %d / %d' % (
        len(A['chapters']), len(B['chapters']), len(A['toc']), len(B['toc']), A['nstyles'], B['nstyles'], len(A['images']), len(B['images']), len(A['pagination']), len(B['pagination'])))
    ptr_slots = {4, 5, 6, 7, 8, 11}
    for k in sorted(set(A['header']) | set(B['header'])):
        ha, hb = A['header'].get(k), B['header'].get(k)
        if k == 2: continue   # EOF offset
        if k in ptr_slots and ha and hb: ok = True   # (offset, size) pairs: values naturally differ, only presence matters
        else: ok = ha == hb
        R.count('header', ok)
        if not ok: R.diff('header', 'header slot %s ref=%s mine=%s' % (k, ha and verify.raw_fmt(ha), hb and verify.raw_fmt(hb)))
    for k, (off, sz, hx) in A['header_regions'].items():
        if k not in ptr_slots: R.p('  NOTE ref header slot %s points to a region at 0x%x size %d (not in our output): %s' % (k, off, sz, hx))
    for k in sorted(set(B['header_regions']) - set(A['header_regions']) - ptr_slots): R.p('  NOTE mine has header region slot %s' % k)
    # --- metadata
    R.section('metadata')
    ma, mb = dict(A['meta']), dict(B['meta'])
    for k in [k for k, _ in A['meta']] + [k for k in mb if k not in ma]:
        if k in IGNORE_META: continue
        ok = ma.get(k) == mb.get(k); R.count('meta', ok)
        if not ok: R.diff('meta', '%s: ref=%s mine=%s' % (k, short(ma.get(k)), short(mb.get(k))))
    R.p('  key order: ref %s' % [k for k, _ in A['meta']] if [k for k, _ in A['meta']] != [k for k, _ in B['meta']] else '  key order identical')
    # --- toc
    R.section('toc')
    for i in range(max(len(A['toc']), len(B['toc']))):
        ta = A['toc'][i] if i < len(A['toc']) else None; tb = B['toc'][i] if i < len(B['toc']) else None
        ea = A['toc_extra'][i] if i < len(A['toc_extra']) else None; eb = B['toc_extra'][i] if i < len(B['toc_extra']) else None
        for nm, x, y in (('title', ta and ta[0], tb and tb[0]), ('href', ta and ta[1], tb and tb[1]), ('level', ta and ta[2], tb and tb[2]), ('spine_idx', ta and ta[3], tb and tb[3]), ('extra', ea, eb)):
            ok = x == y; R.count('toc', ok)
            if not ok: R.diff('toc', 'toc[%d].%s ref=%s mine=%s' % (i, nm, short(x), short(y)))
    # --- images
    R.section('images')
    for i in range(max(len(A['images']), len(B['images']))):
        ia = A['images'][i] if i < len(A['images']) else None; ib = B['images'][i] if i < len(B['images']) else None
        if not ia or not ib:
            R.count('images', 0); R.diff('images', 'image[%d] only in %s: %s' % (i, 'ref' if ia else 'mine', (ia or ib)[:3])); continue
        pd = pixel_diff(ia, ib); ok = ia[:3] == ib[:3] and pd is not None and pd < 8
        R.count('images', ok)
        extra = (A['images_raw'][i], B['images_raw'][i]) if A['images_raw'][i] != B['images_raw'][i] else ''
        if ok and not verbose: continue
        R.p('  image[%d] ref %dx%d kind %s  mine %dx%d kind %s  mean|dpix| %s  %s%s' % (i, ia[0], ia[1], ia[2], ib[0], ib[1], ib[2], '%.2f' % pd if pd is not None else 'n/a', 'OK' if ok else 'DIFF', ' extra slots differ %s' % (extra,) if extra else ''))
    # --- style table
    R.section('style table (ref record -> best match in mine by content)')
    best = {}
    for i, ga in enumerate(SA.recs):
        sid = i + 1; exact = SB.by.get(SA.canon[i])
        if exact: best[sid] = (exact[0], []); R.count('style_table', 1); continue
        cand = min(range(len(SB.recs)), key=lambda j: len(style_slot_diff(ga, SB.recs[j]))) if SB.recs else None
        d = style_slot_diff(ga, SB.recs[cand]) if cand is not None else []
        best[sid] = (cand + 1 if cand is not None else None, d); R.count('style_table', 0)
        for nm, va, vb in d: R.slotdiff[(nm, va, vb)] += 1
        R.diff('style_table', 'ref style %d -> closest mine %s: %s' % (sid, cand + 1 if cand is not None else None, '; '.join('%s ref=%s mine=%s' % x for x in d[:8]) or 'n/a'), limit=12)
    unmatched_b = [j + 1 for j, c in enumerate(SB.canon) if c not in SA.by]
    R.p('  ref records with exact match in mine: %d/%d; mine records with no ref equivalent: %d/%d' % (R.score['style_table'][0], len(SA.recs), len(unmatched_b), len(SB.recs)))
    if verbose:
        for i, g in enumerate(SA.recs): R.p('  ref  style %-3d %s' % (i + 1, verify.style_fmt(g)))
        for i, g in enumerate(SB.recs): R.p('  mine style %-3d %s' % (i + 1, verify.style_fmt(g)))
    # --- chapters
    n = min(len(A['chapters']), len(B['chapters']))
    if len(A['chapters']) != len(B['chapters']): R.p('\n!! chapter count differs: ref %d mine %d (comparing first %d)' % (len(A['chapters']), len(B['chapters']), n))
    pa_g = collections.defaultdict(list); pb_g = collections.defaultdict(list)
    for k, v in A['pagination']: pa_g[k].append(v)
    for k, v in B['pagination']: pb_g[k].append(v)
    for k in range(n):
        if only is not None and k != only: continue
        ca, cb = A['chapters'][k], B['chapters'][k]
        R.section('chapter %d  ref id=%r title=%r | mine id=%r title=%r' % (k, ca['id'], ca['title'], cb['id'], cb['title']))
        if ca['err'] or cb['err']: R.p('  decode errors: ref %s mine %s' % (ca['err'], cb['err']))
        for nm in ('id', 'title', 'np', 'nr', 'f11', 'hextra'):
            ok = ca[nm] == cb[nm]; R.count('chapter', ok)
            if not ok: R.diff('chapter', 'chapter %d header.%s ref=%s mine=%s' % (k, nm, short(ca[nm], 110), short(cb[nm], 110)))
        R.p('  np ref %d mine %d  nr ref %d mine %d  header slots ref %s mine %s  containers ref %d mine %d' % (ca['np'], cb['np'], ca['nr'], cb['nr'], ca['hslots'], cb['hslots'], len(ca['containers']), len(cb['containers'])))
        # containers
        ncd = max(len(ca['containers']), len(cb['containers']))
        def cstyle(S, sid):
            return S.canon[sid - 1] if 1 <= sid <= len(S.canon) else None
        for j in range(ncd):
            xa = ca['containers'][j] if j < len(ca['containers']) else None; xb = cb['containers'][j] if j < len(cb['containers']) else None
            ok = bool(xa and xb and SA.same(SB, xa[0], xb[0]) and xa[1] == xb[1]); R.count('containers', ok)
            if not ok:
                R.diff('containers', 'chapter %d container[%d] ref=%s%s mine=%s%s' % (k, j, xa, ' style: ' + verify.style_fmt(SA.g(xa[0])) if xa and SA.g(xa[0]) else '', xb, ' style: ' + verify.style_fmt(SB.g(xb[0])) if xb and SB.g(xb[0]) else ''))
        # paragraph rows
        ra, rb = ca['rows'], cb['rows']
        for j in range(max(len(ra), len(rb))):
            xa = ra[j] if j < len(ra) else None; xb = rb[j] if j < len(rb) else None
            if not xa or not xb:
                R.count('rows', 0); R.diff('rows', 'chapter %d row %d only in %s: %s' % (k, j, 'ref' if xa else 'mine', xa or xb)); continue
            probs = []
            if xa[0] != xb[0]: probs.append('kind %d/%d' % (xa[0], xb[0]))
            if xa[4] != xb[4]: probs.append('nruns %d/%d' % (xa[4], xb[4]))
            if (xa[2] == 0) != (xb[2] == 0): probs.append('container %d/%d' % (xa[2], xb[2]))
            elif xa[2] and xb[2] and ca['containers'][xa[2] - 1:xa[2]] and cb['containers'][xb[2] - 1:xb[2]] and not SA.same(SB, ca['containers'][xa[2] - 1][0], cb['containers'][xb[2] - 1][0]): probs.append('container style differs')
            R.count('rows', not probs)
            if probs: R.diff('rows', 'chapter %d row %d (ref %s | mine %s): %s' % (k, j, xa, xb, ', '.join(probs)))
            sa, sb = xa[1], xb[1]
            if not SA.same(SB, sa, sb):
                d = style_slot_diff(SA.g(sa) or {}, SB.g(sb) or {}); R.count('run_style', 0)
                for nm, va, vb in d: R.slotdiff[(nm, va, vb)] += 1
                R.diff('run_style', 'chapter %d row %d block style ref#%s vs mine#%s: %s' % (k, j, sa, sb, '; '.join('%s ref=%s mine=%s' % x for x in d[:6])))
            else: R.count('run_style', 1)
        # runs: align by (type, text)
        key = lambda r: (r[0], r[1] if r[0] != 'i' else r[1])
        ka = [key(r) for r in ca['runs']]; kb = [key(r) for r in cb['runs']]
        sm = difflib.SequenceMatcher(None, ka, kb, autojunk=len(ka) > 4000)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                for x, y in zip(ca['runs'][i1:i2], cb['runs'][j1:j2]):
                    R.count('run_text', 1)
                    if x[0] == 'i':
                        ok = x[2:] == y[2:] or SA.same(SB, x[2], y[2]) and x[3] == y[3]; R.count('run_style', ok)
                        if not ok: R.diff('run_style', 'chapter %d image run %d style/extra %s vs %s' % (k, x[1], x[2:], y[2:]))
                        continue
                    ok = SA.same(SB, x[2], y[2]); R.count('run_style', ok)
                    if not ok:
                        d = style_slot_diff(SA.g(x[2]) or {}, SB.g(y[2]) or {})
                        for nm, va, vb in d: R.slotdiff[(nm, va, vb)] += 1
                        R.diff('run_style', 'chapter %d run %s style ref#%s vs mine#%s: %s' % (k, short(x[1], 30), x[2], y[2], '; '.join('%s ref=%s mine=%s' % q for q in d[:6])))
                    if x[0] in 'l?':
                        okl = x[3:2 * 0 + len(x)] == y[3:] if x[0] == '?' else (x[3:] == y[3:]); R.count('links', okl)
                        if not okl: R.diff('links', 'chapter %d link %s ref=%s mine=%s' % (k, short(x[1], 30), x[3:], y[3:]))
            else:
                for r in ca['runs'][i1:i2]: R.count('run_text', 0)
                for r in cb['runs'][j1:j2]: R.count('run_text', 0) if tag == 'insert' else None
                R.diff('run_text', 'chapter %d runs %s ref[%d:%d]=%s  mine[%d:%d]=%s' % (k, tag, i1, i2, short([r[:2] for r in ca['runs'][i1:i2]][:4], 160), j1, j2, short([r[:2] for r in cb['runs'][j1:j2]][:4], 160)))
        # pagination
        pa, pb = pa_g.get(k, []), pb_g.get(k, []); ok = pa == pb; R.count('pagination', ok)
        R.p('  pagination ref %d pages, mine %d pages  %s' % (len(pa), len(pb), 'identical' if ok else 'DIFF'))
        if not ok and (verbose or len(pa) < 12): R.p('    ref %s\n    mine %s' % (pa[:40], pb[:40]))
    # --- summary
    R.section('SUMMARY')
    tot_ok = tot_n = 0
    for c in CATS:
        ok, nn = R.score[c]; tot_ok += ok; tot_n += nn
        R.p('  %-12s %6d / %-6d %s' % (c, ok, nn, '%5.1f%%' % (100.0 * ok / nn) if nn else '  n/a'))
    cats = [c for c in CATS if R.score[c][1]]
    macro = sum(R.score[c][0] / R.score[c][1] for c in cats) / max(1, len(cats))
    R.p('  overall match score: %.1f%% (micro, all items)   %.1f%% (macro, mean of categories)' % (100.0 * tot_ok / max(1, tot_n), 100 * macro))
    if R.slotdiff:
        R.p('\n  most common style slot differences (slot: ref -> mine):')
        for (nm, va, vb), c in R.slotdiff.most_common(15): R.p('    %5dx  %-8s %s -> %s' % (c, nm, va, vb))
    R.macro = macro
    return R

def main(argv):
    verbose = '--verbose' in argv or '-v' in argv; only = None; args = []
    it = iter(argv)
    for a in it:
        if a == '--chapter': only = int(next(it))
        elif a in ('--verbose', '-v', '--all'): pass
        else: args.append(a)
    if '--all' in argv:
        refdir, mydir = args[:2]
        names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(refdir, '*.opub')))
        if not names: sys.exit('no .opub files in %s' % refdir)
        print('%-24s %7s %7s' % ('file', 'micro', 'macro') + ''.join(' %11s' % c[:11] for c in CATS))
        for nm in names:
            mp = os.path.join(mydir, nm)
            if not os.path.exists(mp): print('%-24s missing in %s' % (nm, mydir)); continue
            try:
                R = compare(os.path.join(refdir, nm), mp, quiet=True)
                ok = sum(v[0] for v in R.score.values()); n = sum(v[1] for v in R.score.values())
                print('%-24s %6.1f%% %6.1f%%' % (nm[:24], 100.0 * ok / max(1, n), 100 * R.macro) + ''.join(' %11s' % ('%d/%d' % tuple(R.score[c]) if R.score[c][1] else '-') for c in CATS))
            except Exception as e:
                print('%-24s ERROR %r' % (nm[:24], e))
        return
    if len(args) < 2: sys.exit(__doc__)
    compare(args[0], args[1], only=only, verbose=verbose)

if __name__ == '__main__':
    main(sys.argv[1:])
