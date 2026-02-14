// ══════════════════════════════════════════════
// ColegiosPro — Service Worker
// Push notifications + Offline cache
// ══════════════════════════════════════════════

const CACHE_NAME = 'colegiospro-landing-v1';
const ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=DM+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap'
];

// ─── INSTALL ───
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// ─── ACTIVATE ───
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ─── FETCH (network-first, fallback to cache) ───
self.addEventListener('fetch', (event) => {
  // Skip non-GET and cross-origin requests
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Cache successful responses
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// ─── PUSH NOTIFICATIONS ───
self.addEventListener('push', (event) => {
  let data = { title: 'ColegiosPro', body: 'Tiene un nuevo mensaje', icon: '/icon-192.png' };

  try {
    if (event.data) {
      const payload = event.data.json();
      data = {
        title: payload.title || data.title,
        body: payload.body || data.body,
        icon: payload.icon || data.icon,
        badge: '/icon-192.png',
        vibrate: [200, 100, 200],
        data: {
          url: payload.url || '/',
          chatMessage: payload.chatMessage || null
        },
        actions: [
          { action: 'open', title: 'Abrir' },
          { action: 'reply', title: 'Responder' }
        ]
      };
    }
  } catch (e) {
    data.body = event.data ? event.data.text() : data.body;
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon,
      badge: data.badge || data.icon,
      vibrate: data.vibrate,
      data: data.data,
      actions: data.actions
    })
  );
});

// ─── NOTIFICATION CLICK ───
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const url = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      // If a window is already open, focus it and navigate
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          // Send message to open chat if it's a chat notification
          if (event.notification.data?.chatMessage) {
            client.postMessage({
              type: 'OPEN_CHAT',
              message: event.notification.data.chatMessage
            });
          }
          return;
        }
      }
      // Otherwise open new window
      return clients.openWindow(url);
    })
  );
});

// ─── MESSAGE FROM MAIN THREAD ───
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});