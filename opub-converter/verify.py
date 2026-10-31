#!/usr/bin/env python3
"""Structural comparison of two .opub files:  python3 verify.py ref.opub mine.opub"""
import sys, os, struct, collections

# ---------------------------------------------------------------------------- generic FlatBuffers helpers
def _u32(d, o): return struct.unpack_from('<I', d, o)[0]
def _u16(d, o): return struct.unpack_from('<H', d, o)[0]

def fb_spans(d, t):
    """{slot: (abs_offset, raw_bytes)} for every present field of the table at t. A field's span runs to the next field
    (or the table end), so it may include alignment padding; sizes are therefore a best guess."""
    try:
        vt = t - struct.unpack_from('<i', d, t)[0]; vsz, tsz = _u16(d, vt), _u16(d, vt + 2)
        offs = [_u16(d, vt + 4 + 2 * i) for i in range(max(0, (vsz - 4) // 2))]
    except struct.error: return {}
    present = sorted({o for o in offs if o}); out = {}
    for i, o in enumerate(offs):
        if o: out[i] = (t + o, bytes(d[t + o:t + min([x for x in present if x > o] + [tsz, o + 8])]))
    return out

def _try_str(d, a):
    try:
        tg = a + _u32(d, a); n = _u32(d, tg)
        if 0 < n < 100000 and tg + 5 + n <= len(d) and d[tg + 4 + n] == 0: return d[tg + 4:tg + 4 + n].decode('utf-8')
    except (struct.error, UnicodeDecodeError): pass

def _try_tbl(d, a):
    try:
        tg = a + _u32(d, a)
        if tg % 4 or tg + 4 > len(d): return None
        vt = tg - struct.unpack_from('<i', d, tg)[0]
        if 0 <= vt and vt + 4 <= len(d) and 4 <= _u16(d, vt) <= 400 and _u16(d, vt) % 2 == 0 and 4 <= _u16(d, vt + 2) <= 4096 and vt + _u16(d, vt) <= len(d): return tg
    except struct.error: pass

def fb_gen(d, t, depth=0):
    """Schema-less decode: {slot: ('str', s) | ('tbl', {...}) | ('raw', hex)}. Pointer-looking 4-byte fields are followed
    (string or table) so that the result contains no absolute offsets and can be compared between files."""
    res = {}
    for sl, (a, raw) in fb_spans(d, t).items():
        v = None
        if depth < 4 and len(raw) >= 4 and not any(raw[4:]) and _u32(d, a) >= 8:
            s = _try_str(d, a)
            if s is not None:
                v = ('str', s) if all(c >= ' ' or c in '\n\t' for c in s) else ('vec', s.encode().hex())
            else:
                tg = _try_tbl(d, a)
                if tg is not None: v = ('tbl', fb_gen(d, tg, depth + 1))
        res[sl] = v or ('raw', raw.hex())
    return res

def raw_fmt(hx):
    """Human guess for a raw field value (hex string)."""
    b = bytes.fromhex(hx); n = len(b)
    if n == 1: return str(b[0])
    if n == 2: return str(struct.unpack('<H', b)[0])
    if n in (4,) or (n > 4 and not any(b[4:])):
        u = struct.unpack('<I', b[:4])[0]; f = struct.unpack('<f', b[:4])[0]
        return '%d' % u if (u < 100000 or not (1e-6 < abs(f) < 1e6)) else '%d/f%g' % (u, round(f, 4))
    if n == 8:
        f, u = struct.unpack('<fI', b)
        if u < 16 and (f == 0 or 1e-6 < abs(f) < 1e6): return '%g u%d' % (round(f, 4), u)
        return '%d,%d' % struct.unpack('<II', b)
    return hx if n <= 24 else hx[:48] + '..(%d bytes)' % n

def gen_fmt(v, names=None):
    if v[0] == 'str': return repr(v[1])
    if v[0] == 'vec': return 'vec[' + v[1] + ']'
    if v[0] == 'tbl': return '{' + ', '.join('%s=%s' % ((names or {}).get(k, k), gen_fmt(x)) for k, x in sorted(v[1].items())) + '}'
    return raw_fmt(v[1])

STYLE_SLOTS = {0: 'family', 3: 'color', 5: 'size', 7: 'bold', 9: 'italic', 17: 'lineh', 19: 'align', 21: 'indent', 23: 'mt', 25: 'mr', 27: 'mb', 29: 'ml',
               31: 'pt', 33: 'pr', 35: 'pb', 37: 'pl'}
_LEN = {5, 15, 17, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41}
def _style_tab(rec, t, kind):
    """schema-aware decode of a style table -> {slot: value} (independent of padding / table order)"""
    vt = t - struct.unpack_from('<i', rec, t)[0]; vsz = struct.unpack_from('<H', rec, vt)[0]; out = {}
    for i in range((vsz - 4) // 2):
        of = struct.unpack_from('<H', rec, vt + 4 + 2 * i)[0]
        if not of: continue
        a = t + of; sub = lambda: a + struct.unpack_from('<I', rec, a)[0]
        if kind == 'root':
            if i == 0: p = sub(); out[0] = rec[p + 4:p + 4 + struct.unpack_from('<I', rec, p)[0]].decode('utf-8', 'replace')
            elif i == 3: out[i] = struct.unpack_from('<I', rec, a)[0]
            elif i in _LEN: x, un = struct.unpack_from('<fI', rec, a); out[i] = (round(x, 4), un)
            elif i in (11, 43, 45): out[i] = _style_tab(rec, sub(), {11: 'deco', 43: 'border', 45: 'bg'}[i])
            else: out[i] = rec[a]
        elif kind == 'border': out[i] = _style_tab(rec, sub(), 'side') if i % 2 else rec[a]
        elif kind == 'side': out[i] = (lambda x: (round(x[0], 4), x[1]))(struct.unpack_from('<fI', rec, a)) if i == 1 else struct.unpack_from('<I', rec, a)[0] if i == 5 else rec[a]
        elif kind == 'bg': out[i] = struct.unpack_from('<I', rec, a)[0] if i == 1 else rec[a]
        else: out[i] = rec[a]
    return out
def style_gen(rec):
    """Decoded style record -> {slot: ('str'|'raw', text)} (schema-aware, see _style_tab)."""
    try: return {k: ('str', v if isinstance(v, str) else repr(v)) for k, v in _style_tab(rec, _u32(rec, 0), 'root').items()}
    except (struct.error, IndexError): return {'err': ('raw', rec.hex())}
def style_fmt(g): return ' '.join('%s=%s' % (STYLE_SLOTS.get(k, 's%s' % k), gen_fmt(v)) for k, v in sorted(g.items(), key=lambda kv: str(kv[0])))

# ---------------------------------------------------------------------------- loader
def load(path):
    d = open(path, 'rb').read(); u32 = lambda o: struct.unpack_from('<I', d, o)[0]
    u16 = lambda o: struct.unpack_from('<H', d, o)[0]
    def tab(t):
        v = t - struct.unpack_from('<i', d, t)[0]; n = u16(v)
        return [u16(v + 4 + 2 * i) for i in range((n - 4) // 2)], u16(v + 2)
    def fld(t, i, sz=4):
        f, _ = tab(t); return int.from_bytes(d[t + f[i]:t + f[i] + sz], 'little') if i < len(f) and f[i] else None
    def sub(t, i):
        f, _ = tab(t); p = t + f[i]; return p + u32(p) if i < len(f) and f[i] else None
    def s(t, i):
        q = sub(t, i); return d[q + 4:q + 4 + u32(q)].decode('utf-8', 'replace') if q else None
    def extra(t, known):
        return {k: raw.hex() for k, (a, raw) in fb_spans(d, t).items() if k not in known}
    root = u32(0); f, _ = tab(root); R = {}
    for slot in (4, 5, 6, 7, 8, 11):
        R[slot] = struct.unpack_from('<II', d, root + f[slot]) if slot < len(f) and f[slot] else (0, 0)
    R['eof'] = u32(root + f[2]); R['magic'] = d[root + f[0]:root + f[0] + 4]
    idx = lambda slot: [(u32(R[slot][0] + 8 * i), u32(R[slot][0] + 8 * i + 4)) for i in range(R[slot][1] // 8)]
    o = dict(size=len(d), eof=R['eof'], magic=R['magic'])
    o['header'] = {k: raw.hex() for k, (a, raw) in fb_spans(d, root).items()}
    o['header_regions'] = {}
    for k, hx in o['header'].items():
        b = bytes.fromhex(hx)
        if len(b) >= 8 and k not in (0, 1, 2, 3):
            off, sz = struct.unpack_from('<II', b)
            if 0x60 <= off < len(d) and 0 < sz <= len(d) - off: o['header_regions'][k] = (off, sz, d[off:off + min(sz, 64)].hex())
    # chapters
    chs = []
    for ho, hs in idx(6):
        t = ho + u32(ho); h = dict(id=s(t, 0), title=s(t, 1), np=fld(t, 8) or 0, nr=fld(t, 9) or 0, err=None)
        try:
            T = tab(t)[0]; g = lambda i: struct.unpack_from('<II', d, t + T[i]) if i < len(T) and T[i] else (0, 0)
            f3, f4, f7 = g(3), g(4), g(7)
            B = [u32(f3[0] + f7[0] + 4 * i) for i in range(f7[1] // 4)]
            A = [u32(f3[0] + f4[0] + 4 * i) for i in range(f4[1] // 4)]
            runs = []
            for i in range(h['nr']):
                p = f3[0] + B[2 * i]; r = p + u32(p); typ = fld(r, 0, 1); v = sub(r, 1)
                if typ == 1: runs.append(('t', s(v, 0), fld(v, 1), extra(v, (0, 1))))
                elif typ == 4:
                    w = sub(v, 1); runs.append(('l', s(w, 0), fld(w, 1), s(v, 8), fld(v, 2, 1), tuple(fld(v, k, 2) for k in range(3, 8)), extra(v, (1, 2, 3, 4, 5, 6, 7, 8)), extra(w, (0, 1))))
                elif typ == 2: runs.append(('i', fld(v, 0), fld(v, 1), extra(v, (0, 1))))
                else: runs.append(('?', 'type %s' % typ, 0, d[p:p + max(8, B[2 * i + 1])].hex()[:96]))
            nrb = 2 * h['nr']; ncont = (len(B) - nrb) // 2
            h['containers'] = [(B[nrb + 2 * j], B[nrb + 2 * j + 1]) for j in range(ncont)]
            T11 = struct.unpack_from('<HH', d, t + T[11]) if len(T) > 11 and T[11] else None
            h.update(runs=runs, rows=[tuple(A[5 * j:5 * j + 5]) for j in range(h['np'])], hsize=hs, f11=T11,
                     hextra=extra(t, (0, 1, 3, 4, 5, 6, 7, 8, 9, 11)), hslots=sorted(fb_spans(d, t)))
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            h.update(runs=[], rows=[], hsize=hs, containers=[], f11=None, hextra={}, hslots=[], err='decode error: %r' % e)
        chs.append(h)
    o['chapters'] = chs
    o['toc'] = [(s(t, 0), s(t, 1), fld(t, 2, 2), fld(t, 3, 2)) for t in (a + u32(a) for a, _ in idx(5))]
    o['toc_extra'] = [extra(t, (0, 1, 2, 3)) for t in (a + u32(a) for a, _ in idx(5))]
    o['meta'] = []
    if R[4][0]:
        mt = R[4][0] + u32(R[4][0]); vp = mt + tab(mt)[0][0]; vec = vp + u32(vp)
        o['meta'] = [(s(e, 0), s(e, 1)) for e in (vec + 4 + 4 * k + u32(vec + 4 + 4 * k) for k in range(u32(vec)))]
    o['images'] = []; o['images_raw'] = []
    for a, _ in idx(8):
        r = a + u32(a); vv = sub(sub(r, 1), 0); o['images'].append((fld(r, 4), fld(r, 5), fld(r, 6, 1), _, d[vv + 4:vv + 4 + u32(vv)]))
        o['images_raw'].append(extra(r, (1, 4, 5, 6)))
    o['nstyles'] = len(idx(7)); o['styles'] = [d[a:a + n] for a, n in idx(7)]
    o['pagination'] = [(u32(R[11][0] + 8 * i), u32(R[11][0] + 8 * i + 4)) for i in range(R[11][1] // 8)]
    return o

def props(rec):
    t = struct.unpack_from('<I', rec, 0)[0]; vt = t - struct.unpack_from('<i', rec, t)[0]
    f = [struct.unpack_from('<H', rec, vt + 4 + 2 * i)[0] for i in range((struct.unpack_from('<H', rec, vt)[0] - 4) // 2)]
    def L(sl):
        if sl >= len(f) or not f[sl]: return None
        x, un = struct.unpack_from('<fI', rec, t + f[sl]); return (round(x, 2), un if x else 0)
    u8 = lambda sl: rec[t + f[sl]] if sl < len(f) and f[sl] else None
    return dict(size=L(5), bold=u8(7), italic=u8(9), align=u8(19), indent=L(21), mt=L(23), mr=L(25), mb=L(27), ml=L(29))

def main(a, b):
    A, B = load(a), load(b)
    print('file size       ref %d  mine %d  (%.2f%%)' % (A['size'], B['size'], 100.0 * B['size'] / A['size']))
    print('EOF field ok    ref %s mine %s  magic %s/%s' % (A['eof'] == A['size'], B['eof'] == B['size'], A['magic'], B['magic']))
    print('chapters        ref %d mine %d' % (len(A['chapters']), len(B['chapters'])))
    same_id = sum(x['id'] == y['id'] and x['title'] == y['title'] for x, y in zip(A['chapters'], B['chapters']))
    same_cnt = sum((x['np'], x['nr']) == (y['np'], y['nr']) for x, y in zip(A['chapters'], B['chapters']))
    txt = lambda c: [(r[0], r[1], r[3] if r[0] == 'l' else None) for r in c['runs'] if r[0] != 'i']
    same_txt = sum(txt(x) == txt(y) for x, y in zip(A['chapters'], B['chapters']))
    allr = lambda O: [r[1] for c in O['chapters'] for r in c['runs'] if r[0] != 'i']
    ta, tb = allr(A), allr(B)
    print('id+title equal  %d/%d;  (np,nr) equal %d/%d;  run text+link sequence equal %d/%d' % (same_id, len(A['chapters']), same_cnt, len(A['chapters']), same_txt, len(A['chapters'])))
    print('total runs      ref %d mine %d; identical text strings in order: %d' % (len(ta), len(tb), sum(x == y for x, y in zip(ta, tb))))
    print('joined text eq  %s (ignoring spaces: %s)' % (''.join(ta) == ''.join(tb), ''.join(ta).replace(' ', '') == ''.join(tb).replace(' ', '')))
    print('TOC             ref %d mine %d equal %s' % (len(A['toc']), len(B['toc']), A['toc'] == B['toc']))
    km = lambda O: [k for k, _ in O['meta']]
    print('metadata keys   equal %s ; values equal for %s' % (km(A) == km(B), [k for (k, v), (k2, v2) in zip(A['meta'], B['meta']) if v == v2]))
    print('images          ref %s' % [x[:3] for x in A['images']]); print('                mine %s' % [x[:3] for x in B['images']])
    print('styles          ref %d mine %d' % (A['nstyles'], B['nstyles']))
    pa, pb = A['pagination'], B['pagination']
    g = lambda p: collections.defaultdict(list, {k: [b for a, b in p if a == k] for k in {a for a, _ in p}})
    ga, gb = g(pa), g(pb)
    print('pagination      ref %d pairs, mine %d; chapters with identical page lists: %d/%d' % (len(pa), len(pb), sum(ga[k] == gb[k] for k in ga), len(ga)))
    ra = [r for c in A['chapters'] for r in c['rows']]; rb = [r for c in B['chapters'] for r in c['rows']]
    print('para rows       ref %d mine %d; (kind,first,nruns) equal %d' % (len(ra), len(rb), sum((x[0], x[3], x[4]) == (y[0], y[3], y[4]) for x, y in zip(ra, rb))))
    ok = collections.Counter(); n = 0
    for x, y in zip(A['chapters'], B['chapters']):
        for ra, rb in zip(x['rows'], y['rows']):
            n += 1; pa, pb = props(A['styles'][ra[1] - 1]), props(B['styles'][rb[1] - 1])
            for k in pa: ok[k] += pa[k] == pb[k]
    print('paragraph style props equal (share of %d paragraphs): %s' % (n, {k: round(v / n, 3) for k, v in ok.items()}))
    for x, y in zip(A['chapters'], B['chapters']):
        if txt(x) != txt(y):
            n = next((i for i, (p, q) in enumerate(zip(txt(x), txt(y))) if p != q), min(len(txt(x)), len(txt(y))))
            print('  first diff in', x['id'], 'run', n, repr(txt(x)[n:n + 1])[:150], '|', repr(txt(y)[n:n + 1])[:150]); break

def check(path):
    """Self-consistency of an .opub (header, indexes, chapter regions, style/image/chapter references)."""
    o = load(path); d = open(path, 'rb').read(); errs = []
    if o['eof'] != len(d): errs.append('EOF field != file size')
    ns, ni, nc = o['nstyles'], len(o['images']), len(o['chapters'])
    for c in o['chapters']:
        if sum(r[4] for r in c['rows']) != c['nr'] - sum(1 for r in c['rows'] if False): errs.append('%s: rows cover %d of %d runs' % (c['id'], sum(r[4] for r in c['rows']), c['nr']))
        for r in c['rows']:
            if not 1 <= r[1] <= ns: errs.append('%s: bad paragraph style %d' % (c['id'], r[1]))
        for r in c['runs']:
            if r[0] in 'tl' and not 1 <= r[2] <= ns: errs.append('%s: bad run style' % c['id'])
            if r[0] == 'i' and not 0 <= r[1] < ni: errs.append('%s: bad image index' % c['id'])
    for t, h, lv, ix in o['toc'][1:]:
        if ix is not None and ix >= nc + 2: errs.append('toc index out of range: %s' % t)
    print('check %s: %d chapters, %d styles, %d images, %d toc, %d pagination pairs: %s' % (os.path.basename(path), nc, ns, ni, len(o['toc']), len(o['pagination']), 'OK' if not errs else errs[:10]))

def roundtrip(path):
    """Decode every fragment of a real .opub and re-encode it with opub.py's writer; count byte-identical fragments."""
    import opub
    d = open(path, 'rb').read(); u32 = lambda o: struct.unpack_from('<I', d, o)[0]; u16 = lambda o: struct.unpack_from('<H', d, o)[0]
    root = u32(0); vt = root - struct.unpack_from('<i', d, root)[0]
    sl = lambda i: struct.unpack_from('<II', d, root + u16(vt + 4 + 2 * i))
    idx = lambda i: [(u32(sl(i)[0] + 8 * k), u32(sl(i)[0] + 8 * k + 4)) for k in range(sl(i)[1] // 8)]
    def tab(t):
        v = t - struct.unpack_from('<i', d, t)[0]; return [u16(v + 4 + 2 * i) for i in range((u16(v) - 4) // 2)]
    fld = lambda t, i, sz=4: int.from_bytes(d[t + tab(t)[i]:t + tab(t)[i] + sz], 'little') if i < len(tab(t)) and tab(t)[i] else None
    def sub(t, i):
        p = t + tab(t)[i]; return p + u32(p)
    def s(t, i):
        q = sub(t, i); return d[q + 4:q + 4 + u32(q)].decode()
    res = collections.OrderedDict((k, [0, 0]) for k in ('text block', 'link block', 'image-ref block', 'chapter header', 'toc record', 'metadata', 'image'))
    def chk(k, mine, off, n):
        res[k][1] += 1; res[k][0] += d[off:off + n] == mine and len(mine) == n
    for ho, hs in idx(6):
        t = ho + u32(ho); T = tab(t); g = lambda i: struct.unpack_from('<II', d, t + T[i])
        f3, f7 = g(3), g(7); np_, nr = fld(t, 8), fld(t, 9)
        chk('chapter header', opub.fb_header(s(t, 0), s(t, 1), f3, g(4), g(5), g(6), f7, np_, nr, struct.unpack_from('<HH', d, t + T[11]) if len(T) > 11 and T[11] else None), ho, hs)
        for i in range(nr):
            st, n = u32(f3[0] + f7[0] + 8 * i), u32(f3[0] + f7[0] + 8 * i + 4); p = f3[0] + st; r = p + u32(p); typ = fld(r, 0, 1); v = sub(r, 1)
            if typ == 1: chk('text block', opub.fb_text(s(v, 0), fld(v, 1)), p, n)
            elif typ == 4:
                w = sub(v, 1); m_ = opub.fb_link(s(w, 0), fld(w, 1), s(v, 8), fld(v, 2, 1), fld(v, 3, 2), fld(v, 4, 2)); chk('link block', m_, p, n)
                if d[p:p + n] != m_ and res['link block'][1] - res['link block'][0] < 3: print('   link diff', s(w, 0)[:20], s(v, 8)[:30], fld(v, 2, 1), [fld(v, k, 2) for k in range(3, 8)], len(m_), n)
            else:
                chk('image-ref block', opub.fb_image_ref(fld(v, 0), fld(v, 1)), p, n)
                if d[p:p + n] != opub.fb_image_ref(fld(v, 0), fld(v, 1)): print('   img-ref diff', d[p:p + n].hex(), fld(v, 0), fld(v, 1))
    for i, (a, n) in enumerate(idx(5)):
        t = a + u32(a)
        mine = opub.fb_toc(s(t, 0), s(t, 1)) if i == 0 else opub.fb_toc(s(t, 0), s(t, 1), fld(t, 2, 2), fld(t, 3, 2) or 0)
        chk('toc record', mine, a, n)
    mo, msz = sl(4); mt = mo + u32(mo); vp = mt + tab(mt)[0]; vec = vp + u32(vp)
    kv = [(s(e, 0), s(e, 1)) for e in (vec + 4 + 4 * k + u32(vec + 4 + 4 * k) for k in range(u32(vec)))]
    chk('metadata', opub.fb_meta(kv), mo, msz)
    for a, n in idx(8):
        r = a + u32(a); vv = sub(sub(r, 1), 0); chk('image', opub.fb_image(fld(r, 4), fld(r, 5), d[vv + 4:vv + 4 + u32(vv)], fld(r, 6, 1)), a, n)
    for k, (ok, tot) in res.items(): print('  %-16s %d/%d fragments re-encode byte-identical' % (k, ok, tot))

if __name__ == '__main__':
    if sys.argv[1] == '--roundtrip': roundtrip(sys.argv[2])
    elif sys.argv[1] == '--check': check(sys.argv[2])
    else: main(sys.argv[1], sys.argv[2])
