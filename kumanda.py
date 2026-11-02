#!/usr/bin/env python3
# Telefon Kumandası köprüsü — Termux üzerinde çalışır.
# Desteklenen cihazlar:
#   • Android projeksiyon / Android TV  (ADB, ağ üzerinden)
#   • LG akıllı TV (webOS, ağ üzerinden — ek kurulum gerekmez)
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

def ag_tara():
    ip = yerel_ip()
    if not ip or not ev_agi_mi(ip):
        return {'hata': WIFI_UYARI, 'adb': [], 'lg': []}
    taban = ip.rsplit('.', 1)[0]
    hedefler = [('%s.%d' % (taban, i), p) for i in range(1, 255) for p in (5555, 3000, 3001)]
    adb, lg = [], set()
    with concurrent.futures.ThreadPoolExecutor(128) as h:
        for (adres, p), acik in zip(hedefler, h.map(lambda x: port_acik(*x), hedefler)):
            if not acik or adres == ip:
                continue
            if p == 5555:
                adb.append(adres + ':5555')
            else:
                lg.add(adres)
    # Her adres "açık" görünüyorsa ağ bağlantıları yakalıyor demektir; liste güvenilmez.
    if len(adb) > 20 or len(lg) > 20:
        return {'hata': 'Bu ağda arama güvenilir sonuç vermiyor. Adresi TV\'nin ağ ayarlarından bakıp elle yaz.',
                'adb': [], 'lg': []}
    return {'ag': taban + '.x', 'adb': adb, 'lg': sorted(lg, key=lambda a: int(a.rsplit('.', 1)[1]))}

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
        birlesik, hatalar = {}, []
        for tur, url in AKIS_KAYNAKLARI:
            try:
                veri = _indir(url)
                parca = _akis_json(veri) if tur == 'json' else _akis_xml(veri)
            except Exception as e:
                hatalar.append('%s: %s' % (url.rsplit('/', 1)[-1], e))
                continue
            for ad, liste in parca.items():
                anahtar = _akis_anahtarlari(ad)[0]
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
        AKIS.update(veri=birlesik, zaman=time.time(), hata='; '.join(hatalar))
        try:
            with open(AKIS_DOSYASI + '.tmp', 'w', encoding='utf-8') as f:
                json.dump({'zaman': AKIS['zaman'], 'veri': birlesik}, f, ensure_ascii=False, separators=(',', ':'))
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

# ───────────────────────── cihaz listesi ─────────────────────────
CIHAZLAR = {}
cihaz_kilit = threading.Lock()

def cihazlari_yukle():
    with cihaz_kilit:
        CIHAZLAR.clear()
        p = AYAR.get('projeksiyon')
        if p and p.get('adres'):
            CIHAZLAR['projeksiyon'] = ADBCihaz('projeksiyon', p.get('ad') or 'Projeksiyon', p['adres'])
        t = AYAR.get('tv')
        if t and t.get('adres'):
            CIHAZLAR['tv'] = LGTV('tv', t.get('ad') or 'LG TV', t['adres'], t.get('anahtar'), t.get('mac'))

def cihaz_kaydet(c):
    if isinstance(c, LGTV):
        AYAR['tv'] = {'ad': c.ad, 'adres': c.adres, 'anahtar': c.anahtar, 'mac': c.mac}
    else:
        AYAR['projeksiyon'] = {'ad': c.ad, 'adres': c.adres}
    try:
        ayar_yaz(AYAR)
    except Exception:
        pass

