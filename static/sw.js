// ══════════════════════════════════════════════
// ColegiosPro + SecretariaPro — Service Worker
// Served from /static/sw.js
// ══════════════════════════════════════════════

const CACHE_NAME = 'secretariapro-v6';
const PRECACHE = [
  '/',
  '/demo',
  '/manifest.json',
  '/static/manifest-secretaria.json',
  '/secretaria/',
  '/static/secretaria/secretaria.css',
  '/static/secretaria/secretaria.js',
  '/static/img/icon-192.png',
  '/static/img/pwa/icon-192.png',
  '/static/img/pwa/icon-512.png',
  '/offline.html',
];

// ─── INSTALL ───
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        // addAll falla si UNO falla; usamos add individual para ser tolerante
        return Promise.all(PRECACHE.map(url =>
          cache.add(url).catch(err => console.log('precache miss:', url, err))
        ));
      })
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

// ─── FETCH (network-first con fallback a cache y offline) ───
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = req.url;

  // No cachear WebSockets, APIs, admin, tracking
  if (url.includes('/ws/')) return;
  if (url.includes('/api/')) return;
  if (url.includes('/admin/')) return;
  if (url.includes('/track/')) return;
  if (url.includes('/push/')) return;
  // HTMX partials no deberían cachearse tampoco
  if (req.headers.get('HX-Request') === 'true') return;

  event.respondWith(
    fetch(req)
      .then(response => {
        if (response && response.ok && response.type !== 'opaque') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(req, clone).catch(() => {});
          });
        }
        return response;
      })
      .catch(() =>
        caches.match(req).then(cached => {
          if (cached) return cached;
          // Para navegaciones HTML, mostrar offline.html
          if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
            return caches.match('/offline.html');
          }
          return new Response('', { status: 504, statusText: 'offline' });
        })
      )
  );
});

// ─── PUSH NOTIFICATIONS (enriquecidas) ───
self.addEventListener('push', (event) => {
  let titulo = 'ColegiosPro';
  let opciones = {
    body: 'Tiene un nuevo mensaje',
    icon: '/static/img/pwa/icon-192.png',
    badge: '/static/img/sp-badge-72.png',
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
      try {
        console.log('[SW] Push recibido:', JSON.stringify(payload));
        console.log('[SW] Push data.titulo:', payload.titulo);
        console.log('[SW] Push data.cuerpo:', payload.cuerpo);
        console.log('[SW] Push data.url:', payload.url);
        console.log('[SW] Push data.icon:', payload.icon || payload.icon_url);
        console.log('[SW] Push data.btn1_label:', payload.btn1_label);
        console.log('[SW] Push data.btn2_label:', payload.btn2_label);
        console.log('[SW] Push data.categoria:', payload.categoria);
        console.log('[SW] Push data.urgente:', payload.urgente);
      } catch (e) {}
      // Soporta ambas convenciones: {title,body} y {titulo,cuerpo}
      titulo = payload.title || payload.titulo || titulo;
      if (payload.emoji_grande) titulo = payload.emoji_grande + ' ' + titulo;
      const urgente = !!payload.urgente;
      opciones = {
        body: payload.body || payload.cuerpo || opciones.body,
        // icon: dinámico si el comunicado lo define, si no logo institucional
        icon: payload.icon || payload.icon_url || '/static/img/pwa/icon-192.png',
        // image: imagen grande dinámica del comunicado (Android)
        image: payload.image || payload.imagen_url || payload.gif_url || undefined,
        badge: '/static/img/sp-badge-72.png',
        vibrate: urgente ? [300,100,300,100,300] : [200,100,200],
        tag: payload.categoria || payload.tag || 'general',
        renotify: true,
        requireInteraction: urgente,
        data: {
          url: payload.url || payload.url_destino || '/secretaria/muro',
          audio_url: payload.audio_url || null,
          chatMessage: payload.chatMessage || null,
          categoria: payload.categoria || 'general',
          urgente: urgente,
          btn1_label: payload.btn1_label || '👁 Ver',
          btn2_label: payload.btn2_label || '✓ OK',
        },
        actions: [
          { action: 'abrir', title: payload.btn1_label || '👁 Ver' },
          { action: 'ok',    title: payload.btn2_label || '✓ OK' }
        ],
      };
    }
  } catch (e) {
    console.error('[SW] Error parseando push:', e);
    opciones.body = event.data ? event.data.text() : opciones.body;
  }
  event.waitUntil(self.registration.showNotification(titulo, opciones));
});

// ─── NOTIFICATION CLICK ───
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const dta = event.notification.data || {};
  try {
    console.log('[SW] notification.data completo:', JSON.stringify(dta));
    console.log('[SW] click action:', event.action);
  } catch (e) {}
  if (event.action === 'ok') return; // confirma y cierra
  let url = dta.url || '/secretaria/muro';

  // Si hay audio, pasarlo como ?play=<url> para que base.html lo reproduzca
  if (dta.audio_url) {
    try {
      const sep = url.includes('?') ? '&' : '?';
      url = url + sep + 'play=' + encodeURIComponent(dta.audio_url);
    } catch (e) {}
  }

  // URLs externas: algunos navegadores bloquean clients.openWindow() hacia
  // orígenes distintos. Las enviamos por /redirect?url= (same-origin) que
  // hace un 302 del lado del servidor.
  const origin = self.location.origin;
  const esExterna = /^https?:\/\//i.test(url) && !url.startsWith(origin);
  const urlFinal = esExterna
    ? '/redirect?url=' + encodeURIComponent(url)
    : url;
  try { console.log('[SW] Navegando a URL:', urlFinal, '(externa:', esExterna, ')'); } catch (e) {}

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin)) {
          // Navegar la ventana existente a la URL del comunicado.
          const goto = () => {
            if (typeof client.navigate === 'function') {
              return client.navigate(urlFinal).catch(() => null);
            }
            return null;
          };
          return Promise.resolve(goto()).then(() => {
            try { client.focus(); } catch (e) {}
            if (dta.chatMessage) {
              client.postMessage({ type: 'OPEN_CHAT', message: dta.chatMessage });
            }
            if (dta.audio_url) {
              client.postMessage({ type: 'PLAY_AUDIO', audio_url: dta.audio_url });
            }
          });
        }
      }
      return clients.openWindow(urlFinal);
    })
  );
});