// Network-first service worker: always fresh when online, fully usable
// offline at the bench after one successful load.
const CACHE = 'mosfet-scanner-v1';
const CORE = [
  './', 'index.html', 'css/app.css', 'manifest.webmanifest',
  'js/app.js', 'js/transport.js', 'js/mock.js', 'js/protocol.js',
  'js/convert.js', 'js/scan.js', 'js/chart.js', 'js/store.js', 'js/bringup.js',
  'icons/icon-192.png', 'icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    // no-cache: revalidate with the CDN instead of trusting the local HTTP
    // cache, so updates appear as soon as the edge has them
    fetch(e.request, { cache: 'no-cache' })
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true })));
});
