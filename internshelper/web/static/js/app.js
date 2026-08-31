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

/* ---------- disclosure popovers ---------- */

function closePopovers(except) {
  document.querySelectorAll("details[data-popover][open]").forEach(function (details) {
    if (details !== except) details.removeAttribute("open");
  });
}

document.addEventListener("click", function (event) {
  closePopovers(event.target.closest("details[data-popover]"));
});

document.addEventListener("toggle", function (event) {
  var details = event.target;
  if (details.matches && details.matches("details[data-popover][open]")) {
    closePopovers(details);
  }
}, true);

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") return;
  var details = event.target.closest("details[data-popover]") ||
    document.querySelector("details[data-popover][open]");
  if (!details || !details.open) return;
  event.preventDefault();
  details.removeAttribute("open");
  var summary = details.querySelector(":scope > summary");
  if (summary) summary.focus();
});

/* ---------- pipeline quick status ---------- */

var quickStatusFocusId = null;
var quickStatusFocusLane = null;
var replacementFocusKey = null;
var swapFocusSelector = null;

function closeQuickStatuses(except) {
  document.querySelectorAll("details[data-quick-status][open]").forEach(function (details) {
    if (details !== except) details.removeAttribute("open");
  });
}

function restoreQuickStatusFocus() {
  if (!quickStatusFocusId) return;
  var postingId = quickStatusFocusId;
  var targetLane = quickStatusFocusLane;
  quickStatusFocusId = null;
  quickStatusFocusLane = null;
  var card = Array.prototype.find.call(
    document.querySelectorAll(".board-card[data-id]"),
    function (candidate) { return candidate.dataset.id === postingId; }
  );
  var trigger = card && card.querySelector("[data-status-trigger]");
  if (!trigger || trigger.offsetParent === null) {
    trigger = Array.prototype.find.call(
      document.querySelectorAll("[data-lane-strip]"),
      function (candidate) { return candidate.dataset.laneStrip === targetLane; }
    );
  }
  if (!trigger) trigger = document.querySelector("main#page");
  if (trigger) window.requestAnimationFrame(function () { trigger.focus(); });
}

function restoreReplacementFocus() {
  var selector = swapFocusSelector;
  swapFocusSelector = null;
  if (selector) {
    var focusTarget = function () {
      var target = document.querySelector(selector);
      if (target) target.focus();
    };
    window.requestAnimationFrame(focusTarget);
    window.setTimeout(focusTarget, 75);
  }
  if (!replacementFocusKey) return;
  var key = replacementFocusKey;
  replacementFocusKey = null;
  var focusControl = function () {
    var control = Array.prototype.find.call(
      document.querySelectorAll("[data-replacement-focus]"),
      function (candidate) {
        return candidate.dataset.replacementFocus === key && candidate.tabIndex >= 0;
      }
    );
    if (control) control.focus();
  };
  window.requestAnimationFrame(focusControl);
  window.setTimeout(focusControl, 75);
}

function clearPendingReplacementFocus() {
  quickStatusFocusId = null;
  quickStatusFocusLane = null;
  replacementFocusKey = null;
  swapFocusSelector = null;
}

function positionQuickStatus(details) {
  details.classList.remove("is-above");
  if (!details.open) return;
  window.requestAnimationFrame(function () {
    var trigger = details.querySelector("[data-status-trigger]");
    var popover = details.querySelector(".quick-status-popover");
    if (!trigger || !popover) return;
    var triggerBox = trigger.getBoundingClientRect();
    var popoverBox = popover.getBoundingClientRect();
    var roomAbove = triggerBox.top;
    var roomBelow = window.innerHeight - triggerBox.bottom;
    if (popoverBox.bottom > window.innerHeight - 12 && roomAbove > roomBelow) {
      details.classList.add("is-above");
    }
  });
}

document.addEventListener("click", function (event) {
  var current = event.target.closest("details[data-quick-status]");
  closeQuickStatuses(current);
});

document.addEventListener("toggle", function (event) {
  var details = event.target;
  if (!details.matches || !details.matches("details[data-quick-status]")) return;
  positionQuickStatus(details);
}, true);

