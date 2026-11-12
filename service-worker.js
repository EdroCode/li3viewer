self.addEventListener('install', event => {
  // Cache the core files during install
  const core = [
    '/',
    'web.html',
    'li3_dump.json', // example data file
  ];
  event.waitUntil(
    caches.open('core-v1').then(cache => cache.addAll(core))
  );
});

self.addEventListener('fetch', event => {
  // Use cache-first strategy for same-origin requests
  const {request} = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  // Only handle same-origin requests
  if (url.origin !== location.origin) return;
  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(networkResponse => {
        // Update cache for future use
        const clone = networkResponse.clone();
        caches.open('runtime').then(cache => cache.put(request, clone));
        return networkResponse;
      });
    })
  );
});
