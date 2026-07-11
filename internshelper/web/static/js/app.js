// internsHELPer web UI glue. Kept deliberately small: HTMX does the requests,
// Alpine does the toasts, this file owns the theme cycle, the board keyboard
// router, and the Sortable drag wiring. Keys act by clicking the real
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

/* ---------- keyboard router (board Inbox) ---------- */

var selected = -1;

function inboxCards() {
  // Cards inside a collapsed tier are display:none — j/k must skip them.
  return Array.prototype.slice.call(
    document.querySelectorAll(".board-col--inbox .board-card"))
    .filter(function (c) { return c.offsetParent !== null; });
}

function select(idx) {
  var cards = inboxCards();
  if (!cards.length) { selected = -1; return; }
  idx = Math.max(0, Math.min(idx, cards.length - 1));
  cards.forEach(function (c) { c.classList.remove("is-selected"); });
  cards[idx].classList.add("is-selected");
  cards[idx].scrollIntoView({ block: "nearest" });
  selected = idx;
}

function selectedCard() {
  var cards = inboxCards();
  return selected >= 0 && selected < cards.length ? cards[selected] : null;
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
  if (!document.querySelector(".board-col--inbox")) return;
  switch (e.key) {
    case "j": case "ArrowDown": e.preventDefault(); select(selected + 1); break;
    case "k": case "ArrowUp": e.preventDefault(); select(selected - 1); break;
    case "x": click(selectedCard(), ".card-dismiss"); break;
    case "u": undoNewestToast(); break;
    case "o": {
      var card = selectedCard();
      if (card) card.click(); // open the drawer (the posting link lives there)
      break;
    }
    case "Escape":
      inboxCards().forEach(function (c) { c.classList.remove("is-selected"); });
      selected = -1;
      break;
  }
});

// A dismissed card leaves the DOM (region re-render); keep the selection nearby.
document.body.addEventListener("htmx:afterSwap", function () {
  if (selected >= 0 && document.querySelector(".board-col--inbox")) select(selected);
});

/* ---------- board: SortableJS drag -> POST /board/move | /board/pin ---------- */

var lastDragAt = 0;

function initBoard() {
  if (typeof Sortable === "undefined") return;
  document.querySelectorAll(".board-cards").forEach(function (col) {
    if (col._sortable) return;
    col._sortable = new Sortable(col, {
      group: "board",
      animation: 150,
      onEnd: function (evt) {
        lastDragAt = Date.now();
        if (evt.from === evt.to) return; // no intra-list persistence
        var values = { posting_id: evt.item.dataset.id };
        var url;
        if (evt.to.dataset.status) {           // pipeline lane -> status move
          url = "/board/move";
          values.status = evt.to.dataset.status;
        } else if (evt.to.dataset.tier) {      // tier section -> sticky pin
          url = "/board/pin";
          values.tier = evt.to.dataset.tier;
        } else {
          return;
        }
        htmx.ajax("POST", url, {
          values: values,
          target: "#board-region",
          swap: "innerHTML", // full region re-render keeps counts + order honest
        });
      },
    });
  });
}

// A drop shouldn't also open the card drawer: swallow the click that follows a drag.
document.addEventListener(
  "click",
  function (e) {
    if (Date.now() - lastDragAt < 200 && e.target.closest(".board-card")) {
      e.stopPropagation();
      e.preventDefault();
    }
  },
  true
);

document.addEventListener("DOMContentLoaded", initBoard);
document.body.addEventListener("htmx:afterSwap", initBoard);
document.body.addEventListener("htmx:oobAfterSwap", initBoard);

// If a move/pin fails server-side, the optimistic drag is stale — resync from the DB.
document.body.addEventListener("htmx:responseError", function (evt) {
  var path = evt.detail && evt.detail.pathInfo && evt.detail.pathInfo.requestPath;
  if (path === "/board/move" || path === "/board/pin") window.location.reload();
});
