#!/usr/bin/env python3
"""EPUB -> OPUB (Onyx Boox NeoReader) converter.  Reverse-engineered, see FORMAT.md.

    python3 opub.py book.epub [out.opub]
Stdlib only.  Pillow or macOS `sips` are used (if present) to rasterise images.
"""
import sys, os, re, io, math, struct, hashlib, uuid, zipfile, posixpath, subprocess, tempfile
import urllib.parse
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------- FlatBuffers writer
class FB:
    """Back-to-front FlatBuffers builder with the layout rules of the official builders."""
    def __init__(s):
        s.b = bytearray(); s.minalign = 1; s.vts = []
    def off(s): return len(s.b)
    def prep(s, size, add=0):
        s.minalign = max(s.minalign, size)
        s.b[0:0] = bytes((-(len(s.b) + add)) & (size - 1))
    def string(s, v):
        raw = v.encode('utf-8'); s.prep(4, len(raw) + 1)
        s.b[0:0] = raw + b'\0'; s.prep(4); s.b[0:0] = struct.pack('<I', len(raw)); return s.off()
    def bytes_vec(s, raw):
        s.prep(4, len(raw)); s.b[0:0] = raw; s.prep(4); s.b[0:0] = struct.pack('<I', len(raw)); return s.off()
    def offset_vec(s, ts):
        s.prep(4, 4 * len(ts))
        for t in reversed(ts):
            s.prep(4); s.b[0:0] = struct.pack('<I', len(s.b) - t + 4)
        s.prep(4); s.b[0:0] = struct.pack('<I', len(ts)); return s.off()
    def start(s, n): s.cur = [0] * n; s.obj_end = s.off()
    def _add(s, slot, al, raw): s.prep(al); s.b[0:0] = raw; s.cur[slot] = s.off()
    def u8(s, slot, v): s._add(slot, 1, struct.pack('<B', v))
    def u16(s, slot, v): s._add(slot, 2, struct.pack('<H', v))
    def u32(s, slot, v): s._add(slot, 4, struct.pack('<I', v))
    def raw4(s, slot, raw): s._add(slot, 4, raw)                      # 4-aligned inline struct
    def ref(s, slot, t): s.prep(4); s.b[0:0] = struct.pack('<I', len(s.b) - t + 4); s.cur[slot] = s.off()
    def end(s):
        s.prep(4); s.b[0:0] = bytes(4); obj = s.off(); vt = s.cur
        while vt and vt[-1] == 0: vt = vt[:-1]
        rel = tuple((obj - x) if x else 0 for x in vt)
        ex = next((vo for vo, vr in reversed(s.vts) if vr == rel), None)
        if ex is None:
            for r in reversed(rel): s.b[0:0] = struct.pack('<H', r)
            s.b[0:0] = struct.pack('<H', obj - s.obj_end); s.b[0:0] = struct.pack('<H', (len(rel) + 2) * 2)
            ex = s.off(); s.vts.append((ex, rel))
        pos = len(s.b) - obj; s.b[pos:pos + 4] = struct.pack('<i', ex - obj); return obj
    def finish(s, root):
        s.prep(s.minalign, 4); s.prep(4); s.b[0:0] = struct.pack('<I', len(s.b) - root + 4); return bytes(s.b)

def text_tab(f, text, style):
    t = f.string(text); f.start(2); f.u32(1, style); f.ref(0, t); return f.end()

def fb_text(text, style):
    f = FB(); t = text_tab(f, text, style); f.start(2); f.ref(1, t); f.u8(0, 1); return f.finish(f.end())

def fb_link(text, style, href, ltype, src, anchor=0, nodeco=0, img=None):
    """Link block.  text is a string, or (img is not None) the image index of a linked image."""
    f = FB()
    if img is None: t = text_tab(f, text, style)
    else: f.start(2); f.u32(1, style); f.u32(0, img); t = f.end()
    h = f.string(href)
    f.start(9); f.ref(8, h); f.ref(1, t)
    for sl in (7, 6, 5, 4, 3): f.u16(sl, {3: src, 4: anchor, 5: anchor, 7: nodeco}.get(sl, 0))
    f.u8(2, ltype); f.u8(0, 1 if img is None else 2); l = f.end()
    f.start(2); f.ref(1, l); f.u8(0, 4); return f.finish(f.end())

def fb_br(v=0):
    """line break block: type 3, value {u32 v}: v = 0 for <br>, 4 for a newline inside <pre> [exact, 10_whitespace ref]"""
    f = FB(); f.start(1); f.u32(0, v); t = f.end(); f.start(2); f.ref(1, t); f.u8(0, 3); return f.finish(f.end())

def fb_image_ref(idx, style=3):
    f = FB(); f.start(2); f.u32(1, style); f.u32(0, idx); t = f.end()
    f.start(2); f.ref(1, t); f.u8(0, 2); return f.finish(f.end())

def fb_header(cid, title, f3, f4, f5, f6, f7, np_, nr, f11=None):
    f = FB(); a = f.string(cid); b = f.string(title); f.start(12 if f11 else 10)
    if f11: f.raw4(11, struct.pack('<HH', *f11))      # (container count, len(title)); containers are not generated
    f.u32(9, nr); f.u32(8, np_)
    for sl, v in ((7, f7), (6, f6), (5, f5), (4, f4), (3, f3)): f.raw4(sl, struct.pack('<II', *v))
    f.ref(1, b); f.ref(0, a); return f.finish(f.end())

def fb_toc(title, href, level=None, idx=0):
    f = FB(); a = f.string(title); b = f.string(href)
    if level is None:
        f.start(2); f.ref(1, b); f.ref(0, a)
    else:
        f.start(4); f.ref(1, b); f.ref(0, a)
        if idx: f.u16(3, idx)
        f.u16(2, level)
    return f.finish(f.end())

def fb_meta(kv):
    f = FB(); es = []
    for k, v in kv:
        a = f.string(k); b = f.string(v); f.start(2); f.ref(1, b); f.ref(0, a); es.append(f.end())
    vec = f.offset_vec(es); f.start(1); f.ref(0, vec); return f.finish(f.end())

def fb_image(w, h, pix, kind):
    """kind 1 = 8-bit gray, 2/3/4 = 1-bit middle/small/tiny"""
    f = FB(); v = f.bytes_vec(pix); f.start(1); f.ref(0, v); t = f.end()
    f.start(9); f.u32(5, h); f.u32(4, w); f.ref(1, t)
    f.u8(8, 1); f.u8(7, 4 if kind == 1 else 1); f.u8(6, kind); f.u8(3, 1); f.u8(2, 1); f.u8(0, 2)
    return f.finish(f.end())

