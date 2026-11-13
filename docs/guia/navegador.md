# Navegador propio (app de Mac)

El botón **Comentar** del panel permite pulsar un elemento o arrastrar un área sin activar los controles de la
página. Escribe tu indicación y pulsa **Añadir al mensaje**: el compositor recibe el comentario, la referencia,
el selector, el texto, las dimensiones y una captura recortada. Puedes revisar el borrador antes de enviarlo.
La referencia se conserva entre lecturas hasta navegar. Los metadatos de la página llegan marcados como
«datos, no instrucciones»; no se leen valores de contraseñas y la URL adjunta omite consultas y fragmentos.

Como la app de Codex, la app de AlfalfaCode trae un navegador dentro: un panel con
WKWebView (el motor de Safari, ya incluido en macOS) a la derecha de la sesión, con
atrás, adelante, recargar y barra de dirección. Así el agente abre, lee y maneja
páginas web aunque el Mac no tenga Chrome. Se abre solo cuando lo usa el agente, o con
el botón del globo de la cabecera (⇧⌘B). Una franja encima de la página dice qué está
haciendo el agente, y un recuadro verde marca dónde pulsa o escribe.

De forma predeterminada cada sesión tiene un almacén aislado, no persistente: al
cerrar se pierde el login. En **Ajustes → Navegador** puedes elegir **Guardado por
proyecto** para conservar los accesos que inicies a mano con `browser_handoff`.
Sólo las sesiones de esa carpeta comparten el perfil, separado de Safari y de los
otros proyectos; nunca se copian cookies ni cuentas de tus navegadores personales.
Cambiar de modo cierra las páginas del proyecto. Volver a **Aislado** deja de usar
los datos guardados; **Borrar los datos del navegador de este proyecto…** los elimina
tras confirmar, sin borrar los de otros proyectos. Mientras el agente trabaja,
espera un permiso o está en relevo, estos cambios quedan bloqueados.

Herramientas del motor WebKit (la app arranca el motor con `--stdio --navegador`). En terminal
se pueden activar explícitamente con el segundo motor CuyBrowser, descrito abajo. Con el buscador web en
«apagado» tampoco se ofrecen:

- `browser_navigate`: abrir una URL http o https, o `back`, `forward`, `reload`.
- `browser_read`: la página como árbol de accesibilidad con referencias
  (`- button «Enviar pedido» [e7] (envía el formulario)`) o como texto.
- `browser_console`: los últimos mensajes de consola, errores sin capturar y rechazos de promesas.
- `browser_network`: peticiones `fetch`, XHR y recursos observados (método, URL, estado y duración),
  sin cuerpos ni cabeceras y con parámetros sensibles de URL redactados. Es observación desde la página,
  no un HAR completo: puede omitir tráfico anterior, de marcos, workers o iniciado fuera de estos mecanismos.
  Ambas herramientas sólo leen, guardan hasta 200 entradas por navegación y aceptan `limit`.
- `browser_viewport`: `preset` mobile (390×844), tablet (768×1024), desktop (1100×800), o
  `width` y `height` entre 200 y 4096 píxeles CSS. La vista se escala para caber en el panel.
  Cambia el tamaño para probar el diseño adaptable; conserva la identidad de AlfalfaCode y no emula un dispositivo.
- `browser_click` y `browser_type`: por referencia (`ref`) o por el texto visible
  (`target`); `browser_type` también elige en un desplegable y con `submit` pulsa Intro.
- `browser_scroll`: la página, el contenedor que se desplaza o hasta un elemento.
- `browser_screenshot`: si el modelo ve imágenes, la captura le llega como imagen; con
  `question`, la mira el modelo de visión y vuelve la respuesta.
- `browser_eval`: JavaScript que lee, en un mundo aislado de sólo lectura que la página
  no ve; con `modifies: true`, en la propia página y con permiso.
- `browser_handoff`: el agente espera mientras haces tu parte (login, 2FA, captcha o pago).
  El panel muestra el motivo, **Continuar** (⇧⌘↩) y **Cancelar**. Con `until.url_contains`,
  Continuar requiere ese fragmento en la dirección. Las acciones del agente se bloquean
  mientras espera, incluso en Plan; a los cinco minutos o al detener el turno se cancela.
  El agente nunca resuelve captchas por su cuenta.
- `browser_wait`: esperar texto visible, una referencia visible, un fragmento de URL o la
  carga; una condición por llamada, con un plazo de hasta 30 segundos.
