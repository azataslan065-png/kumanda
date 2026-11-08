# Kumanda

Telefondan projeksiyon ve LG TV kontrolü + Canlı TV.

- `index.html` — kumanda arayüzü → https://azataslan065-png.github.io/kumanda/
- `tv/index.html` — Canlı TV → https://azataslan065-png.github.io/kumanda/tv/
- `kumanda.py` — Termux köprüsü (yalnızca Python, ek paket gerekmez)

## Desteklenen cihazlar
Aynı anda birden fazla cihaz eklenebilir; kumandanın üstündeki düğmelerle aralarında geçilir.
- **LG akıllı TV (webOS)** — Wi‑Fi üzerinden. İlk bağlantıda TV ekranında çıkan izin isteği kabul edilir.
- **Toshiba TV** — üç sistemin üçü de; hangisi olduğu TV'nin açık bağlantı noktalarından kendiliğinden anlaşılır:
  - *Google TV / Android TV*: Android TV Remote v2 (Google TV uygulamasının yolu). İlk bağlantıda TV'de 6 haneli kod çıkar.
  - *VIDAA*: TV'nin MQTT sunucusu. İlk bağlantıda TV'de 4 haneli kod çıkar. VIDAA uygulamasının sertifikası ilk seferde
    [vidaa-control](https://github.com/tombabolewski/vidaa-control) deposundan indirilir.
  - *Smart Center* (Vestel altyapılı eski modeller): DIAL sanal kumanda. Renk ve Bilgi tuş kodları tahminidir.
  - Bu TV'ler kanal listesini ağdan vermediği için kanal listesi kumandada tutulur: bir kanala ilk dokunuşta TV'deki
    numarası sorulur. Smart Center'da "TV'den öğren" numaraları TV'yi dolaşarak kendisi bulur.
- **Android projeksiyon / Android TV** — ADB ile (`pkg install android-tools`, cihazda ADB hata ayıklama açık).

## Kurulum (Termux)
```
pkg install python android-tools -y
curl -L --remote-name-all https://raw.githubusercontent.com/azataslan065-png/kumanda/main/{kumanda.py,index.html,manifest.webmanifest,sw.js,ikon-192.png,ikon-512.png,ikon-maskable-512.png}
python kumanda.py
```
Sonra tarayıcıda `http://127.0.0.1:8080` → ⚙ → "Ağda ara" → bulunan cihazın yanındaki **Ekle**.

Termux arka planda kapanmasın: Termux bildiriminde **Acquire wakelock**, Android ayarlarında Termux için pil **Kısıtlanmamış**.

## Küçük ekran
Kumandanın üstünde TV'de o an ne açıksa görünür:
- TV bir yayın kanalındaysa ve kanalın resmi internet yayını Canlı TV listesinde varsa, o yayın oynatılır
  (sessiz başlar, TV'den birkaç saniye geride olabilir, mobil veride veri harcar; en düşük kalite seçilir).
- LG TV ekran görüntüsü veriyorsa TV'nin o anki ekranı kesintisiz (en fazla 15 kare/sn) gösterilir; 📡 ile kanalın internet yayınına geçilir (webOS `executeOneShot`;
  Netflix gibi korumalı içerikte TV siyah verir, bazı TV'ler hiç vermez).
- İkisi de yoksa açık kanalın adı ve yayın akışı gösterilir. ▾ ile küçültülür.

## Uygulama olarak yükleme
Kumanda yüklenebilir bir web uygulamasıdır (PWA). Chrome'da kumandayı aç → ⋮ → **Uygulamayı yükle**
(ya da kumandada ⚙ → **Yükle**). Ana ekrana ve uygulama listesine simgesiyle eklenir, tam ekran açılır.

## LG tuşları
Tüm tuşlar önce asıl kumandanın tuş kanalından (getPointerInputSocket) gönderilir; webOS 26'da imzasız
eşleşmeyle reddedilebilen uygulama komutları (ses, oynat, kanal) yalnızca yedek olarak denenir.
Tuş adları: Home Assistant aiowebostv `buttons.py`. ⚙ → **Tuş testi** her tuşun son sonucunu gösterir.