document.addEventListener("change", function (event) {
  if (!event.target.matches("[data-quick-status-form] input[name='status']")) return;
  var form = event.target.closest("[data-quick-status-form]");
  var notes = form.querySelector("[data-status-notes]");
  var submit = form.querySelector("[data-status-submit]");
  notes.hidden = false;
  submit.disabled = false;
  submit.textContent = "Move to " + event.target.value;
  positionQuickStatus(form.closest("details[data-quick-status]"));
});

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") return;
  var details = event.target.closest("details[data-quick-status]");
  if (!details || !details.open) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  details.removeAttribute("open");
  var trigger = details.querySelector("[data-status-trigger]");
  if (trigger) trigger.focus();
});

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
  var opener = cards[index].querySelector("[data-drawer-trigger]");
  if (opener) opener.focus({ preventScroll: true });
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
  var inboxDrawerTrigger = target.matches("[data-drawer-trigger]") &&
    target.closest(".board-col--inbox");
  if (target.closest("button, a, summary, details, [contenteditable='true']") &&
      !inboxDrawerTrigger) return;
  if (!document.querySelector(".board-col--inbox")) return;

  switch (event.key) {
    case "j": case "ArrowDown": event.preventDefault(); selectCard(selected + 1); break;
    case "k": case "ArrowUp": event.preventDefault(); selectCard(selected - 1); break;
    case "x": clickWithin(selectedCard(), ".card-dismiss"); break;
    case "f": clickWithin(selectedCard(), ".card-flag"); break;
    case "u": undoNewestToast(); break;
    case "o": clickWithin(selectedCard(), "[data-drawer-trigger]"); break;
    case "Escape":
      inboxCards().forEach(function (card) { card.classList.remove("is-selected"); });
      selected = -1;
      break;
  }
});

/* ---------- posting drawer focus, inert background, and restoration ---------- */

var drawerOpener = null;
var drawerOpenerPostingId = null;
var drawerOpen = false;

document.addEventListener("click", function (event) {
  var trigger = event.target.closest("[data-drawer-trigger]");
  if (trigger) {
    drawerOpener = trigger;
    drawerOpenerPostingId = trigger.dataset.postingId || null;
  }
}, true);

function matchingDrawerTrigger(postingId) {
  if (!postingId) return null;
  return Array.prototype.find.call(
    document.querySelectorAll("[data-drawer-trigger][data-posting-id]"),
    function (candidate) {
      return candidate.dataset.postingId === postingId && candidate.offsetParent !== null;
    }
  );
}

function firstVisibleDrawerTrigger() {
  return Array.prototype.find.call(
    document.querySelectorAll("[data-drawer-trigger]"),
    function (candidate) { return candidate.offsetParent !== null; }
  );
}