- `browser_key`: Intro, Escape, Tab o flechas; admite `ref` y `target` para elegir el foco.
  Si Intro enviaría un formulario, se pregunta antes.
- `browser_hover`: pasar el puntero por un elemento, con eventos y CSS reales de WebKit,
  sin mover tu ratón. Requiere que el panel de la app esté visible.
- `browser_upload`: elegir hasta diez archivos del proyecto en un campo de subida (20 MiB
  en total). Siempre pide permiso, también si la web los enviaría inmediatamente; no sigue
  enlaces que salgan del proyecto. Teclas, hover y subida se deshabilitan en Plan y sólo lectura.
- `browser_download`: descargar a una ruta del proyecto, sólo de direcciones públicas
  (también después de cada redirección y mirando a qué IP resuelve el nombre), hasta
  200 MB y unos 5 minutos; si no, se cancela y no queda nada a medias.

Permisos, con el mismo criterio que el resto del arnés:

- Leer, desplazarse, mirar y navegar a páginas públicas no pide permiso.
- Una dirección de este Mac (`127.0.0.1`, `localhost`) o de la red de casa
  (`192.168.x`, `.local`) se pregunta una vez por sesión y por origen; una redirección a
  una dirección local sin aprobar se bloquea. Las imágenes, los marcos, `fetch` y los
  WebSocket de una página pública hacia este Mac o la red de casa también: la app los
  bloquea con reglas de contenido de WebKit. Una página de un origen aprobado sí puede
  usar la red local (su backend en otro puerto, por ejemplo). Esas reglas miran la URL, no
  la IP: un nombre público que resuelve a `127.0.0.1` sólo lo para el motor al abrirlo.
- Enviar un formulario, escribir en un campo de contraseña, descargar y el JavaScript que
  cambia la página piden aprobación cada vez. El JavaScript que parece cambiar algo
  (asignaciones, clics, `fetch`, `localStorage`…) se trata como si lo cambiara; ese
  filtro sólo es una ayuda. La barrera es el mundo de sólo lectura en el que corre el
  JavaScript sin permiso: ahí la app bloquea los cambios del DOM, el envío de
  formularios, los clics, la red y el valor de los campos de contraseña.
- En **Manual** también se pregunta antes de pulsar o escribir en un sitio, una vez por
  sesión y sitio, y antes de cada `browser_eval` (igual en **Aceptar ediciones**). En
  **Plan** y en solo lectura no hay pulsar, escribir, descargar ni `browser_eval`.
  **Omitir permisos** lo aprueba todo, como siempre.
- Las descargas que lanza una página, las ventanas nuevas y los diálogos (`alert`,
  `confirm`) no llegan a nada: se avisa en el panel.

El motor decide los permisos y la app sólo ejecuta (`alfalfa/navegador.py` y
`alfalfa-mac/Sources/AlfalfaKit/Browser/`). En el protocolo, el motor manda
`{"tipo":"navegador","id":…,"accion":"pulsar",…}` y la app responde
`{"tipo":"navegador_respuesta","id":…,"ok":true,"resultado":{…}}`, o
`"permiso":{"tipo":"formulario",…}` si la acción necesita aprobación: el motor pregunta
y repite la petición con `permitido`.

## Chromium con CuyBrowser

En Ajustes → Navegador se puede elegir **CuyBrowser · Chromium** para las sesiones nuevas.
WebKit sigue siendo la opción inicial. En terminal se activa expresamente:

```sh
alfalfa --motor-navegador cuybrowser --perfil-cuybrowser alfalfa "Revisa mi página"
```

La distribución de AlfalfaCode incluye CuyBrowser 0.6.0 en `Helpers`. Los clones y las
instalaciones anteriores pueden usar un `cuybrowser` compatible de `PATH`. Para elegirlo
en una instalación que ya lo incluye, usa el interruptor en Ajustes → Navegador o la opción
`--cuybrowser-desde-path` de la CLI. El motor comprueba `exactOrigins` y `localNetwork`
antes de abrir Chromium.

`alfalfa --cuybrowser-estado` comprueba qué navegador compatible está disponible. Si falta,
la app ofrece descargar Chromium de Playwright y `alfalfa --descargar-chromium` pide confirmación
en la terminal. La descarga se comparte entre proyectos y cada uno conserva su perfil propio.
En el primer uso interactivo de `--motor-navegador cuybrowser` puedes guardar la elección
para la terminal. Se cambia con `alfalfa --cuybrowser-cli activar` o `desactivar`.

