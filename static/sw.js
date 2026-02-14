// ══════════════════════════════════════════════
// ColegiosPro — Service Worker
// Served from /sw.js (via FastAPI route)
// ══════════════════════════════════════════════

const CACHE_NAME = 'colegiospro-v1';
const ASSETS = [
  '/',
  '/demo',
  '/manifest.json',
  '/static/img/duilio-cta.jpg',
  '/static/img/duilio-chat.jpg',
  '/static/img/icon-192.png',
];

// ─── INSTALL ───
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(ASSETS))
      .catch(err => console.log('Cache addAll failed:', err))
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

// ─── FETCH (network-first) ───
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  // Skip WebSocket requests
  if (event.request.url.includes('/ws/')) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
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
  let data = { title: 'ColegiosPro', body: 'Tiene un nuevo mensaje', icon: '/static/img/icon-192.png' };
  try {
    if (event.data) {
      const payload = event.data.json();
      data = {
        title: payload.title || data.title,
        body: payload.body || data.body,
        icon: payload.icon || data.icon,
        badge: '/static/img/icon-192.png',
        vibrate: [200, 100, 200],
        data: { url: payload.url || '/', chatMessage: payload.chatMessage || null },
        actions: [
          { action: 'open', title: 'Abrir' },
          { action: 'reply', title: 'Responder' }
        ]
      };
    }
  } catch (e) {
    data.body = event.data ? event.data.text() : data.body;
  }
  event.waitUntil(self.registration.showNotification(data.title, data));
});

// ─── NOTIFICATION CLICK ───
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          if (event.notification.data?.chatMessage) {
            client.postMessage({ type: 'OPEN_CHAT', message: event.notification.data.chatMessage });
          }
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});