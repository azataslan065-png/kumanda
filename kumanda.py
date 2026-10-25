#!/usr/bin/env python3
# HY350 MAX Telefon Kumandası — Termux üzerinde çalışır
# Kullanım: python kumanda.py <projeksiyon_ip:port>
# Sonra telefon tarayıcısında: http://127.0.0.1:8080
import subprocess, sys, threading, json, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

HEDEF = sys.argv[1] if len(sys.argv) > 1 else None
PORT = 8080
kilit = threading.Lock()
kabuk = None
ekran = [1920, 1080]

def sargs():
    return ['adb'] + (['-s', HEDEF] if HEDEF else [])

def baglan():
    if HEDEF:
        try:
            r = subprocess.run(['adb', 'connect', HEDEF], capture_output=True, text=True, timeout=10)
            return (r.stdout + r.stderr).strip()
        except Exception as e:
            return str(e)
    return 'hedef yok'

def bagli_mi():
    try:
        r = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    for l in r.stdout.splitlines()[1:]:
        p = l.split('\t')
        if len(p) == 2 and p[1] == 'device' and (not HEDEF or p[0] == HEDEF):
            return True
    return False

def ekran_boyutu():
    try:
        r = subprocess.run(sargs() + ['shell', 'wm', 'size'], capture_output=True, text=True, timeout=6)
        m = re.findall(r'(\d+)x(\d+)', r.stdout)
        if m:
            ekran[0], ekran[1] = int(m[-1][0]), int(m[-1][1])
    except Exception:
        pass

