#!/usr/bin/env python3
# Telefon Kumandası köprüsü — Termux üzerinde çalışır.
# Desteklenen cihazlar (aynı anda birden fazla eklenebilir, kumandada aralarında geçiş yapılır):
#   • LG akıllı TV (webOS)
#   • Toshiba TV — Google TV / Android TV, VIDAA ve Smart Center sistemlerinin üçü de
#   • Android projeksiyon / Android TV  (ADB)
# Hepsi Wi‑Fi üzerinden; telefona Python dışında ek paket gerekmez.
#
# Kullanım:   python kumanda.py
# Sonra aç:   http://127.0.0.1:8080   (ya da azataslan065-png.github.io/kumanda/)
# Cihaz adresleri kumanda sayfasındaki ⚙ menüsünden girilir ve ~/.kumanda.json'a kaydedilir.
# Eski kullanım da çalışır:  python kumanda.py PROJEKSIYON_IP:5555
import base64, json, os, re, socket, ssl, struct, subprocess, sys, threading, time
import concurrent.futures
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

PORT = 8080
AYAR_DOSYASI = os.path.expanduser('~/.kumanda.json')
SAYFA_DOSYASI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
CANLI_TV_URL = 'https://azataslan065-png.github.io/kumanda/tv/'

# ───────────────────────── ayarlar ─────────────────────────
ayar_kilit = threading.Lock()

def ayar_oku():
    try:
        with open(AYAR_DOSYASI, encoding='utf-8') as f:
            a = json.load(f)
            return a if isinstance(a, dict) else {}
    except Exception:
        return {}

def ayar_yaz(a):
    with ayar_kilit:
        gecici = AYAR_DOSYASI + '.tmp'
        with open(gecici, 'w', encoding='utf-8') as f:
            json.dump(a, f, ensure_ascii=False, indent=2)
        os.replace(gecici, AYAR_DOSYASI)

AYAR = ayar_oku()

# ───────────────────────── ağ yardımcıları ─────────────────────────
def yerel_ip():
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(('8.8.8.8', 80))
        ip = u.getsockname()[0]
        u.close()
        return ip
    except Exception:
        return None

def port_acik(ip, port, sure=0.4):
    s = socket.socket()
    s.settimeout(sure)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()

def ev_agi_mi(ip):
    """Ev Wi-Fi'larının kullandığı özel adres aralıkları. Mobil veri (ör. 100.64.0.0/10) bunlara girmez."""
    try:
        a, b = (int(x) for x in ip.split('.')[:2])
    except Exception:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)

WIFI_UYARI = ('Telefon Wi-Fi\'a bağlı değil (mobil veri kullanıyor). '
              'Telefonu TV/projeksiyonla aynı Wi-Fi\'a bağlayıp tekrar ara.')

TARAMA_PORTLARI = (5555, 3000, 3001, 6466, 6467, 36669, 56789)

def ag_tara(taban=None):
    """Ev ağındaki TV ve projeksiyonları bulur; Toshiba'nın hangi sistemle çalıştığını da söyler."""
    ip = yerel_ip()
    if taban is None:
        if not ip or not ev_agi_mi(ip):
            return {'hata': WIFI_UYARI, 'adb': [], 'lg': [], 'cihazlar': []}
        taban = ip.rsplit('.', 1)[0]
    hedefler = [('%s.%d' % (taban, i), p) for i in range(1, 255) for p in TARAMA_PORTLARI]
    acik = {}
    with concurrent.futures.ThreadPoolExecutor(128) as h:
        for (adres, p), a in zip(hedefler, h.map(lambda x: port_acik(*x), hedefler)):
            if a and adres != ip:
                acik.setdefault(adres, set()).add(p)
    # Her adres "açık" görünüyorsa ağ bağlantıları yakalıyor demektir; liste güvenilmez.
    if len(acik) > 25:
        return {'hata': 'Bu ağda arama güvenilir sonuç vermiyor. Adresi TV\'nin ağ ayarlarından bakıp elle yaz.',
                'adb': [], 'lg': [], 'cihazlar': []}
    tvler = [a for a, p in acik.items() if p - {5555}]
    upnp = upnp_bilgileri(tvler) if tvler else {}
    def smartcenter_mi(a):
        try:
            import urllib.request
            with urllib.request.urlopen('http://%s:56789/apps/SmartCenter' % a, timeout=2) as y:
                return y.status == 200
        except Exception:
            return False
    bulunan = []
    for adres in sorted(acik, key=lambda a: int(a.rsplit('.', 1)[1])):
        p, b = acik[adres], upnp.get(adres, {})
        marka = b.get('marka', '')
        if p & {3000, 3001}:
            tur = 'lg'
        elif p & {6466, 6467}:
            tur = 'androidtv'
        elif 36669 in p:
            tur = 'vidaa'
        elif 56789 in p and smartcenter_mi(adres):
            tur = 'smartcenter'
        else:
            tur = None
        if tur:
            m = marka.lower()
            ad = ('LG TV' if tur == 'lg' else 'Toshiba TV' if 'toshiba' in (m + b.get('model', '') + b.get('ad', '')).lower() else
                  (marka.split()[0].title() + ' TV') if marka and not m.startswith(('google', 'android')) else 'TV')
            bulunan.append({'adres': adres, 'tur': tur, 'sistem': SISTEM_ADI[tur], 'ad': ad,
                            'marka': marka, 'model': b.get('model', ''), 'tv_adi': b.get('ad', '')})
        if 5555 in p:
            bulunan.append({'adres': adres + ':5555', 'tur': 'adb', 'sistem': SISTEM_ADI['adb'],
                            'ad': 'Projeksiyon' if not tur else 'Android (ADB)', 'marka': marka,
                            'model': b.get('model', ''), 'tv_adi': b.get('ad', '')})
    return {'ag': taban + '.x', 'cihazlar': bulunan,
            'adb': [c['adres'] for c in bulunan if c['tur'] == 'adb'],
            'lg': [c['adres'] for c in bulunan if c['tur'] == 'lg']}

def mac_duzelt(m):
    m = re.sub(r'[^0-9a-fA-F]', '', m or '')
    return ':'.join(m[i:i + 2] for i in range(0, 12, 2)).lower() if len(m) == 12 else None

def wake_on_lan(mac, ip=None):
    mac = mac_duzelt(mac)
    if not mac:
        return False
    paket = b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16
    hedefler = ['255.255.255.255']
    if ip:
        hedefler += [ip.rsplit('.', 1)[0] + '.255', ip]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    gonderildi = False
    for _ in range(3):
        for h in hedefler:
            for p in (9, 7):
                try:
                    s.sendto(paket, (h, p))
                    gonderildi = True
                except Exception:
                    pass
        time.sleep(0.1)
    s.close()
    return gonderildi

# ───────────────────────── küçük WebSocket istemcisi (yalnızca stdlib) ─────────────────────────
class WS:
    def __init__(self, url, sure=6):
        u = urlparse(url)
        guvenli = u.scheme == 'wss'
        port = u.port or (443 if guvenli else 80)
        ham = socket.create_connection((u.hostname, port), timeout=sure)
        ham.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if guvenli:
            # TV kendi imzaladığı sertifikayı kullanır; yalnızca yerel ağdaki TV'ye bağlanıyoruz.
            b = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            b.check_hostname = False
            b.verify_mode = ssl.CERT_NONE
            ham = b.wrap_socket(ham, server_hostname=u.hostname)
        self.s = ham
        self.yaz_kilit = threading.Lock()
        anahtar = base64.b64encode(os.urandom(16)).decode()
        yol = (u.path or '/') + ('?' + u.query if u.query else '')
        istek = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                 'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n') % (yol, u.hostname, port, anahtar)
        self.s.sendall(istek.encode())
        bas = b''
        while b'\r\n\r\n' not in bas:
            p = self.s.recv(1024)
            if not p:
                raise ConnectionError('el sıkışma kesildi')
            bas += p
            if len(bas) > 16384:
                raise ConnectionError('el sıkışma çok uzun')
        bas, self.tampon = bas.split(b'\r\n\r\n', 1)
        if b' 101 ' not in bas.split(b'\r\n', 1)[0]:
            raise ConnectionError('websocket reddedildi: ' + bas.split(b'\r\n', 1)[0].decode(errors='replace'))
        self.s.settimeout(None)
        self.acik = True

    def _oku(self, n):
        while len(self.tampon) < n:
            p = self.s.recv(65536)
            if not p:
                raise ConnectionError('bağlantı kapandı')
            self.tampon += p
        v, self.tampon = self.tampon[:n], self.tampon[n:]
        return v

    def _cerceve(self, kod, veri):
        b = bytearray([0x80 | kod])
        n = len(veri)
        if n < 126:
            b.append(0x80 | n)
        elif n < 65536:
            b.append(0x80 | 126)
            b += struct.pack('>H', n)
        else:
            b.append(0x80 | 127)
            b += struct.pack('>Q', n)
        maske = os.urandom(4)
        b += maske
        b += bytes(c ^ maske[i % 4] for i, c in enumerate(veri))
        with self.yaz_kilit:
            self.s.sendall(bytes(b))

    def gonder(self, metin):
        try:
            self._cerceve(0x1, metin.encode('utf-8'))
        except Exception:
            self.acik = False
            raise

    def al(self):
        """Bir metin mesajı döndürür; bağlantı kapanırsa None."""
        parcalar = b''
        try:
            while True:
                b1, b2 = self._oku(2)
                fin, kod = b1 & 0x80, b1 & 0x0F
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack('>H', self._oku(2))[0]
                elif n == 127:
                    n = struct.unpack('>Q', self._oku(8))[0]
                maske = self._oku(4) if b2 & 0x80 else None
                veri = self._oku(n)
                if maske:
                    veri = bytes(c ^ maske[i % 4] for i, c in enumerate(veri))
                if kod == 0x8:
                    self.acik = False
                    return None
                if kod == 0x9:
                    self._cerceve(0xA, veri)
                    continue
                if kod == 0xA:
                    continue
                parcalar += veri
                if fin:
                    return parcalar.decode('utf-8', 'replace')
        except Exception:
            self.acik = False
            return None

    def kapat(self):
        self.acik = False
        try:
            self._cerceve(0x8, b'')
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass

# ───────────────────────── LG webOS ─────────────────────────
# Yeni LG yazılımları (webOS 26) için imzasız izin bildirimi.
# Home Assistant'ın aiowebostv kütüphanesindeki düzeltmeyle aynı (Apache-2.0, "Fix webOS 26 pairing handshake").
# Yeni TV'ler eski imzalı sertifikayı "blacklisted certificate" diyerek reddediyor.
LG_KAYIT_YENI = {
    "forcePairing": False,
    "pairingType": "PROMPT",
    "manifest": {
        "appVersion": "1.1",
        "manifestVersion": 1,
        "permissions": [
            "APP_TO_APP", "CLOSE", "CONTROL_AUDIO", "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK",
            "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_MEDIA_RECORDING", "CONTROL_INPUT_TEXT",
            "CONTROL_INPUT_TV", "CONTROL_MOUSE_AND_KEYBOARD", "CONTROL_POWER", "CONTROL_TV_SCREEN",
            "LAUNCH", "LAUNCH_WEBAPP", "READ_APP_STATUS", "READ_COUNTRY_INFO", "READ_CURRENT_CHANNEL",
            "READ_INPUT_DEVICE_LIST", "READ_INSTALLED_APPS", "READ_LGE_SDX", "READ_LGE_TV_INPUT_EVENTS",
            "READ_NETWORK_STATE", "READ_NOTIFICATIONS", "READ_POWER_STATE", "READ_RUNNING_APPS",
            "READ_SETTINGS", "READ_TV_CHANNEL_LIST", "READ_TV_CURRENT_TIME", "READ_UPDATE_INFO",
            "SEARCH", "TEST_OPEN", "TEST_PROTECTED", "TEST_SECURE", "UPDATE_FROM_REMOTE_APP",
            "WRITE_NOTIFICATION_ALERT", "WRITE_NOTIFICATION_TOAST", "WRITE_SETTINGS"
        ]
    }
}

# Eski TV'ler için imzalı izin bildirimi — yalnızca yeni yöntem başarısız olursa denenir.
# (PyWebOSTV / LGWebOSRemote ile aynı, MIT lisanslı kaynaklardan).
LG_IMZA = ("eyJhbGdvcml0aG0iOiJSU0EtU0hBMjU2Iiwia2V5SWQiOiJ0ZXN0LXNpZ25pbm"
           "ctY2VydCIsInNpZ25hdHVyZVZlcnNpb24iOjF9.hrVRgjCwXVvE2OOSpDZ58hR"
           "+59aFNwYDyjQgKk3auukd7pcegmE2CzPCa0bJ0ZsRAcKkCTJrWo5iDzNhMBWRy"
           "aMOv5zWSrthlf7G128qvIlpMT0YNY+n/FaOHE73uLrS/g7swl3/qH/BGFG2Hu4"
           "RlL48eb3lLKqTt2xKHdCs6Cd4RMfJPYnzgvI4BNrFUKsjkcu+WD4OO2A27Pq1n"
           "50cMchmcaXadJhGrOqH5YmHdOCj5NSHzJYrsW0HPlpuAx/ECMeIZYDh6RMqaFM"
           "2DXzdKX9NmmyqzJ3o/0lkk/N97gfVRLW5hA29yeAwaCViZNCP8iC9aO0q9fQoj"
           "oa7NQnAtw==")
LG_KAYIT = {
    "forcePairing": False,
    "pairingType": "PROMPT",
    "manifest": {
        "appVersion": "1.1",
        "manifestVersion": 1,
        "permissions": [
            "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "TEST_OPEN", "TEST_PROTECTED",
            "CONTROL_AUDIO", "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
            "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV", "CONTROL_POWER", "READ_APP_STATUS",
            "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE", "READ_RUNNING_APPS",
            "READ_TV_CHANNEL_LIST", "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE", "READ_COUNTRY_INFO",
            "READ_SETTINGS", "CONTROL_TV_SCREEN", "CONTROL_TV_STANBY", "CONTROL_FAVORITE_GROUP",
            "CONTROL_USER_INFO", "CHECK_BLUETOOTH_DEVICE", "CONTROL_BLUETOOTH", "CONTROL_TIMER_INFO",
            "STB_INTERNAL_CONNECTION", "CONTROL_RECORDING", "READ_RECORDING_STATE", "WRITE_RECORDING_LIST",
            "READ_RECORDING_LIST", "READ_RECORDING_SCHEDULE", "WRITE_RECORDING_SCHEDULE",
            "READ_STORAGE_DEVICE_LIST", "READ_TV_PROGRAM_INFO", "CONTROL_BOX_CHANNEL",
            "READ_TV_ACR_AUTH_TOKEN", "READ_TV_CONTENT_STATE", "READ_TV_CURRENT_TIME",
            "ADD_LAUNCHER_CHANNEL", "SET_CHANNEL_SKIP", "RELEASE_CHANNEL_SKIP", "CONTROL_CHANNEL_BLOCK",
            "DELETE_SELECT_CHANNEL", "CONTROL_CHANNEL_GROUP", "SCAN_TV_CHANNELS", "CONTROL_TV_POWER",
            "CONTROL_WOL"
        ],
        "signatures": [{"signature": LG_IMZA, "signatureVersion": 1}],
        "signed": {
            "appId": "com.lge.test",
            "created": "20140509",
            "localizedAppNames": {"": "LG Remote App", "ko-KR": "리모컨 앱", "zxx-XX": "ЛГ Rэмotэ AПП"},
            "localizedVendorNames": {"": "LG Electronics"},
            "permissions": [
                "TEST_SECURE", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS",
                "READ_LGE_SDX", "READ_NOTIFICATIONS", "SEARCH", "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
                "CONTROL_POWER", "READ_CURRENT_CHANNEL", "READ_RUNNING_APPS", "READ_UPDATE_INFO",
                "UPDATE_FROM_REMOTE_APP", "READ_LGE_TV_INPUT_EVENTS", "READ_TV_CURRENT_TIME"
            ],
            "serial": "2f930e2d2cfe083771f68e4fe7bb07",
            "vendorId": "com.lge"
        }
    }
}

# Kumanda tuşu → sırayla denenecek yollar.
#   ('b', AD)  : asıl kumandanın kullandığı tuş kanalı (getPointerInputSocket). Yeni webOS'ta da çalışır.
#   ('r', URI) : uygulama komutu (ssap). Yeni imzasız eşleşmede bazıları reddedilebilir, yedek olarak kalır.
# Tuş adları Home Assistant'ın aiowebostv/buttons.py listesinden (Ağustos 2026).
LG_TUS = {
    'YUKARI': [('b', 'UP')], 'ASAGI': [('b', 'DOWN')], 'SOL': [('b', 'LEFT')], 'SAG': [('b', 'RIGHT')],
    'TAMAM': [('b', 'ENTER')], 'GERI': [('b', 'BACK')], 'ANA': [('b', 'HOME')], 'CIKIS': [('b', 'EXIT')],
    'AYARLAR': [('b', 'MENU'), ('b', 'QMENU')], 'BILGI': [('b', 'INFO')],
    'KAYNAK': [('b', 'INPUT_HUB')], 'REHBER': [('b', 'GUIDE'), ('b', 'PROGRAM')], 'SON': [('b', 'RECENT')],
    'LISTE': [('b', 'LIST')], 'ALTYAZI': [('b', 'CC')], 'SES_DILI': [('b', 'SAP')], 'ORAN': [('b', 'ASPECT_RATIO')],
    'UYGULAMALAR': [('b', 'MYAPPS')], 'CANLI': [('b', 'DASH')],
    'SESSIZ': [('b', 'MUTE')],
    'SES+': [('b', 'VOLUMEUP'), ('r', 'ssap://audio/volumeUp')],
    'SES-': [('b', 'VOLUMEDOWN'), ('r', 'ssap://audio/volumeDown')],
    'KANAL+': [('b', 'CHANNELUP'), ('r', 'ssap://tv/channelUp')],
    'KANAL-': [('b', 'CHANNELDOWN'), ('r', 'ssap://tv/channelDown')],
    'OYNAT': [('b', 'PLAY'), ('r', 'ssap://media.controls/play')],
    'DURAKLAT': [('b', 'PAUSE'), ('r', 'ssap://media.controls/pause')],
    'DURDUR': [('b', 'STOP'), ('r', 'ssap://media.controls/stop')],
    'GERI_SAR': [('b', 'REWIND'), ('r', 'ssap://media.controls/rewind')],
    'ILERI_SAR': [('b', 'FASTFORWARD'), ('r', 'ssap://media.controls/fastForward')],
    'KIRMIZI': [('b', 'RED')], 'YESIL': [('b', 'GREEN')], 'SARI': [('b', 'YELLOW')], 'MAVI': [('b', 'BLUE')],
    'YILDIZ': [('b', 'ASTERISK')],
    'SIL': [('r', 'ssap://com.webos.service.ime/deleteCharacters')],
    'ENTER': [('r', 'ssap://com.webos.service.ime/sendEnterKey'), ('b', 'ENTER')],
}
for _r in '0123456789':
    LG_TUS[_r] = [('b', _r)]

# Kendi ekran klavyesini kullanan uygulamalar: LG'nin yazı komutu (ime/insertText) bunlara ulaşmaz,
# TV "tamam" dese bile yazı ekrana düşmez.
KENDI_KLAVYELI = {'youtube.leanback.v4': 'YouTube', 'netflix': 'Netflix', 'amazon': 'Prime Video',
                  'com.disney.disneyplus-prod': 'Disney+', 'spotify-beehive': 'Spotify', 'tr.com.exxen': 'Exxen',
                  'com.apple.appletv': 'Apple TV', 'tabii': 'tabii', 'com.blutv.webos': 'BluTV'}

def youtube_kimligi(url):
    u = urlparse(str(url).strip())
    if not u.hostname:
        return None
    host = u.hostname.lower()
    if host.endswith('youtu.be'):
        vid = u.path.strip('/').split('/')[0]
    elif host.endswith('youtube.com'):
        q = parse_qs(u.query)
        vid = (q.get('v') or [''])[0]
        if not vid:
            m = re.match(r'^/(?:shorts|live|embed|v)/([^/?#]+)', u.path)
            vid = m.group(1) if m else ''
    else:
        return None
    return vid if re.match(r'^[A-Za-z0-9_-]{6,20}$', vid or '') else None

IKON_BELLEK = {}
IKON_KLASORU = os.path.expanduser('~/.kumanda_ikonlar')

def _resim_turu(v):
    if v[:8] == b'\x89PNG\r\n\x1a\n': return 'image/png'
    if v[:3] == b'\xff\xd8\xff': return 'image/jpeg'
    if v[:4] == b'RIFF' and v[8:12] == b'WEBP': return 'image/webp'
    if v[:6] in (b'GIF87a', b'GIF89a'): return 'image/gif'
    if v.lstrip()[:5] in (b'<svg ', b'<?xml'): return 'image/svg+xml'
    return None

