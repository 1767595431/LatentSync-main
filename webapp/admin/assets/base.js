(function (g) {
  if (g.__APP_BASE_READY) return;
  function prefixFromPath(p) {
    p = (p || "/").split("?")[0];
    if (p.length > 1) p = p.replace(/\/+$/, "") || "/";
    var tails = ["/login.html", "/docs", "/redoc", "/openapi.json", "/admin"];
    for (var i = 0; i < tails.length; i++) {
      var t = tails[i];
      if (p === t) return "";
      if (p.length > t.length && p.slice(p.length - t.length) === t) return p.slice(0, -t.length);
    }
    if (p === "/") return "";
    return p;
  }
  var base = prefixFromPath((g.location && g.location.pathname) || "/");
  g.APP_BASE = base;
  g.apiUrl = function (path) {
    if (!path) return base ? base + "/" : "/";
    if (/^https?:\/\//i.test(path)) return path;
    var rel = path.charAt(0) === "/" ? path : "/" + path;
    if (base && (rel === base || rel.indexOf(base + "/") === 0)) return rel;
    return base + rel;
  };
  if (g.document && g.document.head) {
    var el = g.document.createElement("base");
    el.href = (base || "") + "/";
    g.document.head.insertBefore(el, g.document.head.firstChild);
  }
  g.__APP_BASE_READY = true;
})(typeof window !== "undefined" ? window : this);
