self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("second-brain-v2").then((cache) => cache.addAll(["/", "/manifest.webmanifest", "/icon.svg", "/favicon.svg"]))
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});

