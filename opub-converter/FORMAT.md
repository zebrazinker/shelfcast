# OPUB (Onyx Boox NeoReader) - reverse-engineered format notes

Derived from one reference pair (Haberman/Swan "Regime Change" .epub -> .opub) plus a small synthetic EPUB.
Everything below marked **[exact]** is reproduced byte-for-byte by `opub.py` when fed the reference data
(`python3 verify.py --roundtrip ref.opub`); **[guess]** means inferred from patterns only.

## Container
No compression and no encryption anywhere (the "zlib streams" / JPEG seen earlier are false positives inside
float/int data). The file is a **concatenation of many small standard FlatBuffers**, each with its own root
offset, each 8-byte aligned in the file, plus raw `(u32 offset, u32 size)` index arrays. All ints little-endian.
Strings are UTF-8, FlatBuffers strings. All tables are written with the stock back-to-front builder rules
(vtable dedupe, size-by-alignment then reverse-slot field order), so a normal builder reproduces them exactly.

```
0x000  header buffer (0x68 bytes, zero padded to 0x100)
0x100  chapter 0 data, chapter 0 header, chapter 1 data, chapter 1 header, ...   (spine order, ALL spine items)
       TOC records            (1 + #navPoints, each a tiny buffer)
       TOC index              (u32 off,size) per record
       style records          (one buffer each)
       style index
       per image: name string "img_0001" (u32 len, bytes, NUL, pad to 16)  +  image buffer
       image index
       metadata buffer        (key/value list)
       pagination table       ((u32 chapter, u32 paragraph) pairs, 8-aligned)
       chapter index          (u32 off,size) of each chapter HEADER buffer
EOF    (offset of EOF is stored in the header = file size)
```

### Header (root table at 0x24, vtable at 0x08) [exact layout]
Slots: `f0`=u32 magic `"BFPE"`, `f1`=u16 0x0300, `f3`=u8 1 (version-ish, copied), then `(offset,size)` pairs of
absolute file positions: `f4` metadata buffer, `f5` TOC index, `f6` chapter index, `f7` style index, `f8` image index,
`f11` pagination table, and `f2` = u32 end-of-file offset.

