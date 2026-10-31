#!/usr/bin/python3
"""Helper fuer "Shelfcast.app": konvertiert EPUBs, waehlt Zielordner.
Ausgabe (UTF-8): Zeile 1: device|local, Zeile 2: Zielordner, Zeile 3: Anzahl OK,
danach je Fehler eine Zeile "ERR<TAB>Dateiname<TAB>Meldung"."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opub

def find_device_books():
    try: names = sorted(os.listdir('/Volumes'))
    except OSError: return None
    for n in names:
        if n.startswith('.') or n == 'Macintosh HD' or 'recovery' in n.lower(): continue
        p = os.path.join('/Volumes', n)
        if os.path.realpath(p) == '/' or not os.path.ismount(p): continue
        try:
            for sub in sorted(os.listdir(p)):
                sp = os.path.join(p, sub)
                if sub.lower() == 'books' and os.path.isdir(sp) and os.access(sp, os.W_OK):
                    return sp
        except OSError: continue
    return None

def unique(d, base):
    p = os.path.join(d, base + '.opub'); i = 2
    while os.path.exists(p):
        p = os.path.join(d, '%s (%d).opub' % (base, i)); i += 1
    return p

def convert_one(f, dest, device):
    """Einzelmodus: schreibt <dest>/<name>.opub, raeumt auf dem Geraet ._-Dateien weg.
    Gibt eine JSON-Zeile aus: {ok, path | error}."""
    import json, subprocess
    try:
        os.makedirs(dest, exist_ok=True)
        with open(f, 'rb') as fh: data = opub.convert(fh.read())
        out = unique(dest, os.path.splitext(os.path.basename(f))[0])
        with open(out, 'wb') as fh: fh.write(data)
        if device:
            subprocess.run(['/bin/sync'], timeout=60)
            try: os.remove(os.path.join(dest, '._' + os.path.basename(out)))
            except OSError: pass
        r = {'ok': True, 'path': out}
    except Exception as e:
        r = {'ok': False, 'error': (str(e).replace('\n', ' ')[:200] or type(e).__name__)}
    sys.stdout.buffer.write((json.dumps(r, ensure_ascii=False) + '\n').encode('utf-8'))

def main(files):
    files = [f for f in files if f.lower().endswith('.epub')]
    dev = find_device_books()
    if dev: kind, dest = 'device', dev
    else:
        kind, dest = 'local', os.path.expanduser('~/Downloads/Boox')
        os.makedirs(dest, exist_ok=True)
    ok, errs = 0, []
    for f in files:
        name = os.path.basename(f)
        try:
            with open(f, 'rb') as fh: data = opub.convert(fh.read())
            out = unique(dest, os.path.splitext(name)[0])
            with open(out, 'wb') as fh: fh.write(data)
            ok += 1
        except Exception as e:
            errs.append('ERR\t%s\t%s' % (name, str(e).replace('\n', ' ')[:200] or type(e).__name__))
    out = sys.stdout.buffer
    out.write(('\n'.join([kind, dest, str(ok)] + errs) + '\n').encode('utf-8'))

if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--one':   # --one <epub> <zielordner> <device|local>
        convert_one(sys.argv[2], sys.argv[3], sys.argv[4] == 'device')
    else:
        main(sys.argv[1:])