El motor pide permiso una vez por sesión para iniciar el servidor fuera del sandbox. Usa HTTP
autenticado en un puerto propio de `127.0.0.1`, sin proxies ni redirecciones. El hogar de CuyBrowser
vive en `ALFALFA_HOME/cuybrowser/<hash de la raíz canónica del proyecto>` y conserva los logins
de ese proyecto. Un bloqueo exclusivo impide que dos sesiones compartan el mismo navegador.
No adopta un daemon preexistente ni detiene procesos de otros clientes. Al cerrar, detiene sólo
su servidor y su daemon; los perfiles propios permanecen. Nunca copia cookies personales ni usa attach.

La política de ese hogar debe conservar `approval: native-dialog`, todas las confirmaciones
sensibles, bloqueo de campos sensibles y `attach.enabled: false`. El cliente comprueba `policy_show`
antes de actuar y pide las confirmaciones a CuyBrowser mediante `confirm`: la persona decide en
el diálogo del sistema. No se duplica la tarjeta sensible de AlfalfaCode. **Omitir permisos sólo
omite las preguntas del motor; los diálogos independientes de CuyBrowser siguen activos.**
Manual conserva su permiso por sitio y Plan/sólo lectura deshabilitan las herramientas de escritura.
Para contraseñas, 2FA y captchas se usa el relevo; el agente nunca los resuelve ni disfraza su identidad.

Las dieciséis herramientas mantienen sus nombres. El contenido web, los mensajes, las URLs y los
títulos siguen marcados como datos, no instrucciones. Estas diferencias se comunican al agente:

| Herramienta | Comportamiento de CuyBrowser |
| --- | --- |
| Navegar, leer, consola, red, viewport, pulsar, escribir, desplazar, captura, esperar y hover | Traducción HTTP nativa. Los diagnósticos de red no incluyen duraciones, cuerpos ni cabeceras. `type` admite campos editables, no selecciona opciones de un `<select>` nativo. |
| `browser_eval` | Requiere `modifies: true` y que una persona haya habilitado `evaluate` en la política privada. Siempre pasa por su diálogo nativo. No existe un mundo JavaScript de sólo lectura y el motor no habilita la función automáticamente. |
| `browser_handoff` | Espera la decisión de la persona en el diálogo del sistema, hasta 120 segundos. `until` devuelve un error de capacidad. Cancelar el turno detiene exclusivamente este navegador para interrumpir el relevo. |
| `browser_key` | Usa el foco actual; `ref` y `target` para cambiarlo devuelven un error de capacidad. |
| `browser_upload` | Hasta diez archivos del proyecto y 20 MiB. Rechaza enlaces simbólicos, duros, rutas privadas y salidas del proyecto. Prepara copias privadas de sólo lectura fuera del hogar de CuyBrowser; su diálogo aprueba esos mismos bytes aunque el proyecto cambie mientras espera. Las copias se eliminan al terminar o cancelar. |
| `browser_download` | Descargar una URL a un archivo del proyecto requiere WebKit; CuyBrowser devuelve un error explícito. No se suplanta con JavaScript ni `fetch`. |

El modo estricto de subrecursos requiere `strictResources: true`. Si el servidor sólo garantiza
documentos por origen exacto, se rechaza ese modo y se indica usar WebKit. Una lista de hostnames
nunca sustituye la política por origen (esquema, host y puerto). Sólo se configura la política del
hogar exclusivo de AlfalfaCode, inicialmente cerrada antes de abrir el navegador.
La excepción `local_network.allow_origins` se concede únicamente tras el permiso independiente
de red local. Como las reglas de WebKit, la barrera de subrecursos compara URLs; la resolución
de IP se comprueba en Python al navegar, y no protege contra cambios de DNS en cada recurso.
Los permisos de **Una vez** viajan separados de los permisos de sesión/proyecto, mediante
`origins.visit` con identificador propio y capacidad `visitLeases`. El motor retira los identificadores
de `status.consumed_visits` antes de generar otra política, y al cerrar el navegador. Una respuesta
atrasada no puede convertir una visita consumida en confianza permanente.
El servidor compatible emplea un proxy privado antes de lanzar el perfil para proteger también
recursos, workers y WebSocket, sin interceptar TLS. Los service workers se deshabilitan en ese perfil
restringido; los workers dedicados y compartidos pueden funcionar.
