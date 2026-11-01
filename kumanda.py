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

# Kumanda tuşu → (yöntem, değer). 'b' = imleç soketi tuşu, 'r' = ssap isteği
LG_TUS = {
    'YUKARI': ('b', 'UP'), 'ASAGI': ('b', 'DOWN'), 'SOL': ('b', 'LEFT'), 'SAG': ('b', 'RIGHT'),
    'TAMAM': ('b', 'ENTER'), 'GERI': ('b', 'BACK'), 'ANA': ('b', 'HOME'), 'CIKIS': ('b', 'EXIT'),
    'AYARLAR': ('b', 'MENU'), 'BILGI': ('b', 'INFO'), 'SESSIZ': ('b', 'MUTE'),
    'SES+': ('r', 'ssap://audio/volumeUp'), 'SES-': ('r', 'ssap://audio/volumeDown'),
    'KANAL+': ('r', 'ssap://tv/channelUp'), 'KANAL-': ('r', 'ssap://tv/channelDown'),
    'OYNAT': ('r', 'ssap://media.controls/play'), 'DURAKLAT': ('r', 'ssap://media.controls/pause'),
    'DURDUR': ('r', 'ssap://media.controls/stop'),
    'GERI_SAR': ('r', 'ssap://media.controls/rewind'), 'ILERI_SAR': ('r', 'ssap://media.controls/fastForward'),
    'KIRMIZI': ('b', 'RED'), 'YESIL': ('b', 'GREEN'), 'SARI': ('b', 'YELLOW'), 'MAVI': ('b', 'BLUE'),
    'SIL': ('r', 'ssap://com.webos.service.ime/deleteCharacters'),
    'ENTER': ('r', 'ssap://com.webos.service.ime/sendEnterKey'),
}
for _r in '0123456789':
    LG_TUS[_r] = ('b', _r)

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

    def ozet(self):
        # Sayfa durumu düzenli sorduğu için: TV kendi kumandasıyla açılırsa burada yakalanır.
        if self.durum == 'kapali' and time.time() - self.son_deneme > 8:
            self.baglan_arkada()
        return {'id': self.id, 'tur': self.tur, 'ad': self.ad, 'adres': self.adres,
                'durum': self.durum, 'mesaj': self.mesaj, 'mac': self.mac or ''}

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
            self._istek_bekle('hello', None, {}, sure=4, kayit=True, mid='hello')
            self._istek_bekle('request', 'ssap://system/getSystemInfo', {}, sure=4, kayit=True)
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
                return self.basarili(self.istek('ssap://system/turnOff'))
            if not self.mac:
                self.mesaj = 'TV\'yi açmak için ⚙ menüsünden TV\'nin MAC adresini gir'
                return False
            ok = wake_on_lan(self.mac, self.adres)
            self.mesaj = 'Açma sinyali gönderildi, TV birkaç saniyede açılır' if ok else 'Açma sinyali gönderilemedi'
            threading.Timer(6, self.baglan_arkada).start()
            threading.Timer(15, self.baglan_arkada).start()
            return ok
        e = LG_TUS.get(ad)
        if not e:
            return False
        if e[0] == 'b':
            return self.imlec_gonder([('type', 'button'), ('name', e[1])])
        yuk = {'count': 1} if ad == 'SIL' else None
        return self.basarili(self.istek(e[1], yuk))

    def yazi(self, metin):
        return self.basarili(self.istek('ssap://com.webos.service.ime/insertText', {'text': metin, 'replace': 0}))

    def imlec_hareket(self, dx, dy):
        return self.imlec_gonder([('type', 'move'), ('dx', int(dx)), ('dy', int(dy)), ('down', 0)])

    def tikla(self):
        return self.imlec_gonder([('type', 'click')])

    def kaydir(self, dx, dy):
        return self.imlec_gonder([('type', 'scroll'), ('dx', int(dx)), ('dy', int(dy))])

    def uygulama(self, uid):
        return self.basarili(self.istek('ssap://system.launcher/launch', {'id': uid}))

    def adres_ac(self, url):
        return self.basarili(self.istek('ssap://system.launcher/open', {'target': url}))

    def girisler(self):
        c = self.istek('ssap://tv/getExternalInputList')
        d = ((c or {}).get('payload') or {}).get('devices') or []
        return [{'id': x.get('id'), 'ad': x.get('label') or x.get('id'), 'bagli': x.get('connected', True)} for x in d]

    def giris_sec(self, gid):
        return self.basarili(self.istek('ssap://tv/switchInput', {'inputId': gid}))

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

# ───────────────────────── ADB (Android projeksiyon / TV) ─────────────────────────
ADB_TUS = {
    'YUKARI': 19, 'ASAGI': 20, 'SOL': 21, 'SAG': 22, 'TAMAM': 23, 'GERI': 4, 'ANA': 3, 'MENU': 82,
    'AYARLAR': 176, 'SES+': 24, 'SES-': 25, 'SESSIZ': 164, 'KANAL+': 166, 'KANAL-': 167,
    'OYNAT_DURAKLAT': 85, 'OYNAT': 126, 'DURAKLAT': 127, 'DURDUR': 86, 'GERI_SAR': 89, 'ILERI_SAR': 90,
    'SON': 187, 'SIL': 67, 'ENTER': 66, 'GUC': 26, 'BILGI': 165, 'CIKIS': 4,
    'KIRMIZI': 183, 'YESIL': 184, 'SARI': 185, 'MAVI': 186,
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
        return self.gonder('input keyevent %d' % k) if k is not None else False

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
        elif not self.izinli():
            self.yanit(403, {'ok': False})
        elif yol.path == '/durum':
            self.yanit(200, {'surum': 2, 'adb': ADB_VAR,
                             'cihazlar': [c.ozet() for c in list(CIHAZLAR.values())],
                             'ayar': {'projeksiyon': (AYAR.get('projeksiyon') or {}).get('adres', ''),
                                      'tv': (AYAR.get('tv') or {}).get('adres', ''),
                                      'mac': (AYAR.get('tv') or {}).get('mac') or ''}})
        elif yol.path == '/tara':
            self.yanit(200, ag_tara())
        elif yol.path == '/girisler':
            c = CIHAZLAR.get(q.get('c'))
            self.yanit(200, {'girisler': c.girisler() if isinstance(c, LGTV) else []})
        else:
            self.yanit(404, {})

    def do_POST(self):
        # Gövde her durumda okunmalı; okunmazsa açık bağlantıda sonraki istek sanılır.
        try:
            n = min(int(self.headers.get('Content-Length', 0) or 0), 65536)
        except ValueError:
            n = 0
        govde = self.rfile.read(n).decode('utf-8', 'replace')
        if not self.izinli():
            self.close_connection = True
            self.yanit(403, {'ok': False})
            return
        v = {k: x[0] for k, x in parse_qs(govde).items()}
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
        self.yanit(200, {'ok': bool(ok), 'durum': c.durum, 'mesaj': c.mesaj})

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
    print('\nKumanda hazır → tarayıcıda aç: http://127.0.0.1:%d' % PORT)
    print('(Kapatmak için Ctrl+C)')
    try:
        ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
    except KeyboardInterrupt:
        print('\nKöprü kapatıldı.')
    except OSError as e:
        print('Sunucu başlatılamadı:', e, '\nKöprü zaten açık olabilir; diğer Termux oturumunu kontrol et.')

if __name__ == '__main__':
    main()
