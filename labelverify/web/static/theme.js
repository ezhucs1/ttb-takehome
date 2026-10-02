// Apply the saved theme before first paint. Dark is the default; "light" is opt-in. A
// file rather than an inline script so the page's Content-Security-Policy can allow
// scripts from this origin only.
(function () {
  var t = "dark";
  try { t = localStorage.getItem("lv-theme") || t; } catch (e) {}
  document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
})();