document.addEventListener("click", function (event) {
  var surface = event.target.closest("[data-card-open], [data-row-open]");
  if (!surface || event.target.closest(
    "button, a, input, select, textarea, summary, details, [data-no-drawer]"
  )) return;
  var trigger = surface.querySelector("[data-drawer-trigger]");
  if (trigger) trigger.click();
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
  window.setTimeout(function () {
    var drawer = document.getElementById("posting-drawer");
    var active = document.activeElement;
    if (drawerOpen && (!drawer || !active || active === document.body || !drawer.contains(active))) {
      focusDrawerContents(0);
    }
  }, 400);
}

function onDrawerClose() {
  if (!drawerOpen) return;
  drawerOpen = false;
  document.body.classList.remove("drawer-is-open");
  var shell = document.querySelector(".shell");
  var drawer = document.getElementById("posting-drawer");
  if (shell) { shell.inert = false; shell.removeAttribute("aria-hidden"); }
  if (drawer) drawer.setAttribute("aria-hidden", "true");
  function restoreOpenerFocus() {
    if (drawerOpen) return;
    var active = document.activeElement;
    if (active && active !== document.body && (!drawer || !drawer.contains(active))) return;
    var target = drawerOpener && document.contains(drawerOpener) &&
      drawerOpener.offsetParent !== null ? drawerOpener : null;
    target = target || matchingDrawerTrigger(drawerOpenerPostingId) ||
      firstVisibleDrawerTrigger() || document.querySelector("main#page");
    if (target) target.focus({ preventScroll: true });
  }
  // Chromium can reject focus in the same frame that an inert ancestor is
  // re-enabled. Retry after the disclosure transition has begun settling.
  window.requestAnimationFrame(restoreOpenerFocus);
  window.setTimeout(restoreOpenerFocus, 75);
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
var pendingDrag = null;

function restorePendingDrag() {
  if (!pendingDrag || !pendingDrag.item || !pendingDrag.parent) return;
  if (pendingDrag.next && pendingDrag.next.parentNode === pendingDrag.parent) {
    pendingDrag.parent.insertBefore(pendingDrag.item, pendingDrag.next);
  } else {
    pendingDrag.parent.appendChild(pendingDrag.item);
  }
  pendingDrag = null;
}

function initBoard() {
  if (typeof Sortable === "undefined") return;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll(".board-cards").forEach(function (column) {
    if (column._sortable) return;
    var role = column.dataset.sortRole;
    column._sortable = new Sortable(column, {
      group: {
        name: "board",
        pull: role !== "inbox-return",
        put: function (to, from) {
          var targetRole = to.el.dataset.sortRole;
          var sourceRole = from.el.dataset.sortRole;
          if (targetRole === "pipeline") return true;
          return targetRole === "inbox-return" && sourceRole === "pipeline";
        },
      },
      sort: false,
      animation: reduced ? 0 : 140,
      ghostClass: "sortable-ghost",
      chosenClass: "sortable-chosen",
      dragClass: "sortable-drag",
      filter: "[data-no-drag]",
      preventOnFilter: false,
      onStart: function (event) {
        document.body.classList.add("board-is-dragging");
        pendingDrag = {
          item: event.item,
          parent: event.from,
          next: event.item.nextElementSibling,
        };
      },
      onEnd: function (event) {
        document.body.classList.remove("board-is-dragging");
        lastDragAt = Date.now();
        if (event.from === event.to || !event.to.dataset.status) {
          pendingDrag = null;
          return;
        }
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

function showToast(text, kind) {
  var region = document.getElementById("toast-region");
  if (!region || !text) return;
  var toast = document.createElement("div");
  toast.className = "toast" + (kind === "error" ? " toast--error" : "");
  toast.setAttribute("role", kind === "error" ? "alert" : "status");
  var mark = document.createElement("span");
  mark.className = "toast-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = kind === "error" ? "!" : "✓";
  var message = document.createElement("span");
  message.className = "toast-text";
  message.textContent = text;
  toast.appendChild(mark);
  toast.appendChild(message);
  region.appendChild(toast);
  window.setTimeout(function () { toast.remove(); }, kind === "error" ? 7000 : 5000);
}

function responseErrorText(event) {
  var xhr = event.detail && event.detail.xhr;
  if (!xhr) return "The request failed. Please try again.";
  try {
    var payload = JSON.parse(xhr.responseText || "{}");
    if (payload.detail) return String(payload.detail);
  } catch (_error) {
    // Non-JSON server responses fall through to a concise status message.
  }
  return xhr.statusText || "The request failed. Please try again.";
}

document.body.addEventListener("htmx:beforeRequest", function (event) {
  var requester = event.detail && event.detail.elt;
  var quickForm = requester && requester.closest && requester.closest("[data-quick-status-form]");
  if (quickForm) {
    quickStatusFocusId = quickForm.dataset.postingId;
    var selectedStatus = quickForm.querySelector("input[name='status']:checked");
    quickStatusFocusLane = selectedStatus && selectedStatus.value;
  }
  var replacementControl = requester && requester.closest &&
    requester.closest("[data-replacement-focus]");
  if (replacementControl) {
    replacementFocusKey = replacementControl.dataset.replacementFocus;
  }
  var activeControl = document.activeElement;
  var swapFocusControl = requester && requester.closest &&
    requester.closest("[data-focus-after-swap]");
  if (!swapFocusControl && requester && requester.contains &&
      activeControl && requester.contains(activeControl) && activeControl.closest) {
    swapFocusControl = activeControl.closest("[data-focus-after-swap]");
  }
  if (swapFocusControl) swapFocusSelector = swapFocusControl.dataset.focusAfterSwap;
  var target = event.detail && event.detail.target;
  if (target) target.setAttribute("aria-busy", "true");
});

document.body.addEventListener("htmx:afterRequest", function (event) {
  var target = event.detail && event.detail.target;
  if (target) target.removeAttribute("aria-busy");
  if (event.detail && event.detail.successful &&
      event.detail.pathInfo && event.detail.pathInfo.requestPath === "/board/move") {
    pendingDrag = null;
  }
});

document.body.addEventListener("htmx:afterSwap", function () {
  initBoard();
  initBoardPanel();
  if (!drawerOpen && selected >= 0 && document.querySelector(".board-col--inbox")) {
    selectCard(selected);
  }
});

document.body.addEventListener("htmx:afterSettle", function (event) {
  var target = event.detail && event.detail.target;
  if (drawerOpen && target && target.id === "drawer-body") {
    if (swapFocusSelector || replacementFocusKey) {
      restoreReplacementFocus();
    } else {
      focusDrawerContents(0);
    }
  }
  if (target && ["board-region", "company-list", "source-list"].includes(target.id)) {
    // Wait for the primary target to settle so an out-of-band nav swap cannot
    // consume the focus key before the replacement control exists.
    restoreQuickStatusFocus();
    restoreReplacementFocus();
  }
});

document.body.addEventListener("htmx:oobAfterSwap", function () {
  initBoard();
});

document.body.addEventListener("htmx:responseError", function (event) {
  var path = event.detail && event.detail.pathInfo && event.detail.pathInfo.requestPath;
  if (path === "/board/move") restorePendingDrag();
  clearPendingReplacementFocus();
  showToast(responseErrorText(event), "error");
});

document.body.addEventListener("htmx:sendError", function () {
  restorePendingDrag();
  clearPendingReplacementFocus();
  showToast("Could not reach internsHELPer. Please try again.", "error");
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
