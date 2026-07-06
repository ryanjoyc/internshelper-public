// internsHELPer web UI glue. Kept deliberately small: HTMX does the requests,
// Alpine does the toasts, this file owns the theme cycle (and, in later phases,
// the keyboard router + Sortable board wiring).

"use strict";

/* ---------- theme: system -> light -> dark -> system ---------- */

function applyTheme() {
  var t = localStorage.getItem("theme");
  var dark = t === "dark" ||
    (t !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  var label = document.getElementById("theme-label");
  if (label) label.textContent = t || "system";
}

document.addEventListener("DOMContentLoaded", function () {
  applyTheme();
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.addEventListener("click", function () {
      var order = [null, "light", "dark"];
      var cur = localStorage.getItem("theme");
      var next = order[(order.indexOf(cur) + 1) % order.length];
      if (next === null) localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
      applyTheme();
    });
  }
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", applyTheme);
});
