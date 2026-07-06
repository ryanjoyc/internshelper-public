// internsHELPer web UI glue. Kept deliberately small: HTMX does the requests,
// Alpine does the toasts, this file owns the theme cycle and the keyboard router
// (and, in Phase 4, the Sortable board wiring). Keys act by clicking the real
// HTMX-wired buttons — no duplicated request logic.

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

/* ---------- keyboard router (review page) ---------- */

var selected = -1;

function listRows() {
  return Array.prototype.slice.call(document.querySelectorAll("#review-list .row"));
}

function select(idx) {
  var rows = listRows();
  if (!rows.length) { selected = -1; return; }
  idx = Math.max(0, Math.min(idx, rows.length - 1));
  rows.forEach(function (r) { r.classList.remove("is-selected"); });
  rows[idx].classList.add("is-selected");
  rows[idx].scrollIntoView({ block: "nearest" });
  selected = idx;
}

function selectedRow() {
  var rows = listRows();
  return selected >= 0 && selected < rows.length ? rows[selected] : null;
}

function click(root, sel) {
  var el = root && root.querySelector(sel);
  if (el) el.click();
}

function undoNewestToast() {
  var toasts = document.querySelectorAll("#toast-region .toast");
  if (toasts.length) click(toasts[toasts.length - 1], "button");
}

document.addEventListener("keydown", function (e) {
  var tag = (e.target.tagName || "").toLowerCase();
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (tag === "input" || tag === "textarea" || tag === "select") {
    if (e.key === "Escape") e.target.blur();
    return;
  }

  var focusCard = document.querySelector("#focus-card .focus-card");
  if (focusCard) {
    switch (e.key) {
      case "m": click(focusCard, ".btn-match"); break;
      case "x": click(focusCard, ".btn-nomatch"); break;
      case "u": undoNewestToast(); break;
      case "r": e.preventDefault(); focusCard.querySelector(".focus-reason").focus(); break;
      case "o": click(focusCard, ".focus-meta a"); break;
      case "ArrowLeft": click(document, ".focus-prev"); break;
      case "ArrowRight": click(document, ".focus-next"); break;
      case "Escape": window.location.href = "/review"; break;
    }
    return;
  }

  if (!document.getElementById("review-list")) return;
  switch (e.key) {
    case "j": case "ArrowDown": e.preventDefault(); select(selected + 1); break;
    case "k": case "ArrowUp": e.preventDefault(); select(selected - 1); break;
    case "m": click(selectedRow(), ".btn-match"); break;
    case "x": click(selectedRow(), ".btn-nomatch"); break;
    case "u": undoNewestToast(); break;
    case "o": click(selectedRow(), ".row-open"); break;
    case "f":
      window.location.href = "/review?mode=focus"; break;
    case "Enter": {
      var row = selectedRow();
      if (row) window.location.href = "/review?mode=focus&offset=" + (row.dataset.offset || 0);
      break;
    }
    case "Escape":
      listRows().forEach(function (r) { r.classList.remove("is-selected"); });
      selected = -1;
      break;
  }
});

// A verdicted row leaves the DOM; keep the selection on the row that slid up.
document.body.addEventListener("htmx:afterSwap", function () {
  if (selected >= 0 && document.getElementById("review-list")) select(selected);
});