class LGTV:
    tur = 'lg'

    def __init__(self, cid, ad, adres, anahtar=None, mac=None):
        self.id, self.ad, self.adres = cid, ad, adres
        self.anahtar, self.mac = anahtar, mac
        self.durum, self.mesaj = 'kapali', ''
        self.ws = None
        self.imlec = None
        self.bekleyen = {}
        self.sayac = 0
        self.kilit = threading.Lock()
        self.baglaniyor = False
        self.son_deneme = 0
        self.sonuclar = {}      # tuş → son deneme sonucu (tuş testi için)
        self.bilgi = {}         # model, webOS sürümü
        self.uygulamalar = []   # TV'de yüklü uygulamalar
        self.klavye = None      # TV'de yazı kutusu seçili mi (None = bilinmiyor)
        self.yazi_izni = None   # TV yazı gönderme iznini verdi mi

    def ozet(self):
        # Sayfa durumu düzenli sorduğu için: TV kendi kumandasıyla açılırsa burada yakalanır.
        if self.durum == 'kapali' and time.time() - self.son_deneme > 8:
            self.baglan_arkada()
        return {'id': self.id, 'tur': self.tur, 'ad': self.ad, 'adres': self.adres,
                'durum': self.durum, 'mesaj': self.mesaj, 'mac': self.mac or '', 'bilgi': self.bilgi,
                'klavye': self.klavye, 'yazi_izni': self.yazi_izni}

    # — bağlantı —
    def baglan_arkada(self):
        with self.kilit:
            if self.baglaniyor or self.durum == 'bagli':
                return
            self.baglaniyor = True
            self.son_deneme = time.time()
        threading.Thread(target=self._baglan, daemon=True).start()

    def _baglan(self):
        try:
            self.durum, self.mesaj = 'baglaniyor', ''
            ws = None
            for url in ('wss://%s:3001/' % self.adres, 'ws://%s:3000/' % self.adres):
                try:
                    ws = WS(url, sure=4)
                    break
                except Exception as e:
                    son_hata = e
            if ws is None:
                self.durum = 'kapali'
                self.mesaj = 'TV kapalı ya da ağda değil'
                return
            self.ws = ws
            threading.Thread(target=self._dinle, args=(ws,), daemon=True).start()
            # Yeni webOS sürümleri eşleşmeden önce "hello" ve sistem bilgisi istiyor.
            merhaba = self._istek_bekle('hello', None, {}, sure=4, kayit=True, mid='hello')
            sistem = self._istek_bekle('request', 'ssap://system/getSystemInfo', {}, sure=4, kayit=True)
            self._bilgi_al(merhaba, sistem)
            cevap = None
            for sablon in (LG_KAYIT_YENI, LG_KAYIT):
                kayit = dict(sablon)
                if self.anahtar:
                    kayit['client-key'] = self.anahtar
                cevap = self._istek_bekle('register', None, kayit, sure=90, kayit=True)
                if cevap and cevap.get('type') == 'registered':
                    break
                # Süre dolduysa, kullanıcı TV'de reddettiyse ya da bağlantı koptuysa
                # eski yöntemle bir daha izin isteme.
                hata = str((cevap or {}).get('error', '')).lower()
                kullanici_reddetti = 'denied' in hata or 'cancel' in hata or 'user' in hata
                if cevap is None or kullanici_reddetti or self.ws is not ws or not ws.acik:
                    break
            if cevap and cevap.get('type') == 'registered':
                yeni = cevap.get('payload', {}).get('client-key')
                if yeni and yeni != self.anahtar:
                    self.anahtar = yeni
                    cihaz_kaydet(self)
                self.durum, self.mesaj = 'bagli', ''
                threading.Thread(target=self._mac_ogren, daemon=True).start()
                threading.Thread(target=self._bagli_sonrasi, daemon=True).start()
            else:
                hata = (cevap or {}).get('error') or 'TV izin vermedi ya da süre doldu'
                self.durum, self.mesaj = 'hata', 'Eşleşme olmadı: ' + str(hata)
                ws.kapat()
        except Exception as e:
            self.durum, self.mesaj = 'hata', str(e)
        finally:
            self.baglaniyor = False

    def _dinle(self, ws):
        while True:
            m = ws.al()
            if m is None:
                break
            try:
                o = json.loads(m)
            except Exception:
                continue
            p = o.get('payload') or {}
            if o.get('type') == 'response' and p.get('pairingType') == 'PROMPT':
                self.durum = 'onay'
                self.mesaj = 'TV ekranındaki izin isteğini TV kumandasıyla kabul et'
                continue
            if o.get('id') == 'klavye':
                self._klavye_olayi(o)
                continue
            b = self.bekleyen.get(o.get('id')) or (self.bekleyen.get('hello') if o.get('type') == 'hello' else None)
            if b:
                b[1].append(o)
                b[0].set()
        if self.ws is ws:
            self.ws = None
            self._imlec_kapat()
            if self.durum in ('bagli', 'onay', 'baglaniyor'):
                self.durum, self.mesaj = 'kapali', 'TV bağlantısı koptu'

    def _yeni_id(self):
        with self.kilit:
            self.sayac += 1
            return 'k%d' % self.sayac

    def _istek_bekle(self, tip, uri, yuk, sure=5, kayit=False, mid=None):
        ws = self.ws
        if ws is None or (not kayit and self.durum != 'bagli'):
            return None
        mid = mid or self._yeni_id()
        olay, kutu = threading.Event(), []
        self.bekleyen[mid] = (olay, kutu)
        try:
            m = {'type': tip, 'id': mid}
            if uri:
                m['uri'] = uri
            if yuk is not None:
                m['payload'] = yuk
            ws.gonder(json.dumps(m))
            if not olay.wait(sure):
                return None
            # register akışında araya 'response' girebilir; 'registered' ya da 'error' bekle
            if tip == 'register':
                bitis = time.time() + sure
                while kutu and kutu[-1].get('type') not in ('registered', 'error') and time.time() < bitis:
                    olay.clear()
                    olay.wait(max(0.1, bitis - time.time()))
            return kutu[-1] if kutu else None
        except Exception:
            return None
        finally:
            self.bekleyen.pop(mid, None)

    def istek(self, uri, yuk=None, sure=5):
        if self.durum != 'bagli':
            self.baglan_arkada()
            return None
        c = self._istek_bekle('request', uri, yuk, sure=sure)
        if c is None and (self.ws is None or not self.ws.acik):
            self.durum = 'kapali'
            self.baglan_arkada()
        return c

    def basarili(self, c):
        return bool(c) and c.get('type') == 'response' and (c.get('payload') or {}).get('returnValue', True) is not False

    # — imleç / tuş soketi —
    def _imlec_al(self):
        if self.imlec and self.imlec.acik:
            return self.imlec
        c = self.istek('ssap://com.webos.service.networkinput/getPointerInputSocket')
        yol = ((c or {}).get('payload') or {}).get('socketPath')
        if not yol:
            return None
        denenecek = [yol]
        if yol.startswith('ws://'):
            denenecek.append(re.sub(r'^ws://([^/:]+):3000', r'wss://\1:3001', yol))
        for y in denenecek:
            try:
                w = WS(y, sure=4)
                threading.Thread(target=lambda: [None for _ in iter(w.al, None)], daemon=True).start()
                self.imlec = w
                return w
            except Exception:
                continue
        return None

    def _imlec_kapat(self):
        if self.imlec:
            self.imlec.kapat()
            self.imlec = None

    def imlec_gonder(self, satirlar):
        for _ in range(2):
            w = self._imlec_al()
            if not w:
                return False
            try:
                w.gonder('\n'.join('%s:%s' % kv for kv in satirlar) + '\n\n')
                return True
            except Exception:
                self._imlec_kapat()
        return False

    # — kumanda işlemleri —
    def tus(self, ad):
        if ad == 'GUC':
            if self.durum == 'bagli':
                c = self.istek('ssap://system/turnOff')
                if self.basarili(c):
                    self.kaydet_sonuc('GUC', True, 'system/turnOff')
                    return True
                ok = self.imlec_gonder([('type', 'button'), ('name', 'POWER')])
                self.kaydet_sonuc('GUC', ok, 'tuş POWER' if ok else '', '' if ok else ((c or {}).get('error') or 'yanıt yok'))
                return ok
            if not self.mac:
                self.mesaj = 'TV\'yi açmak için ⚙ menüsünden TV\'nin MAC adresini gir'
                return False
            ok = wake_on_lan(self.mac, self.adres)
            self.mesaj = 'Açma sinyali gönderildi, TV birkaç saniyede açılır' if ok else 'Açma sinyali gönderilemedi'
            threading.Timer(6, self.baglan_arkada).start()
            threading.Timer(15, self.baglan_arkada).start()
            return ok
        yollar = LG_TUS.get(ad)
        if not yollar:
            self.kaydet_sonuc(ad, False, '', 'bilinmeyen tuş')
            return False
        if self.durum != 'bagli':
            self.baglan_arkada()
            self.kaydet_sonuc(ad, False, '', 'TV bağlı değil')
            return False
        son_hata = ''
        for yol, deger in yollar:
            if yol == 'b':
                if self.imlec_gonder([('type', 'button'), ('name', deger)]):
                    self.kaydet_sonuc(ad, True, 'tuş ' + deger)
                    return True
                son_hata = 'tuş kanalı açılamadı'
            else:
                c = self.istek(deger, {'count': 1} if ad == 'SIL' else None)
                if self.basarili(c):
                    self.kaydet_sonuc(ad, True, deger.split('ssap://')[-1])
                    return True
                son_hata = (c or {}).get('error') or ((c or {}).get('payload') or {}).get('errorText') or 'yanıt yok'
        self.kaydet_sonuc(ad, False, '', son_hata)
        return False

    def kaydet_sonuc(self, ad, ok, yol, hata=''):
        self.sonuclar[ad] = {'ok': ok, 'yol': yol, 'hata': str(hata)[:300], 'zaman': int(time.time())}

    def yazi(self, metin):
        if self.durum != 'bagli':
            self.baglan_arkada()
            self.kaydet_sonuc('YAZI', False, '', 'TV bağlı değil')
            return False
        onde = self.on_plan()
        if onde in KENDI_KLAVYELI:
            ad = KENDI_KLAVYELI[onde]
            ek = (' YouTube videosu için bağlantıyı aşağıdaki "Bağlantı aç" kutusuna yapıştır ya da telefondaki '
                  'YouTube uygulamasında Paylaş → Kumanda de.') if onde == 'youtube.leanback.v4' else \
                 ' Yazıyı TV\'deki ekran klavyesinden ok tuşları ya da Dokunmatik sekmesiyle gir.'
            self.kaydet_sonuc('YAZI', False, '', '%s kendi ekran klavyesini kullanıyor, kumandadan yazı kabul etmiyor.%s' % (ad, ek))
            return False
        c = self.istek('ssap://com.webos.service.ime/insertText', {'text': metin, 'replace': 0})
        ok = self.basarili(c)
        if ok and self.klavye is not False:
            self.yazi_izni = True
            self.kaydet_sonuc('YAZI', True, 'ime/insertText' + (' (' + onde + ')' if onde else ''))
            return True
        if ok:   # TV kabul etti ama açık bir yazı kutusu yok: yazı bir yere düşmez
            self.kaydet_sonuc('YAZI', False, '', 'TV\'de seçili bir yazı kutusu yok, yazı ekrana düşmedi. Önce TV\'de yazı kutusunu seç (OK).')
            return False
        ham = str((c or {}).get('error') or ((c or {}).get('payload') or {}).get('errorText') or '')
        if '401' in ham or 'permission' in ham.lower() or 'denied' in ham.lower():
            self.yazi_izni = False
            neden = ('TV yazı gönderme iznini vermiyor (%s). Bunun yerine TV\'nin ekran klavyesine '
                     'Dokunmatik sekmesinden imleçle dokunabilirsin.' % ham)
        elif self.klavye is not True:
            neden = ('TV\'de bir yazı kutusu seçili değil. Önce TV\'de arama kutusunu seç (OK), '
                     'ekran klavyesi açılınca tekrar gönder.' + (' (TV: %s)' % ham if ham else ''))
        else:
            neden = 'TV yazıyı kabul etmedi: ' + ham
        self.kaydet_sonuc('YAZI', False, '', neden)
        return False

    def imlec_hareket(self, dx, dy):
        return self.imlec_gonder([('type', 'move'), ('dx', int(dx)), ('dy', int(dy)), ('down', 0)])

    def tikla(self):
        return self.imlec_gonder([('type', 'click')])

    def kaydir(self, dx, dy):
        return self.imlec_gonder([('type', 'scroll'), ('dx', int(dx)), ('dy', int(dy))])

    def uygulama(self, uid):
        c = self.istek('ssap://system.launcher/launch', {'id': uid})
        if not self.basarili(c):
            c = self.istek('ssap://com.webos.applicationManager/launch', {'id': uid})
        ok = self.basarili(c)
        self.kaydet_sonuc('UYG:' + uid, ok, 'launch' if ok else '', '' if ok else ((c or {}).get('error') or 'uygulama yüklü değil'))
        return ok

    def adres_ac(self, url):
        """Bağlantıyı TV'de açar. YouTube bağlantıları TV'deki YouTube uygulamasında açılır."""
        vid = youtube_kimligi(url)
        if vid:
            c = self.istek('ssap://system.launcher/launch',
                           {'id': 'youtube.leanback.v4', 'params': {'contentTarget': 'https://www.youtube.com/tv?v=' + vid}})
            if not self.basarili(c):   # eski yöntem
                c = self.istek('ssap://system.launcher/launch', {'id': 'youtube.leanback.v4', 'contentId': vid})
            ok = self.basarili(c)
            self.kaydet_sonuc('ADRES', ok, 'YouTube ' + vid if ok else '', '' if ok else ((c or {}).get('error') or 'YouTube açılamadı'))
            return ok
        c = self.istek('ssap://system.launcher/open', {'target': url})
        ok = self.basarili(c)
        self.kaydet_sonuc('ADRES', ok, 'tarayıcı' if ok else '', '' if ok else ((c or {}).get('error') or 'açılamadı'))
        return ok

    def on_plan(self):
        """TV'de şu an önde olan uygulama (izin verilmezse boş)."""
        p = (self.istek('ssap://com.webos.applicationManager/getForegroundAppInfo', sure=3) or {}).get('payload') or {}
        return p.get('appId') or ''


    # — TV'de şu an ne var (kumandadaki küçük ekran için) —
    def ekran_bilgisi(self):
        app = self.on_plan()
        out = {'uygulama': app, 'uygulama_adi': next((a['ad'] for a in self.uygulamalar if a['id'] == app), ''),
               'kanal': None}
        if not app or 'livetv' in app or app.startswith('com.webos.app.tv'):
            p = (self.istek('ssap://tv/getCurrentChannel', sure=3) or {}).get('payload') or {}
            if p.get('channelName'):
                out['kanal'] = {'ad': p['channelName'].strip(), 'no': str(p.get('channelNumber') or ''),
                                'akis': kanal_akisi(p['channelName'])[:2]}
        return out

    def girisler(self):
        c = self.istek('ssap://tv/getExternalInputList')
        d = ((c or {}).get('payload') or {}).get('devices') or []
        return [{'id': x.get('id'), 'ad': x.get('label') or x.get('id'), 'bagli': x.get('connected', True)} for x in d]

    def giris_sec(self, gid):
        return self.basarili(self.istek('ssap://tv/switchInput', {'inputId': gid}))

    # — kanallar —
    def kanallar(self):
        if self.durum != 'bagli':
            self.baglan_arkada()
            return {'kanallar': [], 'hata': 'TV bağlı değil'}
        c = self.istek('ssap://tv/getChannelList', sure=12)
        if not c:
            return {'kanallar': [], 'hata': 'TV kanal listesini göndermedi'}
        if c.get('type') == 'error' or (c.get('payload') or {}).get('returnValue') is False:
            hata = c.get('error') or (c.get('payload') or {}).get('errorText') or 'bilinmeyen hata'
            return {'kanallar': [], 'hata': 'TV kanal listesini vermedi: %s' % hata}
        liste = []
        for k in (c.get('payload') or {}).get('channelList') or []:
            if k.get('Invisible') or k.get('invisible'):
                continue
            kid = k.get('channelId')
            if not kid:
                continue
            logo = k.get('imgUrl') or k.get('imgUrl2') or k.get('channelLogo') or ''
            liste.append({
                'id': kid,
                'ad': (k.get('channelName') or '').strip() or ('Kanal ' + str(k.get('channelNumber', ''))),
                'no': str(k.get('channelNumber') or ''),
                'radyo': bool(k.get('Radio') or k.get('isRadio') or k.get('channelType') == 'radio'),
                'tur': k.get('channelMode') or '',
                # TV'nin kendi logosu yalnızca https ise kullanılabilir (sayfa https'ten açılıyor)
                'logo': logo if str(logo).startswith('https://') else '',
            })
        akis_hazirla()
        for k in liste:
            k['akis'] = kanal_akisi(k['ad'])
        return {'kanallar': liste, 'simdiki': self.simdiki_kanal(),
                'akis': {'hazir': bool(AKIS['veri']), 'yukleniyor': AKIS['yukleniyor'],
                         'zaman': int(AKIS['zaman']), 'hata': AKIS['hata']}}

    def simdiki_kanal(self):
        p = (self.istek('ssap://tv/getCurrentChannel') or {}).get('payload') or {}
        return p.get('channelId') or ''

    def kanal_ac(self, kid):
        return self.basarili(self.istek('ssap://tv/openChannel', {'channelId': kid}))

    def _bilgi_al(self, merhaba, sistem):
        m = (merhaba or {}).get('payload') or {}
        s = (sistem or {}).get('payload') or {}
        for k, v in (('model', s.get('modelName') or m.get('modelName')),
                     ('webos', m.get('deviceOSReleaseVersion') or m.get('deviceOSVersion')),
                     ('isletim', m.get('deviceOS')), ('alici', s.get('receiverType')),
                     ('ulke', s.get('countryGroup') or s.get('programMode'))):
            if v:
                self.bilgi[k] = str(v)

    def _klavye_olayi(self, o):
        if o.get('type') == 'error':
            hata = str(o.get('error', ''))
            if '401' in hata or 'permission' in hata.lower():
                self.yazi_izni = False
            return
        p = o.get('payload') or {}
        w = p.get('currentWidget')
        if isinstance(w, dict):
            self.klavye = bool(w.get('focus'))
        elif 'focus' in p:
            self.klavye = bool(p.get('focus'))

    def klavye_dinle(self):
        ws = self.ws
        if ws:
            try:
                ws.gonder(json.dumps({'type': 'subscribe', 'id': 'klavye',
                                      'uri': 'ssap://com.webos.service.ime/registerRemoteKeyboard'}))
            except Exception:
                pass

    def _bagli_sonrasi(self):
        self.klavye, self.yazi_izni = None, None
        self.klavye_dinle()
        # Eşleşmeden sonra izin verilirse daha ayrıntılı bilgi gelebilir
        s = (self.istek('ssap://system/getSystemInfo') or {}).get('payload') or {}
        if s.get('modelName'):
            self.bilgi['model'] = s['modelName']
        y = (self.istek('ssap://com.webos.service.update/getCurrentSWInformation') or {}).get('payload') or {}
        if y.get('product_name'):
            self.bilgi['urun'] = y['product_name']
        if y.get('major_ver'):
            self.bilgi['surum'] = '%s.%s' % (y.get('major_ver'), y.get('minor_ver', ''))
        self.uygulamalari_al()

    def uygulamalari_al(self):
        p = (self.istek('ssap://com.webos.applicationManager/listLaunchPoints', sure=8) or {}).get('payload') or {}
        liste = []
        for a in p.get('launchPoints') or []:
            if not a.get('id') or a.get('id') in ('com.webos.app.discovery',):
                continue
            adaylar = [u for u in (a.get('largeIcon'), a.get('icon'), a.get('mediumLargeIcon'), a.get('iconColor')) if isinstance(u, str) and u]
            renk = a.get('bgColor') or ''
            liste.append({'id': a['id'], 'ad': a.get('title') or a['id'], 'ikonlar': adaylar,
                          'ikon': adaylar[0] if adaylar else '',
                          'renk': renk if re.match(r'^#[0-9a-fA-F]{3,8}$', str(renk)) else ''})
        if liste:
            self.uygulamalar = liste
        return self.uygulamalar

    def ikon_getir(self, uid):
        """Uygulama ikonunu TV'den alır ve saklar.
        Sayfa https'ten açıldığı için TV'nin http adresindeki ikonu doğrudan yükleyemez; köprü aktarır."""
        import urllib.request, hashlib
        if uid in IKON_BELLEK:
            return IKON_BELLEK[uid]
        dosya = os.path.join(IKON_KLASORU, hashlib.sha1(uid.encode()).hexdigest() + '.img')
        try:
            with open(dosya, 'rb') as f:
                veri = f.read()
            if veri:
                IKON_BELLEK[uid] = (veri, _resim_turu(veri))
                return IKON_BELLEK[uid]
        except OSError:
            pass
        a = next((x for x in self.uygulamalar if x['id'] == uid), None)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE          # TV kendi imzaladığı sertifikayı kullanır
        for url in (a or {}).get('ikonlar') or ([a['ikon']] if a and a.get('ikon') else []):
            u = urlparse(url)
            if u.scheme not in ('http', 'https'):
                continue
            # TV bazen kendi adresini localhost olarak yazıyor; yalnızca TV'nin kendisine gidilir
            if u.hostname in ('localhost', '127.0.0.1', '::1'):
                url = url.replace(u.netloc, self.adres + (':%d' % u.port if u.port else ''), 1)
            elif u.hostname != self.adres:
                continue
            for _ in range(2):
                try:
                    with urllib.request.urlopen(url, timeout=8, context=tls) as y:
                        veri = y.read(2 * 1024 * 1024)
                    tur = _resim_turu(veri)
                    if tur:
                        IKON_BELLEK[uid] = (veri, tur)
                        try:
                            os.makedirs(IKON_KLASORU, exist_ok=True)
                            with open(dosya, 'wb') as f:
                                f.write(veri)
                        except OSError:
                            pass
                        return veri, tur
                    break        # resim değil, sonraki adaya geç
                except Exception:
                    time.sleep(0.4)
        return None, None

    def _mac_ogren(self):
        if self.mac:
            return
        for uri in ('ssap://com.webos.service.connectionmanager/getinfo',
                    'ssap://com.webos.service.connectionmanager/wifi/getinfo'):
            p = (self.istek(uri) or {}).get('payload') or {}
            for k in ('wifiInfo', 'wiredInfo'):
                m = mac_duzelt((p.get(k) or {}).get('macAddress'))
                if m:
                    self.mac = m
                    cihaz_kaydet(self)
                    return
        # son çare: ARP tablosu (Android'de çoğu zaman erişilemez)
        try:
            for satir in open('/proc/net/arp'):
                if satir.startswith(self.adres + ' '):
                    m = mac_duzelt(satir.split()[3])
                    if m and m != '00:00:00:00:00:00':
                        self.mac = m
                        cihaz_kaydet(self)
                        return
        except Exception:
            pass

