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
curl -LO https://raw.githubusercontent.com/azataslan065-png/kumanda/main/kumanda.py
curl -LO https://raw.githubusercontent.com/azataslan065-png/kumanda/main/index.html
python kumanda.py
```
Sonra tarayıcıda `http://127.0.0.1:8080` → ⚙ → "Ağda ara" → Kaydet.
