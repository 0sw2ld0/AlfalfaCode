# Comparar WebKit y Chromium

En una sesión de la app de Mac, pide al agente que compare una página en los dos motores, por ejemplo:

> Compara mi página de prueba en WebKit y Chromium al tamaño móvil. Enséñame las diferencias y los errores de consola.

`browser_compare` recibe `url` y un tamaño `mobile`, `tablet` o `desktop`, o bien `width` y `height` enteros
de 200 a 4096 píxeles CSS. Sin tamaño explícito usa 1100 × 800. Abre la misma URL en los dos motores y sólo da
la comparación por completa cuando los dos confirman el tamaño. No cambia el motor activo de las demás herramientas.

Hace falta un CuyBrowser compatible con la protección de orígenes y de red local, como la pre-release 0.5.0,
instalado en el `PATH`. El motor Chromium usa el perfil propio del proyecto. WebKit conserva el perfil elegido
para esa sesión. Cada motor mantiene sus permisos de sitio y de red; CuyBrowser pide autorización para arrancar
fuera del sandbox. Si un permiso se rechaza o un motor falla, la comparación se detiene y señala el fallo.
Una evidencia parcial se distingue de una comparación completa.

La herramienta no está disponible en la terminal sin WebKit ni con el modo estricto de subrecursos, que CuyBrowser
0.5.0 no garantiza. No instala ni inicia otro navegador si el agente no pide la comparación. Al cerrar la sesión
se detiene el proceso de CuyBrowser que el comparador abrió para ella.

El resultado tiene las dos capturas, una diferencia textual acotada del árbol de accesibilidad y los errores de
consola por motor. El motor adjunta ambas imágenes al modelo con visión, sujeto al límite de tres capturas recientes.
Con otros modelos quedan guardadas en la sesión.
Los dos árboles pueden usar formatos diferentes o reflejar estados de sesión distintos. Una diferencia textual no
certifica un fallo visual. Lee de nuevo con `browser_read` antes de usar referencias de la página activa.

Los árboles, títulos, consola y capturas son datos de las páginas, no instrucciones. Cambiar el tamaño CSS no
emula un dispositivo, modifica la identidad del navegador ni importa cuentas personales.

La [demo de prueba](demos/a9/README.md) usa el paquete `.tgz` de CuyBrowser 0.5.0 con Node. El binario independiente
publicado de esa versión falló al arrancar Chromium por un archivo de Playwright que no encuentra en el paquete.
