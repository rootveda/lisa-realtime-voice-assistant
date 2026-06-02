// Service worker: when the Pipecat client requests the Daily.co bundle from c.daily.co,
// return the same file from our origin so the app works offline.
const DAILY_BUNDLE_PATH = '/static/call-machine-object-bundle.js';

self.addEventListener('fetch', function (event) {
  const url = event.request.url;
  if (url.indexOf('c.daily.co') !== -1 && url.indexOf('call-machine-object-bundle.js') !== -1) {
    event.respondWith(
      fetch(DAILY_BUNDLE_PATH).then(function (response) {
        return response;
      }).catch(function () {
        return new Response('console.error("Offline: Daily bundle not found. Run download_daily_bundle.sh with network.");', {
          status: 200,
          headers: { 'Content-Type': 'application/javascript' }
        });
      })
    );
  }
});