# ───────────────────────── yayın akışı (EPG) ─────────────────────────
# İki açık kaynak birleştirilir; biri bozulursa diğeri kullanılır.
#  • Digiturk akışı: 7 günlük, günde birkaç kez güncellenir (github.com/esinik/tv-epg)
#  • Türksat akışı: yalnız o gün, her gece güncellenir (github.com/necmikarasucum-eng/iptv-epg-turkey)
AKIS_KAYNAKLARI = [
    ('json', 'https://raw.githubusercontent.com/esinik/tv-epg/main/data/digiturk.json'),
    ('xmlgz', 'https://raw.githubusercontent.com/necmikarasucum-eng/iptv-epg-turkey/main/epg/turksat_epg.xml.gz'),
]
AKIS_DOSYASI = os.path.expanduser('~/.kumanda_akis.json')
AKIS_YENILEME = 4 * 3600
AKIS = {'veri': {}, 'zaman': 0, 'yukleniyor': False, 'hata': ''}
akis_kilit = threading.Lock()

_TR_AKIS = str.maketrans('çğıöşüâîûÇĞİÖŞÜÂÎÛ', 'cgiosuaiuCGIOSUAIU')

def kanal_anahtari(ad):
    """Kumanda sayfasındaki logoAnahtar() ile aynı sadeleştirme."""
    import unicodedata
    s = str(ad or '').translate(_TR_AKIS).lower()
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    s = re.sub(r'[^a-z0-9]', '', s)
    while True:
        y = re.sub(r'(fhd|uhd|hd|sd)$', '', s)
        if y == s or not y:
            break
        s = y
    return s

# Kaynaklarla TV'nin kanal adları farklı yazıldığında
AKIS_TAKMA = {'nationalgeo': 'nationalgeographic', 'natgeowild': 'nationalgeographicwild',
              'number1': 'nr1', 'number1tv': 'nr1', 'trt3trtspor': 'trtspor', 'trt3spor': 'trtspor',
              'benguturktv': 'benguturk', 'nickelodeonsd': 'nickelodeon',
              'haberturktv': 'haberturk', 'szc': 'sozcutv', 'sozcu': 'sozcutv'}

def _akis_anahtarlari(ad):
    k = kanal_anahtari(ad)
    k = AKIS_TAKMA.get(k, k)
    out = [k]
    if k.endswith('tv') and len(k) > 3:
        out.append(k[:-2])
    else:
        out.append(k + 'tv')
    return out

def _indir(url, sure=40):
    import urllib.request, gzip
    istek = urllib.request.Request(url, headers={'User-Agent': 'kumanda/1.0'})
    with urllib.request.urlopen(istek, timeout=sure) as y:
        veri = y.read(60 * 1024 * 1024)
    if url.endswith('.gz') or veri[:2] == b'\x1f\x8b':
        veri = gzip.decompress(veri)
    return veri

def _akis_json(veri):
    from datetime import datetime
    d = json.loads(veri)
    adlar = {c.get('id'): c.get('name') for c in d.get('channels') or []}
    sonuc = {}
    for p in d.get('programmes') or []:
        ad = adlar.get(p.get('channel'))
        try:
            b = datetime.fromisoformat(str(p['start']).replace('Z', '+00:00')).timestamp()
            e = datetime.fromisoformat(str(p['stop']).replace('Z', '+00:00')).timestamp()
        except Exception:
            continue
        if ad and p.get('title') and e > b:
            sonuc.setdefault(ad, []).append((int(b), int(e), str(p['title']).strip()))
    return sonuc

def _akis_xml(veri):
    import xml.etree.ElementTree as ET
    from datetime import datetime
    kok = ET.fromstring(veri)
    adlar = {c.get('id'): (c.findtext('display-name') or '').strip() for c in kok.iter('channel')}
    sonuc = {}
    for p in kok.iter('programme'):
        ad = adlar.get(p.get('channel'))
        baslik = (p.findtext('title') or '').strip()
        try:
            b = datetime.strptime(p.get('start', ''), '%Y%m%d%H%M%S %z').timestamp()
            e = datetime.strptime(p.get('stop', ''), '%Y%m%d%H%M%S %z').timestamp()
        except Exception:
            continue
        if ad and baslik and baslik != '-' and e > b:
            sonuc.setdefault(ad, []).append((int(b), int(e), baslik))
    return sonuc

def akis_guncelle():
    with akis_kilit:
        if AKIS['yukleniyor']:
            return
        AKIS['yukleniyor'] = True
    try:
        birlesik, hatalar, adlar = {}, [], {}
        for tur, url in AKIS_KAYNAKLARI:
            try:
                veri = _indir(url)
                parca = _akis_json(veri) if tur == 'json' else _akis_xml(veri)
            except Exception as e:
                hatalar.append('%s: %s' % (url.rsplit('/', 1)[-1], e))
                continue
            for ad, liste in parca.items():
                anahtar = _akis_anahtarlari(ad)[0]
                adlar.setdefault(anahtar, ad)
                mevcut = birlesik.setdefault(anahtar, [])
                # Önceki (öncelikli) kaynakta o saat dilimi zaten varsa ekleme
                for p in liste:
                    if not any(m[0] < p[1] and p[0] < m[1] for m in mevcut):
                        mevcut.append(p)
        if not birlesik:
            AKIS['hata'] = 'Yayın akışı indirilemedi (%s)' % '; '.join(hatalar)
            return
        for liste in birlesik.values():
            liste.sort()
        AKIS.update(veri=birlesik, adlar=adlar, zaman=time.time(), hata='; '.join(hatalar))
        try:
            with open(AKIS_DOSYASI + '.tmp', 'w', encoding='utf-8') as f:
                json.dump({'zaman': AKIS['zaman'], 'veri': birlesik, 'adlar': adlar}, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(AKIS_DOSYASI + '.tmp', AKIS_DOSYASI)
        except Exception:
            pass
    finally:
        AKIS['yukleniyor'] = False

def akis_hazirla():
    """Kayıtlı akışı yükler; eskiyse arkada yeniler."""
    if not AKIS['veri']:
        try:
            with open(AKIS_DOSYASI, encoding='utf-8') as f:
                k = json.load(f)
            AKIS['veri'] = {a: [tuple(p) for p in l] for a, l in k.get('veri', {}).items()}
            AKIS['zaman'] = k.get('zaman', 0)
            AKIS['adlar'] = k.get('adlar') or {}
        except Exception:
            pass
    if time.time() - AKIS['zaman'] > AKIS_YENILEME and not AKIS['yukleniyor']:
        threading.Thread(target=akis_guncelle, daemon=True).start()

def kanal_akisi(ad, simdi=None, ileri=24 * 3600):
    """Kanalın şu an ve önümüzdeki 24 saatteki programları: [[başlangıç, bitiş, başlık], ...]"""
    simdi = simdi or time.time()
    for k in _akis_anahtarlari(ad):
        liste = AKIS['veri'].get(k)
        if liste:
            return [list(p) for p in liste if p[1] > simdi and p[0] < simdi + ileri][:80]
    return []

# ───────────────────────── rehber: şimdi ne var, arama, maçlar, hatırlatmalar, izleme süresi ─────────────────────────
def _sade(s):
    return re.sub(r'\s+', ' ', str(s or '').translate(_TR_AKIS).lower()).strip()

def akis_adi(anahtar):
    return AKIS.get('adlar', {}).get(anahtar) or anahtar

def simdi_yayinda(simdi=None):
    """Her kanalda şu an ve sıradaki program."""
    akis_hazirla()
    simdi = simdi or time.time()
    out = []
    for anahtar, liste in AKIS['veri'].items():
        su = next((p for p in liste if p[0] <= simdi < p[1]), None)
        sonra = next((p for p in liste if p[0] >= simdi), None)
        if su or sonra:
            out.append({'kanal': akis_adi(anahtar), 'anahtar': anahtar,
                        'simdi': list(su[:3]) if su else None, 'sonra': list(sonra[:3]) if sonra else None})
    return out

def akis_ara(q, simdi=None, sinir=150):
    """Önümüzdeki günlerin yayın akışında program adı ya da kanal adıyla arama."""
    akis_hazirla()
    simdi = simdi or time.time()
    kelimeler = [k for k in _sade(q).split(' ') if k]
    if not kelimeler:
        return []
    out = []
    for anahtar, liste in AKIS['veri'].items():
        kanal = akis_adi(anahtar)
        sk = _sade(kanal)
        for p in liste:
            if p[1] <= simdi:
                continue
            metin = _sade(p[2]) + ' ' + sk
            if all(k in metin for k in kelimeler):
                out.append({'kanal': kanal, 'anahtar': anahtar, 'bas': p[0], 'bit': p[1], 'baslik': p[2]})
    out.sort(key=lambda x: (x['bas'], x['kanal']))
    return out[:sinir]

_MAC_DISLA = re.compile(r'ozet|goller|arsiv|bolum|mac onu|mac sonu|basliyor|rapor|preview|haber|magazin|netbusters|'
                        r'show|moments|garaj|trio|taraftar|muhabir|gamer|squad|yildiz 10 gol|national pride|chasing')
_SPOR_KANALI = re.compile(r'spor|sport|exxen')
_TAKIM = re.compile(r'spor\b|\b(fk|sk|basket|galatasaray|fenerbahce|besiktas|trabzonspor|belediyespor|united|city|'
                    r'real madrid|barcelona|bayern|juventus|milan|inter|arsenal|chelsea|liverpool)\b')

def mac_mi(baslik, kanal):
    """Program bir maç (canlı karşılaşma) mı? Yayın akışlarında tür bilgisi yok; başlıktan anlaşılır."""
    b, k = _sade(baslik), _sade(kanal)
    if _MAC_DISLA.search(b) or re.search(r'\bbant\b', b):
        return None
    if 'formula 1' in b and re.search(r'yaris|siralama|sprint', b):
        return {'lig': 'Formula 1', 'ad': re.sub(r'\s*/\s*canli\s*$', '', baslik, flags=re.I).strip()}
    gov = re.sub(r'\s*[-/]\s*canli\s*-?\s*$', '', baslik, flags=re.I).strip()
    lig = ''
    m = re.match(r'^(.*?(?:hafta|week))\s+(.+)$', gov, flags=re.I)
    if m:
        lig, gov = re.sub(r'\s*\(\d\d-\d\d\)', '', m.group(1)).strip(), m.group(2).strip()
    t = re.split(r'\s+-\s+|\s*-\s*(?=[A-ZÇĞİÖŞÜ])', gov)
    if len(t) < 2 or not all(re.search(r'[A-Za-zÇĞİÖŞÜçğıöşü]{2}', x) for x in t[:2]) or len(t[0]) > 40 or len(t[1]) > 40:
        return None
    if not (_SPOR_KANALI.search(k) or _TAKIM.search(b) or lig):
        return None
    takimlar = [t[0].strip(), t[1].strip()]
    if all(x.isupper() for x in takimlar):
        takimlar = [' '.join(w.upper() if w in ('fk', 'sk', 'ac', 'fc') else w.capitalize() for w in x.lower().split(' ')) for x in takimlar]
    anahtar = '-'.join(re.sub(r'[^a-z]', '', re.sub(r'\b(fk|sk|fc|ac|as|bsb|bld|b)\b', '', _sade(x)))[:6] for x in takimlar)
    return {'lig': lig.title() if lig.isupper() else lig, 'ad': ' - '.join(takimlar), 'anahtar': anahtar}

def maclar(simdi=None, gun=7):
    akis_hazirla()
    simdi = simdi or time.time()
    gruplar = {}
    for anahtar, liste in AKIS['veri'].items():
        kanal = akis_adi(anahtar)
        for p in liste:
            if p[1] <= simdi or p[0] > simdi + gun * 86400:
                continue
            m = mac_mi(p[2], kanal)
            if not m:
                continue
            g = gruplar.setdefault((m.get('anahtar') or _sade(m['ad']), p[0] // 600), {'ad': m['ad'], 'lig': m['lig'], 'bas': p[0], 'bit': p[1],
                                                                     'kanallar': [], 'baslik': p[2]})
            if kanal not in g['kanallar']:
                g['kanallar'].append(kanal)
    return sorted(gruplar.values(), key=lambda g: g['bas'])

# — hatırlatmalar: program başlamadan 5 dk önce TV'de bildirim, telefonda bildirim (Termux:API varsa), kumanda sayfasında uyarı —
HATIRLATMA_ONCE = 5 * 60
BILDIRIMLER = []          # kumanda sayfasının göstereceği son bildirimler
hatirlatma_kilit = threading.Lock()

def hatirlatmalar():
    l = AYAR.get('hatirlatmalar')
    return l if isinstance(l, list) else []

def hatirlatma_ekle(kanal, baslik, bas, bit):
    try:
        bas, bit = int(float(bas)), int(float(bit or 0))
    except (TypeError, ValueError):
        return None, 'Saat geçersiz'
    if bit and bit < time.time():
        return None, 'Bu program bitmiş'
    kanal, baslik = str(kanal or '')[:60], str(baslik or '')[:160]
    with hatirlatma_kilit:
        l = [h for h in hatirlatmalar() if h.get('bit', h['bas']) > time.time() - 3600]
        hid = hashlib.md5(('%s|%s|%d' % (kanal, baslik, bas)).encode()).hexdigest()[:10]
        if not any(h['id'] == hid for h in l):
            l.append({'id': hid, 'kanal': kanal, 'baslik': baslik, 'bas': bas, 'bit': bit, 'yapildi': False})
        l.sort(key=lambda h: h['bas'])
        AYAR['hatirlatmalar'] = l
        ayar_yaz(AYAR)
    return hid, None

def hatirlatma_sil(hid):
    with hatirlatma_kilit:
        AYAR['hatirlatmalar'] = [h for h in hatirlatmalar() if h.get('id') != hid]
        ayar_yaz(AYAR)

TERMUX_BILDIRIM = None

def _telefon_bildirimi(baslik, metin, hid):
    global TERMUX_BILDIRIM
    if TERMUX_BILDIRIM is None:
        import shutil
        TERMUX_BILDIRIM = bool(shutil.which('termux-notification'))
    if not TERMUX_BILDIRIM:
        return False
    try:
        subprocess.run(['termux-notification', '--id', 'kumanda-' + hid, '--title', baslik, '--content', metin,
                        '--priority', 'high', '--sound', '--vibrate', '300,150,300',
                        '--action', 'am start -a android.intent.action.VIEW -d http://127.0.0.1:%d/' % PORT],
                       timeout=10, capture_output=True)
        return True
    except Exception:
        return False

def _tv_bildirimi(metin):
    for c in list(CIHAZLAR.values()):
        if isinstance(c, LGTV) and c.durum == 'bagli':
            try:
                c.istek('ssap://system.notifications/createToast', {'message': metin[:60]}, sure=4)
            except Exception:
                pass

def hatirlatma_dongusu():
    while True:
        time.sleep(20)
        try:
            simdi = time.time()
            degisti = False
            for h in hatirlatmalar():
                if h.get('yapildi') or h['bas'] - HATIRLATMA_ONCE > simdi:
                    continue
                if (h.get('bit') or h['bas'] + 3600) < simdi:
                    h['yapildi'] = True
                    degisti = True
                    continue
                kalan = int(round((h['bas'] - simdi) / 60))
                ne_zaman = ('%d dakika sonra' % kalan) if kalan > 0 else 'başladı'
                metin = '%s %s · %s' % (h['baslik'], ne_zaman, h['kanal'])
                _tv_bildirimi('🔔 ' + metin)
                tel = _telefon_bildirimi('🔔 ' + h['baslik'], '%s, %s' % (ne_zaman.capitalize(), h['kanal']), h['id'])
                BILDIRIMLER.append({'id': h['id'], 'zaman': int(simdi), 'baslik': h['baslik'], 'kanal': h['kanal'],
                                    'bas': h['bas'], 'metin': metin, 'telefon': tel})
                del BILDIRIMLER[:-20]
                h['yapildi'] = True
                degisti = True
            if degisti:
                with hatirlatma_kilit:
                    AYAR['hatirlatmalar'] = [h for h in hatirlatmalar() if (h.get('bit') or h['bas'] + 3600) > simdi - 6 * 3600]
                    ayar_yaz(AYAR)
        except Exception:
            pass

# — izleme süresi: TV açıkken her 30 sn hangi kanal/uygulamada olduğu kaydedilir —
IZLEME_DOSYASI = os.path.expanduser('~/.kumanda_izleme.json')
IZLEME_ARALIK = 30
izleme_kilit = threading.Lock()

def izleme_oku():
    try:
        with open(IZLEME_DOSYASI, encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def izleme_dongusu():
    while True:
        time.sleep(IZLEME_ARALIK)
        try:
            gun = time.strftime('%Y-%m-%d')
            eklenen = []
            for c in list(CIHAZLAR.values()):
                if c.tur == 'adb' or c.durum != 'bagli':
                    continue
                if c.tur == 'androidtv' and c.bilgi.get('ekran') == 'bekleme':
                    continue
                try:
                    b = ekran_bilgisi(c)
                except Exception:
                    b = {}
                k = b.get('kanal')
                ne = (k['ad'] if k else '') or b.get('uygulama_adi') or b.get('uygulama') or 'Diğer'
                ne = re.sub(r'\s+(HD|FHD|UHD|SD)$', '', str(ne).strip(), flags=re.I)[:40]
                eklenen.append((c.id, c.ad, ne))
            if not eklenen:
                continue
            with izleme_kilit:
                d = izleme_oku()
                g = d.setdefault(gun, {})
                for cid, ad, ne in eklenen:
                    x = g.setdefault(cid, {'ad': ad, 'toplam': 0, 'ne': {}})
                    x['ad'] = ad
                    x['toplam'] += IZLEME_ARALIK
                    x['ne'][ne] = x['ne'].get(ne, 0) + IZLEME_ARALIK
                for eski in sorted(d)[:-35]:
                    del d[eski]
                with open(IZLEME_DOSYASI + '.tmp', 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
                os.replace(IZLEME_DOSYASI + '.tmp', IZLEME_DOSYASI)
        except Exception:
            pass

def izleme_ozeti(gun_sayisi=7):
    with izleme_kilit:
        d = izleme_oku()
    bugun = time.strftime('%Y-%m-%d')
    gunler = []
    for i in range(gun_sayisi - 1, -1, -1):
        t = time.strftime('%Y-%m-%d', time.localtime(time.time() - i * 86400))
        g = d.get(t, {})
        gunler.append({'tarih': t, 'toplam': sum(x.get('toplam', 0) for x in g.values()),
                       'cihazlar': {cid: {'ad': x.get('ad', cid), 'toplam': x.get('toplam', 0)} for cid, x in g.items()}})
    def en_cok(g):
        ne = {}
        for x in g.values():
            for a, s in x.get('ne', {}).items():
                ne[a] = ne.get(a, 0) + s
        return sorted(([a, s] for a, s in ne.items()), key=lambda x: -x[1])[:8]
    hafta = {}
    for t in [g['tarih'] for g in gunler]:
        for cid, x in d.get(t, {}).items():
            hafta.setdefault(cid, {'ne': {}})
            for a, s in x.get('ne', {}).items():
                hafta[cid]['ne'][a] = hafta[cid]['ne'].get(a, 0) + s
    return {'bugun': bugun, 'gunler': gunler, 'bugun_en_cok': en_cok(d.get(bugun, {})), 'hafta_en_cok': en_cok(hafta),
            'aralik': IZLEME_ARALIK}

# ───────────────────────── ADB (Android projeksiyon / TV) ─────────────────────────
ADB_TUS = {
    'YUKARI': 19, 'ASAGI': 20, 'SOL': 21, 'SAG': 22, 'TAMAM': 23, 'GERI': 4, 'ANA': 3, 'MENU': 82,
    'AYARLAR': 176, 'SES+': 24, 'SES-': 25, 'SESSIZ': 164, 'KANAL+': 166, 'KANAL-': 167,
    'OYNAT_DURAKLAT': 85, 'OYNAT': 126, 'DURAKLAT': 127, 'DURDUR': 86, 'GERI_SAR': 89, 'ILERI_SAR': 90,
    'SON': 187, 'SIL': 67, 'ENTER': 66, 'GUC': 26, 'BILGI': 165, 'CIKIS': 4,
    'KIRMIZI': 183, 'YESIL': 184, 'SARI': 185, 'MAVI': 186,
    'KAYNAK': 178, 'REHBER': 172, 'ALTYAZI': 175, 'SES_DILI': 222, 'UYGULAMALAR': 284, 'CANLI': 170,
    'YILDIZ': 17, 'LISTE': 172,
}
for _r in '0123456789':
    ADB_TUS[_r] = 7 + int(_r)

TR = str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')

def adb_var():
    try:
        subprocess.run(['adb', 'version'], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

class ADBCihaz:
    tur = 'adb'

    def __init__(self, cid, ad, adres):
        self.id, self.ad, self.adres = cid, ad, adres
        self.kabuk = None
        self.kilit = threading.Lock()
        self.ekran = [1920, 1080]
        self.durum, self.mesaj = 'kapali', ''
        self._son_kontrol = 0
        self.sonuclar = {}

    def ozet(self):
        if time.time() - self._son_kontrol > 3:
            self._son_kontrol = time.time()
            self.durum = 'bagli' if self.bagli_mi() else 'kapali'
            if self.durum == 'kapali' and not self.mesaj:
                self.mesaj = 'Projeksiyon kapalı ya da ADB izni verilmedi'
            if self.durum == 'bagli':
                self.mesaj = ''
        return {'id': self.id, 'tur': self.tur, 'ad': self.ad, 'adres': self.adres,
                'durum': self.durum, 'mesaj': self.mesaj}

    def sargs(self):
        return ['adb', '-s', self.adres]

    def baglan_arkada(self):
        threading.Thread(target=self.baglan, daemon=True).start()

    def baglan(self):
        try:
            r = subprocess.run(['adb', 'connect', self.adres], capture_output=True, text=True, timeout=10)
            self.mesaj = (r.stdout + r.stderr).strip()
            self.ekran_boyutu()
            self._son_kontrol = 0
        except FileNotFoundError:
            self.mesaj = 'adb bulunamadı — Termux\'ta: pkg install android-tools'
        except Exception as e:
            self.mesaj = str(e)

    def bagli_mi(self):
        try:
            r = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=5)
        except Exception:
            return False
        for l in r.stdout.splitlines()[1:]:
            p = l.split('\t')
            if len(p) == 2 and p[0] == self.adres and p[1] == 'device':
                return True
        return False

    def ekran_boyutu(self):
        try:
            r = subprocess.run(self.sargs() + ['shell', 'wm', 'size'], capture_output=True, text=True, timeout=6)
            m = re.findall(r'(\d+)x(\d+)', r.stdout)
            if m:
                self.ekran = [int(m[-1][0]), int(m[-1][1])]
        except Exception:
            pass

    def _kabuk_al(self):
        if self.kabuk is None or self.kabuk.poll() is not None:
            self.kabuk = subprocess.Popen(self.sargs() + ['shell'], stdin=subprocess.PIPE,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        return self.kabuk

    def gonder(self, satir):
        with self.kilit:
            for _ in range(2):
                try:
                    k = self._kabuk_al()
                    k.stdin.write(satir + '\n')
                    k.stdin.flush()
                    return True
                except Exception:
                    self.kabuk = None
                    self.baglan()
            return False

    def tus(self, ad):
        k = ADB_TUS.get(ad)
        ok = self.gonder('input keyevent %d' % k) if k is not None else False
        self.sonuclar[ad] = {'ok': ok, 'yol': ('keyevent %d' % k) if ok else '', 'hata': '' if ok else ('bilinmeyen tuş' if k is None else 'adb bağlı değil'), 'zaman': int(time.time())}
        return ok

    def yazi(self, t):
        t = t.translate(TR)
        t = ''.join(c for c in t if 32 <= ord(c) < 127).replace('%', '').replace(' ', '%s')
        t = t.replace("'", "'\\''")
        return self.gonder("input text '" + t + "'") if t else False

    def dokun(self, x, y):
        return self.gonder('input tap %d %d' % (x * self.ekran[0], y * self.ekran[1]))

    def kaydir_mutlak(self, x1, y1, x2, y2, ms):
        e = self.ekran
        return self.gonder('input swipe %d %d %d %d %d' % (x1 * e[0], y1 * e[1], x2 * e[0], y2 * e[1], ms))

    def uygulama(self, paket):
        return self.gonder('monkey -p %s -c android.intent.category.LAUNCHER 1' % re.sub(r'[^\w.]', '', paket))

    def adres_ac(self, url):
        if not re.match(r'^https?://[\w\-./?=&%#:~+]+$', url):
            return False
        return self.gonder("am start -a android.intent.action.VIEW -d '%s'" % url)

# ───────────────────────── Toshiba TV'ler ─────────────────────────
# Toshiba TV'ler (Vestel üretimi) üç farklı işletim sistemiyle satılıyor; her birinin kontrol yolu ayrı:
#   • Google TV / Android TV  → Android TV Remote v2: Google TV uygulamasının kullandığı yol (6466/6467),
#                                TV'de çıkan 6 haneli kodla eşleşir. (Protokol: tronikos/androidtvremote2, Apache-2.0)
#   • VIDAA                   → TV'nin MQTT sunucusu (36669), TV'de çıkan 4 haneli kodla eşleşir.
#                                (Protokol: tombabolewski/vidaa-control, MIT)
#   • Smart Center (Vestel)   → DIAL sanal kumandası (56789) + durum kanalı (1986).
#                                (Protokol: mutlutekir/ha-vestel-toshiba-tv, T3m3z/pyvesteltv, MIT)
# Hepsi yalnızca Python'un kendi kütüphaneleriyle yazıldı; telefona ek paket gerekmez.
import hashlib, math, secrets

SISTEM_ADI = {'lg': 'LG webOS', 'adb': 'Android (ADB)', 'androidtv': 'Google TV / Android TV',
              'vidaa': 'VIDAA', 'smartcenter': 'Smart Center'}

# ── küçük protobuf kodlayıcı/çözücü ──
def _pb_vint(n):
    if n < 0:
        n += 1 << 64
    b = bytearray()
    while True:
        x, n = n & 0x7F, n >> 7
        if n:
            b.append(x | 0x80)
        else:
            b.append(x)
            return bytes(b)

def pb(*alanlar):
    """pb((1, 5), (2, 'yazı'), (3, [(1, 2)]))  → alan numarası, değer (int / str / bytes / iç içe liste)"""
    out = bytearray()
    for no, v in alanlar:
        if v is None:
            continue
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, int):
            out += _pb_vint(no << 3) + _pb_vint(v)
            continue
        if isinstance(v, str):
            v = v.encode('utf-8')
        elif isinstance(v, (list, tuple)):
            v = pb(*v)
        out += _pb_vint(no << 3 | 2) + _pb_vint(len(v)) + v
    return bytes(out)

def _pb_vint_oku(b, i):
    n = k = 0
    while True:
        if i >= len(b) or k > 63:
            raise ValueError('bozuk protobuf')
        x = b[i]
        i += 1
        n |= (x & 0x7F) << k
        k += 7
        if not x & 0x80:
            return n, i

def pb_coz(b):
    d, i = {}, 0
    while i < len(b):
        anahtar, i = _pb_vint_oku(b, i)
        no, tip = anahtar >> 3, anahtar & 7
        if tip == 0:
            v, i = _pb_vint_oku(b, i)
        elif tip == 2:
            n, i = _pb_vint_oku(b, i)
            v, i = bytes(b[i:i + n]), i + n
        elif tip == 5:
            v, i = bytes(b[i:i + 4]), i + 4
        elif tip == 1:
            v, i = bytes(b[i:i + 8]), i + 8
        else:
            raise ValueError('bozuk protobuf')
        d.setdefault(no, []).append(v)
    return d

def pb_al(d, no, vars=None):
    return (d.get(no) or [vars])[0]

def pb_soketten(s):
    """Uzunluğu başta varint olarak yazılmış bir mesaj okur."""
    n = k = 0
    while True:
        c = s.recv(1)
        if not c:
            raise ConnectionError('bağlantı kapandı')
        n |= (c[0] & 0x7F) << k
        k += 7
        if not c[0] & 0x80:
            break
        if k > 35:
            raise ConnectionError('bozuk mesaj')
    v = b''
    while len(v) < n:
        p = s.recv(n - len(v))
        if not p:
            raise ConnectionError('bağlantı kapandı')
        v += p
    return v

# ── kendinden imzalı RSA sertifikası (Google TV eşleşmesi için; ek kütüphane gerekmez) ──
_KUCUK_ASALLAR = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** .5) + 1, 2))]

