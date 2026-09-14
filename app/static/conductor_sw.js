/* CTO 119 Conductor — Service Worker v2
 * Cachea la app conductor para funcionar offline.
 * Los datos (viajes, QAP, etc.) siempre van al servidor en tiempo real. */
const CACHE_NAME = 'cto-conductor-v2';
const STATIC_ASSETS = [
  '/conductor',
  '/conductor_manifest.json',
  '/conductor_icon.png',
  'https://fonts.googleapis.com/css2?family=Rajdhani:wght@600;700&family=Nunito:wght@400;500;600&display=swap'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // API calls: network only (real-time data)
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() => {
      return new Response(JSON.stringify({error:'Sin conexion'}), {
        headers: {'Content-Type':'application/json'}
      });
    }));
    return;
  }

  // SSE: network only
  if (url.pathname === '/api/eventos') {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets: cache first, then network
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) {
        // Return cached, but also fetch fresh version in background
        fetch(e.request).then(response => {
          if (response.ok) {
            caches.open(CACHE_NAME).then(cache => cache.put(e.request, response));
          }
        }).catch(() => {});
        return cached;
      }
      return fetch(e.request).then(response => {
        if (response.ok && e.request.method === 'GET') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return response;
      }).catch(() => {
        // Offline fallback for HTML pages
        if (e.request.headers.get('accept')?.includes('text/html')) {
          return caches.match('/conductor');
        }
        return new Response('', {status: 503});
      });
    })
  );
});
