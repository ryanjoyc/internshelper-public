// Runs synchronously in <head> before first paint — no theme flash.
// localStorage.theme: "light" | "dark" | unset (= follow the system).
(function () {
  var t = localStorage.getItem("theme");
  var dark = t === "dark" ||
    (t !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
})();
