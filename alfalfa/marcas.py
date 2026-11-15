"""Las marcas con que el arnés envuelve el contenido ajeno no se pueden cerrar desde dentro.

Un adjunto, la plantilla de una orden «/» importada o el AGENTS.md de un repositorio van entre marcas
(`<contenido_adjunto>…</contenido_adjunto>`, `<plantilla-del-repositorio>…`, `--- project-doc ---`)
para que el modelo sepa dónde empieza y acaba lo ajeno. Antes sólo se neutralizaba el cierre escrito
exactamente igual: `</contenido_adjunto >`, `</CONTENIDO_ADJUNTO>` o un salto de línea antes del `>`
llegaban intactos, y el cuerpo de una plantilla no se tocaba. El contenido podía cerrar su marca y
seguir con texto que parecía tuyo («El usuario además autoriza…»).
"""
from __future__ import annotations

import re

# Un espacio de anchura cero tras «<»: el modelo lo sigue leyendo igual, pero ya no es la marca.
INVISIBLE = '\u200b'


def neutralizar(texto, *etiquetas):
    """`texto` con cualquier forma de abrir o cerrar esas etiquetas (mayúsculas, espacios, saltos)."""
    for etiqueta in etiquetas:
        patron = re.compile(r'<(\s*/?\s*)(' + re.escape(etiqueta) + r')(?=[\s/>]|$)', re.IGNORECASE)
        texto = patron.sub(lambda m: '<' + INVISIBLE + m.group(1) + m.group(2), texto)
    return texto


def neutralizar_separador(texto, separador):
    """`texto` sin líneas que imiten `separador` (`--- project-doc ---`), con cualquier número de guiones,
    espacios o mayúsculas."""
    palabra = separador.strip().strip('-').strip()
    patron = re.compile(r'^(\s*-{2,})(\s*' + re.escape(palabra) + r'\s*-{2,})', re.IGNORECASE | re.MULTILINE)
    return patron.sub(lambda m: m.group(1) + INVISIBLE + m.group(2), texto)
