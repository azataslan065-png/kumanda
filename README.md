# Kumanda

Telefondan projeksiyon ve LG TV kontrolü + Canlı TV.

- `index.html` — kumanda arayüzü → https://azataslan065-png.github.io/kumanda/
- `tv/index.html` — Canlı TV → https://azataslan065-png.github.io/kumanda/tv/
- `kumanda.py` — Termux köprüsü (yalnızca Python, ek paket gerekmez)

## Desteklenen cihazlar
- **LG akıllı TV (webOS)** — Wi‑Fi üzerinden. İlk bağlantıda TV ekranında çıkan izin isteği kabul edilir.
- **Android projeksiyon / Android TV** — ADB ile (`pkg install android-tools`, cihazda ADB hata ayıklama açık).

## Kurulum (Termux)
```
pkg install python android-tools -y
curl -L --remote-name-all https://raw.githubusercontent.com/azataslan065-png/kumanda/main/{kumanda.py,index.html,manifest.webmanifest,sw.js,ikon-192.png,ikon-512.png,ikon-maskable-512.png}
python kumanda.py
```
Sonra tarayıcıda `http://127.0.0.1:8080` → ⚙ → "Ağda ara" → Kaydet.

## Uygulama olarak yükleme
Kumanda yüklenebilir bir web uygulamasıdır (PWA). Chrome'da kumandayı aç → ⋮ → **Uygulamayı yükle**
(ya da kumandada ⚙ → **Yükle**). Ana ekrana ve uygulama listesine simgesiyle eklenir, tam ekran açılır.

## LG tuşları
Tüm tuşlar önce asıl kumandanın tuş kanalından (getPointerInputSocket) gönderilir; webOS 26'da imzasız
eşleşmeyle reddedilebilen uygulama komutları (ses, oynat, kanal) yalnızca yedek olarak denenir.
Tuş adları: Home Assistant aiowebostv `buttons.py`. ⚙ → **Tuş testi** her tuşun son sonucunu gösterir.
