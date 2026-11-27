self.addEventListener("push", (event) => {
  if (!event.data) return;
  const alert = event.data.json();
  event.waitUntil(self.registration.showNotification(alert.title, {
    body: alert.body,
    tag: alert.tag,
    data: { url: alert.url, notice: alert.notice, remedy: alert.remedy },
    actions: [{ action: "notice", title: "Read official notice" }],
  }).then(async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clients) client.postMessage({ type: "recall-notification", example: alert.example });
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const alert = event.notification.data;
  const destination = event.action === "notice" ? alert.notice : alert.url;
  event.waitUntil(self.clients.openWindow(destination));
});
