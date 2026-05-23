// Status polling for the recording detail page.
// Attaches to any element with [data-poll-url] and replaces its innerHTML
// with the response body. Stops + reloads when the response carries
// X-Gennotes-Done: 1.
(function () {
  "use strict";
  const cell = document.querySelector("[data-poll-url]");
  if (!cell) return;
  const url = cell.getAttribute("data-poll-url");
  const intervalMs = 2000;
  const timer = setInterval(async () => {
    try {
      const resp = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
      if (!resp.ok) return;
      cell.innerHTML = await resp.text();
      if (resp.headers.get("X-Gennotes-Done") === "1") {
        clearInterval(timer);
        setTimeout(() => window.location.reload(), 300);
      }
    } catch (_) {
      // network blip — try again next tick
    }
  }, intervalMs);
})();
