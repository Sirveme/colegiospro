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

// ─── PUSH NOTIFICATIONS (enriquecidas) ───
self.addEventListener('push', (event) => {
  let titulo = 'ColegiosPro';
  let opciones = {
    body: 'Tiene un nuevo mensaje',
    icon: '/static/img/icon-192.png',
    badge: '/static/img/icon-192.png',
    vibrate: [200, 100, 200],
    tag: 'general',
    renotify: true,
    data: { url: '/', audio_url: null, chatMessage: null },
    actions: [
      { action: 'abrir', title: '👁 Ver' },
      { action: 'ok',    title: '✓ OK' }
    ],
  };
  try {
    if (event.data) {
      const payload = event.data.json();
      // Soporta ambas convenciones: {title,body} y {titulo,cuerpo}
      titulo = payload.title || payload.titulo || titulo;
      if (payload.emoji_grande) titulo = payload.emoji_grande + ' ' + titulo;
      const urgente = !!payload.urgente;
      opciones = {
        body: payload.body || payload.cuerpo || opciones.body,
        icon: payload.icon || payload.imagen_url || opciones.icon,
        // image: imagen grande en la notificación (Android)
        image: payload.imagen_url || payload.gif_url || undefined,
        badge: '/static/img/icon-192.png',
        vibrate: urgente ? [300,100,300,100,300] : [200,100,200],
        tag: payload.categoria || payload.tag || 'general',
        renotify: true,
        requireInteraction: urgente,
        data: {
          url: payload.url || payload.url_destino || '/',
          audio_url: payload.audio_url || null,
          chatMessage: payload.chatMessage || null,
          categoria: payload.categoria || 'general',
          urgente: urgente,
        },
        actions: [
          { action: 'abrir', title: '👁 Ver' },
          { action: 'ok',    title: '✓ OK' }
        ],
      };
    }
  } catch (e) {
    opciones.body = event.data ? event.data.text() : opciones.body;
  }
  event.waitUntil(self.registration.showNotification(titulo, opciones));
});

// ─── NOTIFICATION CLICK ───
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'ok') return; // confirma y cierra
  const dta = event.notification.data || {};
  let url = dta.url || '/';
  // Si hay audio, pasarlo como ?play=<url> para que base.html lo reproduzca
  if (dta.audio_url) {
    try {
      const sep = url.includes('?') ? '&' : '?';
      url = url + sep + 'play=' + encodeURIComponent(dta.audio_url);
    } catch (e) {}
  }
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          if (dta.chatMessage) {
            client.postMessage({ type: 'OPEN_CHAT', message: dta.chatMessage });
          }
          if (dta.audio_url) {
            client.postMessage({ type: 'PLAY_AUDIO', audio_url: dta.audio_url });
          }
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});