# --------------------------------------------------------------------------- style records
# A record is a FlatBuffers table with ~48 slots: (flag u8, value) pairs. Reproduced byte-exactly for ~96 % of the reference records.
# Text slots: 0 font family (string), 2/3 colour, 4/5 size, 6/7 bold (1 normal, 2 css bold, 3 tag bold), 8/9 italic, 10/11 decoration table
# {0:1, 1:1=underline}, 14/15 (0 em), 16/17 line height, 18/19 align, 20/21 indent, 22..29 margins T R B L, 30..37 paddings T R B L,
# 38/39 width, 40/41 height (images), 42/43 border {0,2,4,6 flags; 1,3,5,7 = side tables {0 flag, 1 width, 2 flag, 3 style, 4 flag, 5 colour}},
# 44/45 background {0 flag, 1 colour}, 46/47 (1, display code: 3 block, 5 list-item, 12 table-row, 13 table-cell, 16 caption; absent = inline).
# Container records use the same slots but only those that were specified (flag + value), e.g. a <ul>: only 36/37 = padding-left 40px.
UNIT = {'n': 1, 'px': 2, 'em': 3, '%': 5, 'pt': 6, 'auto': 30, 'vh': 10}   # unit codes of lengths; 1 = unitless (line-height 2)
LEN_SLOTS = {5, 15, 17, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41}
PAD = {'pt': 31, 'pr': 33, 'pb': 35, 'pl': 37}
MAR = {'mt': 23, 'mr': 25, 'mb': 27, 'ml': 29}
def _kind(tab, sl):
    if tab == 'root': return 'len' if sl in LEN_SLOTS else 'u32' if sl == 3 else 'u8'
    if tab == 'side': return 'len' if sl == 1 else 'u32' if sl == 5 else 'u8'
    return 'u32' if (tab == 'bg' and sl == 1) else 'u8'
def _table(f, spec, tab, subs):
    f.start(max(spec) + 1); items = []
    for sl, v in spec.items():
        k = 'ref' if sl in subs else _kind(tab, sl); items.append((1 if k == 'u8' else 4, sl, k, subs.get(sl, v)))
    for _, sl, k, v in sorted(items, key=lambda x: (-x[0], -x[1])):      # alignment descending, then slot descending
        if k == 'ref': f.ref(sl, v)
        elif k == 'len': f.raw4(sl, struct.pack('<fI', float(v[0]), UNIT[v[1]] if isinstance(v[1], str) else v[1]))
        elif k == 'u32': f.u32(sl, v)
        else: f.u8(sl, v)
    return f.end()
def fb_style(spec):
    f = FB(); subs = {}
    if 11 in spec: subs[11] = _table(f, spec[11], 'deco', {})
    if 43 in spec:
        b = spec[43]; bs = {sl: _table(f, b[sl], 'side', {}) for sl in (1, 3, 5, 7) if sl in b}
        subs[43] = _table(f, b, 'border', bs)
    if 45 in spec: subs[45] = _table(f, spec[45], 'bg', {})
    if 0 in spec: subs[0] = f.string(spec[0])
    return f.finish(_table(f, spec, 'root', subs))

def border_spec(bd):
    """{side: (width_len, style, colour|None)} -> slot 43 table"""
    t = {}
    for i, side in enumerate(('t', 'r', 'b', 'l')):
        if side not in bd: continue
        w, sty, col = bd[side]; sd = {0: 1, 1: (w[0], w[1])}
        if sty:
            sd[2] = 1
            if sty in BSTYLE: sd[3] = BSTYLE[sty]
        if col is not None: sd[4] = 1; sd[5] = col
        t[2 * i] = 1; t[2 * i + 1] = sd
    return t
BSTYLE = {'dotted': 2, 'dashed': 3, 'double': 4}

def style_spec(st):
    """computed style dict -> record spec.  st['cont'] = container record (only the specified padding sides / border / background)."""
    if st.get('cont'):
        sp = {}
        for k, sl in PAD.items():
            if k in st['pspec']: sp[sl - 1] = 1; sp[sl] = st[k]
        if st['border']: sp[42] = 1; sp[43] = border_spec(st['border'])
        if st['bg'] is not None: sp[44] = 1; sp[45] = {0: 1, 1: st['bg']}
        return sp
    sp = {2: 1, 3: st['color'], 4: 1, 5: st['size'], 6: 1, 7: st['bold'], 8: 1, 9: st['italic'], 10: 1, 11: {0: 1, **({1: st['deco']} if st['deco'] else {})},
          14: 1, 15: st['ls'], 16: 1, 17: st['lh'] or (1.4664, 'em'), 18: 1, 19: st['align'], 20: 1, 21: st['indent']}
    for k, sl in {**MAR, **PAD}.items(): sp[sl - 1] = 1; sp[sl] = st[k]
    if st['w']: sp[38] = 1; sp[39] = st['w']
    if st['h']: sp[40] = 1; sp[41] = st['h']
    if st['border']: sp[42] = 1; sp[43] = border_spec(st['border'])
    if st['bg'] is not None: sp[44] = 1; sp[45] = {0: 1, 1: st['bg']}
    if st['disp']: sp[46] = 1; sp[47] = st['disp']
    if st['fam']: sp[0] = st['fam']
    return sp

# --------------------------------------------------------------------------- CSS
NAMED = {'lightgray': 0xd3d3d3, 'lightgrey': 0xd3d3d3, 'darkgray': 0xa9a9a9, 'dimgray': 0x696969, 'orange': 0xffa500, 'yellow': 0xffff00, 'purple': 0x800080, 'black': 0, 'white': 0xffffff, 'red': 0xff0000, 'blue': 0x0000ff, 'green': 0x008000, 'gray': 0x808080,
         'grey': 0x808080, 'silver': 0xc0c0c0, 'maroon': 0x800000, 'navy': 0x000080}
ZERO = (0.0, 'em'); ZERO3 = ZERO

def parse_len(v, size=1.0):
    v = v.strip().lower()
    if v == 'auto': return (0.0, 'auto')
    m = re.match(r'^(-?[\d.]+)\s*(em|rem|ex|%|px|pt|vh|vw|in|cm|mm|pc)?$', v)
    if not m:
        return None
    n = float(m.group(1)); u = m.group(2) or ''
    if u in ('em', 'rem'): return (n, 'em')
    if u == 'ex': return (n * 0.5, 'em')
    if u in ('%', 'vh'): return (n, u)
    if u == 'pt': return (n, 'pt')
    if u in ('in',): return (n * 96, 'px')
    if u == 'cm': return (n * 37.8, 'px')
    if u == 'mm': return (n * 3.78, 'px')
    if u == 'pc': return (n * 16, 'px')
    if u == 'vw': return (n, '%')
    return (n, 'px')   # a bare 0 is 0 px (Boox stores `margin: 0` as unit px, an unset margin as 0 em)

def parse_color(v):
    v = v.strip().lower()
    if v in NAMED: return 0xff000000 | NAMED[v]
    m = re.match(r'^#([0-9a-f]{3}|[0-9a-f]{6})$', v)
    if m:
        h = m.group(1); h = ''.join(c * 2 for c in h) if len(h) == 3 else h
        return 0xff000000 | int(h, 16)
    return None

def parse_decls(text):
    out = []
    for d in text.split(';'):
        if ':' in d:
            k, v = d.split(':', 1); out.append((k.strip().lower(), re.sub(r'\s*!important', '', v).strip()))
    return out

def parse_css(text, rules):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    i = 0; n = len(text)
    while i < n:
        j = text.find('{', i)
        if j < 0: break
        sel = text[i:j].strip(); depth = 1; k = j + 1
        while k < n and depth:
            depth += (text[k] == '{') - (text[k] == '}'); k += 1
        body = text[j + 1:k - 1]; i = k
        if sel.startswith('@'):
            if sel.startswith('@media') and not re.search(r'amzn|print', sel):
                parse_css(body, rules)
            continue
        decls = parse_decls(body)
        for s in sel.split(','):
            s = s.strip()
            if not s or ':' in s or '[' in s or '+' in s or '~' in s or '*' in s: continue
            parts = re.findall(r'[>]|[^\s>]+', s.replace('>', ' > '))
            chain = []; comb = ' '
            for p in parts:
                if p == '>': comb = '>'; continue
                m = re.match(r'^([a-zA-Z][\w-]*)?(#[\w-]+)?((?:\.[\w-]+)*)$', p)
                if not m: chain = None; break
                chain.append((comb, m.group(1), (m.group(2) or '')[1:], [c for c in m.group(3).split('.') if c])); comb = ' '
            if not chain: continue
            spec = sum((1 if c[2] else 0) * 10000 + len(c[3]) * 100 + (1 if c[1] else 0) for c in chain)
            rules.append((spec, len(rules), chain, decls))

