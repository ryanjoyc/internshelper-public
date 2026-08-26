// internsHELPer web UI behavior. HTMX owns requests, Alpine owns local disclosure
// state, and this file coordinates theme, keyboard selection, responsive Board
// modes, drawer focus, request feedback, and SortableJS.

"use strict";

/* ---------- theme: system -> light -> dark -> system ---------- */

function applyTheme() {
  var theme = localStorage.getItem("theme");
  var dark = theme === "dark" ||
    (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.dataset.theme = theme || "system";
  var label = document.getElementById("theme-label");
  if (label) label.textContent = theme || "system";
}

/* ---------- persistent company/group disclosure state ---------- */

window.tierOpen = function (key, fallback) {
  var value = localStorage.getItem("tier-open:" + key);
  return value === null ? fallback : value === "1";
};

window.tierSave = function (key, open) {
  localStorage.setItem("tier-open:" + key, open ? "1" : "0");
};

/* ---------- Board minimum-window mode ---------- */

function setBoardPanel(panel, remember) {
  if (panel !== "inbox" && panel !== "pipeline") panel = "inbox";
  document.documentElement.dataset.boardPanel = panel;
  if (remember) localStorage.setItem("board-panel", panel);
  document.querySelectorAll("[data-board-panel]").forEach(function (button) {
    var active = button.dataset.boardPanel === panel;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function initBoardPanel() {
  setBoardPanel(localStorage.getItem("board-panel") || "inbox", false);
  document.querySelectorAll("[data-board-panel]").forEach(function (button) {
    if (button.dataset.panelBound) return;
    button.dataset.panelBound = "true";
    button.addEventListener("click", function () {
      setBoardPanel(button.dataset.boardPanel, true);
    });
  });
}

/* ---------- keyboard selection and semantic click helpers ---------- */

var selected = -1;

function inboxCards() {
  return Array.prototype.slice.call(
    document.querySelectorAll(".board-col--inbox .board-card")
  ).filter(function (card) { return card.offsetParent !== null; });
}

function selectCard(index) {
  var cards = inboxCards();
  if (!cards.length) { selected = -1; return; }
  index = Math.max(0, Math.min(index, cards.length - 1));
  cards.forEach(function (card) { card.classList.remove("is-selected"); });
  cards[index].classList.add("is-selected");
  cards[index].focus({ preventScroll: true });
  cards[index].scrollIntoView({ block: "nearest" });
  selected = index;
}

function selectedCard() {
  var cards = inboxCards();
  return selected >= 0 && selected < cards.length ? cards[selected] : null;
}

function clickWithin(root, selector) {
  var element = root && root.querySelector(selector);
  if (element) element.click();
}

function undoNewestToast() {
  var toasts = document.querySelectorAll("#toast-region .toast");
  if (toasts.length) clickWithin(toasts[toasts.length - 1], "button");
}

document.addEventListener("keydown", function (event) {
  var target = event.target;
  var tag = (target.tagName || "").toLowerCase();
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  if ((target.matches("[data-drawer-trigger], .board-col--strip")) &&
      (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    target.click();
    return;
  }

  if (tag === "input" || tag === "textarea" || tag === "select") {
    if (event.key === "Escape") target.blur();
    return;
  }
  if (target.closest("button, a, [contenteditable='true']")) return;
  if (!document.querySelector(".board-col--inbox")) return;

  switch (event.key) {
    case "j": case "ArrowDown": event.preventDefault(); selectCard(selected + 1); break;
    case "k": case "ArrowUp": event.preventDefault(); selectCard(selected - 1); break;
    case "x": clickWithin(selectedCard(), ".card-dismiss"); break;
    case "f": clickWithin(selectedCard(), ".card-flag"); break;
    case "u": undoNewestToast(); break;
    case "o": if (selectedCard()) selectedCard().click(); break;
    case "Escape":
      inboxCards().forEach(function (card) { card.classList.remove("is-selected"); });
      selected = -1;
      break;
  }
});

/* ---------- posting drawer focus, inert background, and restoration ---------- */

var drawerOpener = null;
var drawerOpen = false;

document.addEventListener("pointerdown", function (event) {
  var trigger = event.target.closest("[data-drawer-trigger]");
  if (trigger) drawerOpener = trigger;
});

document.addEventListener("keydown", function (event) {
  if ((event.key === "Enter" || event.key === " ") &&
      event.target.matches("[data-drawer-trigger]")) drawerOpener = event.target;
});

function drawerFocusable() {
  var drawer = document.getElementById("posting-drawer");
  if (!drawer) return [];
  return Array.prototype.slice.call(drawer.querySelectorAll(
    "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), " +
    "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
  )).filter(function (element) { return element.offsetParent !== null; });
}

function focusDrawerContents(attempt) {
  var drawer = document.getElementById("posting-drawer");
  if (!drawer || drawer.getClientRects().length === 0) {
    if (attempt < 8) window.setTimeout(function () { focusDrawerContents(attempt + 1); }, 25);
    return;
  }
  var focusable = drawerFocusable();
  drawer.setAttribute("aria-hidden", "false");
  (focusable[0] || drawer).focus();
}

function onDrawerOpen() {
  drawerOpen = true;
  document.body.classList.add("drawer-is-open");
  var shell = document.querySelector(".shell");
  if (shell) { shell.inert = true; shell.setAttribute("aria-hidden", "true"); }
  window.requestAnimationFrame(function () { focusDrawerContents(0); });
  // HTMX may dispatch drawer-open before its target swap finishes. Reassert
  // focus after the short drawer transition so replacement content cannot
  // leave keyboard focus on <body>.
  window.setTimeout(function () { if (drawerOpen) focusDrawerContents(0); }, 400);
}

function onDrawerClose() {
  if (!drawerOpen) return;
  drawerOpen = false;
  document.body.classList.remove("drawer-is-open");
  var shell = document.querySelector(".shell");
  var drawer = document.getElementById("posting-drawer");
  if (shell) { shell.inert = false; shell.removeAttribute("aria-hidden"); }
  if (drawer) drawer.setAttribute("aria-hidden", "true");
  window.requestAnimationFrame(function () {
    if (drawerOpener && document.contains(drawerOpener)) drawerOpener.focus();
  });
}

window.addEventListener("drawer-open", onDrawerOpen);
window.addEventListener("drawer-close", onDrawerClose);

document.addEventListener("keydown", function (event) {
  if (!drawerOpen || event.key !== "Tab") return;
  var focusable = drawerFocusable();
  if (!focusable.length) { event.preventDefault(); return; }
  var first = focusable[0];
  var last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
});

/* ---------- SortableJS drag -> POST /board/move ---------- */

var lastDragAt = 0;

function initBoard() {
  if (typeof Sortable === "undefined") return;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll(".board-cards").forEach(function (column) {
    if (column._sortable) return;
    column._sortable = new Sortable(column, {
      group: "board",
      animation: reduced ? 0 : 140,
      ghostClass: "sortable-ghost",
      chosenClass: "sortable-chosen",
      dragClass: "sortable-drag",
      onStart: function () { document.body.classList.add("board-is-dragging"); },
      onEnd: function (event) {
        document.body.classList.remove("board-is-dragging");
        lastDragAt = Date.now();
        if (event.from === event.to) return;
        if (!event.to.dataset.status) return;
        htmx.ajax("POST", "/board/move", {
          values: { posting_id: event.item.dataset.id, status: event.to.dataset.status },
          target: "#board-region",
          swap: "innerHTML",
        });
      },
    });
  });
}

document.addEventListener("click", function (event) {
  if (Date.now() - lastDragAt < 200 && event.target.closest(".board-card")) {
    event.stopPropagation();
    event.preventDefault();
  }
}, true);

/* ---------- HTMX lifecycle feedback ---------- */

document.body.addEventListener("htmx:beforeRequest", function (event) {
  var target = event.detail && event.detail.target;
  if (target) target.setAttribute("aria-busy", "true");
});

document.body.addEventListener("htmx:afterRequest", function (event) {
  var target = event.detail && event.detail.target;
  if (target) target.removeAttribute("aria-busy");
});

document.body.addEventListener("htmx:afterSwap", function () {
  initBoard();
  initBoardPanel();
  if (!drawerOpen && selected >= 0 && document.querySelector(".board-col--inbox")) {
    selectCard(selected);
  }
});

document.body.addEventListener("htmx:afterSettle", function (event) {
  if (drawerOpen && event.detail && event.detail.target &&
      event.detail.target.id === "drawer-body") {
    focusDrawerContents(0);
  }
});

document.body.addEventListener("htmx:oobAfterSwap", function () {
  initBoard();
});

document.body.addEventListener("htmx:responseError", function (event) {
  var path = event.detail && event.detail.pathInfo && event.detail.pathInfo.requestPath;
  if (path === "/board/move") window.location.reload();
});

/* ---------- boot ---------- */

document.addEventListener("DOMContentLoaded", function () {
  applyTheme();
  initBoard();
  initBoardPanel();

  var themeButton = document.getElementById("theme-toggle");
  if (themeButton) {
    themeButton.addEventListener("click", function () {
      var order = [null, "light", "dark"];
      var current = localStorage.getItem("theme");
      var next = order[(order.indexOf(current) + 1) % order.length];
      if (next === null) localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
      applyTheme();
    });
  }

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
});
