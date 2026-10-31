#!/usr/bin/env python3
"""Generate a deterministic corpus of small synthetic EPUB 3 files (with NCX), one feature focus each.

    python3 make_corpus.py [outdir]        # default: tests/corpus/

Stdlib only.  Images are generated in-script (minimal PNG encoder, no JPEG).
06_fonts embeds /System/Library/Fonts/Supplemental/Georgia*.ttf when present (see tests/README.md).
"""
import os, sys, zlib, struct, zipfile, math, html

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = '/System/Library/Fonts/Supplemental'
XH = '<?xml version="1.0" encoding="utf-8"?>\n'

# ----------------------------------------------------------------------------- PNG
def png(w, h, ctype, pix):
    """pix: function (x, y) -> tuple of samples; ctype 0 gray, 2 rgb, 6 rgba."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w): raw += bytes(pix(x, y))
    def chunk(t, b): return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, ctype, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))

def img_color(w, h, seed=0):
    def p(x, y):
        c = ((x // 16 + y // 16) & 1) * 60
        return ((x * 255 // max(w - 1, 1) + seed * 40) % 256, (y * 255 // max(h - 1, 1) + c) % 256, (128 + c + seed * 30) % 256)
    return png(w, h, 2, p)
def img_gray(w, h):
    return png(w, h, 0, lambda x, y: ((x * 255 // max(w - 1, 1) + (y // 8 % 2) * 64) % 256,))
def img_alpha(w, h):
    def p(x, y):
        d = math.hypot(x - w / 2, y - h / 2) / (min(w, h) / 2)
        return (220, 40, 40, 255 if d < 0.8 else (128 if d < 1.0 else 0))
    return png(w, h, 6, p)
def img_cover(title_seed=0):
    def p(x, y):
        band = (y // 30) % 2
        return (30 + title_seed * 20, 60 + band * 100, 140 - band * 60)
    return png(200, 300, 2, p)

# ----------------------------------------------------------------------------- EPUB writer
class Book:
    def __init__(s, name, title, author='Test Author', css='', lang='en'):
        s.name, s.title, s.author, s.lang = name, title, author, lang
        s.css = css or 'body { font-family: serif; } p { margin: 0 0 0.5em 0; }\n'
        s.chapters = []   # dict(id, file, title, body, linear, toc(level|None), head)
        s.files = {}      # path (in OEBPS) -> (bytes, mediatype)
        s.cover = img_cover(len(name) % 5)
        s.extra_css = {}
        s.n = 0
    def chapter(s, title, body, toc=1, linear=True, head='', cid=None, epubtype=None):
        s.n += 1; cid = cid or 'ch%02d' % s.n
        s.chapters.append(dict(id=cid, file='text/%s.xhtml' % cid, title=title, body=body, toc=toc, linear=linear, head=head, etype=epubtype))
        return cid
    def add(s, path, data, mt): s.files[path] = (data, mt)
    def xhtml(s, c):
        return (XH + '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="%s">\n<head>\n'
                '<meta charset="utf-8"/>\n<title>%s</title>\n<link rel="stylesheet" type="text/css" href="../style.css"/>\n%s</head>\n<body>\n%s\n</body>\n</html>\n'
                % (s.lang, html.escape(c['title']), c['head'], c['body']))
    def write(s, outdir):
        uid = 'urn:uuid:00000000-0000-4000-8000-%012d' % (abs(hash_s(s.name)) % 10**12)
        man, spine, navpts = [], [], []
        man.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        man.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        man.append('<item id="css" href="style.css" media-type="text/css"/>')
        man.append('<item id="cover-image" href="images/cover.png" media-type="image/png" properties="cover-image"/>')
        for i, (p, (_, mt)) in enumerate(sorted(s.files.items())):
            man.append('<item id="f%03d" href="%s" media-type="%s"/>' % (i, p, mt))
        for c in s.chapters:
            man.append('<item id="%s" href="%s" media-type="application/xhtml+xml"/>' % (c['id'], c['file']))
            spine.append('<itemref idref="%s"%s/>' % (c['id'], '' if c['linear'] else ' linear="no"'))
        opf = (XH + '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="%s">\n<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
               '<dc:identifier id="uid">%s</dc:identifier>\n<dc:title>%s</dc:title>\n<dc:creator>%s</dc:creator>\n<dc:language>%s</dc:language>\n'
               '<dc:publisher>Corpus Press</dc:publisher>\n<meta property="dcterms:modified">2024-01-01T00:00:00Z</meta>\n<meta name="cover" content="cover-image"/>\n</metadata>\n'
               '<manifest>\n%s\n</manifest>\n<spine toc="ncx">\n%s\n</spine>\n</package>\n'
               % (s.lang, uid, html.escape(s.title), html.escape(s.author), s.lang, '\n'.join(man), '\n'.join(spine)))
        # NCX with nesting from toc levels
        play, stack, out = 0, [], []
        def close_to(lv):
            while len(stack) >= lv: stack.pop(); out.append('</navPoint>')
        for c in s.chapters:
            if not c['toc']: continue
            close_to(c['toc']); play += 1; stack.append(1)
            out.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s"/>' % (play, play, html.escape(c['title']), c['file']))
        close_to(1)
        ncx = (XH + '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="3"/></head>\n'
               '<docTitle><text>%s</text></docTitle>\n<navMap>\n%s\n</navMap>\n</ncx>\n' % (uid, html.escape(s.title), '\n'.join(out)))
        # nav.xhtml (flat/nested via same levels)
        nv, lvl = [], 0
        for c in s.chapters:
            if not c['toc']: continue
            while lvl < c['toc']: nv.append('<ol>'); lvl += 1
            while lvl > c['toc']: nv.append('</ol>'); lvl -= 1
            nv.append('<li><a href="%s">%s</a></li>' % (c['file'], html.escape(c['title'])))
        while lvl: nv.append('</ol>'); lvl -= 1
        nav = (XH + '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><meta charset="utf-8"/><title>Contents</title></head>\n'
               '<body><nav epub:type="toc" id="toc"><h1>Contents</h1>\n%s\n</nav></body></html>\n' % '\n'.join(nv))
        path = os.path.join(outdir, s.name + '.epub')
        def zi(n):
            i = zipfile.ZipInfo(n, (2024, 1, 1, 0, 0, 0)); i.external_attr = 0o644 << 16; return i
        with zipfile.ZipFile(path, 'w') as z:
            i = zi('mimetype'); i.compress_type = zipfile.ZIP_STORED; z.writestr(i, 'application/epub+zip')
            def w(n, data):
                i = zi(n); i.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(i, data if isinstance(data, bytes) else data.encode('utf-8'))
            w('META-INF/container.xml', XH + '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
              '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>\n')
            w('OEBPS/content.opf', opf); w('OEBPS/toc.ncx', ncx); w('OEBPS/nav.xhtml', nav); w('OEBPS/style.css', s.css)
            w('OEBPS/images/cover.png', s.cover)
            for p, (data, _) in sorted(s.files.items()): w('OEBPS/' + p, data)
            for c in s.chapters: w('OEBPS/' + c['file'], s.xhtml(c))
        return path

def hash_s(t):
    h = 0
    for ch in t: h = (h * 131 + ord(ch)) % 10**12
    return h

# ----------------------------------------------------------------------------- text helpers
WORDS = ('the quick brown fox jumps over a lazy dog while seven small birds sing quietly near an old stone bridge '
         'and the river carries autumn leaves toward distant hills where morning light falls on quiet fields').split()
def sentence(i, n=12):
    return ' '.join(WORDS[(i * 7 + k * 3) % len(WORDS)] for k in range(n)).capitalize() + '.'
def para(i, n=12): return '<p>%s</p>' % sentence(i, n)
def paras(a, b, n=12): return '\n'.join(para(i, n) for i in range(a, b))
def h(t, lv=1): return '<h%d>%s</h%d>' % (lv, html.escape(t), lv)

# ----------------------------------------------------------------------------- books
def b01():
    b = Book('01_basic', 'Basic Text', css='body { font-family: serif; } p { margin: 0 0 0.6em 0; } h1 { font-size: 1.6em; } h2 { font-size: 1.3em; }\n')
    b.chapter('Chapter One', h('Chapter One') + '\n' + paras(0, 4) + '\n<p>This has <b>bold</b>, <i>italic</i>, <u>underline</u>, <strong>strong</strong>, <em>emphasis</em> and <span style="font-weight:bold;font-style:italic">both</span> words.</p>\n'
              + h('A Subsection', 2) + paras(4, 6))
    b.chapter('Chapter Two', h('Chapter Two') + '\n' + paras(6, 9) + '\n<p>See <a href="ch03.xhtml">chapter three</a> or a <a href="ch01.xhtml#sec">section in chapter one</a> or <a href="https://example.com/page?q=1">an external link</a>, then an <a href="#local">anchor in this chapter</a>.</p>\n'
              + paras(9, 11) + '\n<p id="local">Target paragraph of the local anchor.</p>\n' + paras(11, 13))
    b.chapters[0]['body'] = b.chapters[0]['body'].replace(h('A Subsection', 2), '<h2 id="sec">A Subsection</h2>')
    b.chapter('Chapter Three', h('Chapter Three') + '\n' + paras(13, 18) + '\n' + h('Last Heading', 3) + '\n' + h('Heading four', 4) + '\n' + h('Heading five', 5) + '\n' + h('Heading six', 6) + '\n' + para(30))
    for c in b.chapters: c['file'] = c['file']
    return b

def b02():
    b = Book('02_lists', 'Lists')
    b.chapter('Unordered', h('Unordered') + '\n<ul><li>First item</li><li>Second item with <b>bold</b></li><li>Third item<ul><li>Nested one</li><li>Nested two<ul><li>Deep nested</li></ul></li></ul></li><li>Fourth item</li></ul>\n' + para(1))
    b.chapter('Ordered', h('Ordered') + '\n<ol><li>One</li><li>Two<ol><li>Two A</li><li>Two B</li></ol></li><li>Three</li></ol>\n'
              '<ol start="5"><li>Starts at five</li><li>Six</li></ol>\n<ol style="list-style-type: lower-roman"><li>Roman one</li><li>Roman two</li></ol>\n'
              '<ul style="list-style-type: none"><li>No marker</li></ul>\n<ul><li><p>Item with paragraph</p><p>Second paragraph in item</p></li><li>Plain</li></ul>')
    b.chapter('Definitions', h('Definitions') + '\n<dl><dt>Term one</dt><dd>Definition of the first term.</dd><dt>Term two</dt><dd>First definition.</dd><dd>Second definition.</dd></dl>\n' + para(2))
    return b

def b03():
    css = ('body { font-family: serif; } p { margin: 0 0 0.5em 0; }\n.box { border: 1px solid #000; padding: 0.5em; background-color: #eeeeee; margin: 1em 0; }\n'
           '.thick { border: 3px solid black; padding: 1em; }\n.left { border-left: 4px solid gray; padding-left: 1em; }\n'
           'blockquote { margin: 1em 2em; font-style: italic; }\nhr { border: 0; border-top: 1px solid #000; margin: 1em 0; }\naside { border: 1px dashed #666; padding: 0.4em; }\n')
    b = Book('03_boxes', 'Boxes and Containers', css=css)
    b.chapter('Boxes', h('Boxes') + '\n' + para(0) + '\n<div class="box"><p>Text inside a bordered, padded, shaded box.</p><p>A second paragraph in the same box.</p></div>\n' + para(1)
              + '\n<div class="thick"><p>Thick border box with one paragraph.</p></div>\n<div class="left"><p>Left-border only.</p></div>\n<div><div class="box"><p>Nested box outer content.</p><div class="thick"><p>Inner box.</p></div></div></div>\n' + para(2))
    b.chapter('Quotes and rules', h('Quotes and rules') + '\n' + para(3) + '\n<blockquote><p>A block quotation spanning a line or two of text for the reader.</p><p>Second quoted paragraph.</p></blockquote>\n<hr/>\n' + para(4)
              + '\n<hr style="border-top: 3px solid black"/>\n<hr class="plain"/>\n' + para(5))
    b.chapter('Asides', h('Asides') + '\n' + para(6) + '\n<aside><p>Sidebar content inside an aside element.</p></aside>\n<aside epub:type="sidebar"><h3>Sidebar title</h3><p>Sidebar body.</p></aside>\n'
              '<section><p>Plain section content.</p></section>\n<figure><p>Figure content</p><figcaption>Figure caption</figcaption></figure>\n' + para(7))
    return b

def b04():
    css = 'body { font-family: serif; } table { border-collapse: collapse; margin: 1em 0; } td, th { border: 1px solid #000; padding: 0.3em; } th { background-color: #dddddd; } caption { font-weight: bold; }\n'
    b = Book('04_tables', 'Tables', css=css)
    b.chapter('Simple', h('Simple table') + '\n' + para(0) + '\n<table><tr><td>A1</td><td>B1</td><td>C1</td></tr><tr><td>A2</td><td>B2</td><td>C2</td></tr><tr><td>A3</td><td>B3</td><td>C3</td></tr></table>\n' + para(1))
    b.chapter('Headers', h('Header table') + '\n<table><caption>Table caption text</caption><thead><tr><th>Name</th><th>Qty</th><th>Price</th></tr></thead><tbody>'
              '<tr><td>Apple</td><td>3</td><td>1.20</td></tr><tr><td>Pear</td><td>10</td><td>0.80</td></tr></tbody><tfoot><tr><td>Total</td><td>13</td><td>2.00</td></tr></tfoot></table>\n' + para(2))
    b.chapter('Spans', h('Spanning table') + '\n<table><tr><th colspan="3">Wide header</th></tr><tr><td rowspan="2">Tall</td><td>x</td><td>y</td></tr><tr><td colspan="2">Two wide</td></tr></table>\n'
              '<table style="width:100%"><tr><td>Left cell with some longer text content</td><td>Right cell with other long content that wraps</td></tr></table>\n'
              '<table><tr><td><p>Para in cell</p><p>Second para in cell</p></td><td><b>Bold</b> and <i>italic</i></td></tr></table>\n' + para(3))
    return b

def b05():
    b = Book('05_images', 'Images')
    b.add('images/inline.png', img_color(120, 80, 1), 'image/png')
    b.add('images/fullpage.png', img_color(480, 725, 2), 'image/png')
    b.add('images/wide.png', img_color(900, 300, 3), 'image/png')
    b.add('images/alpha.png', img_alpha(100, 100), 'image/png')
    b.add('images/gray.png', img_gray(200, 120), 'image/png')
    b.add('images/icon.png', img_color(16, 16, 4), 'image/png')
    b.add('images/tall.png', img_color(300, 1200, 5), 'image/png')
    I = lambda n, extra='': '<img src="../images/%s.png" alt="%s"%s/>' % (n, n, extra)
    b.chapter('Inline', h('Inline image') + '\n' + para(0) + '\n<p>' + I('inline') + '</p>\n' + para(1) + '\n<p>Text with an icon ' + I('icon') + ' in the middle of a sentence.</p>\n<p style="text-align:center">' + I('icon') + '</p>\n'
              '<div>' + I('gray') + '</div>\n<p>' + I('alpha') + '</p>\n' + para(2))
    b.chapter('Full page', '<div style="text-align:center">' + I('fullpage') + '</div>', cid='fullpage')
    b.chapter('Wide', h('Wide image') + '\n<p>' + I('wide') + '</p>\n' + para(3) + '\n<p>' + I('tall') + '</p>\n<p>' + I('inline', ' width="60" height="40"') + '</p>\n<p>' + I('inline', ' style="width:50%"') + '</p>\n'
              '<figure>' + I('inline') + '<figcaption>A figure caption.</figcaption></figure>\n<p><a href="ch01.xhtml">' + I('icon') + '</a></p>')
    return b

def b06():
    avail = os.path.exists(os.path.join(FONT_DIR, 'Georgia.ttf'))
    css = ''
    if avail:
        css += ('@font-face { font-family: "Georgia Embedded"; src: url("fonts/Georgia.ttf"); font-weight: normal; font-style: normal; }\n'
                '@font-face { font-family: "Georgia Embedded"; src: url("fonts/Georgia-Bold.ttf"); font-weight: bold; font-style: normal; }\n'
                '@font-face { font-family: "Georgia Embedded"; src: url("fonts/Georgia-Italic.ttf"); font-weight: normal; font-style: italic; }\n')
    css += ('body { font-family: serif; } p { margin: 0 0 0.5em 0; }\n.emb { font-family: "Georgia Embedded", serif; }\n.sans { font-family: Helvetica, Arial, sans-serif; }\n.mono { font-family: "Courier New", monospace; }\n'
            '.l { text-align: left; } .r { text-align: right; } .c { text-align: center; } .j { text-align: justify; }\n.ind { text-indent: 1.5em; } .ind2 { text-indent: 24px; } .neg { text-indent: -1em; margin-left: 1em; }\n'
            '.em { font-size: 1.5em; } .px { font-size: 20px; } .pct { font-size: 80%; } .pt { font-size: 14pt; } .big { font-size: x-large; }\n'
            '.ls { letter-spacing: 0.2em; } .sc { font-variant: small-caps; } .red { color: #cc0000; } .blue { color: blue; } .bg { background-color: #ffff99; }\n'
            '.lh { line-height: 2; } .lh15 { line-height: 150%; } .up { text-transform: uppercase; } .del { text-decoration: line-through; }\n')
    b = Book('06_fonts', 'Fonts and Text Styles', css=css)
    if avail:
        for fn, nm in (('Georgia.ttf', 'Georgia.ttf'), ('Georgia Bold.ttf', 'Georgia-Bold.ttf'), ('Georgia Italic.ttf', 'Georgia-Italic.ttf')):
            b.add('fonts/' + nm, open(os.path.join(FONT_DIR, fn), 'rb').read(), 'font/ttf')
    s = sentence
    b.chapter('Font families', h('Font families') + '\n<p class="emb">Embedded font paragraph. %s <b>Bold embedded.</b> <i>Italic embedded.</i></p>\n<p class="sans">Sans family: %s</p>\n<p class="mono">Mono family: %s</p>\n'
              '<p>Default serif: %s</p>\n<p style="font-family: Georgia, serif">Inline style Georgia: %s</p>\n<p>Span <span class="sans">in sans</span> and <span class="mono">in mono</span> inside a serif paragraph.</p>' % (s(0), s(1), s(2), s(3), s(4)))
    b.chapter('Alignment and indent', h('Alignment') + '\n' + '\n'.join('<p class="%s">%s class %s. %s %s</p>' % (c, c, c, s(i), s(i + 1)) for i, c in enumerate(['l', 'r', 'c', 'j']))
              + '\n<p class="ind">Indented 1.5em. %s %s</p>\n<p class="ind2">Indented 24px. %s</p>\n<p class="neg">Hanging indent. %s %s</p>\n<p class="j ind">Justified and indented. %s %s</p>' % (s(0), s(1), s(2), s(3), s(4), s(5), s(6)))
    b.chapter('Sizes and spacing', h('Sizes') + '\n' + '\n'.join('<p class="%s">Class %s. %s</p>' % (c, c, s(i)) for i, c in enumerate(['em', 'px', 'pct', 'pt', 'big', 'ls', 'sc', 'up', 'lh', 'lh15']))
              + '\n<p>Inline <span class="em">larger em</span> <span class="pct">smaller pct</span> <small>small</small> <big>big</big> text.</p>')
    b.chapter('Colors and decoration', h('Colors') + '\n<p class="red">Red text. %s</p>\n<p class="blue">Blue text. %s</p>\n<p>Mixed <span class="red">red</span> and <span class="blue">blue</span> and <span class="bg">highlighted</span> and <span class="del">struck</span>.</p>\n'
              '<p style="color:#008000; font-size:1.2em; text-align:center">Inline-styled green centered.</p>\n<p><font color="red">Font tag red</font> <tt>teletype</tt> <code>code</code> <kbd>kbd</kbd></p>' % (s(0), s(1)))
    return b

def b07():
    b = Book('07_structure', 'Structure Edge Cases')
    b.add('images/title.png', img_color(300, 450, 6), 'image/png')
    b.chapter('Title page (image only)', '<div style="text-align:center"><img src="../images/title.png" alt="title"/></div>', cid='titlepage')
    b.chapter('Empty chapter', '', cid='empty')
    b.chapter('Whitespace only chapter', '<div> </div>', cid='blank')
    for n in range(1, 11):
        b.chapter('Short %d' % n, h('Short chapter %d' % n) + '\n' + paras(0, n - 1 if n > 1 else 0), cid='short%02d' % n, toc=2 if n % 2 == 0 else 1)
    # deep TOC chain
    b.chapter('Part One', h('Part One') + '\n' + para(1), toc=1, cid='part1')
    b.chapter('Part One / Chapter A', h('Chapter A') + '\n' + paras(2, 6), toc=2, cid='p1a')
    b.chapter('Part One / Chapter A / Section i', h('Section i', 2) + '\n' + paras(6, 9), toc=3, cid='p1a1')
    b.chapter('Part One / Chapter B', h('Chapter B') + '\n' + paras(9, 12), toc=2, cid='p1b')
    b.chapter('Part Two', h('Part Two') + '\n' + para(12), toc=1, cid='part2')
    b.chapter('Hidden from TOC', h('Not in TOC') + '\n' + paras(0, 4), toc=None, cid='notoc')
    b.chapter('Non-linear item', h('Non-linear') + '\n' + paras(0, 3), toc=None, linear=False, cid='nonlinear')
    b.chapter('Long chapter', h('Long chapter') + '\n' + '\n'.join(para(i, 8 + i % 20) for i in range(600)), cid='long')
    b.chapter('Final', h('Final') + '\n' + para(0), cid='final')
    return b

def b08():
    css = 'body { font-family: serif; } p { margin: 0 0 0.5em 0; } sup { font-size: 0.75em; } a.noteref { text-decoration: none; }\naside.fn { font-size: 0.85em; }\n'
    b = Book('08_footnotes', 'Footnotes and Anchors', css=css)
    b.chapter('Notes', h('Notes') + '\n<p>A sentence with a note<a epub:type="noteref" class="noteref" href="#fn1"><sup>1</sup></a> and another one<a epub:type="noteref" href="#fn2" id="r2"><sup>2</sup></a> in the same paragraph.</p>\n'
              + para(1) + '\n<p id="mid">Paragraph with id <span id="inner">and a span id</span> inside; links to <a href="#mid">itself</a> and to <a href="ch02.xhtml#far">a later chapter anchor</a>.</p>\n'
              '<aside epub:type="footnote" id="fn1" class="fn"><p><a href="#r1">1</a> First footnote text.</p></aside>\n<aside epub:type="footnote" id="fn2" class="fn"><p>Second footnote text, a bit longer than the first.</p></aside>\n'
              '<p>Plain footnote style: text<a href="#n3" id="r3"><sup>[3]</sup></a>.</p>\n<div id="n3"><p>3. Note in a div.</p></div>\n' + para(2))
    b.chapter('Scripts', h('Sub and superscripts') + '\n<p>Water is H<sub>2</sub>O and E = mc<sup>2</sup>; footnote-like text<sup>a</sup> and x<sub>i</sub><sup>2</sup>.</p>\n<p>Ordinal 1<sup>st</sup>, 2<sup>nd</sup>.</p>\n'
              + paras(3, 6) + '\n<p id="far">The far anchor target.</p>\n<p><a href="ch01.xhtml#fn1">Back to the first note</a></p>', cid='ch02')
    b.chapters[0]['id'] = 'ch01'; b.chapters[0]['file'] = 'text/ch01.xhtml'
    return b

def b09():
    b = Book('09_long', 'Very Long Book')
    for c in range(40):
        b.chapter('Chapter %d' % (c + 1), h('Chapter %d' % (c + 1)) + '\n' + '\n'.join(para(c + i, 10 + (i * 3) % 14) for i in range(150)), toc=1)
    return b

def b10():
    css = 'body { font-family: serif; } p { margin: 0 0 0.5em 0; } pre { font-family: monospace; font-size: 0.85em; }\n.rtl { direction: rtl; text-align: right; }\n'
    b = Book('10_whitespace', 'Whitespace and Scripts', css=css)
    b.chapter('Whitespace', h('Whitespace') + '\n<pre>line one\n  indented two\n\tTabbed three\nline   four  with   spaces</pre>\n<p>Line one<br/>line two<br/>line three<br/><br/>after double br</p>\n<p>Non&#160;breaking&#160;spaces and a&#160;&#160;&#160;run.</p>\n'
              '<p>Multiple     spaces   collapse\n   across    newlines.</p>\n<p>  Leading and trailing spaces  </p>\n<p><b>bold</b> <i>italic</i> <u>under</u><b>glued</b> <span> spaced </span><em>x</em>\n<em>y</em></p>\n'
              '<p>Soft­hyphen­ated extra­ordinarily long­words here.</p>\n<p>Zero​width and word⁠joiner and em space and thin space.</p>\n<p> </p>\n<p></p>\n<p><br/></p>\n' + para(1))
    b.chapter('Scripts', h('Scripts') + '\n<p class="rtl" dir="rtl">שלום עולם, זוהי בדיקה של עברית.</p>\n<p class="rtl" dir="rtl">مرحبا بالعالم، هذا اختبار للغة العربية.</p>\n'
              '<p>Mixed English and עברית in one line.</p>\n<p>中文测试：这是一段用于测试的中文文本，没有空格。第二句话在这里。</p>\n<p>日本語のテストです。これはカタカナとひらがなと漢字。</p>\n<p>한국어 테스트 문장입니다.</p>\n'
              '<p>Emoji: \U0001F600 \U0001F4DA ❤️ \U0001F1E9\U0001F1EA \U0001F468‍\U0001F469‍\U0001F467 end.</p>\n<p>Accents: café, naïve, über, ßaẞ, Łódź, ภาษาไทย, Русский, ελληνικά.</p>\n'
              '<p>Quotes “double” ‘single’ — dashes – ellipsis… &amp; &lt;tag&gt; ampersands &amp;amp;.</p>\n<p>A very long unbroken token: ' + 'x' * 260 + ' and then a sentence that follows it. ' + sentence(3, 40) + sentence(4, 40) + sentence(5, 40) + '</p>')
    return b

BOOKS = [b01, b02, b03, b04, b05, b06, b07, b08, b09, b10]

def validate(path):
    with zipfile.ZipFile(path) as z:
        i = z.infolist()[0]
        assert i.filename == 'mimetype' and i.compress_type == zipfile.ZIP_STORED, path
        assert z.read('mimetype') == b'application/epub+zip'
        assert z.testzip() is None
        import xml.dom.minidom as md
        for n in z.namelist():
            if n.endswith(('.xhtml', '.opf', '.ncx', '.xml')): md.parseString(z.read(n))

def main(outdir=None):
    outdir = outdir or os.path.join(HERE, 'corpus'); os.makedirs(outdir, exist_ok=True)
    for f in BOOKS:
        p = f().write(outdir); validate(p); print('%-28s %8d bytes' % (os.path.basename(p), os.path.getsize(p)))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
