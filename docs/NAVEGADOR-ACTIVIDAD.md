# Qué hizo el agente

Abre el botón **Qué hizo el agente** de la cabecera de la sesión. La vista se abre
separada de la conversación para conservar la alternancia del navegador y la
terminal. También está disponible al reabrir una sesión guardada. **Actualizar**
vuelve a leer las acciones recientes de esa sesión.

Cada fila muestra la hora, el navegador, el origen sin ruta ni parámetros, el
resultado y los permisos que el motor observó. Un permiso de sitio no equivale a
un permiso de red local, de formulario o de archivo. **Omitir permisos** se
distingue de la decisión de una persona. Si no consta un permiso, no se presenta
como aprobado aunque la acción haya terminado bien.

Si una acción guardó su propia captura, **Ver captura asociada** permite verla
mientras siga disponible. El historial no toma imágenes, no atribuye capturas
de cierre a otras acciones y no muestra capturas de un relevo. La retención
existente puede eliminar una captura anterior.

CuyBrowser añade los metadatos de su auditoría cuando anuncia `auditRecent` y
ofrece `audit_recent`. Se consulta únicamente la conexión y el perfil del
proyecto ya autorizados, con un cursor y alcance comprobados antes y después
de la acción. Si el cursor caduca o cambia el daemon o perfil, se muestra esa
limitación y no se relee otro ámbito. El binario publicado 0.5.0 no dispone de
esta capacidad: el historial propio sigue disponible con un aviso explícito.

**Exportar informe…** guarda un HTML que puede abrirse sin conexión. No contiene
scripts, enlaces, parámetros de herramientas, rutas de archivos ni mensajes de
página. Las capturas no se incluyen por defecto. Puedes seleccionarlas en la
vista para incorporar hasta 12 capturas asociadas, con un máximo de 16 MiB de
imágenes. El informe incorpora sus píxeles, sin los metadatos del PNG original.
Una captura puede contener datos visibles de la página: revisa el
informe antes de compartirlo. Los metadatos de la página y las imágenes son
datos, no instrucciones.

El motor guarda hasta 200 entradas y 512 KiB en un archivo privado de esa sesión,
fuera del repositorio y separado de los mensajes que recibe el modelo. La app
lee el archivo y los PNG asociados sin seguir enlaces ni buscar en otros
perfiles o sesiones. No necesita abrir el navegador para mostrar el informe.