### Chapter (one per spine item, in this order): `[blocks][para table][pad8][block table][pad8][header buffer]`
* A chapter's *header buffer comes after its content*. The index entry points at the header.
* **Block** = one buffer, root `{u8 type, union value}`, 8-aligned in the stream, last one not padded:
  * type 1 text run: `{str text, u32 style}`
  * type 2 image: `{u32 image index, u32 style}` (style = id of the `<img>` element's style record)
  * type 4 link run: `{u8 1, Text, u8 ltype (1 internal, 3 external), u16 src_chapter (internal links), u16 anchor_par, u16 anchor_par, u16 0, u16 0, str href}`.
    href is the raw `href` attribute. For links to an id in the *same* chapter the two anchors hold the paragraph index of that id; cross-chapter links keep 0 [exact for all 4632 links].
  * Runs are text nodes: one run per DOM text node (and per inline link), then split into pieces of <= 200 UTF-8 bytes
    with a tokenizer (`\w+ | whitespace | single punctuation`, greedy). Whitespace at the end of a text node moves to the start
    of the next run (not for the paragraph end). 99.9% of runs reproduce [exact except for the index chapters].
* **Paragraph table** (5 x u32 per paragraph, placed right after the last block's *unpadded* end):
  `(kind, style_id, container, first_run, n_runs)`; kind 1 = text, 2..7 = heading h1..h6 (level+1), 11 = `<hr>` (n_runs 0).
  `style_id` = block style (margins). `container` is an index into the (optional) container list, see below.
* **Block table** (8-aligned): `(start_i, raw_size_i)` for each block; start = aligned running offset inside the chapter,
  raw size = unpadded buffer size. Optional trailing `(style, parent)` pairs = containers (see below).
* **Chapter header** buffer `{str id (manifest id), str title, f3=(region_start, region_size), f4=(para table off,len), f5=(0, blocks_end),
  f6=(end of para table, 0), f7=(block table off, len), u32 n_paragraphs, u32 n_runs, [f11 = u16 n_containers, u16 len(title)]}`;
  offsets in f4..f7 are relative to `region_start`, which is the chapter's absolute start (0x100 for the first one).
  `region_size = f7.off + f7.len`. [exact: 52/52 headers rebuilt identically]

### Styles (rewritten after the 10-book corpus in tests/ref, produced by Boox's own engine)
Ids are **1-based** (`n`-th record), allocated in first-use order across the book; equal records share an id. Style 1 is the first chapter's `body` style.
Allocation order while walking a chapter: container records (outermost first) -> the row's block style -> run styles in order. Rows that end up
empty register nothing. A record is a stock FlatBuffers table, ~48 slots, fields written by alignment (4-byte first) then descending slot;
child tables are created in the order deco(11), border(43: sides top,right,bottom,left), background(45), family string(0). `opub.fb_style` reproduces
~96 % of the reference records byte-for-byte [exact]; the rest differ only in table padding of border sides. Slots:
`0` font-family (string = the raw CSS value, e.g. `"Playfair", serif`; only when some CSS set it, inherited) [exact];
`2/3` colour ARGB; `4/5` font-size as (f32, unit) **keeping the CSS unit** (em 3, px 2, % 5, pt 6; keyword sizes ignored; block size is relative to the parent) [exact];
`6/7` bold: 1 normal, **3 = from a tag (b, strong, th, h1-h6)**, **2 = from CSS font-weight** [exact]; `8/9` italic 1/2;
`10/11` decoration table `{0:1, 1: 1 underline | 4 line-through}` (links and `u` underline, `a {text-decoration:none}` removes it, inherited into children) [exact];
`14/15` letter-spacing (f32, unit); `16/17` line-height (default 1.4664 em; unitless `2` = unit 1, `150%` = unit 5) [exact]; `18/19` align 1 left 2 right 3 center 4 justify;
`20/21` text-indent; `22-29` margins T R B L; `30-37` paddings T R B L (a bare `0` has unit px (2), an unset value 0 em (3); `auto` = unit 30; pt kept as unit 6) [exact];
`38/39` width, `40/41` height (images: `width=`/`height=` attributes or CSS; the pixel data stays at natural size) [exact];
`42/43` border `{0,2,4,6 flags; 1,3,5,7 sides top,right,bottom,left}`, side = `{0:1, 1: width(px), 2:1, 3: style (absent solid, 3 dashed; 2 dotted/4 double are [guess]), 4:1, 5: colour}`,
a side with only `border:0` carries just flag 0 and the 0 px width; `44/45` background `{0:1, 1: colour}`;
`46/47` display: absent = inline element, `47` = 3 block, 5 list-item, 12 table-row, 13 table-cell, 16 caption [exact].
UA defaults used by the engine (visible in the records): p margin 1em top/bottom; h1-h6 size 2/1.5/1.17/1/.83/.75 em and margins .67/.83/1/1.33/1.67/2.33 em;
sub/sup/small .83em, big 1.17em; ul/ol/dd padding-left 40px; li padding-left 2.5em; th bold+centered; caption centered; a blue+underline. Margins of non-leaf
blocks (`blockquote`, `div`, `table`, `figure` ...) are **dropped**, they appear nowhere.
**Run styles**: a run uses the computed style of its parent element; if the parent is the element that owns the row, margins/paddings/border/background are zeroed
(the row's own block style keeps them). Table cells are not row owners (the `tr` is), so cell runs keep the cell's padding/border [exact].
**Container records** (referenced from the block table, see below) hold only what was specified, as flag+value pairs: a `ul` = `{36:1, 37: 40px}`, a bordered
padded `div` = padding sides + border + background; margins are not stored. An element becomes a container iff it has non-zero padding, a border or a background
(`ul`/`ol`/`dd` through the UA padding, `hr` through its border, `td`/`li`/table parts never) [exact on 03_boxes, 02_lists].

### TOC / metadata / images / pagination
* TOC record 0 `{docTitle, "toc.ncx"}`, then one record per navPoint `{title, href, u16 level (depth, 1 based), u16 spine_index (omitted when 0)}`;
  `href` as written in the NCX (relative to the OPF) [exact: 50/50].
* Metadata = vector of `{key, value}`: bookId (`urn:uuid:<random>`), md5sum (md5 of the .epub? **[guess]**, reference value did not match its epub),
  isbn, creator (authors joined with ","), publisher, title (main title only), language, cover-origin/-middle/-small/-tiny,
  external-cover-origin/-middle/-small/-tiny (= decimal indices into the image table) [exact].
* Image buffer `{u8 2, ref -> {ubyte[] pixels}, u8 1, u8 1, u32 width, u32 height, u8 kind, u8 (4 if kind==1 else 1), u8 1}`.
  kind 1 = 8-bit gray, row-major, no padding, white=255 (in-book images are shrunk to fit 480x725, never enlarged);
  kind 2/3/4 = 1-bit (MSB first, 1=white, rows padded to bytes) at 256x384 / 176x264 / 128x192 (cover thumbnails). Image table order:
  in-book images by first use, then the 4 cover sizes, then 4 "external" covers (264x396 gray + three 1-bit; in the reference these are a
  generic book-stack icon, we use the real cover).
* Pagination table: pairs `(chapter, paragraph)` of page starts, chapter k with `np` paragraphs gets `n` pairs `(k, floor(i*np/n))` [exact positions].
  **Rule for `n` [fitted on the corpus + Haberman + Kingsbridge]:** while the book has <= ~1000 paragraphs, every paragraph starts a page (`n = np`; 07_structure: 685 paragraphs
  -> 685 pairs). Above that Boox divides by `c = 1.0194 * N / 1000` (N = total paragraphs; Haberman 4.89, Kingsbridge ~10.2, 09_long 6.16): `n = ceil(np / c)`, chapters of
  1-2 paragraphs get 1 page and of 3-6 at least 2. It then scales the largest chapters down so that the total is 999 (Haberman) or 1000 (09_long: 40 chapters x 25 pairs).
  50/52 Haberman and 45/59 Kingsbridge chapters match exactly; the rest differ by one page (the exact rounding is not known).
  **Device finding: >= 1000 pairs makes the reader's progress stay at 0 %**, so `opub.py` caps at `MAX_PAGES = 999` (drops the last pages of the last chapters, else raises the divisor).
  Empty chapters are removed from the file (below), so a pair never points past `np`.
* **Empty chapters are dropped** (no paragraph after whitespace removal, e.g. empty body, `<div> </div>`): they are not in the chapter list; TOC entries pointing at them stay and
  get `spine_index` = number of chapters kept before them (so they point at the next kept chapter) [exact, 07_structure]. `linear="no"` is ignored (chapter kept).
* Images: **one table entry per chapter and image** (the same file used in two chapters is stored twice, within a chapter once); table order = first use, then 4 covers + 4 external covers.
  In-book images are shrunk to fit **480x800**, never enlarged [exact]. Gray = Rec.601 luma of the RGB, alpha composited on white [exact for flat colours; gradient images
  come out ~1-10 levels darker in the reference, formula unknown; JPEG covers (Haberman) are lighter than luma -> `opub.py` keeps the empirical `(luma+3*max)/4` for the non-PNG/sips path only]; thumbnails are 1-bit dithered (Floyd-Steinberg-like, details differ). The 4 "external" covers are a
  fixed generic icon in the reference; we use the real cover. SVG `<image xlink:href>` (calibre cover pages) counts as an image [exact, Kingsbridge].

### Row / run details learned from the corpus
* **Row kinds**: 1 text, 2-7 h1-h6, 11 hr, **40 `<pre>`**. Rows without any text/image run (`<p></p>`, `<p> </p>`, `<p><br/></p>`) are dropped.
* `<br>` does **not** start a new row: it is a block of type 3 `{u8 3, ref -> {u32 v}}` inside the row, v = 0 for `<br>`, 4 for a newline in `<pre>`. In `<pre>` the text is kept verbatim, one run per line.
* Runs: a text node longer than 200 bytes is cut at 200-byte boundaries; a single token longer than that is cut as well [exact]. NBSP/ZWSP/soft hyphen are content (not whitespace).
* **Lists** are containers + markers: `ul`/`ol` -> container `{pl 40px}` (nested lists nest via `parent`); every `li` row has style = li (padding-left 2.5em / own font-size, display 5), first run = marker
  text in the *row block style* (`"• "` level 1, `"○ "` level 2, `"▪ "` level 3, `"1. "` with `start`, `i.` lower-roman, `a.`), then the text in the zeroed style. An `li` that contains a nested list
  gets **no marker and no padding**; `list-style-type:none` keeps the padding but no marker; blocks inside an `li` get the 2.5em added to their padding-left [exact, 02_lists].
  `dl`: `dt` plain, `dd` = container `{pl 40px}` + row with padding-left 40px.
* **Tables**: no containers. Each `tr` is one row (style display 12) whose runs are the cells' text in order (run style = cell record: display 13, padding, border, background); `caption` is its own row
  (display 16); blocks inside a cell do not break the row; thead/tbody/tfoot are transparent; spans are ignored; rows without text are dropped [exact, 04_tables].
* **hr**: a container holding the border (the container is not referenced by any row, `container` = 0 in the row) plus a kind-11 row whose style also carries the border and margins [exact].
* Images: type-2 block `{image index, style}`; style = the `img` element's record. An image directly in a non-leaf block (e.g. `figure` with `figcaption`) forms its own row whose block style is the image style.
  A linked image is a type-4 link block with link-slot 0 = 2 and inner `{u32 image index, u32 style}`.
* Links: ltype 1 internal, 3 external, **2 for `epub:type="noteref"`**; slot 7 was 1 in exactly one reference link (`a.noteref` with `text-decoration:none` and no id) and 0 in all other ~3800 -> always 0 here **[unknown]**.
* Chapter header `f11` = `(n_containers, 0)` (the second u16 is 0 in every reference) [exact].
* Embedded fonts: Boox ignores `@font-face` files (06_fonts.opub has no font data); only the family string is stored, `"Georgia Embedded", serif` verbatim [exact].
* metadata `isbn` = the first `dc:identifier` whatever it is (calibre uuid in Kingsbridge) [exact for 2 books].

## Not understood / not generated
* Haberman's index chapters: nested `ul`s with `li` padding-top 8px etc. produce a different container/row split than ours (containers 140/2744 there); CSS-driven details of `li` containers are not decoded.
* `float`, `display:inline-block`, `vertical-align`, `text-transform`, `font-variant` (no record difference seen for the last two), footnote popups, link slot 7.
* Exact gray conversion of colour gradients; the 1-bit dither variant; the generic "external" cover icon.
* Which pages Boox gives chapters whose rounding differs by one page; whether NeoReader accepts files lacking features we do not generate: **needs a real device test**.
