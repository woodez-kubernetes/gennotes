// Generic confirm-before-submit. Attach via [data-confirm="..."] on any form.
(function () {
  "use strict";
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const message = form.getAttribute("data-confirm");
      if (!window.confirm(message)) {
        e.preventDefault();
      }
    });
  });
})();