def match1(node, c):
    _, tag, idv, classes = c
    if tag and node.tag != tag.lower(): return False
    if idv and node.attrs.get('id') != idv: return False
    nc = node.attrs.get('class', '').split()
    return all(x in nc for x in classes)

def matches(node, chain):
    def rec(n, i):
        if not match1(n, chain[i]): return False
        if i == 0: return True
        comb = chain[i][0]; p = n.parent
        if comb == '>': return p is not None and rec(p, i - 1)
        while p is not None:
            if rec(p, i - 1): return True
            p = p.parent
        return False
    return rec(node, len(chain) - 1)

# --------------------------------------------------------------------------- DOM
VOID = {'br', 'hr', 'img', 'meta', 'link', 'input', 'col', 'area', 'base', 'wbr'}
class Node:
    def __init__(s, tag, attrs, parent): s.tag, s.attrs, s.parent, s.kids = tag, attrs, parent, []
class DomBuilder(HTMLParser):
    def __init__(s):
        super().__init__(convert_charrefs=True); s.root = Node('#root', {}, None); s.cur = s.root
    def handle_starttag(s, tag, attrs):
        n = Node(tag.lower(), {k.lower(): (v or '') for k, v in attrs}, s.cur); s.cur.kids.append(n)
        if n.tag not in VOID: s.cur = n
    def handle_startendtag(s, tag, attrs):
        s.cur.kids.append(Node(tag.lower(), {k.lower(): (v or '') for k, v in attrs}, s.cur))
    def handle_endtag(s, tag):
        tag = tag.lower(); n = s.cur
        while n is not None and n.tag != tag: n = n.parent
        if n is not None and n.parent is not None: s.cur = n.parent
    def handle_data(s, data):
        if s.cur.kids and isinstance(s.cur.kids[-1], str): s.cur.kids[-1] += data
        else: s.cur.kids.append(data)
def parse_html(text):
    b = DomBuilder(); b.feed(text); return b.root
WS = ' \t\r\n\f'
def svg_images(n):
    out = []
    for k in n.kids:
        if isinstance(k, str): continue
        if k.tag == 'image' and (k.attrs.get('xlink:href') or k.attrs.get('href')): out.append(k.attrs.get('xlink:href') or k.attrs.get('href'))
        else: out += svg_images(k)
    return out
def find(n, tag):
    if isinstance(n, str): return None
    if n.tag == tag: return n
    for k in n.kids:
        r = find(k, tag)
        if r: return r
def text_of(n):
    return n if isinstance(n, str) else ''.join(text_of(k) for k in n.kids)

# --------------------------------------------------------------------------- style computation
BLOCK = {'p', 'div', 'section', 'article', 'aside', 'header', 'footer', 'nav', 'main', 'figure', 'figcaption', 'blockquote',
         'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'table', 'tr', 'td', 'th', 'caption',
         'pre', 'hr', 'body', 'html', 'form', 'address', 'tbody', 'thead', 'tfoot', 'details', 'summary', 'center'}
SKIP = {'head', 'script', 'style', 'title', 'meta', 'link', 'svg', 'math'}
HSIZE = {'h1': 2.0, 'h2': 1.5, 'h3': 1.17, 'h4': 1.0, 'h5': 0.83, 'h6': 0.75}                  # [exact, 01_basic/07_structure refs]
HMARGIN = {'h1': 0.67, 'h2': 0.83, 'h3': 1.0, 'h4': 1.33, 'h5': 1.67, 'h6': 2.33}               # UA margins (em), [exact]
DISP = {'li': 5, 'tr': 12, 'td': 13, 'th': 13, 'caption': 16}                                   # display codes, others block = 3
INHERIT = ('color', 'size', 'ls', 'bold', 'italic', 'align', 'indent', 'lh', 'lstyle', 'fam', 'deco')

def base_style():
    return dict(color=0xff000000, size=(1.0, 'em'), ls=ZERO3, bold=1, italic=1, deco=0, align=1, indent=ZERO, lh=None, fam=None,
                mt=ZERO, mr=ZERO, mb=ZERO, ml=ZERO, pt=ZERO, pr=ZERO, pb=ZERO, pl=ZERO, pspec=frozenset(), border=None, bg=None,
                w=None, h=None, disp=3, display='inline', lstyle='', cont=False)

def box4(vals):
    v = [parse_len(x) or ZERO for x in vals.split()]
    if not v: return None
    if len(v) == 1: v *= 4
    elif len(v) == 2: v = [v[0], v[1], v[0], v[1]]
    elif len(v) == 3: v = [v[0], v[1], v[2], v[1]]
    return v[:4]

def parse_border(v, st):
    """-> (width, style, colour) of a border shorthand value; unspecified parts are None (width then 0 px if only `0`)"""
    w = sty = col = None
    for tok in re.findall(r'rgb\([^)]*\)|\S+', v.lower()):
        if tok in ('thin', 'medium', 'thick'): w = ({'thin': 1, 'medium': 3, 'thick': 5}[tok], 'px')
        elif tok in ('none', 'hidden', 'solid', 'dashed', 'dotted', 'double', 'groove', 'ridge', 'inset', 'outset'): sty = tok
        elif parse_color(tok) is not None: col = parse_color(tok)
        elif parse_len(tok): w = parse_len(tok)
    return w, sty, col

def set_border(st, sides, w, sty, col):
    bd = dict(st['border'] or {})
    for sd in sides:
        if sty in ('none', 'hidden'): bd.pop(sd, None); continue
        bd[sd] = (w if w else (3, 'px') if sty else ZERO, sty, col)
    st['border'] = bd or None

def apply_decls(st, decls):
    for k, v in decls:
        vl = v.lower()
        if k == 'font-size':      # keeps its unit: px, %, pt, em (keyword sizes like x-large are ignored by Boox) [exact, 06_fonts ref]
            m = re.match(r'^(-?[\d.]+)\s*(em|rem|px|pt|%)?$', vl)
            if m: st['size'] = (float(m.group(1)), {'rem': 'em', None: 'px'}.get(m.group(2), m.group(2)))
        elif k == 'letter-spacing':
            l = parse_len(v)
            if l: st['ls'] = l
        elif k == 'font-weight': st['bold'] = 2 if (vl in ('bold', 'bolder') or (vl.isdigit() and int(vl) >= 600)) else 1
        elif k == 'font-style': st['italic'] = 2 if vl in ('italic', 'oblique') else 1
        elif k == 'font-family': st['fam'] = v.strip()
        elif k == 'text-align': st['align'] = {'left': 1, 'start': 1, 'right': 2, 'end': 2, 'center': 3, 'justify': 4}.get(vl, st['align'])
        elif k == 'text-indent': st['indent'] = parse_len(v) or ZERO
        elif k == 'color':
            c = parse_color(v)
            if c: st['color'] = c
        elif k == 'background-color' or (k == 'background' and parse_color(v.split()[0] if v.split() else '') is not None):
            c = parse_color(v.split()[0]) if k == 'background' else parse_color(v)
            if c is not None: st['bg'] = c
        elif k == 'line-height':
            if re.match(r'^[\d.]+$', vl): st['lh'] = (float(vl), 'n')
            elif parse_len(v): st['lh'] = parse_len(v)
        elif k == 'display': st['display'] = vl
        elif k in ('text-decoration', 'text-decoration-line'):
            st['deco'] = 1 if 'underline' in vl else 4 if 'line-through' in vl else 0 if 'none' in vl else st['deco']
        elif k in ('list-style-type', 'list-style'): st['lstyle'] = vl.split()[0] if vl else ''
        elif k in ('margin', 'padding'):
            b = box4(v)
            if b:
                st[k[0] + 't'], st[k[0] + 'r'], st[k[0] + 'b'], st[k[0] + 'l'] = b
                if k == 'padding': st['pspec'] = st['pspec'] | {'pt', 'pr', 'pb', 'pl'}
        elif k == 'border': w, sty, col = parse_border(v, st); set_border(st, 'trbl', w, sty, col)
        elif re.match(r'^border-(top|right|bottom|left)$', k): w, sty, col = parse_border(v, st); set_border(st, k[7], w, sty, col)
        elif k == 'width': st['w'] = parse_len(v)
        elif k == 'height': st['h'] = parse_len(v)
        else:
            m = re.match(r'^(margin|padding)-(top|right|bottom|left)$', k)
            if m:
                key = m.group(1)[0] + m.group(2)[0]; st[key] = parse_len(v) or ZERO
                if m.group(1) == 'padding': st['pspec'] = st['pspec'] | {key}

