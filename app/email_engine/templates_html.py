# ══════════════════════════════════════════════════════════
# app/email_engine/templates_html.py
# Plantillas HTML para campañas. Variables Jinja2:
#   {{ municipalidad }}, {{ departamento }}, {{ provincia }},
#   {{ alcalde }}, {{ nombre }}, {{ correo }},
#   {{ link_registro }}, {{ link_guia }}, {{ link_demo }},
#   {{ link_baja }}, {{ link_objecion }}, {{ pixel }}
# ══════════════════════════════════════════════════════════


# ─── PLANTILLA A — Secretaria ─────────────────────────────────
TEMPLATE_A_SECRETARIA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Herramienta gratuita para secretarias</title>
</head>
<body style="margin:0;padding:0;background:#F8F5EF;font-family:Arial,sans-serif">

{{ pixel }}

<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:20px 0">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:white;border-radius:16px;overflow:hidden;
              max-width:600px;width:100%">

  <tr><td style="background:linear-gradient(135deg,#0F3460,#0D7A60);
                  padding:32px 40px;text-align:center">
    <div style="font-size:13px;letter-spacing:2px;text-transform:uppercase;
                color:rgba(255,255,255,0.6);margin-bottom:8px">
      SecretariaPro · Perú Sistemas Pro
    </div>
    <div style="font-size:26px;font-weight:700;color:white;line-height:1.2">
      ¿Cuánto tiempo le toma<br>redactar un oficio?
    </div>
  </td></tr>

  <tr><td style="padding:36px 40px">

    <p style="font-size:15px;color:#333;line-height:1.7;margin:0 0 16px">
      Estimada secretaria de <strong>{{ municipalidad }}</strong>:
    </p>

    <p style="font-size:15px;color:#333;line-height:1.7;margin:0 0 24px">
      Si la respuesta es <em>más de 15 minutos</em>, tenemos algo para ti.
      SecretariaPro convierte lo que te dicen coloquialmente en un documento
      oficial impecable — en segundos.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="background:#F0FAF4;border-left:4px solid #0D7A60;
                    border-radius:0 10px 10px 0;padding:16px 20px">
      <div style="font-size:13px;color:#0D7A60;font-weight:700;margin-bottom:6px">
        ¿Te suena familiar?
      </div>
      <div style="font-size:14px;color:#333;line-height:1.6">
        "Me dictaron algo y tengo que convertirlo en oficio formal"<br>
        "La asamblea duró 3 horas y tengo que hacer el acta"<br>
        "No sé si este tono es el correcto para el Ministerio"
      </div>
    </td></tr>
    </table>

    <div style="margin:24px 0">
      <div style="font-size:15px;color:#333;line-height:1.7">
        SecretariaPro sabe que cada institución es diferente. Por eso la prueba es
        <strong>100% gratuita</strong> — sin pedir autorización, sin instalar nada.
      </div>
    </div>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:8px 0 24px">
      <a href="{{ link_registro }}"
         style="display:inline-block;background:#0D7A60;color:white;
                font-size:16px;font-weight:700;padding:18px 36px;
                border-radius:100px;text-decoration:none;letter-spacing:0.5px">
        TOCA Y ACCEDE — ES GRATIS
      </a>
      <div style="font-size:12px;color:#888;margin-top:8px">
        Acceso gratuito disponible esta semana
      </div>
    </td></tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px">
    <tr>
      <td width="50%" style="padding:8px;vertical-align:top">
        <div style="background:#F8F5EF;border-radius:10px;padding:16px">
          <div style="font-size:20px;margin-bottom:6px">🎤</div>
          <div style="font-size:13px;font-weight:700;color:#0F3460">Dicta o escribe</div>
          <div style="font-size:12px;color:#666;margin-top:4px">
            Como te lo dijeron. La IA hace el resto.
          </div>
        </div>
      </td>
      <td width="50%" style="padding:8px;vertical-align:top">
        <div style="background:#F8F5EF;border-radius:10px;padding:16px">
          <div style="font-size:20px;margin-bottom:6px">📄</div>
          <div style="font-size:13px;font-weight:700;color:#0F3460">7 tipos de documento</div>
          <div style="font-size:12px;color:#666;margin-top:4px">
            Oficio, Circular, Acta, Resolución y más.
          </div>
        </div>
      </td>
    </tr>
    <tr>
      <td width="50%" style="padding:8px;vertical-align:top">
        <div style="background:#F8F5EF;border-radius:10px;padding:16px">
          <div style="font-size:20px;margin-bottom:6px">📅</div>
          <div style="font-size:13px;font-weight:700;color:#0F3460">Año oficial automático</div>
          <div style="font-size:12px;color:#666;margin-top:4px">
            "Año del Bicentenario..." siempre incluido.
          </div>
        </div>
      </td>
      <td width="50%" style="padding:8px;vertical-align:top">
        <div style="background:#F8F5EF;border-radius:10px;padding:16px">
          <div style="font-size:20px;margin-bottom:6px">🔢</div>
          <div style="font-size:13px;font-weight:700;color:#0F3460">Numeración correlativa</div>
          <div style="font-size:12px;color:#666;margin-top:4px">
            OFICIO N° 045-2026 — automático.
          </div>
        </div>
      </td>
    </tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="background:#FBF5E6;border:1px solid #C9943A;
                    border-radius:10px;padding:20px">
      <div style="font-size:14px;font-weight:700;color:#C9943A;margin-bottom:8px">
        DESCARGA GRATIS — Guía de Eficiencia Administrativa 2026
      </div>
      <div style="font-size:13px;color:#333;margin-bottom:14px">
        8 páginas con plantillas de tonos, checklist antes de firmar y cómo
        hablar con tu jefe para modernizar tu oficina.
      </div>
      <a href="{{ link_guia }}"
         style="display:inline-block;background:#C9943A;color:white;
                font-size:14px;font-weight:700;padding:12px 24px;
                border-radius:100px;text-decoration:none">
        LINK ACTIVO — TOCA PARA DESCARGAR
      </a>
    </td></tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:24px">
    <tr><td style="background:#EEF4FB;border:1px solid #B8D4F0;
                    border-radius:10px;padding:16px 20px">
      <div style="font-size:13px;font-weight:700;color:#0F3460;margin-bottom:6px">
        ACCEDE GRATIS Y CONGELA EL PRECIO
      </div>
      <div style="font-size:13px;color:#333;line-height:1.6">
        Las instituciones que se registren esta semana acceden gratis el primer
        mes y congelan el precio actual cuando decidan suscribirse. El precio
        sube en mayo.
      </div>
    </td></tr>
    </table>

  </td></tr>

  <tr><td style="background:#0F3460;padding:24px 40px;text-align:center">
    <div style="font-size:12px;color:rgba(255,255,255,0.6);line-height:1.6">
      Perú Sistemas Pro E.I.R.L. · RUC 20615446565 · Iquitos, Loreto<br>
      WhatsApp:
      <a href="{{ link_demo }}" style="color:#C9943A;text-decoration:none">+51 967 317 946</a>
      ·
      <a href="{{ link_baja }}" style="color:rgba(255,255,255,0.4);text-decoration:none;font-size:11px">
        No deseo recibir más correos
      </a>
    </div>
    <div style="margin-top:12px">
      <a href="{{ link_objecion }}"
         style="color:rgba(255,255,255,0.5);font-size:11px;text-decoration:none">
        ¿Por qué no te interesa? Cuéntanos →
      </a>
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


