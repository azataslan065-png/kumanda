// Kumanda — service worker
// Yalnızca uygulama kabuğunu (sayfa, manifest, ikonlar) önbelleğe alır; köprüye giden
// istekler (tuşlar, durum, kanallar) hiçbir zaman önbellekten verilmez.
// Strateji: önce ağ (her zaman en güncel sürüm), ağ yoksa önbellek.
// cache:'no-cache' → tarayıcının HTTP önbelleği (GitHub Pages 10 dk) atlanır, sayfa her açılışta sunucuya doğrulatılır.
const ONBELLEK = 'kumanda-kabuk-v2';
const KABUK = ['./', 'manifest.webmanifest', 'ikon-192.png', 'ikon-512.png', 'ikon-maskable-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(ONBELLEK).then(c => c.addAll(KABUK.map(u => new Request(u, { cache: 'no-cache' })))).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(adlar => Promise.all(adlar.filter(a => a.startsWith('kumanda-') && a !== ONBELLEK).map(a => caches.delete(a))))
    .then(() => self.clients.claim()));
});

function kabukMu(url) {
  const kapsam = new URL(self.registration.scope);
  if (url.origin !== kapsam.origin || !url.pathname.startsWith(kapsam.pathname)) return false;
  const yol = url.pathname.slice(kapsam.pathname.length);
  return yol === '' || yol === 'index.html' || KABUK.includes(yol);
}

self.addEventListener('fetch', e => {
  const istek = e.request;
  if (istek.method !== 'GET') return;
  const url = new URL(istek.url);
  if (!kabukMu(url)) return;              // köprü API'si, kanallar, logolar: dokunma
  const anahtar = (url.pathname.endsWith('/') || url.pathname.endsWith('index.html')) ? './' : istek;
  e.respondWith(
    // Sayfa açılış (navigate) isteklerine RequestInit verilemez; adresle yeniden istenir
    (istek.mode === 'navigate' ? fetch(url.href, { cache: 'no-cache', credentials: 'same-origin' })
                               : fetch(istek, { cache: 'no-cache' })).then(yanit => {
      if (yanit.ok) { const kopya = yanit.clone(); caches.open(ONBELLEK).then(c => c.put(anahtar, kopya)); }
      return yanit;
    }).catch(() => caches.match(anahtar, { ignoreSearch: true }))
  );
});
