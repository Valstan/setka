/**
 * TG egress-relay для контент-радара SETKA (Ф0.3).
 *
 * Зачем: с прод-VPS (RU-хостинг) заблокирован весь Telegram, кроме
 * api.telegram.org — включая t.me/s/ (web-превью каналов) и медиа-CDN
 * (probe Ф0, PR #196). Этот Cloudflare Worker (free-tier) — внешний фетчер:
 * VPS ходит сюда, воркер ходит в Telegram.
 *
 * Маршруты (все требуют заголовок X-Relay-Secret == env.RELAY_SECRET):
 *   GET /s/<channel>[?before=N]  → проксирует https://t.me/s/<channel>
 *                                  (HTML web-превью; редиректы НЕ следуем —
 *                                  редирект = сигнал «канал без превью»)
 *   GET /media?u=<encoded URL>   → проксирует медиа ТОЛЬКО с телеграмных
 *                                  CDN-хостов (allowlist ниже)
 *   GET /health                  → 200 ok (без секрета)
 *
 * Деплой: scripts/deploy_tg_relay.sh (Cloudflare API, токен на VPS).
 */

const MEDIA_HOST_RE = /^(?:[a-z0-9-]+\.)*(?:cdn-telegram\.org|telesco\.pe|telegram-cdn\.org|t\.me|telegram\.org)$/i;

const UA =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/125.0 Safari/537.36';

// Полный браузерный набор: t.me отдаёт datacenter-клиентам с куцыми
// заголовками деградированную страницу (без ленты сообщений).
const BROWSER_HEADERS = {
    'user-agent': UA,
    accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'cache-control': 'no-cache',
    pragma: 'no-cache',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'upgrade-insecure-requests': '1',
};

function deny(status, text) {
    return new Response(text, { status, headers: { 'content-type': 'text/plain' } });
}

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (url.pathname === '/health') {
            return new Response('ok');
        }
        if (request.method !== 'GET') {
            return deny(405, 'method not allowed');
        }
        if (request.headers.get('x-relay-secret') !== env.RELAY_SECRET) {
            return deny(403, 'forbidden');
        }

        // /s/<channel> — HTML web-превью канала.
        const sMatch = url.pathname.match(/^\/s\/([A-Za-z0-9_]{3,64})$/);
        if (sMatch) {
            const target = new URL(`https://t.me/s/${sMatch[1]}`);
            const before = url.searchParams.get('before');
            if (before && /^\d+$/.test(before)) target.searchParams.set('before', before);
            // Один вариант фетча t.me: буферизация + внутренний таймаут.
            // Тело БУФЕРИЗУЕМ (как /media): стриминг через CF по HTTP/1.1 вешает
            // httpx на VPS до ReadTimeout. AbortSignal не даёт зависшему соединению
            // съесть весь бюджет — некоторые гиганты (напр. @ASupersharij) отдают
            // AJAX-вариант заглушкой без ленты и держат сокет открытым.
            const fetchVariant = async (useAjax) => {
                const headers = useAjax
                    ? { ...BROWSER_HEADERS, 'x-requested-with': 'XMLHttpRequest', 'content-length': '0' }
                    : { ...BROWSER_HEADERS };
                const resp = await fetch(target, {
                    method: useAjax ? 'POST' : 'GET',
                    headers,
                    redirect: 'manual', // редирект = «у канала нет web-превью»
                    signal: AbortSignal.timeout(20000),
                });
                const loc = resp.headers.get('location');
                if (loc) return { status: resp.status, body: '', loc };
                return { status: resp.status, body: await resp.text(), loc: null };
            };
            const hasFeed = (r) => r && (r.loc || /data-post="[A-Za-z0-9_]+\/\d+"/.test(r.body));

            // AJAX даёт больше сообщений/страницу — пробуем первым. Если завис/
            // отдал заглушку без ленты (и не редирект) — фолбэк на обычный GET:
            // datacenter-IP получает деградированную, но РАБОЧУЮ страницу, свежих
            // сообщений хватает для мониторинга «есть ли новое».
            let result = null;
            try {
                result = await fetchVariant(true);
            } catch (e) {
                result = null;
            }
            if (!hasFeed(result)) {
                try {
                    const g = await fetchVariant(false);
                    if (hasFeed(g) || !result) result = g;
                } catch (e) {
                    /* GET тоже не вышел — отдадим что есть от AJAX (или 504 ниже) */
                }
            }
            if (!result) return deny(504, 'relay: t.me fetch timed out');
            const headers = new Headers({ 'content-type': 'text/html' });
            if (result.loc) headers.set('x-relay-redirect', result.loc);
            return new Response(result.body, { status: result.status, headers });
        }

        // /media?u=<url> — медиа только с телеграмных CDN.
        if (url.pathname === '/media') {
            const raw = url.searchParams.get('u');
            if (!raw) return deny(400, 'missing u');
            let target;
            try {
                target = new URL(raw);
            } catch (e) {
                return deny(400, 'bad u');
            }
            if (target.protocol !== 'https:' || !MEDIA_HOST_RE.test(target.hostname)) {
                return deny(403, 'host not allowed');
            }
            const resp = await fetch(target, { headers: { 'user-agent': UA } });
            // Тело БУФЕРИЗУЕМ, не стримим: стриминг через CF по HTTP/1.1
            // подвешивает httpx-клиент на VPS до ReadTimeout (факт деплоя
            // Ф0.3; curl по HTTP/2 работал). Файлы ≤20 MB (лимит архива) —
            // в память воркера (128 MB) помещаются с запасом.
            const blob = await resp.arrayBuffer();
            return new Response(blob, {
                status: resp.status,
                headers: {
                    'content-type': resp.headers.get('content-type') || 'application/octet-stream',
                },
            });
        }

        return deny(404, 'not found');
    },
};
