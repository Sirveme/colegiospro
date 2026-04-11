// SecretariaPro — JS del módulo

// ─── Toggle tema (cicla los 4 temas disponibles) ───
var SP_TEMAS = ["claro", "oscuro", "pastel", "elegante"];
function toggleTema() {
  var html = document.documentElement;
  var actual = html.getAttribute("data-tema") || "claro";
  var idx = SP_TEMAS.indexOf(actual);
  var nuevo = SP_TEMAS[(idx + 1) % SP_TEMAS.length];
  spAplicarTemaInline(nuevo);
}

function spAplicarTemaInline(nombre) {
  if (SP_TEMAS.indexOf(nombre) === -1) nombre = "claro";
  document.documentElement.setAttribute("data-tema", nombre);
  try { localStorage.setItem("sp-tema", nombre); } catch (e) {}
  spActualizarIconoTema();
}

function spAplicarFuenteInline(size) {
  if (["pequeno","normal","grande","xl"].indexOf(size) === -1) size = "normal";
  document.documentElement.setAttribute("data-fuente", size);
  try { localStorage.setItem("sp-fuente", size); } catch (e) {}
}

function spActualizarIconoTema() {
  var icon = document.getElementById("sp-tema-icon");
  if (!icon) return;
  var t = document.documentElement.getAttribute("data-tema") || "claro";
  var iconos = { claro: "☀", oscuro: "🌙", pastel: "🌸", elegante: "✨" };
  icon.textContent = iconos[t] || "☀";
}

// ─── Selector de tipo de documento ───
function spSeleccionarTipo(tipoId) {
  var input = document.getElementById("tipo-seleccionado");
  if (input) input.value = tipoId;
  var btns = document.querySelectorAll(".sp-tipo-btn");
  btns.forEach(function (b) {
    if (b.dataset.tipo === tipoId) b.classList.add("activo");
    else b.classList.remove("activo");
  });
}

// ─── Dictado por voz (Web Speech API) ───
var spRecognition = null;
var spVozActiva = false;

function spToggleVoz() {
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    alert("Tu navegador no soporta dictado por voz. Usa Chrome.");
    return;
  }
  if (spVozActiva && spRecognition) {
    spRecognition.stop();
    return;
  }
  spRecognition = new SR();
  spRecognition.lang = "es-PE";
  spRecognition.continuous = true;
  spRecognition.interimResults = true;

  var btn = document.getElementById("btn-voz");
  var estado = document.getElementById("voz-estado");
  var ta = document.getElementById("instruccion");
  var textoBase = ta ? (ta.value || "") : "";

  spRecognition.onstart = function () {
    spVozActiva = true;
    if (btn) { btn.textContent = "🔴"; btn.classList.add("activo"); }
    if (estado) estado.style.display = "block";
  };

  spRecognition.onresult = function (e) {
    var final = "";
    var interim = "";
    for (var i = e.resultIndex; i < e.results.length; i++) {
      var t = e.results[i][0].transcript;
      if (e.results[i].isFinal) final += t;
      else interim += t;
    }
    if (ta) {
      ta.value = (textoBase + " " + final + " " + interim).trim();
    }
  };

  spRecognition.onerror = function () {
    spVozActiva = false;
    if (btn) { btn.textContent = "🎤"; btn.classList.remove("activo"); }
    if (estado) estado.style.display = "none";
  };

  spRecognition.onend = function () {
    spVozActiva = false;
    if (btn) { btn.textContent = "🎤"; btn.classList.remove("activo"); }
    if (estado) estado.style.display = "none";
  };

  spRecognition.start();
}

// ─── Toast tras guardar preferencias ───
function spOnPrefsGuardadas(e) {
  if (e && e.detail && e.detail.successful) {
    spToast("Preferencias guardadas");
  }
}

document.addEventListener("DOMContentLoaded", function () {
  // El tema ya fue aplicado por el script inline del <head>; aquí solo el icono.
  spActualizarIconoTema();
});

// HTMX boost reemplaza el body — re-sincronizar el icono después de navegar
document.addEventListener("htmx:afterSwap", function () {
  spActualizarIconoTema();
});


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

// ─── Spinner del botón "Generar documento" ───
// Reemplaza el contenido del botón btn-generar con un spinner + mensaje
// rotativo cada 2 segundos mientras dura la request HTMX.
var SP_LOADER_MSGS = [
  "Redactando…",
  "Revisando el tono…",
  "Aplicando formato oficial…",
  "Casi listo…"
];
var _spInterval = null;
var _spBtnLabelOriginal = null;