def _asal_mi(n, tur=20):
    for p in _KUCUK_ASALLAR:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(tur):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True

def _asal_uret(bit):
    while True:
        n = secrets.randbits(bit) | (3 << (bit - 2)) | 1
        if _asal_mi(n):
            return n

def _der(etiket, icerik):
    n = len(icerik)
    if n < 128:
        uz = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        uz = bytes([0x80 | len(b)]) + b
    return bytes([etiket]) + uz + icerik

def _der_int(x):
    return _der(0x02, x.to_bytes(max(1, (x.bit_length() + 8) // 8), 'big'))

def _der_seq(*p):
    return _der(0x30, b''.join(p))

def _der_parcalar(b):
    i, out = 0, []
    while i < len(b):
        t, n = b[i], b[i + 1]
        i += 2
        if n & 0x80:
            k = n & 0x7F
            n = int.from_bytes(b[i:i + k], 'big')
            i += k
        out.append((t, b[i:i + n]))
        i += n
    return out

def _bayt(x):
    return x.to_bytes((x.bit_length() + 7) // 8, 'big')

def _pem(bas, v):
    t = base64.b64encode(v).decode()
    return '-----BEGIN %s-----\n%s\n-----END %s-----\n' % (bas, '\n'.join(t[i:i + 64] for i in range(0, len(t), 64)), bas)

def _pem_der(metin):
    m = re.search(r'-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----', metin, re.S)
    return base64.b64decode(''.join(m.group(1).split())) if m else b''

def sertifika_uret(ad='Kumanda'):
    from datetime import datetime, timedelta, timezone
    e = 65537
    while True:
        p, q = _asal_uret(1024), _asal_uret(1024)
        n, phi = p * q, (p - 1) * (q - 1)
        if p != q and n.bit_length() == 2048 and math.gcd(e, phi) == 1:
            break
    d = pow(e, -1, phi)
    oid_rsa, oid_imza = bytes.fromhex('06092a864886f70d010101'), bytes.fromhex('06092a864886f70d01010b')
    oid_sha256, oid_cn, bos = bytes.fromhex('0609608648016503040201'), bytes.fromhex('0603550403'), b'\x05\x00'
    isim = _der_seq(_der(0x31, _der_seq(oid_cn, _der(0x0C, ad.encode()))))
    bas = datetime.now(timezone.utc) - timedelta(days=2)
    zaman = lambda t: _der(0x17, t.strftime('%y%m%d%H%M%SZ').encode())
    imza_alg = _der_seq(oid_imza, bos)
    tbs = _der_seq(_der(0xA0, _der_int(2)), _der_int(secrets.randbits(62) + 1), imza_alg, isim,
                   _der_seq(zaman(bas), zaman(bas + timedelta(days=3650))), isim,
                   _der_seq(_der_seq(oid_rsa, bos), _der(0x03, b'\x00' + _der_seq(_der_int(n), _der_int(e)))))
    ozet = _der_seq(_der_seq(oid_sha256, bos), _der(0x04, hashlib.sha256(tbs).digest()))
    em = b'\x00\x01' + b'\xff' * (256 - 3 - len(ozet)) + b'\x00' + ozet
    imza = pow(int.from_bytes(em, 'big'), d, n).to_bytes(256, 'big')
    sert = _der_seq(tbs, imza_alg, _der(0x03, b'\x00' + imza))
    anahtar = _der_seq(*(_der_int(x) for x in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    return _pem('CERTIFICATE', sert), _pem('RSA PRIVATE KEY', anahtar)

def sertifika_rsa(der):
    """X.509 sertifikasından (DER) RSA modülü ve üssü."""
    sert = _der_parcalar(der)[0][1]
    alanlar = _der_parcalar(_der_parcalar(sert)[0][1])
    if alanlar[0][0] == 0xA0:
        alanlar = alanlar[1:]
    bit = _der_parcalar(alanlar[5][1])[1][1][1:]
    n, e = _der_parcalar(_der_parcalar(bit)[0][1])
    return int.from_bytes(n[1], 'big'), int.from_bytes(e[1], 'big')

ATV_KLASOR = os.path.expanduser('~/.kumanda_atv')
ATV_SERT, ATV_ANAHTAR = os.path.join(ATV_KLASOR, 'istemci.pem'), os.path.join(ATV_KLASOR, 'istemci.key')
atv_sert_kilit = threading.Lock()

def atv_sertifika():
    with atv_sert_kilit:
        if not (os.path.exists(ATV_SERT) and os.path.exists(ATV_ANAHTAR)):
            os.makedirs(ATV_KLASOR, exist_ok=True)
            s, a = sertifika_uret()
            with open(ATV_ANAHTAR, 'w') as f:
                f.write(a)
            try:
                os.chmod(ATV_ANAHTAR, 0o600)
            except Exception:
                pass
            with open(ATV_SERT, 'w') as f:
                f.write(s)
    return ATV_SERT, ATV_ANAHTAR

def _tls_istemci(adres, port, sert=None, anahtar=None, sure=5):
    b = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    b.check_hostname = False
    b.verify_mode = ssl.CERT_NONE     # TV'ler kendi imzaladığı sertifikayı kullanır; yalnızca yerel ağ
    if sert:
        b.load_cert_chain(sert, anahtar)
    ham = socket.create_connection((adres, port), timeout=sure)
    try:
        return b.wrap_socket(ham, server_hostname=adres)
    except Exception:
        ham.close()
        raise

class _TVTemel:
    """Toshiba sürücülerinin ortak parçaları."""
    tur = ''
    TUSLAR = {}

    def __init__(self, kayit):
        self.id, self.adres = kayit['id'], kayit['adres']
        self.ad = kayit.get('ad') or 'Toshiba TV'
        self.mac = kayit.get('mac')
        self.k = dict(kayit)            # ayar dosyasındaki kayıt (eşleşme anahtarları burada tutulur)
        self.durum, self.mesaj = 'kapali', ''
        self.sonuclar, self.bilgi = {}, {}
        self.kilit = threading.Lock()
        self.baglaniyor = False
        self.son_deneme = 0
        self.kod_bilgi = None           # TV'de çıkan eşleşme kodu bekleniyorsa {'uzunluk': 6, 'harf': True}
        self.uygulamalar = []
        self.son_kanal = ''
        self.ogrenme = None

    def kayit(self):
        k = dict(self.k)
        k.update(id=self.id, tur=self.tur, ad=self.ad, adres=self.adres, mac=self.mac)
        return k

    def ozet(self):
        if self.durum in ('kapali', 'hata') and not self.baglaniyor and time.time() - self.son_deneme > 8:
            self.baglan_arkada()
        return {'id': self.id, 'tur': self.tur, 'ad': self.ad, 'adres': self.adres, 'durum': self.durum,
                'mesaj': self.mesaj, 'mac': self.mac or '', 'bilgi': self.bilgi,
                'kod': self.kod_bilgi if self.durum == 'kod' else None, 'ogrenme': self.ogrenme}

    def baglan_arkada(self):
        with self.kilit:
            if self.baglaniyor or self.durum in ('bagli', 'kod'):
                return
            self.baglaniyor = True
            self.son_deneme = time.time()
        threading.Thread(target=self._baglan_sar, daemon=True).start()

    def _baglan_sar(self):
        try:
            self._baglan()
        except Exception as e:
            self.durum, self.mesaj = 'hata', str(e)
        finally:
            self.baglaniyor = False
            self.son_deneme = time.time()

    def kaydet_sonuc(self, ad, ok, yol, hata=''):
        self.sonuclar[ad] = {'ok': bool(ok), 'yol': yol, 'hata': '' if ok else (hata or 'olmadı')[:300],
                             'zaman': int(time.time())}
        if ok and (ad in ('KANAL+', 'KANAL-', 'ANA', 'UYGULAMALAR', 'INTERNET') or ad.startswith('UYG:')):
            self.son_kanal = ''          # TV başka kanala/uygulamaya geçti, hangisi bilinmiyor
        return ok

    def kapali_guc(self, ad):
        """TV kapalıyken Güç: Wake-on-LAN."""
        if self.mac and wake_on_lan(self.mac, self.adres):
            self.son_deneme = time.time() - 4   # birkaç saniye sonra bağlanmayı dene
            return self.kaydet_sonuc(ad, True, 'Wake-on-LAN')
        return self.kaydet_sonuc(ad, False, '', 'TV kapalı. Uzaktan açmak için ⚙ menüsünde TV\'nin MAC adresini gir '
                                                 've TV\'de "Wi‑Fi ile aç / Wake on LAN" ayarını aç.')

    def yazi(self, t):
        return self.kaydet_sonuc('YAZI', False, '', 'Bu TV yazı göndermeyi desteklemiyor')

    def adres_ac(self, url):
        return self.kaydet_sonuc('ADRES', False, '', 'Bu TV bağlantı açmayı desteklemiyor')

    def kod_gir(self, kod):
        return False, 'Şu an eşleşme kodu beklenmiyor'

    def kapat(self):
        pass

# ── Google TV / Android TV (Android TV Remote v2) ──
ATV_OZELLIK = 1 | 2 | 4 | 32 | 64 | 512      # ping, tuş, yazı, güç, ses, uygulama bağlantısı
ATV_TUS = dict(ADB_TUS)                      # Android tuş kodları aynı
ATV_TUS.update({'GUC': 26, 'CIKIS': 4, 'LISTE': 172})
ATV_UYGULAMALAR = [
    {'id': 'youtube', 'ad': 'YouTube', 'link': 'https://www.youtube.com', 'renk': '#e62117'},
    {'id': 'netflix', 'ad': 'Netflix', 'link': 'https://www.netflix.com/title', 'renk': '#141414'},
    {'id': 'prime', 'ad': 'Prime Video', 'link': 'https://app.primevideo.com', 'renk': '#00a8e1'},
    {'id': 'disney', 'ad': 'Disney+', 'link': 'https://www.disneyplus.com', 'renk': '#0b1a5b'},
    {'id': 'spotify', 'ad': 'Spotify', 'link': 'https://open.spotify.com', 'renk': '#1db954'},
    {'id': 'play', 'ad': 'Google Play', 'link': 'market://launch?id=com.android.vending', 'renk': '#01875f'},
]

class AndroidTVCihaz(_TVTemel):
    tur = 'androidtv'
    TUSLAR = ATV_TUS

    def __init__(self, kayit):
        super().__init__(kayit)
        self.s = None
        self.eslesme = None
        self.yaz_kilit = threading.Lock()
        self.ime = (0, 0)
        self.klavye = None
        self.on_uygulama = ''
        self.uygulamalar = ATV_UYGULAMALAR

    def ozet(self):
        o = super().ozet()
        o['klavye'] = self.klavye
        return o

    def _gonder(self, mesaj, s=None):
        s = s or self.s
        if not s:
            raise ConnectionError('TV bağlı değil')
        with self.yaz_kilit:
            s.sendall(_pb_vint(len(mesaj)) + mesaj)

    def _baglan(self):
        self.durum, self.mesaj = 'baglaniyor', ''
        if not (os.path.exists(ATV_SERT) and os.path.exists(ATV_ANAHTAR)):
            self.mesaj = 'İlk bağlantı için güvenlik anahtarı hazırlanıyor (yalnızca bir kez)…'
        sert, anahtar = atv_sertifika()
        if not port_acik(self.adres, 6466, 2):
            self.durum = 'kapali'
            self.mesaj = 'TV kapalı ya da ağda değil' if not port_acik(self.adres, 6467, 1) else 'TV yanıt vermiyor'
            return
        hazir = threading.Event()
        try:
            s = _tls_istemci(self.adres, 6466, sert, anahtar)
            s.settimeout(20)
            threading.Thread(target=self._dinle, args=(s, hazir), daemon=True).start()
        except (ssl.SSLError, ConnectionResetError, BrokenPipeError):
            s = None                                  # TV tanımadığı kumandayı el sıkışmada reddeder
        if s is not None and hazir.wait(8) and self.s is s:
            return
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        self._eslesme_baslat()

    def _dinle(self, s, hazir):
        try:
            while True:
                m = pb_coz(pb_soketten(s))
                if 1 in m:                                         # remote_configure
                    cfg = pb_coz(m[1][0])
                    tv = pb_coz(pb_al(cfg, 2, b''))
                    for k, no in (('model', 1), ('marka', 2), ('surum', 6)):
                        v = pb_al(tv, no)
                        if v:
                            self.bilgi[k] = v.decode('utf-8', 'replace')
                    ozellik = (pb_al(cfg, 1, 0) or ATV_OZELLIK) & ATV_OZELLIK
                    self._gonder(pb((1, [(1, ozellik), (2, [(3, 1), (4, '1'), (5, 'atvremote'), (6, '1.0.0')])])), s)
                    self.s = s
                    self.durum, self.mesaj = 'bagli', ''
                    self.kod_bilgi = None
                    hazir.set()
                elif 2 in m:                                       # remote_set_active
                    self._gonder(pb((2, [(1, ATV_OZELLIK)])), s)
                elif 8 in m:                                       # ping
                    self._gonder(pb((9, [(1, pb_al(pb_coz(m[8][0]), 1, 0))])), s)
                elif 20 in m:                                      # öndeki uygulama
                    app = pb_coz(pb_al(pb_coz(m[20][0]), 1, b''))
                    self.on_uygulama = (pb_al(app, 12) or b'').decode('utf-8', 'replace')
                elif 21 in m:                                      # yazı kutusu açıldı
                    d = pb_coz(m[21][0])
                    self.ime = (pb_al(d, 1, 0), pb_al(d, 2, 0))
                    self.klavye = True
                elif 22 in m:
                    self.klavye = True
                elif 40 in m:                                      # TV açık/bekleme
                    self.bilgi['ekran'] = 'açık' if pb_al(pb_coz(m[40][0]), 1, 0) else 'bekleme'
        except Exception:
            pass
        finally:
            hazir.set()
            try:
                s.close()
            except Exception:
                pass
            if self.s is s:
                self.s = None
                if self.durum == 'bagli':
                    self.durum, self.mesaj = 'kapali', 'TV bağlantısı koptu'

    # — eşleşme: TV ekranında 6 haneli kod çıkar —
    @staticmethod
    def _dis(*alanlar):
        return pb((1, 2), (2, 200), *alanlar)

    def _eslesme_al(self, s, beklenen):
        m = pb_coz(pb_soketten(s))
        durum = pb_al(m, 2, 0)
        if durum != 200:
            raise ValueError({402: 'Kod yanlış', 401: 'TV bu eşleşme ayarını kabul etmedi'}.get(durum, 'TV eşleşmeyi reddetti (%s)' % durum))
        if beklenen not in m:
            raise ValueError('TV beklenmeyen yanıt verdi')
        return m

    def _eslesme_baslat(self):
        sert, anahtar = atv_sertifika()
        try:
            s = _tls_istemci(self.adres, 6467, sert, anahtar)
        except Exception as e:
            self.durum, self.mesaj = 'hata', 'TV eşleşmeye izin vermedi: %s' % e
            return
        try:
            s.settimeout(10)
            self._gonder(self._dis((10, [(1, 'atvremote'), (2, 'Kumanda')])), s)
            ack = self._eslesme_al(s, 11)
            ad = pb_al(pb_coz(ack[11][0]), 1)
            if ad:
                self.bilgi['tv_adi'] = ad.decode('utf-8', 'replace')
            self._gonder(self._dis((20, [(1, [(1, 3), (2, 6)]), (3, 1)])), s)
            self._eslesme_al(s, 20)
            self._gonder(self._dis((30, [(1, [(1, 3), (2, 6)]), (2, 1)])), s)
            self._eslesme_al(s, 31)
        except Exception as e:
            s.close()
            self.durum, self.mesaj = 'hata', 'Eşleşme başlatılamadı: %s' % e
            return
        s.settimeout(None)
        if self.eslesme:
            try:
                self.eslesme.close()
            except Exception:
                pass
        self.eslesme = s
        self.kod_bilgi = {'uzunluk': 6, 'harf': True}
        self.durum = 'kod'
        self.mesaj = 'TV ekranında çıkan 6 haneli kodu yaz (harf de olabilir)'

    def kod_gir(self, kod):
        kod = re.sub(r'\s', '', str(kod or '')).upper()
        if not re.match(r'^[0-9A-F]{6}$', kod):
            return False, 'Kod 6 karakter olmalı (0–9 ve A–F)'
        s = self.eslesme
        if not s:
            self.son_deneme = 0
            self.durum = 'kapali'
            self.baglan_arkada()
            return False, 'Eşleşme süresi doldu, TV\'de yeni kod çıkacak'
        try:
            sn, se = sertifika_rsa(s.getpeercert(True))
            with open(ATV_SERT) as f:
                cn, ce = sertifika_rsa(_pem_der(f.read()))
            h = hashlib.sha256(_bayt(cn) + _bayt(ce) + _bayt(sn) + _bayt(se) + bytes.fromhex(kod[2:])).digest()
            if h[0] != int(kod[:2], 16):
                return False, 'Kod yanlış, TV ekranındaki kodu tekrar kontrol et'
            s.settimeout(10)
            self._gonder(self._dis((40, [(1, h)])), s)
            self._eslesme_al(s, 41)
        except Exception as e:
            try:
                s.close()
            except Exception:
                pass
            self.eslesme = None
            self.durum, self.mesaj, self.kod_bilgi = 'hata', 'Eşleşme olmadı: %s' % e, None
            return False, self.mesaj
        try:
            s.close()
        except Exception:
            pass
        self.eslesme = None
        self.kod_bilgi = None
        self.durum, self.mesaj = 'baglaniyor', 'Eşleşti, bağlanılıyor…'
        self.k['eslesti'] = True
        cihaz_kaydet(self)
        threading.Thread(target=self._baglan_sar, daemon=True).start()
        return True, ''

    def tus(self, ad):
        kod = ATV_TUS.get(ad)
        if kod is None:
            return self.kaydet_sonuc(ad, False, '', 'Bu TV\'de bu tuş yok')
        if self.durum != 'bagli' or not self.s:
            if ad == 'GUC':
                return self.kapali_guc(ad)
            self.baglan_arkada()
            return self.kaydet_sonuc(ad, False, '', 'TV bağlı değil')
        try:
            self._gonder(pb((10, [(1, kod), (2, 3)])))
            return self.kaydet_sonuc(ad, True, 'tuş %d' % kod)
        except Exception as e:
            return self.kaydet_sonuc(ad, False, '', str(e))

    def yazi(self, t):
        if self.durum != 'bagli' or not self.s:
            return self.kaydet_sonuc('YAZI', False, '', 'TV bağlı değil')
        if not self.klavye:
            return self.kaydet_sonuc('YAZI', False, '', 'TV\'de seçili bir yazı kutusu yok. Önce TV\'de arama kutusunu seç, sonra gönder.')
        n = len(t) - 1
        try:
            self._gonder(pb((21, [(1, self.ime[0]), (2, self.ime[1]), (3, [(1, 1), (2, [(1, n), (2, n), (3, t)])])])))
            return self.kaydet_sonuc('YAZI', True, 'yazı kutusu')
        except Exception as e:
            return self.kaydet_sonuc('YAZI', False, '', str(e))

    def _link(self, link, ad):
        if self.durum != 'bagli' or not self.s:
            self.baglan_arkada()
            return self.kaydet_sonuc(ad, False, '', 'TV bağlı değil')
        try:
            self._gonder(pb((90, [(1, link)])))
            return self.kaydet_sonuc(ad, True, link)
        except Exception as e:
            return self.kaydet_sonuc(ad, False, '', str(e))

    def uygulamalari_al(self):
        return self.uygulamalar

    def uygulama(self, uid):
        u = next((a for a in self.uygulamalar if a['id'] == uid), None)
        link = u['link'] if u else ('market://launch?id=' + re.sub(r'[^\w.]', '', uid))
        return self._link(link, 'UYG:' + uid)

    def adres_ac(self, url):
        vid = youtube_kimligi(url)
        if vid:
            return self._link('https://www.youtube.com/watch?v=' + vid, 'ADRES')
        return self._link(url, 'ADRES')

    def kapat(self):
        for s in (self.s, self.eslesme):
            try:
                s and s.close()
            except Exception:
                pass

# ── VIDAA (MQTT) ──
class MQTTRet(Exception):
    pass

class MQTT:
    """Yalnızca bu iş için yeter küçüklükte MQTT 3.1.1 istemcisi (QoS 0)."""
    def __init__(self, adres, port, istemci, kullanici, sifre, sert=None, anahtar=None, sure=6):
        self.s = _tls_istemci(adres, port, sert, anahtar, sure)
        self.yaz_kilit = threading.Lock()
        self.pid = 0
        st = self._st
        bayrak = 0x02 | (0x80 if kullanici else 0) | (0x40 if sifre else 0)
        govde = st('MQTT') + bytes([4, bayrak]) + struct.pack('>H', 60) + st(istemci)
        govde += (st(kullanici) if kullanici else b'') + (st(sifre) if sifre else b'')
        self._paket(0x10, govde)
        tip, _, veri = self.oku()
        if tip != 0x20 or len(veri) < 2:
            self.kapat()
            raise ConnectionError('TV beklenmeyen yanıt verdi')
        if veri[1]:
            self.kapat()
            raise MQTTRet(veri[1])
        self.s.settimeout(None)
        self.acik = True

    @staticmethod
    def _st(x):
        b = x.encode('utf-8') if isinstance(x, str) else x
        return struct.pack('>H', len(b)) + b

    def _paket(self, bas, govde):
        n, uz = len(govde), bytearray()
        while True:
            x, n = n % 128, n // 128
            uz.append(x | (0x80 if n else 0))
            if not n:
                break
        with self.yaz_kilit:
            self.s.sendall(bytes([bas]) + bytes(uz) + govde)

    def _tam(self, n):
        v = b''
        while len(v) < n:
            p = self.s.recv(n - len(v))
            if not p:
                raise ConnectionError('bağlantı kapandı')
            v += p
        return v

    def oku(self):
        b = self._tam(1)[0]
        n = k = 0
        while True:
            x = self._tam(1)[0]
            n |= (x & 0x7F) << k
            k += 7
            if not x & 0x80:
                break
        return b & 0xF0, b & 0x0F, self._tam(n) if n else b''

    def abone(self, konular):
        self.pid = self.pid % 65000 + 1
        self._paket(0x82, struct.pack('>H', self.pid) + b''.join(self._st(k) + b'\x00' for k in konular))

    def yayinla(self, konu, yuk=''):
        if isinstance(yuk, (dict, list)):
            yuk = json.dumps(yuk, separators=(',', ':'))
        if isinstance(yuk, str):
            yuk = yuk.encode('utf-8')
        self._paket(0x30, self._st(konu) + yuk)

    def kapat(self):
        self.acik = False
        try:
            self._paket(0xE0, b'')
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass

VIDAA_SERT_KAYNAK = 'https://raw.githubusercontent.com/tombabolewski/vidaa-control/main/vidaa/certs/'
VIDAA_KLASOR = os.path.expanduser('~/.kumanda_vidaa')
VIDAA_DESEN = '38D65DC30F45109A369A86FCE866A85B'
VIDAA_EK = {'yeni': 'h!i@s#$v%i^d&a*a', 'eski': 'h*i&s%e!r^v0i1c9'}
VIDAA_XOR = 0x569814772b03a968

def vidaa_sertifika():
    """VIDAA TV'leri yalnızca VIDAA uygulamasının sertifikasıyla konuşur; ilk seferde indirilir."""
    s, a = os.path.join(VIDAA_KLASOR, 'vidaa_client.pem'), os.path.join(VIDAA_KLASOR, 'vidaa_client.key')
    if not (os.path.exists(s) and os.path.exists(a)):
        os.makedirs(VIDAA_KLASOR, exist_ok=True)
        for yol, ad in ((s, 'vidaa_client.pem'), (a, 'vidaa_client.key')):
            veri = _indir(VIDAA_SERT_KAYNAK + ad, sure=20)
            if b'-----BEGIN' not in veri:
                raise ValueError('sertifika dosyası bozuk')
            with open(yol + '.tmp', 'wb') as f:
                f.write(veri)
            os.replace(yol + '.tmp', yol)
    return s, a

def vidaa_kimlik(uuid, yontem, zaman=None):
    """VIDAA uygulamasının ürettiği MQTT kullanıcı adı/şifresi (yontem: yeni / orta / eski)."""
    zaman = int(zaman or time.time())
    md5 = lambda x: hashlib.md5(x.encode()).hexdigest().upper()
    istemci = '%s$his$%s_vidaacommon_001' % (uuid, md5('%s$%s' % (VIDAA_DESEN, uuid))[:6])
    kullanici = 'his$%d' % (zaman if yontem == 'eski' else zaman ^ VIDAA_XOR)
    deger = 'his%d%s' % (sum(int(c) for c in str(zaman)) % 10, VIDAA_EK['yeni' if yontem == 'yeni' else 'eski'])
    return istemci, kullanici, md5('%d$%s' % (zaman, md5(deger)[:6]))

VIDAA_TUS = {
    'YUKARI': 'KEY_UP', 'ASAGI': 'KEY_DOWN', 'SOL': 'KEY_LEFT', 'SAG': 'KEY_RIGHT', 'TAMAM': 'KEY_OK',
    'GERI': 'KEY_RETURNS', 'ANA': 'KEY_HOME', 'AYARLAR': 'KEY_MENU', 'MENU': 'KEY_MENU', 'CIKIS': 'KEY_EXIT',
    'SES+': 'KEY_VOLUMEUP', 'SES-': 'KEY_VOLUMEDOWN', 'SESSIZ': 'KEY_MUTE',
    'KANAL+': 'KEY_CHANNELUP', 'KANAL-': 'KEY_CHANNELDOWN', 'GUC': 'KEY_POWER',
    'OYNAT': 'KEY_PLAY', 'DURAKLAT': 'KEY_PAUSE', 'DURDUR': 'KEY_STOP', 'ILERI_SAR': 'KEY_FORWARDS', 'GERI_SAR': 'KEY_BACK',
    'KIRMIZI': 'KEY_RED', 'YESIL': 'KEY_GREEN', 'SARI': 'KEY_YELLOW', 'MAVI': 'KEY_BLUE',
    'ALTYAZI': 'KEY_SUBTITLE', 'BILGI': 'KEY_INFO', 'REHBER': 'KEY_EPG',
}
for _r in '0123456789':
    VIDAA_TUS[_r] = 'KEY_' + _r
VIDAA_YEDEK_UYG = [{'id': 'youtube', 'ad': 'YouTube', 'url': 'youtube', 'urlType': 37, 'storeType': 0},
                   {'id': 'netflix', 'ad': 'Netflix', 'url': 'netflix', 'urlType': 37, 'storeType': 0},
                   {'id': 'amazon', 'ad': 'Prime Video', 'url': 'amazon', 'urlType': 37, 'storeType': 0}]

class VidaaCihaz(_TVTemel):
    tur = 'vidaa'
    TUSLAR = VIDAA_TUS

    def __init__(self, kayit):
        super().__init__(kayit)
        self.m = None
        self.cevaplar = {}            # konu sonu → (Event, son yük)
        if not self.k.get('uuid'):
            b = bytearray(secrets.token_bytes(6))
            b[0] = (b[0] | 0x02) & 0xFE   # yerel yönetimli MAC biçimi
            self.k['uuid'] = ':'.join('%02x' % x for x in b)

    def _konu(self, servis, eylem):
        return '/remoteapp/tv/%s/%s/actions/%s' % (servis, self.k['istemci'], eylem)

    def _bekle_hazirla(self, *adlar):
        for a in adlar:
            self.cevaplar[a] = (threading.Event(), None)

    def _bekle(self, ad, sure):
        e = self.cevaplar.get(ad)
        if e and e[0].wait(sure):
            return self.cevaplar[ad][1]
        return None

    def _protokol(self):
        try:
            import urllib.request
            with urllib.request.urlopen('http://%s:38400/MediaServer/rendererdevicedesc.xml' % self.adres, timeout=3) as y:
                x = y.read(65536).decode('utf-8', 'replace')
            for a, b in (('modelName', 'model'), ('friendlyName', 'tv_adi'), ('manufacturer', 'marka')):
                m = re.search(r'<%s>([^<]+)</%s>' % (a, a), x)
                if m:
                    self.bilgi[b] = m.group(1).strip()
            m = re.search(r'transport_protocol[^0-9]{0,4}(\d+)', x, re.I)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def _mqtt(self, istemci, kullanici, sifre):
        sert, anahtar = vidaa_sertifika()
        return MQTT(self.adres, 36669, istemci, kullanici, sifre, sert, anahtar)

    def _abone_ol(self):
        cid = self.k['istemci']
        taban = '/remoteapp/mobile/%s' % cid
        self.m.abone([taban + '/ui_service/data/authentication', taban + '/ui_service/data/authenticationcode',
                      taban + '/platform_service/data/tokenissuance', taban + '/ui_service/data/tokenissuance',
                      taban + '/ui_service/data/applist', taban + '/ui_service/data/sourcelist',
                      taban + '/ui_service/data/state', taban + '/platform_service/data/gettvinfo',
                      '/remoteapp/mobile/broadcast/ui_service/state', '/remoteapp/mobile/broadcast/ui_service/volume'])
        threading.Thread(target=self._dinle, args=(self.m,), daemon=True).start()
        threading.Thread(target=self._canli_tut, args=(self.m,), daemon=True).start()

    def _baglan(self):
        self.durum, self.mesaj = 'baglaniyor', ''
        if not port_acik(self.adres, 36669, 2):
            self.durum, self.mesaj = 'kapali', 'TV kapalı ya da ağda değil'
            return
        try:
            vidaa_sertifika()
        except Exception as e:
            self.durum, self.mesaj = 'hata', 'VIDAA bağlantı sertifikası indirilemedi (internet gerekli, yalnızca ilk sefer): %s' % e
            return
        k, simdi = self.k, time.time()
        if k.get('istemci') and k.get('kullanici'):
            for sifre, yenile in ((k.get('erisim'), False), (k.get('yenileme'), True)):
                bitis = k.get('yenileme_bitis' if yenile else 'erisim_bitis') or 0
                if not sifre or simdi > bitis:
                    continue
                try:
                    self.m = self._mqtt(k['istemci'], k['kullanici'], sifre)
                except MQTTRet:
                    continue
                self._bekle_hazirla('tokenissuance')
                self._abone_ol()
                if yenile:
                    self.m.yayinla('/remoteapp/tv/platform_service/%s/data/gettoken' % k['istemci'], {'refreshtoken': sifre})
                    if not self._bekle('tokenissuance', 8):
                        self.m.kapat()
                        continue
                self.durum, self.mesaj = 'bagli', ''
                self._sonra()
                return
        # İlk eşleşme: TV ekranında 4 haneli kod çıkar
        protokol = self._protokol()
        sira = ['yeni', 'orta', 'eski'] if protokol is None else (
            ['yeni'] if protokol >= 3290 else ['orta'] if protokol >= 3000 else ['eski'])
        if protokol is None:
            sira.append('sabit')
        son = None
        for yontem in sira:
            if yontem == 'sabit':
                istemci, kullanici, sifre = k['uuid'].replace(':', '').upper() + '$vidaa_common', 'hisenseservice', 'multimqttservice'
            else:
                istemci, kullanici, sifre = vidaa_kimlik(k['uuid'], yontem)
            try:
                self.m = self._mqtt(istemci, kullanici, sifre)
            except MQTTRet as e:
                son = e
                continue
            k.update(istemci=istemci, kullanici=kullanici, yontem=yontem)
            break
        else:
            self.durum, self.mesaj = 'hata', 'TV bağlantıyı kabul etmedi (%s). TV\'de Ayarlar → Ağ → "Mobil cihazla kontrol" açık olmalı.' % son
            return
        self._bekle_hazirla('authentication', 'tokenissuance')
        self._abone_ol()
        self.m.yayinla(self._konu('ui_service', 'vidaa_app_connect'),
                       {'app_version': 2, 'connect_result': 0, 'device_type': 'Mobile App'})
        self.kod_bilgi = {'uzunluk': 4, 'harf': False}
        self.durum = 'kod'
        self.mesaj = 'TV ekranında çıkan 4 haneli kodu yaz'

    def _sonra(self):
        try:
            self.m.yayinla(self._konu('platform_service', 'gettvinfo'), '')
        except Exception:
            pass

    def _canli_tut(self, m):
        while m.acik:
            time.sleep(25)
            try:
                m._paket(0xC0, b'')
            except Exception:
                break

    def _dinle(self, m):
        try:
            while True:
                tip, bayrak, veri = m.oku()
                if tip != 0x30:
                    continue
                n = struct.unpack('>H', veri[:2])[0]
                konu = veri[2:2 + n].decode('utf-8', 'replace')
                yuk = veri[2 + n + (2 if bayrak & 0x06 else 0):]
                try:
                    o = json.loads(yuk.decode('utf-8', 'replace') or 'null')
                except Exception:
                    o = yuk.decode('utf-8', 'replace')
                son = konu.rsplit('/', 1)[-1]
                if son == 'tokenissuance' and isinstance(o, dict) and o.get('accesstoken'):
                    s = time.time()
                    self.k.update(erisim=o['accesstoken'], yenileme=o.get('refreshtoken') or self.k.get('yenileme'),
                                  erisim_bitis=s + float(o.get('accesstoken_duration_day') or 7) * 86400 - 3600,
                                  yenileme_bitis=s + float(o.get('refreshtoken_duration_day') or 30) * 86400 - 3600)
                    cihaz_kaydet(self)
                elif son == 'gettvinfo' and isinstance(o, dict):
                    for a, b in (('model_name', 'model'), ('tv_name', 'tv_adi'), ('sw_version', 'surum')):
                        if o.get(a):
                            self.bilgi[b] = str(o[a])
                elif son == 'state' and isinstance(o, dict) and o.get('statetype') == 'authentication' and self.durum == 'bagli':
                    self.k.pop('erisim', None)          # TV eşleşmeyi unutmuş
                if son in ('authentication', 'authenticationcode'):
                    son = 'authentication'
                e = self.cevaplar.get(son)
                if e:
                    self.cevaplar[son] = (e[0], o)
                    e[0].set()
        except Exception:
            pass
        finally:
            m.kapat()
            if self.m is m:
                self.m = None
                if self.durum in ('bagli', 'kod'):
                    self.durum, self.mesaj = 'kapali', 'TV bağlantısı koptu'
                    self.kod_bilgi = None

    def kod_gir(self, kod):
        kod = re.sub(r'\D', '', str(kod or ''))
        if len(kod) != 4:
            return False, 'Kod 4 rakam olmalı'
        if self.durum != 'kod' or not self.m:
            self.son_deneme = 0
            self.baglan_arkada()
            return False, 'Eşleşme süresi doldu, TV\'de yeni kod çıkacak'
        self._bekle_hazirla('authentication')
        self.m.yayinla(self._konu('ui_service', 'authenticationcode'), {'authNum': int(kod)})
        c = self._bekle('authentication', 10)
        if not (isinstance(c, dict) and str(c.get('result')) == '1'):
            return False, 'Kod kabul edilmedi, TV ekranındaki kodu tekrar kontrol et'
        self._bekle_hazirla('tokenissuance')
        self.m.yayinla('/remoteapp/tv/platform_service/%s/data/gettoken' % self.k['istemci'], {'refreshtoken': ''})
        self.m.yayinla(self._konu('ui_service', 'authenticationcodeclose'), '')
        self._bekle('tokenissuance', 8)
        self.kod_bilgi = None
        self.durum, self.mesaj = 'bagli', ''
        cihaz_kaydet(self)
        self._sonra()
        return True, ''

    def _yayin(self, ad, servis, eylem, yuk=''):
        if self.durum != 'bagli' or not self.m:
            self.baglan_arkada()
            return self.kaydet_sonuc(ad, False, '', 'TV bağlı değil' if self.durum != 'kod' else 'Önce TV\'deki kodu gir')
        try:
            self.m.yayinla(self._konu(servis, eylem), yuk)
            return self.kaydet_sonuc(ad, True, eylem + (' ' + yuk if isinstance(yuk, str) and yuk else ''))
        except Exception as e:
            return self.kaydet_sonuc(ad, False, '', str(e))

    def tus(self, ad):
        k = VIDAA_TUS.get(ad)
        if not k:
            return self.kaydet_sonuc(ad, False, '', 'Bu TV\'de bu tuş yok')
        if ad == 'GUC' and self.durum != 'bagli':
            return self.kapali_guc(ad)
        return self._yayin(ad, 'remote_service', 'sendkey', k)

    def _liste_iste(self, eylem, ad):
        if self.durum != 'bagli' or not self.m:
            return None
        self._bekle_hazirla(ad)
        try:
            self.m.yayinla(self._konu('ui_service', eylem), '')
        except Exception:
            return None
        return self._bekle(ad, 5)

    def uygulamalari_al(self):
        l = self._liste_iste('applist', 'applist')
        if isinstance(l, dict):
            l = l.get('applist') or l.get('list') or []
        if isinstance(l, list) and l:
            self.uygulamalar = [{'id': str(a.get('appId') or a.get('name')), 'ad': a.get('name') or '?',
                                 'url': a.get('url', ''), 'urlType': a.get('urlType', 37), 'storeType': a.get('storeType', 0)}
                                for a in l if isinstance(a, dict) and (a.get('name') or a.get('appId'))]
        return self.uygulamalar or VIDAA_YEDEK_UYG

    def uygulama(self, uid):
        u = next((a for a in (self.uygulamalar or []) + VIDAA_YEDEK_UYG if a['id'] == uid), None)
        if not u:
            return self.kaydet_sonuc('UYG:' + uid, False, '', 'Uygulama TV\'de bulunamadı')
        return self._yayin('UYG:' + uid, 'ui_service', 'launchapp',
                           {'name': u['ad'], 'urlType': u.get('urlType', 37), 'storeType': u.get('storeType', 0), 'url': u.get('url', '')})

    def girisler(self):
        l = self._liste_iste('sourcelist', 'sourcelist')
        if isinstance(l, dict):
            l = l.get('sourcelist') or []
        return [{'id': str(g.get('sourceid')), 'ad': g.get('displayname') or g.get('sourcename') or str(g.get('sourceid')),
                 'bagli': str(g.get('is_signal', g.get('has_signal', 1))) != '0'}
                for g in (l or []) if isinstance(g, dict) and g.get('sourceid') is not None]

    def giris_sec(self, gid):
        return self._yayin('GIRIS', 'ui_service', 'changesource', {'sourceid': str(gid)})

    def kapat(self):
        if self.m:
            self.m.kapat()

# ── Smart Center (Vestel altyapılı eski Toshiba'lar) ──
# Tuş kodları: ha-vestel-toshiba-tv (const.py) ve pyvesteltv. Renk/Bilgi kodları Vestel'in RC5 düzeninden
# tahmindir; ⚙ → Tuş testi'nde hangilerinin işe yaradığı görülebilir.
SC_TUS = {
    'GUC': 1012, 'SESSIZ': 1013, 'SES+': 1016, 'SES-': 1017, 'KANAL+': 1032, 'KANAL-': 1033,
    'YUKARI': 1020, 'ASAGI': 1019, 'SOL': 1021, 'SAG': 1022, 'TAMAM': 1053, 'GERI': 1010,
    'ANA': 1048, 'AYARLAR': 1066, 'MENU': 1066, 'CIKIS': 1037, 'KAYNAK': 1056, 'INTERNET': 1065, 'FAVORI': 1062,
    'OYNAT': 1025, 'DURDUR': 1049, 'GERI_SAR': 1027, 'ILERI_SAR': 1028,
    'KIRMIZI': 1055, 'YESIL': 1054, 'SARI': 1050, 'MAVI': 1052, 'BILGI': 1015,
}
for _r in '0123456789':
    SC_TUS[_r] = 1000 + int(_r)
SC_UYGULAMALAR = [{'id': 'YouTube', 'ad': 'YouTube', 'renk': '#e62117'}, {'id': 'Netflix', 'ad': 'Netflix', 'renk': '#141414'},
                  {'id': 'INTERNET', 'ad': 'İnternet portalı', 'renk': '#2f62c6'}]

class SmartCenterCihaz(_TVTemel):
    tur = 'smartcenter'
    TUSLAR = SC_TUS

    def __init__(self, kayit):
        super().__init__(kayit)
        self.uygulamalar = SC_UYGULAMALAR
        self.yol = kayit.get('yol') or 'sc'      # hangi istek biçimi çalıştı
        self.ogrenebilir = False
        self._son_kontrol = 0

    def _http(self, yol, govde=None, sure=3, yontem=None):
        import urllib.request, urllib.error
        istek = urllib.request.Request('http://%s:56789%s' % (self.adres, yol), data=govde, method=yontem,
                                       headers={'Content-Type': 'text/plain; charset="utf-8"', 'Connection': 'close'})
        try:
            with urllib.request.urlopen(istek, timeout=sure) as y:
                return y.status, y.read(65536)
        except urllib.error.HTTPError as e:
            return e.code, b''

    def ozet(self):
        if time.time() - self._son_kontrol > 4 and not self.baglaniyor:
            self._son_kontrol = time.time()
            self.baglan_arkada()
        return super().ozet()

    def baglan_arkada(self):
        with self.kilit:
            if self.baglaniyor:
                return
            self.baglaniyor = True
            self.son_deneme = time.time()
        threading.Thread(target=self._baglan_sar, daemon=True).start()

    def _baglan(self):
        try:
            kod, govde = self._http('/apps/SmartCenter', sure=2)
        except Exception:
            kod, govde = None, b''
        if kod is None:
            self.durum, self.mesaj = 'kapali', 'TV kapalı ya da ağda değil'
            return
        if self.durum != 'bagli':
            m = re.search(rb'<state>([^<]*)</state>', govde or b'')
            if m:
                self.bilgi['smartcenter'] = m.group(1).decode('utf-8', 'replace')
            program = self.durum_sor('GETPROGRAM')
            self.ogrenebilir = bool(program)
            if program:
                self.bilgi['kanal'] = program
        self.durum, self.mesaj = 'bagli', ''

    def durum_sor(self, komut, sure=2):
        """TV'nin 1986 numaralı kanalı: GETPROGRAM, GETSOURCE, GETMUTE…"""
        try:
            with socket.create_connection((self.adres, 1986), timeout=sure) as s:
                s.settimeout(sure)
                s.sendall((komut + '\r\n').encode())
                v = b''
                while b'\n' not in v and len(v) < 4096:
                    p = s.recv(1024)
                    if not p:
                        break
                    v += p
        except Exception:
            return ''
        satir = v.split(b'\n', 1)[0].decode('utf-8', 'replace').strip()
        return (satir.split(' is ', 1)[1] if ' is ' in satir else satir.split(' ', 1)[-1] if ' ' in satir else satir).strip()

    def _tus_gonder(self, kod):
        bicimler = [('sc', '/apps/SmartCenter', '<remote><key code=%d /></remote>' % kod),
                    ('vr', '/apps/vr/remote', '<?xml version="1.0" ?><remote><key code="%d"/></remote>' % kod)]
        bicimler.sort(key=lambda b: b[0] != self.yol)
        hata = ''
        for ad, yol, govde in bicimler:
            try:
                d, _ = self._http(yol, govde.encode(), sure=3, yontem='POST')
            except Exception as e:
                hata = str(e)
                continue
            if 200 <= d < 300:
                if ad != self.yol:
                    self.yol = self.k['yol'] = ad
                    cihaz_kaydet(self)
                return True, ad
            hata = 'TV %d yanıtı verdi' % d
        return False, hata

    def tus(self, ad):
        kod = SC_TUS.get(ad)
        if kod is None:
            return self.kaydet_sonuc(ad, False, '', 'Bu TV\'de bu tuş yok')
        if self.durum != 'bagli' and ad == 'GUC' and self.mac:
            return self.kapali_guc(ad)
        ok, bilgi = self._tus_gonder(kod)
        if ok:
            self.durum = 'bagli'
            return self.kaydet_sonuc(ad, True, 'kod %d' % kod)
        if ad == 'GUC':
            return self.kapali_guc(ad)
        self.durum, self.mesaj = 'kapali', 'TV yanıt vermiyor'
        return self.kaydet_sonuc(ad, False, '', bilgi or 'TV yanıt vermiyor')

    def _dial(self, uygulama, govde=b'', ad=None):
        try:
            d, _ = self._http('/apps/' + uygulama, govde, sure=5, yontem='POST')
        except Exception as e:
            return self.kaydet_sonuc(ad or 'UYG:' + uygulama, False, '', str(e))
        return self.kaydet_sonuc(ad or 'UYG:' + uygulama, 200 <= d < 300, 'DIAL ' + uygulama, 'TV %d yanıtı verdi' % d)

    def uygulamalari_al(self):
        return self.uygulamalar

    def uygulama(self, uid):
        if uid == 'INTERNET':
            return self.tus('INTERNET')
        return self._dial(re.sub(r'[^\w.]', '', uid))

    def adres_ac(self, url):
        vid = youtube_kimligi(url)
        if not vid:
            return self.kaydet_sonuc('ADRES', False, '', 'Bu TV\'de yalnızca YouTube bağlantıları açılabiliyor')
        return self._dial('YouTube', ('v=' + vid).encode(), 'ADRES')

    def simdiki_ad(self):
        return self.durum_sor('GETPROGRAM') if self.ogrenebilir else ''

    def kanal_ogren(self, adet):
        if self.ogrenme and self.ogrenme.get('calisiyor'):
            return False
        self.ogrenme = {'calisiyor': True, 'no': 0, 'adet': adet, 'bulunan': 0, 'iptal': False}
        threading.Thread(target=self._ogren, args=(adet,), daemon=True).start()
        return True

    def _ogren(self, adet):
        # Olmayan bir numara yazılınca TV bulunduğu kanalda kalır; bu yüzden ad değişmediyse o numara atlanır.
        # TV'nin başlangıçta açık olduğu kanal 1'de "değişmeden" görülürse kesin sayılmaz: sonra başka
        # numarada yeniden çıkarsa doğru numarası odur.
        bulunan, onceki, bos = [], self.durum_sor('GETPROGRAM'), 0
        o = self.ogrenme
        o['baslangic'] = onceki
        try:
            for no in range(1, adet + 1):
                if o.get('iptal'):
                    break
                for ch in str(no):
                    self.tus(ch)
                    time.sleep(0.35)
                time.sleep(3.2)
                ad = self.durum_sor('GETPROGRAM')
                o.update(no=no, bulunan=len(bulunan))
                degisti = ad != onceki
                var = next((k for k in bulunan if k['ad'] == ad), None)
                if ad and not var:
                    bulunan.append({'ad': ad, 'no': str(no), 'kesin': degisti})
                    bos = 0
                elif ad and var and degisti and not var['kesin']:
                    var.update(no=str(no), kesin=True)
                    bos = 0
                else:
                    bos += 1
                    if bos >= 10 and bulunan:
                        break
                onceki = ad or onceki
            for b in bulunan:
                b.pop('kesin', None)
            # TV'yi öğrenmeden önceki kanala geri al
            ilk = next((b for b in bulunan if b['ad'] == o.get('baslangic')), None)
            if ilk and not o.get('iptal'):
                for ch in ilk['no']:
                    self.tus(ch)
                    time.sleep(0.35)
            if bulunan:
                kayit = elle_liste(self)
                adlar = {kanal_anahtari(k['ad']) for k in bulunan}
                # Öğrenilenler + numarası elle girilmiş, öğrenilmeyen kanallar
                kalan = [k for k in kayit if k.get('no') and kanal_anahtari(k['ad']) not in adlar
                         and not any(b['no'] == k['no'] for b in bulunan)]
                self.k['kanallar'] = bulunan + kalan
                cihaz_kaydet(self)
        finally:
            o.update(calisiyor=False, bulunan=len(bulunan))

# ── Toshiba kanal listesi ──
# Bu TV'ler kendi kanal listesini ağdan vermiyor. Liste kumandada tutulur: kanala ilk dokunuşta
# TV'deki numarası sorulur, sonra o numara rakam tuşlarıyla açılır.
VARSAYILAN_KANALLAR = [
    'TRT 1', 'ATV', 'Show TV', 'Star TV', 'Kanal D', 'NOW', 'TV8', 'TV8,5', 'Kanal 7', 'Beyaz TV', '360', 'Teve2',
    'TRT 2', 'TRT Haber', 'TRT Spor', 'TRT Spor Yıldız', 'TRT Belgesel', 'TRT Çocuk', 'TRT Müzik', 'TRT Türk',
    'TRT Avaz', 'TRT Kurdî', 'TRT Arabi', 'TRT World', 'TRT 4K', 'A Haber', 'A Spor', 'A Para', 'A2',
    'Habertürk', 'CNN Türk', 'NTV', 'Haber Global', 'TGRT Haber', 'Halk TV', 'Sözcü TV', 'Tele1', 'TV100',
    'Ekol TV', '24', 'Ülke TV', 'Bloomberg HT', 'Flash Haber', 'KRT', 'TV 4', 'DMAX', 'TLC', 'Show Max',
    'Dream Türk', 'Minika Çocuk', 'Minika Go', 'TGRT Belgesel', 'Diyanet TV', 'Lalegül TV', 'Semerkand TV',
    'Kral Pop', 'Power Türk', 'Number1 TV',
]

def elle_liste(c):
    l = c.k.get('kanallar')
    if not isinstance(l, list):
        l = [{'ad': a, 'no': ''} for a in VARSAYILAN_KANALLAR]
    return [k for k in l if isinstance(k, dict) and k.get('ad')]

def _elle_id(ad):
    return 'm:' + (kanal_anahtari(ad) or ad)

def elle_kanallar(c):
    akis_hazirla()
    l = elle_liste(c)
    numarali = sorted((k for k in l if k.get('no')), key=lambda k: (int(k['no']), k['ad']))
    sirali = numarali + [k for k in l if not k.get('no')]
    simdiki = ''
    ad = getattr(c, 'simdiki_ad', lambda: '')()
    if ad:
        hedef = kanal_anahtari(ad)
        simdiki = next((_elle_id(k['ad']) for k in sirali if kanal_anahtari(k['ad']) == hedef), '')
    return {'kanallar': [{'id': _elle_id(k['ad']), 'ad': k['ad'], 'no': k.get('no') or '', 'radyo': False,
                          'logo': '', 'akis': kanal_akisi(k['ad'])} for k in sirali],
            'elle': True, 'simdiki': simdiki or c.son_kanal,
            'ogren': bool(getattr(c, 'ogrenebilir', False)), 'ogrenme': c.ogrenme,
            'akis': {'hazir': bool(AKIS['veri']), 'yukleniyor': AKIS['yukleniyor'],
                     'zaman': int(AKIS['zaman']), 'hata': AKIS['hata']}}

def elle_kanal_ac(c, kid):
    k = next((k for k in elle_liste(c) if _elle_id(k['ad']) == kid), None)
    no = (k or {}).get('no') or (kid[3:] if kid.startswith('no:') else '')
    if not re.match(r'^\d{1,4}$', no or ''):
        return c.kaydet_sonuc('KANAL', False, '', 'Bu kanalın TV\'deki numarası girilmemiş')
    for ch in no:
        if not c.tus(ch):
            return c.kaydet_sonuc('KANAL', False, '', (c.sonuclar.get(ch) or {}).get('hata') or 'TV bağlı değil')
        time.sleep(0.3)
    c.son_kanal = kid if k else ''
    return c.kaydet_sonuc('KANAL', True, 'rakamlar ' + no)

def elle_kanal_duzenle(c, ad, no, sil=False):
    ad = re.sub(r'\s+', ' ', str(ad or '')).strip()[:60]
    no = re.sub(r'\D', '', str(no or ''))[:4]
    if not ad:
        return 'Kanal adı boş'
    l = elle_liste(c)
    anahtar = kanal_anahtari(ad) or ad
    var = next((k for k in l if (kanal_anahtari(k['ad']) or k['ad']) == anahtar), None)
    if sil:
        l = [k for k in l if k is not var]
    elif var:
        var['no'] = no
    else:
        l.append({'ad': ad, 'no': no})
    if no and not sil:
        for k in l:          # aynı numara iki kanalda olmasın
            if k['no'] == no and (kanal_anahtari(k['ad']) or k['ad']) != anahtar:
                k['no'] = ''
    c.k['kanallar'] = l
    cihaz_kaydet(c)
    return None

def ekran_bilgisi(c):
    """Kumandadaki küçük ekran: TV'de hangi kanal/uygulama açık."""
    if isinstance(c, LGTV):
        return c.ekran_bilgisi()
    out = {'uygulama': getattr(c, 'on_uygulama', ''), 'uygulama_adi': '', 'kanal': None}
    if c.tur in TOSHIBA_TURLERI:
        l = elle_liste(c)
        ad = getattr(c, 'simdiki_ad', lambda: '')()
        if not ad and c.son_kanal:
            ad = next((k['ad'] for k in l if _elle_id(k['ad']) == c.son_kanal), '')
        if ad:
            no = next((k.get('no', '') for k in l if kanal_anahtari(k['ad']) == kanal_anahtari(ad)), '')
            out['kanal'] = {'ad': ad, 'no': no, 'akis': kanal_akisi(ad)[:2]}
    return out

# ───────────────────────── YouTube (TV'deki YouTube uygulamasıyla eşleşme) ─────────────────────────
# Telefondaki YouTube uygulamasının "TV'de izle" özelliğiyle aynı yol (YouTube Lounge). Kumanda TV'deki YouTube'a
# bir kez bağlanınca TV hangi videoyu, kaçıncı saniyede, oynatıyor mu duraklatılmış mı — anında bildirir.
# Bağlantı iki yoldan kurulur: TV'nin DIAL servisi (kendiliğinden) ya da TV'deki YouTube'un gösterdiği "TV kodu".
YT_API = 'https://www.youtube.com/api/lounge'

def _yt_istek(yol, form=None, params=None, sure=15, akis=False):
    import urllib.request, urllib.parse
    url = YT_API + yol + ('?' + urllib.parse.urlencode(params) if params else '')
    veri = urllib.parse.urlencode(form).encode() if form is not None else None
    istek = urllib.request.Request(url, data=veri, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 14) kumanda'})
    y = urllib.request.urlopen(istek, timeout=sure)
    return y if akis else y.read().decode('utf-8', 'replace')

def _yt_olaylar(satirlar):
    """'uzunluk\\n[json]' parçalarını çözer: [[sıra, [olay, veri…]], …]"""
    tampon = ''
    for satir in satirlar:
        if not tampon and satir.strip().isdigit():
            continue
        tampon += satir
        try:
            parca = json.loads(tampon)
        except ValueError:
            continue
        tampon = ''
        for o in parca:
            yield o

def dial_youtube_ekran(adres, sure=3):
    """TV'nin DIAL servisinden YouTube ekran kimliği (YouTube uygulaması açıkken verir)."""
    import urllib.request
    yerler = set()
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(0.4)
        m = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\n'
             'ST: urn:dial-multiscreen-org:service:dial:1\r\n\r\n').encode()
        for h in (adres, '239.255.255.250'):
            u.sendto(m, (h, 1900))
        son = time.time() + sure
        while time.time() < son:
            try:
                v, (ip, _) = u.recvfrom(4096)
            except socket.timeout:
                continue
            if ip == adres:
                k = re.search(rb'(?im)^location:\s*(\S+)', v)
                if k:
                    yerler.add(k.group(1).decode())
        u.close()
    except Exception:
        pass
    for yer in yerler:
        try:
            with urllib.request.urlopen(yer, timeout=3) as y:
                uygulama = y.headers.get('Application-URL')
            if not uygulama:
                continue
            with urllib.request.urlopen(uygulama.rstrip('/') + '/YouTube', timeout=3) as y:
                x = y.read(65536).decode('utf-8', 'replace')
            k = re.search(r'<screenId>([^<]+)</screenId>', x)
            if k:
                return k.group(1).strip()
        except Exception:
            continue
    return None

class YouTubeBag:
    """Bir TV'deki YouTube uygulamasıyla Lounge bağlantısı."""
    def __init__(self, cihaz):
        self.c = cihaz
        k = (AYAR.get('youtube') or {}).get(cihaz.id) or {}
        self.ekran_id = k.get('ekran')
        self.cihaz_id = k.get('cihaz') or secrets.token_hex(16)
        self.ekran_adi = k.get('ad', '')
        self.jeton = None
        self.sid = self.gs = None
        self.aid = 0
        self.durum = 'yok' if not self.ekran_id else 'baglaniyor'
        self.mesaj = ''
        self.video = None   # {'id','zaman','sure','durum','alindi'}
        self.calisiyor = False
        self.kilit = threading.Lock()

    def kaydet(self):
        AYAR.setdefault('youtube', {})[self.c.id] = {'ekran': self.ekran_id, 'cihaz': self.cihaz_id, 'ad': self.ekran_adi}
        ayar_yaz(AYAR)

    def ozet(self):
        v = dict(self.video) if self.video else None
        if v:
            v['simdi'] = round(v['zaman'] + (time.time() - v['alindi']) * (1 if v['durum'] == 1 else 0), 2)
            if v['sure']:
                v['simdi'] = min(v['simdi'], v['sure'])
            v['yas'] = round(time.time() - v['alindi'], 1)
            b = YT_BASLIK.get(v['id'])
            if b is None and v['id']:
                threading.Thread(target=youtube_baslik, args=(v['id'],), daemon=True).start()
            v.update(b or {})
        return {'durum': self.durum, 'mesaj': self.mesaj, 'ad': self.ekran_adi, 'video': v}

    # — eşleşme —
    def kod_ile(self, kod):
        kod = re.sub(r'\D', '', str(kod or ''))
        if len(kod) < 8:
            return False, 'TV kodu 12 rakam olmalı'
        try:
            d = json.loads(_yt_istek('/pairing/get_screen', {'pairing_code': kod}))
            s = d['screen']
        except Exception:
            return False, 'Kod kabul edilmedi. TV\'deki YouTube\'da kodu yeniden göster ve tekrar dene.'
        self.ekran_id, self.jeton, self.ekran_adi = s['screenId'], s.get('loungeToken'), s.get('name', '')
        self.kaydet()
        self.baslat()
        return True, ''

    def dial_ile(self):
        e = dial_youtube_ekran(self.c.adres)
        if not e:
            return False, 'TV kendiliğinden bağlanmadı. TV\'deki YouTube\'da Ayarlar → "Telefonu TV\'ye bağla" → "TV kodu ile bağla"daki kodu yaz.'
        self.ekran_id = e
        self.kaydet()
        self.baslat()
        return True, ''

    def unut(self):
        self.ekran_id = None
        self.durum, self.video = 'yok', None
        (AYAR.get('youtube') or {}).pop(self.c.id, None)
        ayar_yaz(AYAR)

    # — bağlantı ve dinleme —
    def baslat(self):
        with self.kilit:
            if self.calisiyor or not self.ekran_id:
                return
            self.calisiyor = True
        threading.Thread(target=self._dongu, daemon=True).start()

    def _jeton_yenile(self):
        d = json.loads(_yt_istek('/pairing/get_lounge_token_batch', {'screen_ids': self.ekran_id}))
        s = d['screens'][0]
        self.jeton = s['loungeToken']

    def _ortak(self):
        return {'name': 'Kumanda', 'loungeIdToken': self.jeton, 'SID': self.sid, 'AID': self.aid,
                'gsessionid': self.gs, 'device': 'REMOTE_CONTROL', 'app': 'youtube-desktop', 'VER': '8', 'v': '2'}

    def _isle(self, olaylar):
        for sira, (olay, *veri) in olaylar:
            self.aid = sira
            d = veri[0] if veri else {}
            if olay == 'c':
                self.sid = d
            elif olay == 'S':
                self.gs = d
            elif olay in ('nowPlaying', 'onStateChange') and isinstance(d, dict):
                vid = d.get('videoId') or (self.video or {}).get('id')
                if olay == 'nowPlaying' and not d.get('videoId'):
                    self.video = None
                    continue
                try:
                    self.video = {'id': vid, 'zaman': float(d.get('currentTime') or 0), 'sure': float(d.get('duration') or 0),
                                  'durum': int(d.get('state', -1)), 'alindi': time.time()}
                except (TypeError, ValueError):
                    pass
            elif olay == 'loungeScreenDisconnected':
                self.video = None
            elif olay == 'loungeStatus' and isinstance(d, dict):
                try:
                    for e in json.loads(d.get('devices', '[]')):
                        if e.get('type') == 'LOUNGE_SCREEN' and e.get('name'):
                            self.ekran_adi = e['name']
                except Exception:
                    pass

    def komut(self, ad, **p):
        if not self.sid:
            return False
        govde = {'count': 1, 'ofs': self.ofs, 'req0__sc': ad}
        for a, v in p.items():
            govde['req0_' + a] = v
        self.ofs += 1
        try:
            _yt_istek('/bc/bind', govde, dict(self._ortak(), RID=self.ofs))
            return True
        except Exception:
            return False

    def _dongu(self):
        import urllib.error
        bekle = 2
        try:
            while self.ekran_id and self.c.id in CIHAZLAR:
                try:
                    if not self.jeton:
                        self._jeton_yenile()
                    self.sid = self.gs = None
                    self.aid, self.ofs = 0, 1
                    govde = {'app': 'web', 'mdx-version': '3', 'name': 'Kumanda', 'id': self.cihaz_id,
                             'device': 'REMOTE_CONTROL', 'capabilities': 'que,dsdtr,atp', 'magnaKey': 'cloudPairedDevice',
                             'ui': 'false', 'theme': 'cl', 'loungeIdToken': self.jeton}
                    try:
                        metin = _yt_istek('/bc/bind', govde, {'RID': 1, 'VER': 8, 'CVER': 1, 'auth_failure_option': 'send_error'})
                    except urllib.error.HTTPError as e:
                        if e.code in (401, 403):
                            self.jeton = None
                        raise
                    self._isle(_yt_olaylar(metin.splitlines()))
                    if not self.sid:
                        raise ConnectionError('YouTube bağlantıyı açmadı')
                    self.durum, self.mesaj, bekle = 'bagli', '', 2
                    self.komut('getNowPlaying')
                    # uzun süreli dinleme: TV her değişiklikte olay gönderir
                    while self.sid:
                        try:
                            y = _yt_istek('/bc/bind', None, dict(self._ortak(), RID='rpc', CI='0', TYPE='xmlhttp'), sure=90, akis=True)
                        except urllib.error.HTTPError as e:
                            if e.code in (400, 404, 410):
                                break                        # oturum bitti → yeniden bağlan
                            if e.code == 401:
                                self.jeton = None
                                break
                            raise
                        with y:
                            self._isle(_yt_olaylar(l.decode('utf-8', 'replace') for l in iter(y.readline, b'')))
                except Exception as e:
                    self.durum, self.mesaj = 'baglaniyor', 'YouTube\'a bağlanılamadı: %s' % e
                    time.sleep(bekle)
                    bekle = min(bekle * 2, 60)
        finally:
            self.calisiyor = False

YOUTUBE = {}
YT_BASLIK = {}

def youtube_baslik(vid):
    """Videonun adı ve kanalı (YouTube oEmbed; anahtar gerekmez)."""
    if vid in YT_BASLIK:
        return YT_BASLIK[vid]
    YT_BASLIK[vid] = {}
    try:
        import urllib.request, urllib.parse
        u = 'https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote('https://www.youtube.com/watch?v=' + vid)
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=8) as y:
            d = json.loads(y.read().decode('utf-8', 'replace'))
        YT_BASLIK[vid] = {'baslik': d.get('title', ''), 'kanal': d.get('author_name', '')}
    except Exception:
        YT_BASLIK.pop(vid, None)
    return YT_BASLIK.get(vid, {})

def _yt_metin(x):
    if isinstance(x, dict):
        return x.get('simpleText') or ''.join(r.get('text', '') for r in x.get('runs') or [])
    return ''

def youtube_ara(q, sinir=25):
    """YouTube araması (youtube.com arama sayfasındaki veriden; anahtar gerekmez)."""
    import urllib.request, urllib.parse
    q = q.strip()
    if not q:
        return []
    u = 'https://www.youtube.com/results?hl=tr&gl=TR&search_query=' + urllib.parse.quote(q)
    istek = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36',
                                              'Accept-Language': 'tr-TR,tr;q=0.9'})
    with urllib.request.urlopen(istek, timeout=12) as y:
        h = y.read(6 * 1024 * 1024).decode('utf-8', 'replace')
    m = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*(\{.+?\});\s*</script>', h, re.S)
    if not m:
        raise ValueError('arama sonucu okunamadı')
    veri = json.loads(m.group(1))
    out, gor = [], set()
    def gez(o):
        if len(out) >= sinir:
            return
        if isinstance(o, dict):
            vr = o.get('videoRenderer') or o.get('compactVideoRenderer') or o.get('videoWithContextRenderer')
            if isinstance(vr, dict) and vr.get('videoId') and vr['videoId'] not in gor:
                gor.add(vr['videoId'])
                out.append({'id': vr['videoId'], 'baslik': _yt_metin(vr.get('title') or vr.get('headline')),
                            'kanal': _yt_metin(vr.get('ownerText') or vr.get('longBylineText') or vr.get('shortBylineText')),
                            'sure': _yt_metin(vr.get('lengthText')), 'izlenme': _yt_metin(vr.get('shortViewCountText') or vr.get('viewCountText')),
                            'zaman': _yt_metin(vr.get('publishedTimeText'))})
                return
            for v in o.values():
                gez(v)
        elif isinstance(o, list):
            for v in o:
                gez(v)
    gez(veri)
    return out

def youtube_bag(c):
    b = YOUTUBE.get(c.id)
    if b is None or b.c is not c:
        b = YOUTUBE[c.id] = YouTubeBag(c)
    if b.ekran_id:
        b.baslat()
    return b

# ── ağda TV'nin markasını/modelini bulma (UPnP) ──
def upnp_bilgileri(ipler, sure=2.5):
    """SSDP ile cihazların açıklama dosyasını bulur: {ip: {'marka','model','ad'}}"""
    import urllib.request
    yerler = {}
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(0.3)
        ara = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\n'
               'ST: %s\r\n\r\n')
        for st in ('ssdp:all', 'urn:dial-multiscreen-org:service:dial:1', 'urn:schemas-upnp-org:device:MediaRenderer:1'):
            for hedef in ['239.255.255.250'] + list(ipler):
                try:
                    u.sendto((ara % st).encode(), (hedef, 1900))
                except Exception:
                    pass
        son = time.time() + sure
        while time.time() < son:
            try:
                v, (ip, _) = u.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if ip in ipler:
                m = re.search(rb'(?im)^location:\s*(\S+)', v)
                if m:
                    yerler.setdefault(ip, set()).add(m.group(1).decode('utf-8', 'replace'))
        u.close()
    except Exception:
        pass
    for ip in ipler:      # VIDAA açıklama dosyası sabit adreste
        yerler.setdefault(ip, set()).add('http://%s:38400/MediaServer/rendererdevicedesc.xml' % ip)
    sonuc = {}
    def oku(ip):
        b = {}
        for yer in sorted(yerler.get(ip, ()))[:6]:
            if urlparse(yer).hostname != ip:
                continue
            try:
                with urllib.request.urlopen(yer, timeout=2) as y:
                    x = y.read(131072).decode('utf-8', 'replace')
            except Exception:
                continue
            for etiket, a in (('manufacturer', 'marka'), ('modelName', 'model'), ('friendlyName', 'ad')):
                m = re.search(r'<%s>\s*([^<]+?)\s*</%s>' % (etiket, etiket), x)
                if m and not b.get(a):
                    b[a] = m.group(1)
            if b.get('marka') and b.get('model'):
                break
        return ip, b
    with concurrent.futures.ThreadPoolExecutor(8) as h:
        for ip, b in h.map(oku, list(ipler)):
            if b:
                sonuc[ip] = b
    return sonuc

def toshiba_sistemi(adres):
    """Toshiba TV'nin hangi sistemle çalıştığını açık portlarından anlar."""
    if port_acik(adres, 6466, 1.2) or port_acik(adres, 6467, 1.2):
        return 'androidtv'
    if port_acik(adres, 36669, 1.2):
        return 'vidaa'
    if port_acik(adres, 56789, 1.2):
        return 'smartcenter'
    return None

# ───────────────────────── cihaz listesi ─────────────────────────
# Ayar dosyasında: "cihazlar": [{"id", "tur", "ad", "adres", "mac", …türe özel eşleşme bilgileri}]
CIHAZLAR = {}
cihaz_kilit = threading.Lock()
TURLER = ('lg', 'adb', 'androidtv', 'vidaa', 'smartcenter')
TOSHIBA_TURLERI = ('androidtv', 'vidaa', 'smartcenter')

def ayar_gocu():
    """Eski sürümün tek TV + tek projeksiyon ayarını cihaz listesine taşır."""
    if isinstance(AYAR.get('cihazlar'), list):
        return
    liste = []
    t = AYAR.get('tv')
    if t and t.get('adres'):
        liste.append({'id': 'tv', 'tur': 'lg', 'ad': t.get('ad') or 'LG TV', 'adres': t['adres'],
                      'anahtar': t.get('anahtar'), 'mac': t.get('mac')})
    p = AYAR.get('projeksiyon')
    if p and p.get('adres'):
        liste.append({'id': 'projeksiyon', 'tur': 'adb', 'ad': p.get('ad') or 'Projeksiyon', 'adres': p['adres']})
    AYAR['cihazlar'] = liste
    AYAR.pop('tv', None)
    AYAR.pop('projeksiyon', None)
    try:
        ayar_yaz(AYAR)
    except Exception:
        pass

def cihaz_olustur(k):
    t = k.get('tur')
    if t == 'lg':
        c = LGTV(k['id'], k.get('ad') or 'LG TV', k['adres'], k.get('anahtar'), k.get('mac'))
        c.k = dict(k)
        return c
    if t == 'adb':
        return ADBCihaz(k['id'], k.get('ad') or 'Projeksiyon', k['adres'])
    sinif = {'androidtv': AndroidTVCihaz, 'vidaa': VidaaCihaz, 'smartcenter': SmartCenterCihaz}.get(t)
    return sinif(k) if sinif else None

def cihazlari_yukle():
    ayar_gocu()
    with cihaz_kilit:
        for c in CIHAZLAR.values():
            try:
                if getattr(c, 'ws', None):
                    c.ws.kapat()
                getattr(c, 'kapat', lambda: None)()
            except Exception:
                pass
        CIHAZLAR.clear()
        for k in AYAR.get('cihazlar') or []:
            if isinstance(k, dict) and k.get('id') and k.get('adres'):
                c = cihaz_olustur(k)
                if c:
                    CIHAZLAR[k['id']] = c

def cihaz_kaydet(c):
    liste = AYAR.setdefault('cihazlar', [])
    if isinstance(c, LGTV):
        yeni = dict(getattr(c, 'k', {}) or {})
        yeni.update(id=c.id, tur='lg', ad=c.ad, adres=c.adres, anahtar=c.anahtar, mac=c.mac)
    elif isinstance(c, ADBCihaz):
        yeni = {'id': c.id, 'tur': 'adb', 'ad': c.ad, 'adres': c.adres}
    else:
        yeni = c.kayit()
    for i, k in enumerate(liste):
        if k.get('id') == c.id:
            liste[i] = yeni
            break
    else:
        liste.append(yeni)
    try:
        ayar_yaz(AYAR)
    except Exception:
        pass

def _yeni_kimlik(tur):
    var = {k.get('id') for k in AYAR.get('cihazlar') or []}
    tercih = {'lg': 'tv', 'adb': 'projeksiyon'}.get(tur, tur)
    if tercih not in var:
        return tercih
    i = 2
    while '%s%d' % (tercih, i) in var:
        i += 1
    return '%s%d' % (tercih, i)

def cihaz_ekle(tur, adres, ad='', mac=''):
    """⚙ → Cihaz ekle. tur: lg / adb / toshiba (sistem otomatik) / androidtv / vidaa / smartcenter"""
    tur, adres = (tur or '').strip(), (adres or '').strip()
    ad = re.sub(r'\s+', ' ', ad or '').strip()[:30]
    if tur == 'adb' and adres and ':' not in adres:
        adres += ':5555'
    if not adres or not re.match(r'^[\w.\-]+(:\d+)?$', adres) or (tur != 'adb' and ':' in adres):
        return None, 'Adres geçersiz (örnek: 192.168.1.50)'
    m = mac_duzelt(mac) if mac else None
    if mac and not m:
        return None, 'MAC adresi geçersiz (örnek: a8:23:fe:12:34:56)'
    if tur == 'toshiba':
        tur = toshiba_sistemi(adres)
        if not tur:
            return None, ('TV\'ye ulaşılamadı. TV açık ve aynı Wi‑Fi\'da olmalı. Google TV\'de: Ayarlar → Sistem → '
                          '"Uzaktan kumanda / Google TV uygulaması"; VIDAA\'da: Ayarlar → Ağ → "Mobil cihazla kontrol" açık olmalı.')
        ad = ad or 'Toshiba TV'
    if tur not in TURLER:
        return None, 'Bilinmeyen cihaz türü'
    liste = AYAR.setdefault('cihazlar', [])
    for k in liste:
        if k.get('tur') == tur and k.get('adres') == adres:
            if m:
                k['mac'] = m
                ayar_yaz(AYAR)
                cihazlari_yukle()
            return k['id'], None
    ad = ad or {'lg': 'LG TV', 'adb': 'Projeksiyon'}.get(tur, 'Toshiba TV')
    adlar = {k.get('ad') for k in liste}
    if ad in adlar:
        i = 2
        while '%s %d' % (ad, i) in adlar:
            i += 1
        ad = '%s %d' % (ad, i)
    k = {'id': _yeni_kimlik(tur), 'tur': tur, 'ad': ad, 'adres': adres}
    if m:
        k['mac'] = m
    liste.append(k)
    ayar_yaz(AYAR)
    c = cihaz_olustur(k)
    with cihaz_kilit:
        CIHAZLAR[k['id']] = c
    c.baglan_arkada()
    if tur != 'adb':
        akis_hazirla()
    return k['id'], None

def cihaz_sil(cid):
    AYAR['cihazlar'] = [k for k in AYAR.get('cihazlar') or [] if k.get('id') != cid]
    ayar_yaz(AYAR)
    c = CIHAZLAR.pop(cid, None)
    if c:
        try:
            if getattr(c, 'ws', None):
                c.ws.kapat()
            getattr(c, 'kapat', lambda: None)()
        except Exception:
            pass
    return c is not None

def cihaz_yeniden_adlandir(cid, ad):
    ad = re.sub(r'\s+', ' ', ad or '').strip()[:30]
    c = CIHAZLAR.get(cid)
    if not c or not ad:
        return False
    c.ad = ad
    cihaz_kaydet(c)
    return True

def cihaz_ayarla(proj_adres, tv_adres, tv_mac):
    """Eski kumanda sayfasının ⚙ menüsü (tek LG TV + tek projeksiyon). Yeni sayfa /ekle ve /sil kullanır."""
    proj_adres, tv_adres = (proj_adres or '').strip(), (tv_adres or '').strip()
    liste = AYAR.setdefault('cihazlar', [])
    for tur, adres, kid in (('adb', proj_adres, 'projeksiyon'), ('lg', tv_adres, 'tv')):
        eski = next((k for k in liste if k.get('id') == kid), None)
        if not adres:
            if eski:
                cihaz_sil(kid)
            continue
        if eski and eski.get('adres') == (adres if tur == 'lg' or ':' in adres else adres + ':5555'):
            if tur == 'lg' and tv_mac:
                m = mac_duzelt(tv_mac)
                if not m:
                    return 'MAC adresi geçersiz (örnek: a8:23:fe:12:34:56)'
                eski['mac'] = m
                ayar_yaz(AYAR)
            continue
        if eski:
            cihaz_sil(kid)
        _, hata = cihaz_ekle(tur, adres, '', tv_mac if tur == 'lg' else '')
        if hata:
            return hata
    cihazlari_yukle()
    for c in CIHAZLAR.values():
        c.baglan_arkada()
    return None

# ───────────────────────── HTTP sunucusu ─────────────────────────
# Kumandayı kullanabilecek sayfalar: indirilen HTML (file:// → "null"),
# bu sunucunun kendi sayfası ve GitHub Pages. Diğer siteler reddedilir.
IZINLI = {'null', 'http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT,
          'https://azataslan065-png.github.io'}
GITHUB_SAYFA = 'https://azataslan065-png.github.io/kumanda/'
# Uygulama olarak yükleme (PWA) dosyaları — köprü klasöründen sunulur
STATIK = {'manifest.webmanifest': 'application/manifest+json', 'sw.js': 'text/javascript; charset=utf-8',
          'ikon-192.png': 'image/png', 'ikon-512.png': 'image/png', 'ikon-maskable-512.png': 'image/png'}

# Köprü ev ağından da erişilebilir (GitHub sayfası telefondaki 127.0.0.1'e erişemediği için).
# Ağdan gelen her istek bu gizli anahtarı taşımak zorunda; telefonun kendisinden gelenler muaf.
def erisim_anahtari():
    a = AYAR.get('erisim_anahtari')
    if not a:
        import secrets
        a = AYAR['erisim_anahtari'] = secrets.token_urlsafe(18)
        try:
            ayar_yaz(AYAR)
        except Exception:
            pass
    return a

def sayi(v, k, vars=0.0, alt=None, ust=None):
    try:
        x = float(v.get(k, vars))
    except (TypeError, ValueError):
        x = vars
    if alt is not None:
        x = max(alt, x)
    if ust is not None:
        x = min(ust, x)
    return x

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def kaynak(self):
        return self.headers.get('Origin')

    def telefondan(self):
        return self.client_address[0].startswith('127.') or self.client_address[0] == '::1'

    def izinli(self, v=None):
        o = self.kaynak()
        if not (o is None or o in IZINLI):
            return False
        if self.telefondan():
            return True
        import hmac
        return hmac.compare_digest(str((v or {}).get('k', '')), erisim_anahtari())

    def yerel_sayfadan(self):
        """Yalnızca telefonda çalışan kendi sayfamız (GitHub değil) bağlantı bilgisini görebilir."""
        return self.telefondan() and self.kaynak() in (None, 'null', 'http://127.0.0.1:%d' % PORT,
                                                        'http://localhost:%d' % PORT)

    def cors(self):
        o = self.kaynak()
        if o in IZINLI:
            self.send_header('Access-Control-Allow-Origin', o)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self):
        o = self.kaynak()
        self.send_response(204 if (o is None or o in IZINLI) else 403)
        self.cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def yanit(self, kod, govde, tur='application/json'):
        if not isinstance(govde, (str, bytes)):
            govde = json.dumps(govde, ensure_ascii=False)
        b = govde.encode() if isinstance(govde, str) else govde
        self.send_response(kod)
        self.cors()
        self.send_header('Content-Type', tur + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        yol = urlparse(self.path)
        q = {k: x[0] for k, x in parse_qs(yol.query).items()}
        if yol.path in ('/', '/index.html'):
            try:
                with open(SAYFA_DOSYASI, 'rb') as f:
                    self.yanit(200, f.read(), 'text/html')
            except OSError:
                self.yanit(200, '<meta charset="utf-8"><p style="font:16px sans-serif;padding:20px">'
                                'index.html bulunamadı. Termux\'ta:<br><code>curl -LO https://raw.githubusercontent.com/'
                                'azataslan065-png/kumanda/main/index.html</code><br>ya da '
                                '<a href="https://azataslan065-png.github.io/kumanda/">kumandayı buradan aç</a>.</p>',
                           'text/html')
        elif yol.path.lstrip('/') in STATIK:
            ad = yol.path.lstrip('/')
            try:
                with open(os.path.join(os.path.dirname(SAYFA_DOSYASI), ad), 'rb') as f:
                    veri = f.read()
                self.send_response(200)
                self.send_header('Content-Type', STATIK[ad])
                self.send_header('Cache-Control', 'no-cache')
                if ad == 'sw.js':
                    self.send_header('Service-Worker-Allowed', '/')
                self.send_header('Content-Length', str(len(veri)))
                self.end_headers()
                self.wfile.write(veri)
            except OSError:
                self.yanit(404, {})
        elif not self.izinli(q):
            self.yanit(403, {'ok': False, 'hata': 'anahtar'})
        elif yol.path == '/durum':
            ozetler = []
            for c in list(CIHAZLAR.values()):
                o = c.ozet()
                o['sistem'] = SISTEM_ADI.get(c.tur, c.tur)
                ozetler.append(o)
            liste = AYAR.get('cihazlar') or []
            ilk = lambda t: next((k for k in liste if k.get('tur') == t), {})
            d = {'surum': 14, 'bildirimler': BILDIRIMLER[-3:], 'adb': ADB_VAR, 'cihazlar': ozetler,
                 # eski kumanda sayfası için
                 'ayar': {'projeksiyon': ilk('adb').get('adres', ''), 'tv': ilk('lg').get('adres', ''),
                          'mac': ilk('lg').get('mac') or ''}}
            if self.yerel_sayfadan():
                ip = yerel_ip()
                if ip and ev_agi_mi(ip):
                    d['github'] = {'sunucu': 'http://%s:%d' % (ip, PORT), 'k': erisim_anahtari(),
                                   'sayfa': GITHUB_SAYFA}
            self.yanit(200, d)
        elif yol.path == '/simdi':
            self.yanit(200, {'kanallar': simdi_yayinda(), 'akis': {'hazir': bool(AKIS['veri']), 'yukleniyor': AKIS['yukleniyor'],
                                                                    'hata': AKIS['hata']}})
        elif yol.path == '/ara':
            self.yanit(200, {'sonuclar': akis_ara(q.get('q', '')[:80])})
        elif yol.path == '/maclar':
            self.yanit(200, {'maclar': maclar(), 'akis': {'hazir': bool(AKIS['veri']), 'yukleniyor': AKIS['yukleniyor']}})
        elif yol.path == '/hatirlatmalar':
            import shutil
            self.yanit(200, {'liste': hatirlatmalar(), 'bildirimler': BILDIRIMLER[-10:],
                             'telefon': bool(shutil.which('termux-notification')), 'once': HATIRLATMA_ONCE})
        elif yol.path == '/izleme':
            self.yanit(200, izleme_ozeti())
        elif yol.path == '/tara':
            self.yanit(200, ag_tara())
        elif yol.path == '/girisler':
            c = CIHAZLAR.get(q.get('c'))
            self.yanit(200, {'girisler': c.girisler() if c is not None and hasattr(c, 'girisler') and c.durum == 'bagli' else []})
        elif yol.path == '/kanallar':
            c = CIHAZLAR.get(q.get('c'))
            if isinstance(c, LGTV):
                self.yanit(200, c.kanallar())
            elif c is not None and c.tur in TOSHIBA_TURLERI:
                self.yanit(200, elle_kanallar(c))
            else:
                self.yanit(200, {'kanallar': [], 'hata': 'Bu cihazda kanal listesi yok'})
        elif yol.path == '/tani':
            c = CIHAZLAR.get(q.get('c'))
            if not c:
                self.yanit(200, {'hata': 'cihaz yok'})
            else:
                tuslar = LG_TUS if c.tur == 'lg' else ADB_TUS if c.tur == 'adb' else c.TUSLAR
                self.yanit(200, {'tur': c.tur, 'bilgi': getattr(c, 'bilgi', {}), 'sonuclar': c.sonuclar,
                                 'tuslar': sorted(tuslar)})
        elif yol.path == '/uygulamalar':
            c = CIHAZLAR.get(q.get('c'))
            if isinstance(c, LGTV) and c.durum == 'bagli':
                liste = c.uygulamalari_al() if q.get('yenile') or not c.uygulamalar else c.uygulamalar
                self.yanit(200, {'uygulamalar': [{'id': a['id'], 'ad': a['ad'], 'ikon': bool(a.get('ikon')), 'renk': a.get('renk', '')} for a in liste]})
            elif c is not None and c.tur in TOSHIBA_TURLERI:
                liste = c.uygulamalari_al() if (q.get('yenile') or not c.uygulamalar) and c.durum == 'bagli' else (c.uygulamalar or c.uygulamalari_al())
                self.yanit(200, {'uygulamalar': [{'id': a['id'], 'ad': a['ad'], 'ikon': False, 'renk': a.get('renk', '')} for a in liste]})
            else:
                self.yanit(200, {'uygulamalar': [], 'hata': 'TV bağlı değil'})
        elif yol.path == '/ikon':
            c = CIHAZLAR.get(q.get('c'))
            veri, tur = c.ikon_getir(q.get('id', '')) if isinstance(c, LGTV) else (None, None)
            if veri:
                self.send_response(200)
                self.cors()
                self.send_header('Content-Type', tur)
                self.send_header('Cache-Control', 'public, max-age=604800, immutable')
                self.send_header('Content-Length', str(len(veri)))
                self.end_headers()
                self.wfile.write(veri)
            else:
                self.yanit(404, {})
        elif yol.path == '/youtube':
            c = CIHAZLAR.get(q.get('c'))
            if c is None or c.tur == 'adb':
                self.yanit(200, {'durum': 'yok'})
            else:
                yb = youtube_bag(c)
                if not yb.ekran_id and c.durum == 'bagli' and time.time() - getattr(yb, 'dial_zaman', 0) > 60:
                    yb.dial_zaman = time.time()          # TV'deki YouTube açıksa kendiliğinden eşleşmeyi dene
                    threading.Thread(target=yb.dial_ile, daemon=True).start()
                o = yb.ozet()
                o['eslesik'] = bool(yb.ekran_id)
                self.yanit(200, o)
        elif yol.path == '/youtube_ara':
            try:
                self.yanit(200, {'sonuclar': youtube_ara(q.get('q', '')[:100])})
            except Exception as e:
                self.yanit(200, {'sonuclar': [], 'hata': 'YouTube araması yapılamadı (internet bağlantısı?): %s' % e})
        elif yol.path == '/simdiki':
            c = CIHAZLAR.get(q.get('c'))
            s = ''
            if isinstance(c, LGTV) and c.durum == 'bagli':
                s = c.simdiki_kanal()
            elif c is not None and c.tur in TOSHIBA_TURLERI:
                s = elle_kanallar(c)['simdiki']
            self.yanit(200, {'simdiki': s})
        else:
            self.yanit(404, {})

    def do_POST(self):
        # Gövde her durumda okunmalı; okunmazsa açık bağlantıda sonraki istek sanılır.
        try:
            n = min(int(self.headers.get('Content-Length', 0) or 0), 65536)
        except ValueError:
            n = 0
        govde = self.rfile.read(n).decode('utf-8', 'replace')
        v = {k: x[0] for k, x in parse_qs(govde).items()}
        if not self.izinli(v):
            self.close_connection = True
            self.yanit(403, {'ok': False, 'hata': 'anahtar'})
            return
        if self.path == '/ayarla':
            hata = cihaz_ayarla(v.get('projeksiyon'), v.get('tv'), v.get('mac'))
            self.yanit(200, {'ok': hata is None, 'hata': hata})
            return
        if self.path == '/ekle':
            try:
                cid, hata = cihaz_ekle(v.get('tur'), v.get('adres'), v.get('ad'), v.get('mac'))
            except Exception as e:
                cid, hata = None, str(e)
            self.yanit(200, {'ok': hata is None, 'id': cid, 'hata': hata})
            return
        if self.path == '/hatirla':
            hid, hata = hatirlatma_ekle(v.get('kanal'), v.get('baslik'), v.get('bas'), v.get('bit'))
            self.yanit(200, {'ok': hata is None, 'id': hid, 'hata': hata})
            return
        if self.path == '/hatirla_sil':
            hatirlatma_sil(v.get('id', ''))
            self.yanit(200, {'ok': True})
            return
        if self.path == '/sil':
            self.yanit(200, {'ok': cihaz_sil(v.get('c', ''))})
            return
        if self.path == '/ad':
            self.yanit(200, {'ok': cihaz_yeniden_adlandir(v.get('c', ''), v.get('ad', ''))})
            return
        c = CIHAZLAR.get(v.get('c'))
        if c is None:
            self.yanit(200, {'ok': False, 'hata': 'Cihaz seçilmedi ya da ayarlanmadı'})
            return
        ok, neden = False, ''
        toshiba = c.tur in TOSHIBA_TURLERI
        try:
            if self.path == '/tus':
                ok = c.tus(v.get('t', ''))
            elif self.path == '/yazi':
                ok = c.yazi(v.get('t', '')[:500])
            elif self.path == '/baglan':
                if toshiba and c.durum == 'kod':      # "Yeni kod iste"
                    c.kapat()
                    c.durum, c.kod_bilgi = 'kapali', None
                if getattr(c, 'durum', '') in ('hata', 'kapali'):
                    c.son_deneme = 0
                c.baglan_arkada()
                ok = True
            elif self.path == '/youtube_bagla':
                yb = youtube_bag(c)
                ok, neden = yb.kod_ile(v['kod']) if v.get('kod') else yb.dial_ile()
            elif self.path == '/youtube_komut':
                yb = youtube_bag(c)
                k = v.get('komut', '')
                if yb.durum != 'bagli':
                    ok, neden = False, 'Önce TV\'deki YouTube\'u kumandaya bağla'
                elif k == 'sar':
                    ok = yb.komut('seekTo', newTime='%.1f' % sayi(v, 't', 0, 0, 86400))
                    if ok and yb.video:
                        yb.video.update(zaman=sayi(v, 't', 0, 0, 86400), alindi=time.time())
                else:
                    ad = {'oynat': 'play', 'duraklat': 'pause', 'sonraki': 'next', 'onceki': 'previous'}.get(k)
                    ok = bool(ad) and yb.komut(ad)
                    if ok and yb.video and k in ('oynat', 'duraklat'):
                        yb.video.update(zaman=yb.ozet()['video']['simdi'], durum=1 if k == 'oynat' else 2, alindi=time.time())
                if not ok and not neden:
                    neden = 'TV\'deki YouTube komutu almadı'
            elif self.path == '/youtube_oynat':
                vid = v.get('id', '')
                if not re.match(r'^[A-Za-z0-9_-]{6,20}$', vid):
                    ok, neden = False, 'Geçersiz video'
                else:
                    yb = youtube_bag(c)
                    if yb.durum == 'bagli':
                        ok = yb.komut('addVideo', videoId=vid, videoSources='XX') if v.get('sira') == '1' else yb.komut('setPlaylist', videoId=vid)
                    elif v.get('sira') == '1':
                        ok, neden = False, 'Sıraya eklemek için TV\'deki YouTube\'u kumandaya bağla'
                    else:
                        ok = c.adres_ac('https://www.youtube.com/watch?v=' + vid)   # bağlı değilse TV'de bağlantıyla aç
            elif self.path == '/youtube_unut':
                youtube_bag(c).unut()
                ok = True
            elif self.path == '/eslestir':
                ok, neden = c.kod_gir(v.get('kod', '')) if toshiba else (False, 'Bu cihaz kodla eşleşmiyor')
            elif self.path == '/uygulama':
                ok = c.uygulama(v.get('id', ''))
            elif self.path == '/ac':
                url = v.get('url') or CANLI_TV_URL
                ok = c.adres_ac(url) if url.startswith(('http://', 'https://')) else False
            elif self.path == '/giris' and hasattr(c, 'giris_sec'):
                ok = c.giris_sec(v.get('id', ''))
            elif self.path == '/kanal' and isinstance(c, LGTV):
                ok = c.kanal_ac(v.get('id', ''))
            elif self.path == '/kanal' and toshiba:
                ok = elle_kanal_ac(c, v.get('id', ''))
                if not ok:
                    neden = (c.sonuclar.get('KANAL') or {}).get('hata', '')
            elif self.path == '/kanal_no' and toshiba:
                neden = elle_kanal_duzenle(c, v.get('ad'), v.get('no'), v.get('sil') == '1') or ''
                ok = not neden
            elif self.path == '/kanal_ogren' and toshiba:
                if v.get('iptal') == '1':
                    if c.ogrenme:
                        c.ogrenme['iptal'] = True
                    ok = True
                elif not getattr(c, 'ogrenebilir', False):
                    neden = 'Bu TV kanal adını bildirmiyor; numaraları elle gir'
                else:
                    ok = c.kanal_ogren(int(sayi(v, 'adet', 60, 5, 300)))
                    neden = '' if ok else 'Öğrenme zaten sürüyor'
            elif self.path == '/imlec' and isinstance(c, LGTV):
                ok = c.imlec_hareket(sayi(v, 'dx', 0, -300, 300), sayi(v, 'dy', 0, -300, 300))
            elif self.path == '/tikla' and isinstance(c, LGTV):
                ok = c.tikla()
            elif self.path == '/teker' and isinstance(c, LGTV):
                ok = c.kaydir(sayi(v, 'dx', 0, -50, 50), sayi(v, 'dy', 0, -50, 50))
            elif self.path == '/dokun' and isinstance(c, ADBCihaz):
                ok = c.dokun(sayi(v, 'x', 0, 0, 1), sayi(v, 'y', 0, 0, 1))
            elif self.path == '/kaydir' and isinstance(c, ADBCihaz):
                ok = c.kaydir_mutlak(*(sayi(v, k, 0, 0, 1) for k in ('x1', 'y1', 'x2', 'y2')),
                                     int(sayi(v, 'ms', 250, 50, 2000)))
            else:
                self.yanit(404, {'ok': False})
                return
        except Exception as e:
            self.yanit(200, {'ok': False, 'hata': str(e)})
            return
        if not ok and not neden:
            anahtar = {'/yazi': 'YAZI', '/tus': v.get('t', ''), '/uygulama': 'UYG:' + v.get('id', ''), '/ac': 'ADRES',
                       '/giris': 'GIRIS'}.get(self.path)
            neden = ((getattr(c, 'sonuclar', {}) or {}).get(anahtar) or {}).get('hata', '') if anahtar else ''
        self.yanit(200, {'ok': bool(ok), 'durum': c.durum, 'mesaj': c.mesaj, 'hata': neden})

ADB_VAR = False

def main():
    global ADB_VAR
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help', 'yardim'):
        print('python kumanda.py              → köprüyü başlatır\n'
              'python kumanda.py --tara       → ağdaki TV/projeksiyonları bulur\n'
              'python kumanda.py --tv IP      → LG TV ekler\n'
              'python kumanda.py --toshiba IP → Toshiba TV ekler (sistemi kendisi bulur)\n'
              'python kumanda.py IP:5555      → projeksiyon (ADB) ekler')
        return
    ayar_gocu()
    if args and args[0] == '--tara':
        print('Ağ taranıyor…')
        r = ag_tara()
        if r.get('hata'):
            print(r['hata'])
        for c in r.get('cihazlar', []):
            print('•', c['ad'], '·', c['sistem'], '→', c['adres'], ('(%s %s)' % (c['marka'], c['model'])).replace('( )', ''))
        if not r.get('cihazlar') and not r.get('hata'):
            print('Cihaz bulunamadı.')
        return
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('--tv', '--toshiba') and i + 1 < len(args):
            _, hata = cihaz_ekle('lg' if a == '--tv' else 'toshiba', args[i + 1])
            if hata:
                print(hata)
            i += 2
        elif a == '--mac' and i + 1 < len(args):
            for k in AYAR.get('cihazlar') or []:
                if k.get('tur') == 'lg':
                    k['mac'] = mac_duzelt(args[i + 1])
            ayar_yaz(AYAR)
            i += 2
        else:
            _, hata = cihaz_ekle('adb', a)
            if hata:
                print(hata)
            i += 1

    ADB_VAR = adb_var()
    cihazlari_yukle()
    if not CIHAZLAR:
        print('Henüz cihaz eklenmedi. Kumanda sayfasındaki ⚙ menüsünden ekleyebilirsin.')
    for c in CIHAZLAR.values():
        print('•', c.ad, '(%s)' % SISTEM_ADI.get(c.tur, c.tur), '→', c.adres)
        c.baglan_arkada()
    erisim_anahtari()
    threading.Thread(target=hatirlatma_dongusu, daemon=True).start()
    threading.Thread(target=izleme_dongusu, daemon=True).start()
    if any(c.tur != 'adb' for c in CIHAZLAR.values()):
        akis_hazirla()   # yayın akışını arkada indir
    print('\nKumanda hazır → tarayıcıda aç: http://127.0.0.1:%d' % PORT)
    ip = yerel_ip()
    if ip and ev_agi_mi(ip):
        print('GitHub sayfası için: kumandada ⚙ → "GitHub sayfasını bağla"  (ağ adresi %s)' % ip)
    print('(Kapatmak için Ctrl+C)')
    try:
        ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
    except KeyboardInterrupt:
        print('\nKöprü kapatıldı.')
    except OSError as e:
        print('Sunucu başlatılamadı:', e, '\nKöprü zaten açık olabilir; diğer Termux oturumunu kontrol et.')

if __name__ == '__main__':
    main()