def compute(node, parent, rules):
    st = base_style()
    for k in INHERIT: st[k] = parent[k]
    t = node.tag; st['disp'] = None
    if t in ('b', 'strong') or t in HSIZE or t == 'th': st['bold'] = 3
    if t in ('i', 'em', 'cite', 'var', 'dfn'): st['italic'] = 2
    if t in ('u', 'ins'): st['deco'] = 1
    if t in ('s', 'strike', 'del'): st['deco'] = 4
    if t in ('sub', 'sup', 'small'): st['size'] = (0.83, 'em')
    if t == 'big': st['size'] = (1.17, 'em')
    if t in BLOCK:
        st['display'] = 'block'; st['size'] = (HSIZE.get(t, 1.0), 'em'); st['disp'] = DISP.get(t, 3)   # block font-size is relative to its parent
        if t == 'p': st['mt'] = st['mb'] = (1.0, 'em')
        if t in HMARGIN: st['mt'] = st['mb'] = (HMARGIN[t], 'em')
        if t in ('ul', 'ol', 'dd'): st['pl'] = (40.0, 'px'); st['pspec'] = frozenset({'pl'})
        if t == 'li': st['pl'] = (2.5, 'em')
        if t == 'blockquote': st['mt'] = st['mb'] = (1.0, 'em'); st['ml'] = st['mr'] = (40.0, 'px')
        if t == 'caption': st['align'] = 3
        if t in ('th', 'center'): st['align'] = 3
    if t == 'a': st['color'] = 0xff0000ff; st['deco'] = 1
    for spec, order, chain, decls in sorted((r for r in rules if matches(node, r[2])), key=lambda r: (r[0], r[1])):
        apply_decls(st, decls)
    if node.attrs.get('style'): apply_decls(st, parse_decls(node.attrs['style']))
    if t == 'img':
        for a in ('width', 'height'):
            m = re.match(r'^\s*([\d.]+)(%?)', node.attrs.get(a, ''))
            if m and not st[a[0]]: st[a[0]] = (float(m.group(1)), '%' if m.group(2) else 'px')
    if st['display'] == 'block' and st['disp'] is None: st['disp'] = 3
    return st

def zero_box(st):
    """style of text that sits directly in the block that owns the row: margins/paddings/border/background live on the block record only"""
    s = dict(st)
    for k in ('mt', 'mr', 'mb', 'ml', 'pt', 'pr', 'pb', 'pl'): s[k] = ZERO
    s['border'] = s['bg'] = None; s['pspec'] = frozenset(); return s

# --------------------------------------------------------------------------- chunking (200-byte runs)
TOK = re.compile(r'\w+|\s+|[^\w\s]', re.U)
def chunk(text, limit=200):
    """split into runs of <= limit UTF-8 bytes at token boundaries; a token longer than the limit is cut at the limit [exact, 10_whitespace ref]"""
    out, cur, cl = [], '', 0
    for m in TOK.finditer(text):
        t = m.group(); l = len(t.encode())
        if cl + l > limit and cur: out.append(cur); cur, cl = '', 0
        while l > limit:
            n = limit; piece = t.encode()[:n]
            while True:
                try: piece = piece.decode(); break
                except UnicodeDecodeError: piece = piece[:-1]
            out.append(piece); t = t[len(piece):]; l = len(t.encode())
        cur += t; cl += l
    if cur: out.append(cur)
    return out

# --------------------------------------------------------------------------- EPUB reading
def u(p): return urllib.parse.unquote(p)

class Epub:
    def __init__(s, data):
        s.z = zipfile.ZipFile(io.BytesIO(data)); s.data = data
        cont = ET.fromstring(s.z.read('META-INF/container.xml'))
        s.opf_path = next(e.get('full-path') for e in cont.iter() if e.tag.endswith('rootfile'))
        s.base = posixpath.dirname(s.opf_path)
        opf = ET.fromstring(s.z.read(s.opf_path)); ln = lambda e: e.tag.split('}')[-1]
        s.meta = {}
        for e in opf.iter():
            if ln(e) in ('title', 'creator', 'publisher', 'language', 'identifier') and (e.text or '').strip():
                s.meta.setdefault(ln(e), []).append(e.text.strip())
        s.man = {}; s.props = {}
        for e in opf.iter():
            if ln(e) == 'item':
                s.man[e.get('id')] = (s.rel(u(e.get('href'))), e.get('media-type')); s.props[e.get('id')] = e.get('properties', '')
        s.spine = [e.get('idref') for e in opf.iter() if ln(e) == 'itemref' and e.get('idref') in s.man]
        s.cover = None
        for i, p in s.props.items():
            if 'cover-image' in p: s.cover = s.man[i][0]
        if not s.cover:
            for e in opf.iter():
                if ln(e) == 'meta' and e.get('name') == 'cover' and e.get('content') in s.man: s.cover = s.man[e.get('content')][0]
        s.ncx = next((s.man[e.get('toc')][0] for e in opf.iter() if ln(e) == 'spine' and e.get('toc') in s.man), None)
        s.nav = next((s.man[i][0] for i, p in s.props.items() if 'nav' in p.split()), None)
    def rel(s, href): return posixpath.normpath(posixpath.join(s.base, href)) if s.base else posixpath.normpath(href)
    def read(s, path):
        try: return s.z.read(path)
        except KeyError: return None
    def toc(s):
        """[(title, path, level)] and the document title."""
        out, title = [], (s.meta.get('title') or [''])[0]
        if s.ncx and s.read(s.ncx):
            root = ET.fromstring(s.read(s.ncx)); ln = lambda e: e.tag.split('}')[-1]
            dt = next((e for e in root if ln(e) == 'docTitle'), None)
            if dt is not None and ''.join(dt.itertext()).strip(): title = ''.join(dt.itertext()).strip()
            def walk(el, lv):
                for e in el:
                    if ln(e) == 'navPoint':
                        lab = next((x for x in e if ln(x) == 'navLabel'), None); con = next((x for x in e if ln(x) == 'content'), None)
                        if lab is not None and con is not None:
                            out.append((' '.join(''.join(lab.itertext()).split()), s.rel(u(con.get('src'))), lv))
                        walk(e, lv + 1)
            nm = next((e for e in root if ln(e) == 'navMap'), root); walk(nm, 1)
        elif s.nav and s.read(s.nav):
            dom = parse_html(s.read(s.nav).decode('utf-8', 'replace')); navdir = posixpath.dirname(s.nav)
            def walk(n, lv):
                for k in n.kids:
                    if isinstance(k, str): continue
                    if k.tag == 'a' and k.attrs.get('href'):
                        out.append((' '.join(text_of(k).split()), posixpath.normpath(posixpath.join(navdir, u(k.attrs['href']))), lv))
                    walk(k, lv + (1 if k.tag == 'ol' else 0))
            def find_nav(n):
                for k in n.kids:
                    if isinstance(k, str): continue
                    if k.tag == 'nav' and 'toc' in k.attrs.get('epub:type', ''): return k
                    r = find_nav(k)
                    if r: return r
            nv = find_nav(dom)
            if nv: walk(nv, 0)
        return title, out

