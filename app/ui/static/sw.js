/* ==============================================================================
 * Smart Water Controller Service Worker (PWA)
 * Strictly caches static assets only. NEVER CACHES /api/*, /metrics, or /healthz.
 * ============================================================================== */

const CACHE_NAME = 'smart-water-static-v1.2.0';

const STATIC_PRECACHE_URLS = [
  '/',
  '/static/css/app.css',
  '/static/css/vendor.min.css',
  '/static/js/app.js',
  '/static/js/circuit.js',
  '/static/js/rules_builder.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_PRECACHE_URLS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // 1. STRICT SAFETY INVARIANT: NEVER cache API, metrics, or healthz requests!
  if (
    url.pathname.startsWith('/api') ||
    url.pathname.startsWith('/metrics') ||
    url.pathname === '/healthz' ||
    request.method !== 'GET'
  ) {
    // Network-only, bypass cache completely
    event.respondWith(fetch(request));
    return;
  }

  // 2. Static Assets: Stale-While-Revalidate caching strategy
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      const fetchPromise = fetch(request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return networkResponse;
      }).catch(() => cachedResponse);

      return cachedResponse || fetchPromise;
    })
  );
});
