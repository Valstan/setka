/* Service worker Радара (Ф0.4): минимальный — оболочка PWA + сетевой
 * приоритет. Контент всегда свежий (network-first), офлайн отдаём
 * закэшированную оболочку страницы. Push-обработчик появится в Ф0.5. */
const CACHE = 'radar-shell-v1';
const SHELL = ['/radar'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    // Кэшируем только GET оболочки /radar; API и медиа — всегда сеть.
    if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
    if (url.pathname === '/radar') {
        event.respondWith(
            fetch(event.request)
                .then((resp) => {
                    const copy = resp.clone();
                    caches.open(CACHE).then((c) => c.put(event.request, copy));
                    return resp;
                })
                .catch(() => caches.match(event.request))
        );
    }
});
