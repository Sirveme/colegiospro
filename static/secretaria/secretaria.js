// SecretariaPro — JS mínimo
function spCopiar(docId) {
  var el = document.getElementById("sp-doc-texto-" + docId);
  if (!el) return;
  var texto = el.innerText;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(texto).then(function () {
      spToast("Texto copiado");
    });
  } else {
    var ta = document.createElement("textarea");
    ta.value = texto;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); spToast("Texto copiado"); }
    catch (e) { spToast("No se pudo copiar"); }
    document.body.removeChild(ta);
  }
}

function spToast(msg) {
  var t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1c1f2a;color:#fff;padding:.55rem 1rem;border-radius:8px;font-size:.9rem;box-shadow:0 6px 24px rgba(0,0,0,.2);z-index:9999;";
  document.body.appendChild(t);
  setTimeout(function () { t.remove(); }, 1800);
}
