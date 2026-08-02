/* Service worker Радара: оболочка PWA + сетевой приоритет (Ф0.4) +
 * web-push (Ф0.5). Контент всегда свежий (network-first), офлайн отдаём
 * закэшированную оболочку страницы. */
/* Оболочка = корень своего scope, а не жёсткий '/radar'.
 *
 * На радар.вмалмыже.рф Радар живёт на КОРНЕ (переезд 2026-07-26), и SW там
 * регистрируется со scope '/'. Прежний жёсткий '/radar' на этом хосте отдавал
 * редирект 302 на '/', а Cache API по спеку отказывается класть redirected-ответ
 * — install падал целиком, SW не активировался, и вместе с ним молча умирали
 * офлайн-оболочка и web-push (`navigator.serviceWorker.ready` не резолвится
 * никогда). До 2026-08-02 это было не видно вовсе: на голом http SW не
 * регистрируется, а включение https дефект расконсервировало. */
const SCOPE_PATH = new URL(self.registration.scope).pathname.replace(/\/$/, '') || '/';
const CACHE = 'radar-shell-v6';
const SHELL = [SCOPE_PATH];

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
            icon: '/static/radar/icon-192.png',
            badge: '/static/radar/icon-192.png',
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
    // Кэшируем только GET самой оболочки (корень scope); API и медиа — всегда сеть.
    if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
    if ((url.pathname.replace(/\/$/, '') || '/') === SCOPE_PATH) {
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