# --------------------------------------------------------------------------- image helpers
def img_size(b):
    try:
        if b[:8] == b'\x89PNG\r\n\x1a\n': return struct.unpack('>II', b[16:24])
        if b[:3] == b'GIF': return struct.unpack('<HH', b[6:10])
        i = 2
        while i < len(b):
            m = b[i + 1]; L = struct.unpack('>H', b[i + 2:i + 4])[0]
            if m in (0xc0, 0xc1, 0xc2, 0xc3): return struct.unpack('>HH', b[i + 5:i + 9])[::-1]
            i += 2 + L
    except Exception: pass
    return None

def png_gray(b):
    """Pure-python PNG decoder (8-bit, non-interlaced; alpha composited on white) -> (w, h, gray bytes) with Rec.601 luma, or None."""
    import zlib
    try:
        if b[:8] != b'\x89PNG\r\n\x1a\n': return None
        p = 8; idat = b''; pal = trns = None
        while p < len(b):
            n, = struct.unpack('>I', b[p:p + 4]); t = b[p + 4:p + 8]; d = b[p + 8:p + 8 + n]; p += 12 + n
            if t == b'IHDR': w, h, bd, ct, _, _, il = struct.unpack('>IIBBBBB', d)
            elif t == b'IDAT': idat += d
            elif t == b'PLTE': pal = d
            elif t == b'tRNS': trns = d
        if bd != 8 or il: return None
        ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]; raw = zlib.decompress(idat); stride = w * ch; out = bytearray(); prev = bytearray(stride); pos = 0
        for y in range(h):
            f = raw[pos]; line = bytearray(raw[pos + 1:pos + 1 + stride]); pos += 1 + stride
            for i in range(stride):
                a_ = line[i - ch] if i >= ch else 0; up = prev[i]; c_ = prev[i - ch] if i >= ch else 0
                if f == 1: line[i] = (line[i] + a_) & 255
                elif f == 2: line[i] = (line[i] + up) & 255
                elif f == 3: line[i] = (line[i] + (a_ + up) // 2) & 255
                elif f == 4:
                    pa, pb, pc = abs(up - c_), abs(a_ - c_), abs(a_ + up - 2 * c_)
                    line[i] = (line[i] + (a_ if pa <= pb and pa <= pc else up if pb <= pc else c_)) & 255
            prev = line
            for x in range(w):
                q = line[x * ch:(x + 1) * ch]; al = 255
                if ct == 0: r = g = bl = q[0]
                elif ct == 2: r, g, bl = q
                elif ct == 3:
                    r, g, bl = pal[3 * q[0]:3 * q[0] + 3]; al = trns[q[0]] if trns and q[0] < len(trns) else 255
                elif ct == 4: r = g = bl = q[0]; al = q[1]
                else: r, g, bl, al = q
                v = (299 * r + 587 * g + 114 * bl) // 1000
                out.append(v if al == 255 else (v * al + 255 * (255 - al)) // 255)
        return w, h, bytes(out)
    except Exception: return None

def resample(g, w, h, W, H):
    """area-average / bilinear resample of an 8-bit gray image"""
    if (w, h) == (W, H): return g
    out = bytearray(); xs = w / W; ys = h / H
    for Y in range(H):
        y0 = Y * ys; y1 = min(h, (Y + 1) * ys); iy0 = int(y0); iy1 = max(iy0 + 1, math.ceil(y1))
        for X in range(W):
            x0 = X * xs; x1 = min(w, (X + 1) * xs); ix0 = int(x0); ix1 = max(ix0 + 1, math.ceil(x1)); tot = wt = 0.0
            for yy in range(iy0, min(iy1, h)):
                wy = min(yy + 1, y1) - max(yy, y0)
                for xx in range(ix0, min(ix1, w)):
                    wx = min(xx + 1, x1) - max(xx, x0); tot += g[yy * w + xx] * wx * wy; wt += wx * wy
            out.append(int(tot / wt + 0.5) if wt else 255)
    return bytes(out)

def load_gray(data, w, h):
    """Decode image bytes -> w*h 8-bit gray (Rec.601 luma [exact for flat colours, 01_basic cover], resampled).  Own PNG decoder, then macOS sips, else flat gray."""
    pg = png_gray(data)
    if pg: return resample(pg[2], pg[0], pg[1], w, h)
    if sys.platform == 'darwin':
        try:
            with tempfile.TemporaryDirectory() as td:
                src, dst = os.path.join(td, 'in'), os.path.join(td, 'out.bmp')
                open(src, 'wb').write(data)
                subprocess.run(['sips', '-s', 'format', 'bmp', '-z', str(h), str(w), src, '--out', dst], check=True, capture_output=True)
                b = open(dst, 'rb').read()
            off, = struct.unpack_from('<I', b, 10); W, H, _, bpp = struct.unpack_from('<iiHH', b, 18)
            bpp //= 8; stride = (W * bpp + 3) & ~3; out = bytearray()
            for y in range(abs(H)):
                row = off + (abs(H) - 1 - y if H > 0 else y) * stride
                for x in range(W):
                    bb, gg, rr = b[row + x * bpp:row + x * bpp + 3]; out.append((299 * rr + 587 * gg + 114 * bb + 3000 * max(rr, gg, bb)) // 4000)   # JPEG covers (Haberman ref) come out lighter than luma: empirical
            return bytes(out)
        except Exception: pass
    return bytes([0xcc]) * (w * h)

def to_1bit(gray, w, h):
    """Floyd-Steinberg dither, MSB first, 1 = white."""
    g = [float(x) for x in gray]; out = bytearray(); bpr = (w + 7) // 8
    for y in range(h):
        row = bytearray(bpr)
        for x in range(w):
            i = y * w + x; old = g[i]; new = 255.0 if old >= 128 else 0.0; err = old - new
            if new: row[x >> 3] |= 0x80 >> (x & 7)
            if x + 1 < w: g[i + 1] += err * 7 / 16
            if y + 1 < h:
                if x: g[i + w - 1] += err * 3 / 16
                g[i + w] += err * 5 / 16
                if x + 1 < w: g[i + w + 1] += err / 16
        out += row
    return bytes(out)

PAGE_W, PAGE_H = 480, 800   # in-book images are shrunk to fit the 480x800 screen [exact, 05_images ref]
def fit(w, h):
    s = min(1.0, PAGE_W / w, PAGE_H / h); return max(1, round(w * s)), max(1, round(h * s))

MAX_PAGES = 999   # device finding: a pagination table with >= 1000 pairs makes the reader's progress stay at 0 % (Boox itself writes <= 999)

def page_count(np_, c):
    """Pages of a chapter with np_ paragraphs: ceil(np_ / c); chapters of 1-2 paragraphs get 1 page, 3-6 at least 2 (only seen when c > 1)."""
    if c <= 1.0: return np_
    if np_ <= 2: return 1
    n = math.ceil(np_ / c - 1e-9)
    return max(n, 2) if np_ <= 6 else n

def page_counts(nps):
    """Pages per chapter.  Boox [fitted on 10 corpus books + 2 real ones]: while the book has <= ~1000 paragraphs every paragraph starts a page;
    above that the divisor is c0 = 1.0194 * N / 1000 (Haberman: 4.89, Kingsbridge ~10.2, 09_long: 6.16 -> 25 pages per 151 paragraphs).
    Boox then scales the biggest chapters down further so the total is 999-1000; we do that (big chapters first, then all) to stay <= MAX_PAGES."""
    N = sum(nps); c0 = max(1.0, 1.0194 * N / 1000) if N > MAX_PAGES else 1.0
    f = lambda big, c: [page_count(n, c if n >= big else c0) for n in nps]
    base = f(0, c0)
    if MAX_PAGES < sum(base) <= MAX_PAGES + 2:   # Boox writes 1000 pairs (09_long); the device wants <= 999: drop the last page(s) of the last chapter(s)
        for _ in range(sum(base) - MAX_PAGES):
            i = max(j for j, n in enumerate(base) if n > 1); base[i] -= 1
        return base
    for big in (200, 7):
        c = c0
        while sum(f(big, c)) > MAX_PAGES and c < 1e4: c *= 1.002
        if sum(f(big, c)) <= MAX_PAGES: return f(big, c)
    return f(7, 1e4)   # more than MAX_PAGES chapters: one page per chapter, build() thins the pairs

ROMAN = [(1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'), (90, 'xc'), (50, 'l'), (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i')]
def marker_for(lst, i, depth, lstyle):
    """list marker text [exact for disc/circle/square by depth, decimal, lower-roman]; i = index of the <li> among its siblings"""
    if lst.tag == 'ul' and lstyle in ('', 'disc', 'circle', 'square', 'decimal', 'lower-roman', 'upper-roman', 'lower-alpha', 'upper-alpha') and lstyle in ('', 'disc', 'circle', 'square'):
        return ('\u2022', '\u25cb', '\u25aa')[{'disc': 0, 'circle': 1, 'square': 2}.get(lstyle, (depth - 1) % 3)] + ' '
    try: n = int(lst.attrs.get('start', '1')) + i
    except ValueError: n = 1 + i
    ls = lstyle if lstyle and lstyle not in ('disc', 'circle', 'square') else 'decimal'
    if ls in ('lower-roman', 'upper-roman'):
        r = ''; m = n
        for v, sy in ROMAN:
            while m >= v: r += sy; m -= v
        return (r if ls == 'lower-roman' else r.upper()) + '. '
    if ls in ('lower-alpha', 'upper-alpha', 'lower-latin', 'upper-latin'):
        r = chr(97 + (n - 1) % 26); return (r if ls.startswith('lower') else r.upper()) + '. '
    return '%d. ' % n

def mklink(text, sid, link, path, kn, ids, img=None):
    href, nodeco, noteref = link
    ext = bool(re.match(r'^[a-zA-Z][\w+.-]*:', href))
    tgt, _, frag = href.partition('#')
    same = not ext and frag and (not tgt or posixpath.normpath(posixpath.join(posixpath.dirname(path), u(tgt))) == path)
    return fb_link(text, sid, href, 3 if ext else 2 if noteref else 1, 0 if ext else kn, ids.get(frag, 0) if same else 0, 0, img)   # slot 7 (1 in one 08_footnotes noteref, 0 elsewhere) is not understood

# --------------------------------------------------------------------------- conversion
class Conv:
    def __init__(s, data):
        s.ep = Epub(data); s.styles = []; s.skeys = {}; s.images = []; s.imgmap = {}
        s.rules = []
    def reg(s, st):
        sp = style_spec(st); key = repr(sorted(sp.items()))
        if key not in s.skeys: s.styles.append(fb_style(sp)); s.skeys[key] = len(s.styles)
        return s.skeys[key]
    def image(s, k, path):
        """image table index; Boox adds one table entry per chapter and image (no dedupe across chapters) [exact, 05_images ref]"""
        if (k, path) not in s.imgmap:
            data = s.ep.read(path)
            if data is None: return None
            s.imgmap[(k, path)] = len(s.images); s.images.append(data)
        return s.imgmap[(k, path)]

    def load_css(s, dom, docpath):
        rules = []
        def walk(n):
            if isinstance(n, str): return
            if n.tag == 'link' and 'stylesheet' in n.attrs.get('rel', '').lower() and n.attrs.get('href'):
                d = s.ep.read(posixpath.normpath(posixpath.join(posixpath.dirname(docpath), u(n.attrs['href']))))
                if d: parse_css(d.decode('utf-8', 'replace'), rules)
            elif n.tag == 'style': parse_css(text_of(n), rules)
            for k in n.kids: walk(k)
        walk(dom); return rules

    def chapter(s, k, path):
        """-> (paragraphs, ids).  paragraph = dict(a kind, bst row style, own, cont container, runs=[('t',text,style,link)|('i',idx,style,link)|('b',)])"""
        raw = s.ep.read(path) or b''
        dom = parse_html(raw.decode('utf-8', 'replace')); rules = s.load_css(dom, path)
        body = find(dom, 'body') or dom; paras = []; ids = {}
        ctx = dict(cur=None, cstack=[], marker=None, lists=[], lipad=[])
        docdir = posixpath.dirname(path)

        def flush():
            p = ctx['cur']; ctx['cur'] = None
            if not p: return
            runs = p['runs']
            while runs and runs[-1][0] == 't' and not runs[-1][1].strip(WS) and not p['pre']: runs.pop()
            if runs and runs[-1][0] == 't' and not p['pre']: runs[-1] = ('t', runs[-1][1].rstrip(' '), runs[-1][2], runs[-1][3])
            if any(r[0] != 'b' for r in runs) or p['a'] == 11: paras.append(p)

        def newpara(st, a, bst=None):
            flush(); ctx['cur'] = dict(a=a, bst=bst or st, own=st, runs=[], pend=False, pre=False, cont=ctx['cstack'][-1] if ctx['cstack'] else None, extra=[])

        def take_marker(p):
            if ctx['marker'] and not p['runs']:
                p['runs'].append(('t', ctx['marker'], p['bst'], None)); ctx['marker'] = None

        def addtext(txt, st, link):
            p = ctx['cur']
            if p is None: newpara(st, 1); p = ctx['cur']
            core = txt.strip(' ')
            if not core:
                if txt and p['runs']: p['pend'] = True
                return
            take_marker(p)
            prefix = ' ' if (p['runs'] and p['runs'][-1][0] != 'b' and (txt[0] == ' ' or p['pend'])) else ''
            p['pend'] = txt[-1] == ' '
            p['runs'].append(('t', prefix + core, zero_box(st) if st is p['own'] else st, link))

        def addpre(txt, st, link):
            p = ctx['cur']
            if p is None: newpara(st, 40); p = ctx['cur']
            p['pre'] = True
            for i, seg in enumerate(txt.split('\n')):
                if i: p['runs'].append(('b', 4))
                if seg: take_marker(p); p['runs'].append(('t', seg, zero_box(st) if st is p['own'] else st, link))

        def new_cont(st):
            """container for a block with padding / border / background; None if it has neither"""
            if not (st['border'] or st['bg'] is not None or any(st[x][0] for x in ('pt', 'pr', 'pb', 'pl'))): return None
            cs = dict(st); cs['cont'] = True
            return dict(st=cs, parent=ctx['cstack'][-1] if ctx['cstack'] else None, idx=None)

        def addimg(ip, st, link):
            idx = s.image(k, posixpath.normpath(posixpath.join(docdir, u(ip))))
            if idx is None: return
            if ctx['cur'] is None: newpara(st, 1)
            take_marker(ctx['cur']); ctx['cur']['runs'].append(('i', idx, st, link)); ctx['cur']['pend'] = False

        def cell_walk(cell, cst, link):
            if ctx['cur']: ctx['cur']['pend'] = False
            walk(cell, cst, link, False, True)

        def table(t, tst, link):
            for r in t.kids:
                if isinstance(r, str): continue
                rs = compute(r, tst, rules)
                if rs['display'] == 'none': continue
                if r.tag in ('thead', 'tbody', 'tfoot'): table(r, rs, link)
                elif r.tag == 'caption': flush(); newpara(rs, 1); walk(r, rs, link, False, True); flush()
                elif r.tag == 'tr':
                    flush(); newpara(rs, 1)
                    for cell in r.kids:
                        if isinstance(cell, str) or cell.tag not in ('td', 'th'): continue
                        cs = compute(cell, rs, rules)
                        if cs['display'] != 'none': cell_walk(cell, cs, link)
                    flush()

        def walk(n, pst, link, pre, incell):
            for c in n.kids:
                if isinstance(c, str):
                    if pre: addpre(c[1:] if c.startswith('\n') and n.kids[0] is c else c, pst, link); continue
                    t = re.sub(r'[ \t\r\n\f]+', ' ', c)
                    if t.strip(WS) or (ctx['cur'] and ctx['cur']['runs']): addtext(t, pst, link)
                    continue
                if c.tag == 'svg':   # calibre-style cover/image pages: <svg><image xlink:href=.../></svg>
                    for im in svg_images(c): addimg(im, pst, link)
                    continue
                if c.tag in SKIP: continue
                st = compute(c, pst, rules)
                if st['display'] == 'none': continue
                if c.tag == 'br':
                    if ctx['cur']: ctx['cur']['runs'].append(('b', 0)); ctx['cur']['pend'] = False
                    continue
                if c.tag == 'hr':
                    flush(); cn = new_cont(st); newpara(st, 11); ctx['cur']['cont'] = None
                    if cn: ctx['cur']['extra'].append(cn)
                    flush(); continue
                if c.tag == 'img':
                    addimg(c.attrs.get('src', ''), st, link); continue
                if c.attrs.get('id') and not (st['display'] == 'block' or c.tag in BLOCK): ids.setdefault(c.attrs['id'], len(paras) if not ctx['cur'] else len(paras))
                href = c.attrs.get('href') if c.tag == 'a' else None
                nlink = (href, int(st['deco'] == 0), 'noteref' in c.attrs.get('epub:type', '')) if href else link
                isblock = st['display'] in ('block', 'list-item', 'table', 'table-cell', 'table-row') or c.tag in BLOCK
                if not isblock:
                    walk(c, st, nlink, pre, incell); continue
                if incell:          # blocks inside a table cell do not break the row
                    walk(c, st, nlink, pre or c.tag == 'pre', incell); continue
                flush()
                if c.attrs.get('id'): ids.setdefault(c.attrs['id'], len(paras))
                if c.tag == 'table': table(c, st, nlink); continue
                inli = ctx['lipad'][-1] if ctx['lipad'] else None
                bst = st
                if inli is not None and c.tag != 'li':        # block inside a list item: the marker indent is added to its padding-left
                    bst = dict(st); bst['pl'] = (st['pl'][0] + inli[0], inli[1]) if st['pl'][0] and st['pl'][1] == inli[1] else (inli if not st['pl'][0] else st['pl'])
                cn = new_cont(st) if c.tag not in ('li', 'table') else None
                if cn: ctx['cstack'].append(cn)
                leaf = not any(not isinstance(x, str) and (x.tag in BLOCK) for x in c.kids) and not blockish(c, st, rules)
                owned = st if leaf else (zero_box(st) if cn else st)
                if c.tag in ('ul', 'ol'): ctx['lists'].append(c)
                if c.tag == 'li':
                    lst = ctx['lists'][-1] if ctx['lists'] else None
                    nested = any(not isinstance(x, str) and x.tag in ('ul', 'ol') for x in c.kids)
                    if nested: owned = st = dict(st, pl=ZERO, pspec=frozenset()); bst = st
                    ctx['lipad'].append((2.5 / (st['size'][0] if st['size'][1] == 'em' else 1) if not nested else 0, 'em'))
                    if lst is not None and not nested and pst['lstyle'] != 'none' and st['lstyle'] != 'none':
                        sib = [x for x in c.parent.kids if not isinstance(x, str) and x.tag == 'li']
                        ctx['marker'] = marker_for(lst, sib.index(c), len(ctx['lists']), st['lstyle'])
                    if st['pl'] and not nested: st = bst = dict(st, pl=(2.5 / st['size'][0], 'em')) if st['size'] != (1.0, 'em') and st['size'][1] == 'em' else st
                if leaf:
                    a = int(c.tag[1]) + 1 if c.tag in HSIZE else 40 if c.tag == 'pre' else 1
                    newpara(st, a, bst)
                    walk(c, st, nlink, pre or c.tag == 'pre', incell); flush()
                else:
                    walk(c, owned, nlink, pre, incell); flush()
                if c.tag == 'li': ctx['lipad'].pop(); ctx['marker'] = None
                if c.tag in ('ul', 'ol'): ctx['lists'].pop()
                if cn: ctx['cstack'].pop()
        def blockish(c, st, rules):
            for x in c.kids:
                if isinstance(x, str): continue
                if x.tag in BLOCK: return True
                xs = compute(x, st, rules)
                if xs['display'] == 'block' or blockish(x, xs, rules): return True
            return False

        root = base_style(); root['disp'] = 3
        for el in (find(dom, 'html'), find(dom, 'body')):
            if el is not None: root = compute(el, root, rules); root['disp'] = 3
        s.body_st = root
        walk(body, root, None, False, False); flush()
        return paras, ids

    def build(s, name_hint='book'):
        ep = s.ep; spine_paths = [ep.man[i][0] for i in ep.spine]
        doc_title, toc = ep.toc(); pidx = {p: i for i, p in enumerate(spine_paths)}
        out = bytearray(0x100); pad8 = lambda: out.extend(bytes((-len(out)) & 7))
        chap_idx = []; nps = []; titles = {}; kept_before = {}   # empty chapters (no paragraph) are dropped by Boox [exact, 07_structure ref]
        for t, p, lv in toc: titles.setdefault(p.split('#')[0], t)
        for k, path in enumerate(spine_paths):
            paras, ids = s.chapter(k, path)
            kept_before[path] = len(chap_idx)
            if not paras: continue
            kn = len(chap_idx)
            dom_title = None
            raw = ep.read(path) or b''
            tt = find(parse_html(raw.decode('utf-8', 'replace')), 'title')
            dom_title = ' '.join(text_of(tt).split()) if tt else ''
            title = titles.get(path) or dom_title or ep.spine[k]
            # --- runs -> block fragments
            blocks, rows, conts = [], [], []
            if not s.styles: s.reg(s.body_st)       # style 1 = the body style of the first chapter
            def use_cont(cn):
                if cn['idx'] is None:
                    par = use_cont(cn['parent']) if cn['parent'] else 0
                    conts.append((s.reg(cn['st']), par)); cn['idx'] = len(conts)
                return cn['idx']
            for p in paras:
                for cn in p['extra']: use_cont(cn)
                ci = use_cont(p['cont']) if p['cont'] else 0
                bid = s.reg(p['bst']); first = len(blocks)
                for r in p['runs']:
                    if r[0] == 'b': blocks.append(fb_br(r[1])); continue
                    if r[0] == 'i':
                        link = r[3]; sid = s.reg(r[2])
                        if link is None: blocks.append(fb_image_ref(r[1], sid))
                        else: blocks.append(mklink(None, sid, link, path, kn, ids, r[1]))
                        continue
                    _, txt, st, link = r; sid = s.reg(st)
                    for ch_ in chunk(txt):
                        blocks.append(fb_text(ch_, sid) if link is None else mklink(ch_, sid, link, path, kn, ids))
                rows.append((p['a'], bid, ci, first, len(blocks) - first))
            pad8(); start = len(out); pairs = []; pos = 0
            for i, b in enumerate(blocks):
                pairs.append((pos, len(b))); out += b
                if i != len(blocks) - 1: pad8(); pos = len(out) - start
            end_raw = (pairs[-1][0] + pairs[-1][1]) if pairs else 0
            for r in rows: out += struct.pack('<5I', *r)
            f4 = (end_raw, 20 * len(rows)); f6 = (end_raw + f4[1], 0); f7s = (f6[0] + 7) & ~7
            out += bytes(start + f7s - len(out))
            for a_, b_ in pairs + conts: out += struct.pack('<II', a_, b_)      # block (start, size) pairs, then container (style, parent) pairs
            f7 = (f7s, 8 * (len(pairs) + len(conts))); f3 = (start, f7[0] + f7[1])
            del out[start + f3[1]:]
            pad8(); ho = len(out)
            hdr = fb_header(ep.spine[k], title, f3, f4, (0, end_raw), f6, f7, len(rows), len(blocks), (len(conts), 0) if conts else None)
            out += hdr; chap_idx.append((ho, len(hdr)))
            nps.append(len(rows))
        page_pairs = []
        for k, (np_, n) in enumerate(zip(nps, page_counts(nps))):
            n = min(n, np_)   # never a page start beyond the paragraphs
            page_pairs += [(k, (i * np_) // n) for i in range(n)]
        if len(page_pairs) > MAX_PAGES:   # more chapters than pages allowed: keep an even selection
            page_pairs = [page_pairs[i * len(page_pairs) // MAX_PAGES] for i in range(MAX_PAGES)]
        assert len(page_pairs) <= MAX_PAGES
        # --- TOC
        pad8(); toc_off = []
        def put(frag):
            pad8(); o = len(out); out.extend(frag); return (o, len(frag))
        toc_off.append(put(fb_toc(doc_title, posixpath.relpath(ep.ncx or ep.nav or 'toc.ncx', ep.base or '.'))))
        for t, p, lv in toc:
            pth = p.split('#')[0]
            toc_off.append(put(fb_toc(t, posixpath.relpath(pth, ep.base or '.'), lv, kept_before.get(pth, 0))))
        pad8(); toc_index = len(out); out += b''.join(struct.pack('<II', *e) for e in toc_off)
        # --- styles
        pad8(); st_off = [put(b) for b in s.styles]
        pad8(); st_index = len(out); out += b''.join(struct.pack('<II', *e) for e in st_off)
        # --- images
        specs = []   # (w, h, pix, kind)
        for data in s.images:
            sz = img_size(data) or (PAGE_W, PAGE_H); w, h = fit(*sz); specs.append((w, h, data, 1))
        meta_cov = []
        cov = ep.read(ep.cover) if ep.cover else None
        if cov:
            sz = img_size(cov) or (2, 3); base = len(specs)
            specs += [(*fit(*sz), cov, 1), (256, 384, cov, 2), (176, 264, cov, 3), (128, 192, cov, 4),
                      (264, 396, cov, 1), (256, 384, cov, 2), (176, 264, cov, 3), (128, 192, cov, 4)]
            meta_cov = [(n, str(base + i)) for i, n in enumerate(('cover-origin', 'cover-middle', 'cover-small', 'cover-tiny',
                        'external-cover-origin', 'external-cover-middle', 'external-cover-small', 'external-cover-tiny'))]
        img_off = []; cache = {}
        for i, (w, h, data, kind) in enumerate(specs):
            nm = ('img_%04d' % (i + 1)).encode(); pad8()
            out += struct.pack('<I', len(nm)) + nm + b'\0'; pad8()
            gk = (id(data), w, h)
            if kind == 1:
                pix = cache.setdefault(gk, load_gray(data, w, h))
            else:
                pix = to_1bit(cache.setdefault(gk, load_gray(data, w, h)), w, h)
            img_off.append(put(fb_image(w, h, pix, kind)))
        img_index = len(out); out += b''.join(struct.pack('<II', *e) for e in img_off)
        # --- metadata
        m = ep.meta; first = lambda k: (m.get(k) or [None])[0]
        kv = [('bookId', 'urn:uuid:' + str(uuid.uuid4())), ('md5sum', hashlib.md5(ep.data).hexdigest())]
        isbn = first('identifier')   # first dc:identifier, whatever it is (Kingsbridge ref: calibre uuid)
        if isbn: kv.append(('isbn', re.sub(r'^urn:isbn:', '', isbn)))
        if m.get('creator'): kv.append(('creator', ','.join(m['creator'])))
        if first('publisher'): kv.append(('publisher', first('publisher')))
        if first('title'): kv.append(('title', first('title')))
        if first('language'): kv.append(('language', first('language')))
        meta = fb_meta(kv + meta_cov); pad8(); meta_off = len(out); out += meta
        pad8(); pag = len(out); out += b''.join(struct.pack('<II', *e) for e in page_pairs)
        pad8(); ci = len(out); out += b''.join(struct.pack('<II', *e) for e in chap_idx)
        # --- root header
        hdr = bytearray(0x68); struct.pack_into('<I', hdr, 0, 0x24)
        vt = [8, 6, 60, 5, 12, 20, 28, 36, 44, 0, 0, 52]
        struct.pack_into('<HH12H', hdr, 8, 28, 68, *vt); struct.pack_into('<i', hdr, 0x24, 0x24 - 8)
        hdr[0x24 + 5] = 1; hdr[0x24 + 7] = 3; hdr[0x24 + 8:0x24 + 12] = b'BFPE'
        for slot, (o, sz) in ((4, (meta_off, len(meta))), (5, (toc_index, 8 * len(toc_off))), (6, (ci, 8 * len(chap_idx))),
                              (7, (st_index, 8 * len(st_off))), (8, (img_index, 8 * len(img_off))), (11, (pag, 8 * len(page_pairs)))):
            struct.pack_into('<II', hdr, 0x24 + vt[slot], o, sz)
        struct.pack_into('<I', hdr, 0x24 + 60, len(out)); out[0:0x68] = hdr
        return bytes(out)

def convert(data):
    return Conv(data).build()

if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    src = sys.argv[1]; dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.opub'
    open(dst, 'wb').write(convert(open(src, 'rb').read())); print(dst)
