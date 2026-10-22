// Applies the saved light/dark choice before the page paints (a blocking script in the head: no flash of the wrong theme).
// No saved choice means "follow the system". Storage can be blocked (private windows): then the system setting applies.
(function () {
  try {
    var choice = localStorage.getItem("fabulist-theme");
    if (choice === "light" || choice === "dark") document.documentElement.setAttribute("data-theme", choice);
  } catch (_) { /* follow the system */ }
})();
