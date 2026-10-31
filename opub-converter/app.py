#!/usr/bin/env python3
"""Tiny local web UI:  python3 app.py  ->  drop .epub files on the page, get .opub downloads."""
import os, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from opub import convert

PAGE = b"""<!doctype html><meta charset=utf-8><title>EPUB to OPUB</title>
<style>body{font:16px system-ui;max-width:34em;margin:3em auto;padding:0 1em}#d{border:3px dashed #888;border-radius:12px;padding:3em 1em;text-align:center}
#d.o{background:#eef}li{margin:.4em 0}</style>
<h2>EPUB &rarr; OPUB (Boox NeoReader)</h2>
<div id=d>Drop .epub files here or <input id=f type=file accept=.epub multiple></div><ul id=l></ul>
<script>
async function go(files){for(const f of files){const li=document.createElement('li');li.textContent=f.name+' ...';l.append(li);
 try{const r=await fetch('/convert?name='+encodeURIComponent(f.name),{method:'POST',body:f});if(!r.ok)throw new Error(await r.text());
 const b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=f.name.replace(/\\.epub$/i,'')+'.opub';
 a.textContent='download '+a.download+' ('+(b.size>>10)+' KB)';li.textContent=f.name+' \\u2192 ';li.append(a);a.click()}
 catch(e){li.textContent=f.name+': '+e.message}}}
f.onchange=()=>go(f.files);d.ondragover=e=>{e.preventDefault();d.className='o'};d.ondragleave=()=>d.className='';
d.ondrop=e=>{e.preventDefault();d.className='';go([...e.dataTransfer.files].filter(x=>/\\.epub$/i.test(x.name)))}
</script>"""

class H(BaseHTTPRequestHandler):
    def do_GET(s):
        s.send_response(200); s.send_header('Content-Type', 'text/html'); s.send_header('Content-Length', str(len(PAGE))); s.end_headers(); s.wfile.write(PAGE)
    def do_POST(s):
        try:
            out = convert(s.rfile.read(int(s.headers['Content-Length'])))
            s.send_response(200); s.send_header('Content-Type', 'application/octet-stream'); s.send_header('Content-Length', str(len(out))); s.end_headers(); s.wfile.write(out)
        except Exception as e:
            msg = ('conversion failed: %s' % e).encode(); s.send_response(500); s.send_header('Content-Length', str(len(msg))); s.end_headers(); s.wfile.write(msg)
    def log_message(s, *a): pass

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(('127.0.0.1', port), H); url = 'http://127.0.0.1:%d/' % srv.server_port
    print('serving', url, '(Ctrl-C to stop)'); threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
