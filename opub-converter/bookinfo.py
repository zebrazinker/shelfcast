#!/usr/bin/python3
"""Gibt JSON {title, author, cover(base64 oder null)} fuer eine EPUB-Datei aus."""
import os, sys, json, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opub

def info(path):
    with open(path, 'rb') as fh: data = fh.read()
    e = opub.Epub(data)
    cover = None
    if e.cover:
        c = e.read(e.cover)
        if c and len(c) < 8_000_000: cover = base64.b64encode(c).decode('ascii')
    return {'title': (e.meta.get('title') or [''])[0],
            'author': ', '.join(e.meta.get('creator') or []), 'cover': cover}

if __name__ == '__main__':
    try: r = info(sys.argv[1])
    except Exception as ex: r = {'title': '', 'author': '', 'cover': None, 'error': str(ex)[:200]}
    sys.stdout.buffer.write(json.dumps(r, ensure_ascii=False).encode('utf-8') + b'\n')