function spStartSpinner() {
  var btn = document.getElementById("btn-generar");
  if (!btn) return;
  if (_spBtnLabelOriginal === null) {
    _spBtnLabelOriginal = btn.innerHTML;
  }
  btn.disabled = true;
  var i = 0;
  btn.innerHTML = '<span class="sp-spinner"></span> ' + SP_LOADER_MSGS[0];
  if (_spInterval) clearInterval(_spInterval);
  _spInterval = setInterval(function () {
    i = (i + 1) % SP_LOADER_MSGS.length;
    btn.innerHTML = '<span class="sp-spinner"></span> ' + SP_LOADER_MSGS[i];
  }, 2000);
}

function spStopSpinner() {
  if (_spInterval) {
    clearInterval(_spInterval);
    _spInterval = null;
  }
  var btn = document.getElementById("btn-generar");
  if (!btn) return;
  btn.disabled = false;
  btn.innerHTML = _spBtnLabelOriginal || "Generar documento";
}

// ─── Spinner del botón Corregir (modo Corrector) ───
var SP_CORR_MSGS = [
  "Procesando…",
  "Aplicando la acción…",
  "Revisando el resultado…",
  "Casi listo…"
];
var _spCorrInterval = null;
var _spCorrBtnLabel = null;

function spStartCorrSpinner() {
  var btn = document.getElementById("btn-corregir");
  if (!btn) return;
  if (_spCorrBtnLabel === null) _spCorrBtnLabel = btn.innerHTML;
  btn.disabled = true;
  var i = 0;
  btn.innerHTML = '<span class="sp-spinner"></span> ' + SP_CORR_MSGS[0];
  if (_spCorrInterval) clearInterval(_spCorrInterval);
  _spCorrInterval = setInterval(function () {
    i = (i + 1) % SP_CORR_MSGS.length;
    btn.innerHTML = '<span class="sp-spinner"></span> ' + SP_CORR_MSGS[i];
  }, 2000);
}

function spStopCorrSpinner() {
  if (_spCorrInterval) {
    clearInterval(_spCorrInterval);
    _spCorrInterval = null;
  }
  var btn = document.getElementById("btn-corregir");
  if (!btn) return;
  btn.disabled = false;
  btn.innerHTML = _spCorrBtnLabel || "Procesar texto";
}

// Feedback visual al seleccionar archivo
document.addEventListener("change", function (e) {
  if (e.target && e.target.id === "doc-referencia") {
    var estado = document.getElementById("doc-estado");
    if (!estado) return;
    if (e.target.files && e.target.files[0]) {
      estado.style.display = "block";
      estado.style.color = "#2d8a48";
      estado.textContent = "✓ Archivo cargado: " + e.target.files[0].name;
    } else {
      estado.style.display = "none";
      estado.textContent = "";
    }
  }
});

// ─── Ficha del destinatario ───
function spAbrirFicha() {
  var sel = document.getElementById("sp-institucion-select");
  var panel = document.getElementById("sp-ficha-panel");
  var body = document.getElementById("sp-ficha-body");
  if (!sel || !panel || !body) return;
  var instId = sel.value;
  if (!instId) {
    spToast("Primero elige un destinatario en el selector");
    return;
  }
  panel.classList.add("is-open");
  panel.setAttribute("aria-hidden", "false");
  body.innerHTML = '<p class="sp-placeholder">Cargando ficha…</p>';
  fetch("/secretaria/destinatario/" + encodeURIComponent(instId) + "/ficha", {
    credentials: "same-origin",
    headers: { "Accept": "text/html" }
  })
    .then(function (r) { return r.text(); })
    .then(function (html) {
      body.innerHTML = html;
      if (window.htmx) window.htmx.process(body);
    })
    .catch(function () {
      body.innerHTML = '<p class="sp-alert sp-alert-error">No se pudo cargar la ficha.</p>';
    });
}

function spCerrarFicha() {
  var panel = document.getElementById("sp-ficha-panel");
  if (!panel) return;
  panel.classList.remove("is-open");
  panel.setAttribute("aria-hidden", "true");
}

function spFichaGuardadaToast(e) {
  // El form usa htmx — interceptamos solo para mostrar un toast en éxito.
  // htmx dispara htmx:afterRequest después; aquí solo evitamos el submit nativo.
  // Devolvemos true para dejar que htmx tome el control.
  return true;
}

document.addEventListener("htmx:afterRequest", function (e) {
  // Si una request del form de ficha terminó OK, toast.
  var path = (e.detail && e.detail.requestConfig && e.detail.requestConfig.path) || "";
  if (/\/destinatario\/\d+\/ficha$/.test(path) && e.detail.successful) {
    spToast("Ficha guardada");
  }
});

// Cerrar panel con Escape
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") spCerrarFicha();
});

// Por si una request HTMX falla, paramos el spinner
document.addEventListener("htmx:responseError", spStopSpinner);
document.addEventListener("htmx:sendError", spStopSpinner);