def cihaz_ayarla(proj_adres, tv_adres, tv_mac):
    """Sayfadaki ⚙ menüsünden gelen adresleri uygular. Boş alan = o cihazı kaldır."""
    proj_adres = (proj_adres or '').strip()
    tv_adres = (tv_adres or '').strip()
    if proj_adres and ':' not in proj_adres:
        proj_adres += ':5555'
    if proj_adres and not re.match(r'^[\w.\-]+:\d+$', proj_adres):
        return 'Projeksiyon adresi geçersiz (örnek: 192.168.1.40:5555)'
    if tv_adres and not re.match(r'^[\w.\-]+$', tv_adres):
        return 'TV adresi geçersiz (örnek: 192.168.1.50)'
    mac = mac_duzelt(tv_mac) if tv_mac else None
    if tv_mac and not mac:
        return 'MAC adresi geçersiz (örnek: a8:23:fe:12:34:56)'

    if proj_adres:
        eski = AYAR.get('projeksiyon') or {}
        AYAR['projeksiyon'] = {'ad': eski.get('ad') or 'Projeksiyon', 'adres': proj_adres}
    else:
        AYAR.pop('projeksiyon', None)
    if tv_adres:
        eski = AYAR.get('tv') or {}
        ayni = eski.get('adres') == tv_adres
        AYAR['tv'] = {'ad': eski.get('ad') or 'LG TV', 'adres': tv_adres,
                      'anahtar': eski.get('anahtar') if ayni else None,
                      'mac': mac or (eski.get('mac') if ayni else None)}
    else:
        AYAR.pop('tv', None)
    ayar_yaz(AYAR)
    eski_tv = CIHAZLAR.get('tv')
    if eski_tv and eski_tv.ws:
        eski_tv.ws.kapat()
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
            d = {'surum': 8, 'adb': ADB_VAR,
                 'cihazlar': [c.ozet() for c in list(CIHAZLAR.values())],
                 'ayar': {'projeksiyon': (AYAR.get('projeksiyon') or {}).get('adres', ''),
                          'tv': (AYAR.get('tv') or {}).get('adres', ''),
                          'mac': (AYAR.get('tv') or {}).get('mac') or ''}}
            if self.yerel_sayfadan():
                ip = yerel_ip()
                if ip and ev_agi_mi(ip):
                    d['github'] = {'sunucu': 'http://%s:%d' % (ip, PORT), 'k': erisim_anahtari(),
                                   'sayfa': GITHUB_SAYFA}
            self.yanit(200, d)
        elif yol.path == '/tara':
            self.yanit(200, ag_tara())
        elif yol.path == '/girisler':
            c = CIHAZLAR.get(q.get('c'))
            self.yanit(200, {'girisler': c.girisler() if isinstance(c, LGTV) else []})
        elif yol.path == '/kanallar':
            c = CIHAZLAR.get(q.get('c'))
            self.yanit(200, c.kanallar() if isinstance(c, LGTV) else {'kanallar': [], 'hata': 'Kanal listesi yalnızca LG TV\'de var'})
        elif yol.path == '/tani':
            c = CIHAZLAR.get(q.get('c'))
            if not c:
                self.yanit(200, {'hata': 'cihaz yok'})
            else:
                self.yanit(200, {'tur': c.tur, 'bilgi': getattr(c, 'bilgi', {}), 'sonuclar': c.sonuclar,
                                 'tuslar': sorted(LG_TUS if c.tur == 'lg' else ADB_TUS)})
        elif yol.path == '/uygulamalar':
            c = CIHAZLAR.get(q.get('c'))
            if isinstance(c, LGTV) and c.durum == 'bagli':
                liste = c.uygulamalari_al() if q.get('yenile') or not c.uygulamalar else c.uygulamalar
                self.yanit(200, {'uygulamalar': [{'id': a['id'], 'ad': a['ad'], 'ikon': bool(a.get('ikon')), 'renk': a.get('renk', '')} for a in liste]})
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
        elif yol.path == '/simdiki':
            c = CIHAZLAR.get(q.get('c'))
            self.yanit(200, {'simdiki': c.simdiki_kanal() if isinstance(c, LGTV) and c.durum == 'bagli' else ''})
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
        c = CIHAZLAR.get(v.get('c'))
        if c is None:
            self.yanit(200, {'ok': False, 'hata': 'Cihaz seçilmedi ya da ayarlanmadı'})
            return
        ok = False
        try:
            if self.path == '/tus':
                ok = c.tus(v.get('t', ''))
            elif self.path == '/yazi':
                ok = c.yazi(v.get('t', '')[:500])
            elif self.path == '/baglan':
                c.baglan_arkada()
                ok = True
            elif self.path == '/uygulama':
                ok = c.uygulama(v.get('id', ''))
            elif self.path == '/ac':
                url = v.get('url') or CANLI_TV_URL
                ok = c.adres_ac(url) if url.startswith(('http://', 'https://')) else False
            elif self.path == '/giris' and isinstance(c, LGTV):
                ok = c.giris_sec(v.get('id', ''))
            elif self.path == '/kanal' and isinstance(c, LGTV):
                ok = c.kanal_ac(v.get('id', ''))
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
        neden = ''
        if not ok:
            anahtar = {'/yazi': 'YAZI', '/tus': v.get('t', ''), '/uygulama': 'UYG:' + v.get('id', ''), '/ac': 'ADRES'}.get(self.path)
            neden = ((getattr(c, 'sonuclar', {}) or {}).get(anahtar) or {}).get('hata', '') if anahtar else ''
        self.yanit(200, {'ok': bool(ok), 'durum': c.durum, 'mesaj': c.mesaj, 'hata': neden})

ADB_VAR = False

def main():
    global ADB_VAR
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help', 'yardim'):
        print('python kumanda.py              → köprüyü başlatır\n'
              'python kumanda.py --tara       → ağdaki projeksiyon/TV\'leri bulur\n'
              'python kumanda.py --tv IP      → LG TV adresini kaydeder\n'
              'python kumanda.py --mac MAC    → TV\'yi açmak için MAC adresini kaydeder\n'
              'python kumanda.py IP:5555      → projeksiyon adresini kaydeder')
        return
    if args and args[0] == '--tara':
        print('Ağ taranıyor…')
        r = ag_tara()
        print('Projeksiyon/Android (ADB):', ', '.join(r['adb']) or 'bulunamadı')
        print('LG TV:', ', '.join(r['lg']) or 'bulunamadı')
        return
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--tv' and i + 1 < len(args):
            AYAR['tv'] = {'ad': 'LG TV', 'adres': args[i + 1], 'anahtar': None,
                          'mac': (AYAR.get('tv') or {}).get('mac')}
            i += 2
        elif a == '--mac' and i + 1 < len(args):
            AYAR.setdefault('tv', {'ad': 'LG TV', 'adres': ''})['mac'] = mac_duzelt(args[i + 1])
            i += 2
        else:
            AYAR['projeksiyon'] = {'ad': 'Projeksiyon', 'adres': a if ':' in a else a + ':5555'}
            i += 1
    if args:
        ayar_yaz(AYAR)

    ADB_VAR = adb_var()
    cihazlari_yukle()
    if not CIHAZLAR:
        print('Henüz cihaz eklenmedi. Kumanda sayfasındaki ⚙ menüsünden ekleyebilirsin.')
    for c in CIHAZLAR.values():
        print('•', c.ad, '→', c.adres)
        c.baglan_arkada()
    erisim_anahtari()
    if 'tv' in CIHAZLAR:
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