# ─── PLANTILLA B — Alcalde / Directivo ────────────────────────
TEMPLATE_B_ALCALDE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Comunicación institucional impecable</title>
</head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:Arial,sans-serif">
{{ pixel }}
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:20px 0">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:white;border-radius:16px;overflow:hidden;
              max-width:600px;width:100%">

  <tr><td style="background:#0F3460;padding:32px 40px;text-align:center">
    <div style="font-size:11px;letter-spacing:3px;text-transform:uppercase;
                color:rgba(255,255,255,0.5);margin-bottom:10px">
      Para la autoridad de {{ municipalidad }}
    </div>
    <div style="font-size:24px;font-weight:700;color:white;line-height:1.2">
      Usted pone la firma.<br>
      <span style="color:#C9943A">Nosotros garantizamos que el contenido esté a su altura.</span>
    </div>
  </td></tr>

  <tr><td style="padding:36px 40px">
    <p style="font-size:15px;color:#333;line-height:1.7;margin:0 0 20px">
      Estimada autoridad de <strong>{{ municipalidad }}, {{ departamento }}</strong>:
    </p>
    <p style="font-size:15px;color:#333;line-height:1.7;margin:0 0 24px">
      Cada documento que sale de su despacho refleja la imagen de su municipio.
      Un error de tono, una numeración incorrecta o una estructura inadecuada
      puede hacer rebotar un trámite — o simplemente quedar mal ante otra
      institución.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px">
    <tr><td style="background:#FEF2F2;border-left:4px solid #DC2626;
                    border-radius:0 10px 10px 0;padding:16px 20px">
      <div style="font-size:13px;font-weight:700;color:#DC2626;margin-bottom:6px">
        Los 3 dolores más comunes
      </div>
      <div style="font-size:13px;color:#333;line-height:1.7">
        ✗ Documentos que regresan con correcciones<br>
        ✗ Redacciones que no representan la formalidad del cargo<br>
        ✗ Si la secretaria se va, se pierde el estilo institucional
      </div>
    </td></tr>
    </table>

    <p style="font-size:15px;color:#333;line-height:1.7;margin:0 0 24px">
      <strong>SecretariaPro</strong> es la herramienta que estandariza la
      comunicación de su institución. El Colegio de Contadores de Loreto —
      con más de 1,800 miembros — ya lo usa.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:8px 0 24px">
      <a href="{{ link_registro }}"
         style="display:inline-block;background:#0F3460;color:white;
                font-size:15px;font-weight:700;padding:18px 36px;
                border-radius:100px;text-decoration:none">
        VER DEMO — LINK ACTIVO
      </a>
      <div style="font-size:12px;color:#888;margin-top:8px">
        Su secretaria puede empezar hoy, sin costo.
      </div>
    </td></tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="background:#EEF4FB;border-radius:10px;padding:20px">
      <div style="font-size:13px;font-weight:700;color:#0F3460;margin-bottom:8px">
        ¿Qué gana su institución?
      </div>
      <div style="font-size:13px;color:#333;line-height:1.8">
        ✓ Documentos con estructura oficial peruana correcta<br>
        ✓ Año oficial, numeración correlativa y tonos automáticos<br>
        ✓ Estandarización — el mismo estilo, sin importar quién redacte<br>
        ✓ Primer mes sin costo · Sin contrato
      </div>
    </td></tr>
    </table>

  </td></tr>

  <tr><td style="background:#0F3460;padding:24px 40px;text-align:center">
    <div style="font-size:12px;color:rgba(255,255,255,0.6);line-height:1.6">
      Perú Sistemas Pro E.I.R.L. · RUC 20615446565 · Iquitos, Loreto<br>
      <a href="{{ link_demo }}" style="color:#C9943A;text-decoration:none">
        WhatsApp: +51 967 317 946
      </a>
      ·
      <a href="{{ link_baja }}"
         style="color:rgba(255,255,255,0.4);text-decoration:none;font-size:11px">
        No deseo recibir más correos
      </a>
    </div>
    <div style="margin-top:10px">
      <a href="{{ link_objecion }}"
         style="color:rgba(255,255,255,0.4);font-size:11px;text-decoration:none">
        ¿Por qué no le interesa? Cuéntenos →
      </a>
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


PLANTILLAS = {
    "A_secretaria": {
        "label": "A — Secretaria",
        "asunto_default": "Para la secretaria de {{ municipalidad }}: redacta oficios en 15 seg",
        "html": TEMPLATE_A_SECRETARIA,
    },
    "B_alcalde": {
        "label": "B — Alcalde / Directivo",
        "asunto_default": "Para la autoridad de {{ municipalidad }}: comunicación institucional a su altura",
        "html": TEMPLATE_B_ALCALDE,
    },
}
