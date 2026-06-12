/* Service worker Радара: оболочка PWA + сетевой приоритет (Ф0.4) +
 * web-push (Ф0.5). Контент всегда свежий (network-first), офлайн отдаём
 * закэшированную оболочку страницы. */
const CACHE = 'radar-shell-v2';
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

self.addEventListener('push', (event) => {
    let data = { title: 'Радар', body: 'Новые элементы в ленте', url: '/radar' };
    try {
        if (event.data) data = Object.assign(data, event.data.json());
    } catch (e) { /* не-JSON payload — показываем дефолт */ }
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/radar/icon.svg',
            badge: '/static/radar/icon.svg',
            data: { url: data.url },
            tag: 'radar-new-items', // новые пуши заменяют старый, не копятся
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || '/radar';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
            for (const w of wins) {
                if (w.url.includes('/radar') && 'focus' in w) return w.focus();
            }
            return clients.openWindow(url);
        })
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