def kabuk_al():
    global kabuk
    if kabuk is None or kabuk.poll() is not None:
        kabuk = subprocess.Popen(sargs() + ['shell'], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    return kabuk

def gonder(satir):
    global kabuk
    with kilit:
        for _ in range(2):
            try:
                k = kabuk_al()
                k.stdin.write(satir + '\n')
                k.stdin.flush()
                return True
            except Exception:
                kabuk = None
                baglan()
        return False

TR = str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')

def yazi_komutu(t):
    t = t.translate(TR)
    t = ''.join(c for c in t if 32 <= ord(c) < 127)
    t = t.replace('%', '').replace(' ', '%s')
    t = t.replace("'", "'\\''")
    return "input text '" + t + "'" if t else None

# Kumandayı kullanabilecek sayfalar: indirilen HTML (file:// → "null"),
# bu sunucunun kendi sayfası ve GitHub Pages. Diğer siteler reddedilir.
IZINLI = {'null', 'http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT,
          'https://azataslan065-png.github.io'}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def kaynak(self):
        return self.headers.get('Origin')

    def izinli(self):
        o = self.kaynak()
        return o is None or o in IZINLI

    def cors(self):
        o = self.kaynak()
        if o in IZINLI:
            self.send_header('Access-Control-Allow-Origin', o)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self):
        self.send_response(204 if self.izinli() else 403)
        self.cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def yanit(self, kod, govde, tur='application/json'):
        b = govde.encode() if isinstance(govde, str) else govde
        self.send_response(kod)
        self.cors()
        self.send_header('Content-Type', tur + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self.yanit(200, SAYFA, 'text/html')
        elif self.path == '/durum':
            self.yanit(200, json.dumps({'bagli': bagli_mi(), 'hedef': HEDEF}))
        else:
            self.yanit(404, '{}')

    def do_POST(self):
        if not self.izinli():
            self.yanit(403, '{"ok": false}')
            return
        n = int(self.headers.get('Content-Length', 0) or 0)
        v = {k: x[0] for k, x in parse_qs(self.rfile.read(n).decode()).items()}
        ok = False
        try:
            if self.path == '/tus':
                ok = gonder('input keyevent %d' % int(v['k']))
            elif self.path == '/yazi':
                c = yazi_komutu(v.get('t', ''))
                ok = gonder(c) if c else False
            elif self.path == '/dokun':
                x = int(max(0, min(1, float(v['x']))) * ekran[0])
                y = int(max(0, min(1, float(v['y']))) * ekran[1])
                ok = gonder('input tap %d %d' % (x, y))
            elif self.path == '/kaydir':
                f = [max(0.0, min(1.0, float(v[k]))) for k in ('x1', 'y1', 'x2', 'y2')]
                ms = int(max(50, min(2000, float(v.get('ms', 250)))))
                ok = gonder('input swipe %d %d %d %d %d' % (
                    f[0] * ekran[0], f[1] * ekran[1], f[2] * ekran[0], f[3] * ekran[1], ms))
            elif self.path == '/baglan':
                msj = baglan()
                ekran_boyutu()
                self.yanit(200, json.dumps({'bagli': bagli_mi(), 'mesaj': msj}))
                return
        except (KeyError, ValueError):
            ok = False
        self.yanit(200, json.dumps({'ok': ok}))

SAYFA = r'''<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#0d0f14">
<title>HY350 Kumanda</title>
<style>
:root{--bg:#0d0f14;--kart:#171a22;--tus:#222634;--tus2:#2c3142;--yazi:#e8ebf2;--soluk:#8a90a2;--vurgu:#4f8cff;--kirmizi:#ff4d5e;--yesil:#2ecc71}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none}
html,body{margin:0;background:var(--bg);color:var(--yazi);font-family:system-ui,-apple-system,Roboto,sans-serif;touch-action:manipulation}
.kap{max-width:420px;margin:0 auto;padding:14px 16px 28px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.3px}
.durum{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--soluk);background:var(--kart);padding:7px 11px;border-radius:20px;border:0}
.nokta{width:9px;height:9px;border-radius:50%;background:var(--kirmizi)}
.durum.ok .nokta{background:var(--yesil)}
.sekmeler{display:flex;background:var(--kart);border-radius:12px;padding:4px;margin-bottom:14px}
.sekmeler button{flex:1;background:none;border:0;color:var(--soluk);padding:9px;border-radius:9px;font-size:14px;font-weight:600}
.sekmeler button.aktif{background:var(--tus2);color:var(--yazi)}
.b{background:var(--tus);border:0;color:var(--yazi);border-radius:14px;font-size:15px;font-weight:600;display:flex;align-items:center;justify-content:center;gap:6px;min-height:52px;transition:transform .06s,background .06s}
.b:active,.b.bas{transform:scale(.94);background:var(--tus2)}
.b svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.ust{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px}
.guc{color:var(--kirmizi)}
.dpad{position:relative;width:260px;height:260px;margin:4px auto 18px;border-radius:50%;background:var(--kart)}
.dpad .b{position:absolute;width:76px;height:76px;border-radius:50%;background:transparent}
.dpad .b:active,.dpad .b.bas{background:var(--tus2)}
.dpad .y{top:6px;left:92px}.dpad .a{bottom:6px;left:92px}.dpad .s{left:6px;top:92px}.dpad .sg{right:6px;top:92px}
.dpad .tamam{top:75px;left:75px;width:110px;height:110px;background:var(--vurgu);font-size:17px}
.dpad .tamam:active,.dpad .tamam.bas{background:#3a73e0}
.orta{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px}
.alt{display:grid;grid-template-columns:64px 1fr 64px;gap:10px;margin-bottom:16px}
.ses{display:flex;flex-direction:column;gap:10px}
.medya{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.yazi{display:flex;gap:8px;margin-top:6px}
.yazi input{flex:1;background:var(--kart);border:1px solid var(--tus2);color:var(--yazi);border-radius:12px;padding:0 14px;font-size:16px;min-height:50px;user-select:text;-webkit-user-select:text}
.yazi .b{padding:0 16px}
.not{font-size:12px;color:var(--soluk);margin:8px 2px 0;line-height:1.4}
.ped{width:100%;aspect-ratio:16/9;background:var(--kart);border:1px solid var(--tus2);border-radius:14px;position:relative;touch-action:none;overflow:hidden}
.ped span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--soluk);font-size:13px;pointer-events:none;text-align:center;padding:20px}
.iz{position:absolute;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:var(--vurgu);opacity:.6;pointer-events:none}
.gizli{display:none}
</style></head><body><div class="kap">
<header><h1>HY350 MAX</h1><button class="durum" id="durum"><span class="nokta"></span><span id="durumYazi">Kontrol…</span></button></header>
<div class="sekmeler"><button class="aktif" data-s="kumanda">Kumanda</button><button data-s="ped">Dokunmatik</button></div>

<section id="kumanda">
<div class="ust">
<button class="b guc" data-k="26"><svg viewBox="0 0 24 24"><path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/></svg></button>
<button class="b" data-k="176"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></button>
<button class="b" data-k="164"><svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="m23 9-6 6M17 9l6 6"/></svg></button>
</div>

<div class="dpad">
<button class="b y" data-k="19" data-tekrar><svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg></button>
<button class="b s" data-k="21" data-tekrar><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg></button>
<button class="b tamam" data-k="23">OK</button>
<button class="b sg" data-k="22" data-tekrar><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button>
<button class="b a" data-k="20" data-tekrar><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>
</div>

<div class="orta">
<button class="b" data-k="4"><svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>Geri</button>
<button class="b" data-k="3"><svg viewBox="0 0 24 24"><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>Ana</button>
<button class="b" data-k="82"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>Menü</button>
</div>

<div class="alt">
<div class="ses">
<button class="b" data-k="24" data-tekrar><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
<button class="b" data-k="25" data-tekrar><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
</div>
<div class="medya" style="grid-template-rows:1fr 1fr">
<button class="b" data-k="89"><svg viewBox="0 0 24 24"><path d="M11 19 2 12l9-7zM22 19l-9-7 9-7z"/></svg></button>
<button class="b" data-k="85"><svg viewBox="0 0 24 24"><path d="M6 4l12 8-12 8z"/></svg></button>
<button class="b" data-k="90"><svg viewBox="0 0 24 24"><path d="m13 19 9-7-9-7zM2 19l9-7-9-7z"/></svg></button>
<button class="b" data-k="187" style="grid-column:span 3"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>Son uygulamalar</button>
</div>
<div class="ses">
<button class="b" data-k="166" data-tekrar>CH+</button>
<button class="b" data-k="167" data-tekrar>CH−</button>
</div>
</div>

<div class="yazi"><input id="metin" placeholder="Projeksiyona yazı gönder…" autocomplete="off"><button class="b" id="gonder">Gönder</button></div>
<div class="yazi"><button class="b" data-k="67" style="flex:1">⌫ Sil</button><button class="b" data-k="66" style="flex:1">↵ Enter</button></div>
<p class="not">Türkçe harfler sadeleştirilir (ç→c, ş→s…).</p>
</section>

<section id="ped" class="gizli">
<div class="ped" id="pedAlan"><span>Dokun = tıkla · Kaydır = sürükle/kaydır<br>Alan projeksiyon ekranıyla birebir eşleşir</span></div>
<div class="orta" style="margin-top:14px">
<button class="b" data-k="4">Geri</button><button class="b" data-k="3">Ana</button><button class="b" data-k="85">⏯</button>
</div>
</section>
</div>
<script>
const $=s=>document.querySelector(s);
function post(yol,veri){return fetch(yol,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(veri)}).then(r=>r.json()).catch(()=>({ok:false}))}
function titre(){try{navigator.vibrate&&navigator.vibrate(12)}catch(e){}}
function tus(k){titre();return post('/tus',{k})}

document.querySelectorAll('[data-k]').forEach(b=>{
  let z1,z2;const k=b.dataset.k,tekrar=b.hasAttribute('data-tekrar');
  const dur=()=>{clearTimeout(z1);clearInterval(z2);b.classList.remove('bas')};
  b.addEventListener('pointerdown',e=>{e.preventDefault();b.classList.add('bas');tus(k);
    if(tekrar){z1=setTimeout(()=>{z2=setInterval(()=>tus(k),130)},420)}});
  ['pointerup','pointerleave','pointercancel'].forEach(t=>b.addEventListener(t,dur));
  b.addEventListener('contextmenu',e=>e.preventDefault());
});

function yaziGonder(){const t=$('#metin').value;if(!t)return;titre();post('/yazi',{t}).then(r=>{if(r.ok)$('#metin').value=''})}
$('#gonder').onclick=yaziGonder;
$('#metin').addEventListener('keydown',e=>{if(e.key==='Enter')yaziGonder()});

document.querySelectorAll('.sekmeler button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.sekmeler button').forEach(x=>x.classList.toggle('aktif',x===b));
  ['kumanda','ped'].forEach(id=>$('#'+id).classList.toggle('gizli',id!==b.dataset.s));
});

const ped=$('#pedAlan');let bas=null;
function konum(e){const r=ped.getBoundingClientRect();return{x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height}}
function iz(p){const d=document.createElement('div');d.className='iz';d.style.left=(p.x*100)+'%';d.style.top=(p.y*100)+'%';ped.appendChild(d);setTimeout(()=>d.remove(),300)}
ped.addEventListener('pointerdown',e=>{ped.setPointerCapture(e.pointerId);bas={...konum(e),t:Date.now()}});
ped.addEventListener('pointerup',e=>{if(!bas)return;const s=konum(e),ms=Date.now()-bas.t;
  const d=Math.hypot((s.x-bas.x)*16,(s.y-bas.y)*9);titre();
  if(d<0.4){iz(s);post('/dokun',{x:s.x,y:s.y})}
  else{iz(bas);iz(s);post('/kaydir',{x1:bas.x,y1:bas.y,x2:s.x,y2:s.y,ms:Math.max(120,ms)})}
  bas=null});

async function durum(){try{const r=await (await fetch('/durum')).json();
  $('#durum').classList.toggle('ok',r.bagli);$('#durumYazi').textContent=r.bagli?'Bağlı':'Bağlı değil'}
  catch(e){$('#durum').classList.remove('ok');$('#durumYazi').textContent='Sunucu kapalı'}}
$('#durum').onclick=async()=>{$('#durumYazi').textContent='Bağlanıyor…';await post('/baglan',{});durum()};
durum();setInterval(durum,5000);
</script></body></html>'''

if __name__ == '__main__':
    print('Bağlanılıyor:', baglan())
    ekran_boyutu()
    print('Durum:', 'BAĞLI' if bagli_mi() else 'BAĞLI DEĞİL (adb pair yaptın mı?)')
    print('Ekran:', '%dx%d' % tuple(ekran))
    print('Kumanda hazır → tarayıcıda aç: http://127.0.0.1:%d' % PORT)
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
