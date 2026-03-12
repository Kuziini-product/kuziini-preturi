// Kuziini Service Worker - Push Notifications
const CACHE_NAME = 'kuziini-v1';

// Install - cache minimal assets
self.addEventListener('install', (e) => {
  self.skipWaiting();
});

// Activate - claim clients immediately
self.addEventListener('activate', (e) => {
  e.waitUntil(self.clients.claim());
});

// Push notification received
self.addEventListener('push', (e) => {
  let data = {title: 'Kuziini', body: 'Notificare noua', icon: '/icon-192.png'};
  try {
    if (e.data) data = {...data, ...e.data.json()};
  } catch (_) {
    if (e.data) data.body = e.data.text();
  }
  const options = {
    body: data.body || '',
    icon: data.icon || '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'kuziini-notif',
    renotify: true,
    data: {url: data.url || '/'},
    vibrate: [200, 100, 200],
    actions: data.actions || []
  };
  e.waitUntil(self.registration.showNotification(data.title, options));
});

// Click on notification - open app
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(clients => {
      // Focus existing window if open
      for (const c of clients) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          return c.focus();
        }
      }
      // Otherwise open new window
      return self.clients.openWindow(url);
    })
  );
});
