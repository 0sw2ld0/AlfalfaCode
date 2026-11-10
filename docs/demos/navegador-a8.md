# Demo de A8: permisos por sitio y proyecto

Demo nativa propia compilada en depuración con las fuentes reales de AlfalfaKit,
BrowserPane, BrowserSitePermissionButtons y BrowserSiteSettings. El componente
recibe explícitamente el proyecto y su modelo de Ajustes; el host sustituye sólo
el contenedor de la app para no abrir conversaciones ni proveedores.

El motor de prueba usa Navegador y Transport reales sin inferencia; la edición de
reglas pasa por `alfalfa --navegador-sitios --stdio`, con propuesta, diff y respuesta
de permiso correlacionada. WKWebView usa un almacén no persistente propio. HOME,
CFFIXED_USER_HOME, ALFALFA_HOME y TMPDIR apuntan a carpetas ficticias. Las páginas
están en 127.0.0.1:18484 y :18485; no se usa el puerto 8765 ni un navegador personal.

[Resultado: 37 de 37 comprobaciones](navegador-a8/resultado.json):

- Una vez, sesión y siempre en este proyecto pasan por el canal real del motor.
  La visita se consume al salir y volver pregunta de nuevo; el permiso de sesión
  desaparece al reiniciar el motor, mientras el del proyecto permanece.
- La red local y el JavaScript que modifica conservan sus permisos separados.
- Ajustes muestra el antes y después y guarda sólo tras confirmar. Un bloqueado
  no recibe peticiones.
- Una espera con texto ausente, interrumpida por navegación A→B, autoriza el sitio
  y devuelve un error explícito; no anuncia que el texto apareció.
- Con modo estricto, fetch hacia B no contacta con B aunque su acceso de red local
  ya esté autorizado. El permiso de sitio de su sesión anterior ha caducado.

Capturas exclusivamente del contentView del helper propio:

![Los cuatro alcances del permiso por origen](navegador-a8/permisos.png)

![Listas y modo estricto del proyecto](navegador-a8/ajustes.png)

Los diez focales WK adicionales usan servidores propios y comprueban cero
contactos antes del permiso, leases consumidos que no reviven con un snapshot
viejo, rechazo de POST 307 sin reenviar el formulario ni su cuerpo, token/documento
vinculados y que ninguna excepción levanta la barrera local. GET y HEAD pueden
reanudarse. Los demás métodos requieren un intento nuevo explícito.

Validación del árbol final: Python 3.11 y 3.13, 2327 pruebas y 2 omitidas en cada uno;
ruff global limpio; Swift: 298 pruebas; SwiftLint: 205 avisos frente a 206 de la base,
sin ampliar la lista de excepciones.

El ejecutable, sus dos servidores y sus puertos terminaron al acabar. El hogar
ficticio no contiene una carpeta de conversaciones. La evidencia completa y el
manifiesto de fuentes quedan en `fase2-evidencia-alfalfa/a8` del workspace de ensayo.
No se fusionó ni publicó nada como parte de esta demo.
