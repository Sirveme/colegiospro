// SecretariaPro — JS del módulo

// ─── Copiar al portapapeles ───
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

// ─── Imprimir ───
// Marca el documento como "imprimible" para que el @media print del CSS
// oculte el resto de la página y muestre solo el documento.
function spImprimir(docId) {
  var el = document.getElementById("sp-doc-texto-" + docId);
  if (!el) { window.print(); return; }
  document.body.classList.add("sp-printing");
  el.closest(".sp-doc").classList.add("sp-print-target");
  var cleanup = function () {
    document.body.classList.remove("sp-printing");
    el.closest(".sp-doc").classList.remove("sp-print-target");
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);
  window.print();
}

// ─── Toast simple ───
function spToast(msg) {
  var t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1c1f2a;color:#fff;padding:.55rem 1rem;border-radius:8px;font-size:.9rem;box-shadow:0 6px 24px rgba(0,0,0,.2);z-index:9999;";
  document.body.appendChild(t);
  setTimeout(function () { t.remove(); }, 1800);
}

// ─── Búsqueda de RUC en SUNAT ───
function spBuscarRuc() {
  var input = document.getElementById("sp-ruc-input");
  var msg = document.getElementById("sp-ruc-msg");
  if (!input || !msg) return;
  var ruc = (input.value || "").trim();
  msg.textContent = "";
  msg.className = "sp-ruc-msg";
  if (!/^\d{11}$/.test(ruc)) {
    msg.textContent = "El RUC debe tener exactamente 11 dígitos.";
    msg.classList.add("sp-ruc-msg--error");
    return;
  }
  msg.textContent = "Consultando SUNAT…";
  fetch("/secretaria/api/sunat-ruc?ruc=" + encodeURIComponent(ruc), {
    credentials: "same-origin",
    headers: { "Accept": "application/json" }
  })
    .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
    .then(function (res) {
      var d = res.data || {};
      if (!d.ok) {
        msg.textContent = d.error || "No se pudo consultar el RUC.";
        msg.classList.add("sp-ruc-msg--error");
        return;
      }
      // Pre-llenar el formulario
      var setVal = function (id, value) {
        var el = document.getElementById(id);
        if (el && value) el.value = value;
      };
      setVal("f-ruc", d.ruc);
      setVal("f-nombre", d.nombre_institucion);
      setVal("f-direccion", d.direccion);
      setVal("f-region", d.departamento);
      setVal("f-ciudad", d.distrito || d.provincia);
      msg.textContent = "✓ Datos cargados desde SUNAT — completa los campos del titular.";
      msg.classList.add("sp-ruc-msg--ok");
    })
    .catch(function () {
      msg.textContent = "Error de red al consultar SUNAT.";
      msg.classList.add("sp-ruc-msg--error");
    });
}

// ─── HTMX hooks ───
// Re-aplicar nada especial; hx-boost en <body> ya maneja navegación.
// Cuando htmx reemplaza el body, los listeners onclick=" " quedan vivos
// porque están en HTML inline.
document.addEventListener("htmx:afterSwap", function (e) {
  // Por si en el futuro queremos rerun de algo tras navegar
});
