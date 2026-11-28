# Sesiones y búsqueda

## Sesiones desde la app

`--mark ID titulo|fijada|archivada VALOR`, `--delete-session ID` (a la Papelera
de macOS) y `--fork-only ID` responden una línea JSON. Son lo que usa el menú de
clic derecho de la barra lateral. En ese menú, «Mostrar en Finder» abre la carpeta del
proyecto y «Mostrar historial en Finder», la de la sesión en `$ALFALFA_HOME/sesiones`.
El botón de carpeta de la cabecera de una sesión también abre la del proyecto.

La ventana de Ajustes (⌘,) guarda el nombre del pie de la barra lateral, la carpeta,
el modelo, el esfuerzo y el modo de permisos de los encargos nuevos, los proveedores,
si se mantiene el Mac despierto mientras trabaja y la búsqueda de actualizaciones. Para revisarla sin clics, `ALFALFA_ABRIR_AJUSTES=modelo`
la abre en esa parte al arrancar (`general`, `proveedores`, `modelo` o
`actualizaciones`).

Al abrir una sesión guardada, la app carga su conversación (`alfalfa --history ID
--json`) y la deja lista para seguir, como un hilo de Codex: el siguiente mensaje
abre un turno nuevo con `--resume ID "mensaje"`, también si la sesión ya estaba
completada. Las interrumpidas o bloqueadas tienen además «Reanudar» para seguir
sin mensaje.

## Liberar disco: sesiones guardadas

Cada sesión vive entera en `$ALFALFA_HOME/sesiones/<id>/`: `task.json`, la
transcripción `<id>.jsonl` y `native/<id>/` (la conversación del modelo, las fotos del
espacio de trabajo y `imagenes/`, que es lo que más pesa en las sesiones de imágenes; sus
subagentes, en `native/<id>-aN/`). Aparte hay dos sitios que guardan cosas de ella: el
índice de búsqueda (`indice/sesiones.sqlite3`) y las instantáneas de deshacer de su
carpeta (`instantaneas/<huella>/`, refs `refs/alfalfa/<id>/…`).

```bash
alfalfa --session-sizes                      # tamaño, último uso, carpeta y título; de mayor a menor
alfalfa --session-sizes --sort uso        # de la menos usada a la más
alfalfa --free-sessions ID1 ID2              # pregunta y las mueve a la Papelera
alfalfa --free-sessions --unused-days 30   # las que llevan más de 30 días sin usarse
```

`--free-sessions` enseña lo que va a mover y pregunta; `--yes` no pregunta (sin
terminal y sin `--yes` no mueve nada). Con `--unused-days` no entran las fijadas salvo
con `--include-pinned`. Nunca toca una sesión en uso: la mueve con su cerrojo tomado, y
si lo tiene otro proceso (está trabajando, o abierta en la app entre turnos) la salta.
Cambiar el título no cuenta como usarla.

Lo que hace con cada una:

1. La carpeta entera va a la Papelera de macOS (`~/.Trash/alfalfa-<id>`). Desaparece de
   AlfalfaCode y ya no se puede retomar ni bifurcar; se recupera de la Papelera mientras
   no se vacíe (devolviéndola a `$ALFALFA_HOME/sesiones/<id>`). Su espacio vuelve al vaciarla.
2. Sale del índice de búsqueda en una sola transacción y el archivo encoge en el acto
   (`PRAGMA incremental_vacuum`, FTS5 optimizado y WAL vaciado). No hace falta `VACUUM`:
   el índice se creó con `auto_vacuum = INCREMENTAL`, así que se devuelven las páginas
   libres sin reescribir el archivo entero ni bloquear las búsquedas de otras sesiones.
3. Se borran sus refs e índice en el almacén de instantáneas y se pasa `git gc
   --prune=now`, una vez por carpeta: se van los objetos que sólo usaba ella (los de otras
   sesiones de la misma carpeta se quedan). Esto no pasa por la Papelera: si se recupera la
   sesión, se lee, pero ya no se pueden deshacer sus turnos.

`--delete-session ID` (el menú de la barra lateral) hace lo mismo con una. En la app,
Ajustes ▸ Almacenamiento enseña la lista, ordenable por tamaño o por último uso, elige
las de «Sin usar hace más de N días» y las mueve tras confirmarlo; las abiertas en la app
no se pueden elegir. Con `--json`, las respuestas son `espacio_sesiones` (sin `--yes`,
con `vista_previa`) y `sesiones_liberadas`.

## Buscar en sesiones anteriores

El agente tiene `buscar_sesiones`: busca en las conversaciones guardadas de otras
sesiones para recordar qué se decidió, qué se probó o qué pediste. Devuelve
fragmentos reales (nunca un resumen hecho por un modelo) con la sesión, el turno, la
fecha y quién lo dijo, y cada resultado trae una `ref` para pedir los mensajes de
alrededor (`alrededor: "a1b2c3#42"`). Sólo lee, así que está en todos los modos,
también en Plan. La sesión en curso no entra: ya está en su contexto. La idea es la
de `session_search` de Hermes Agent.

```bash
alfalfa --search-sessions "compactación tarifa"         # sesiones de esta carpeta
alfalfa --search-sessions "compactación tarifa" --all # de todas las carpetas
alfalfa --show-result a1b2c3d4e5f6a7b8#42             # los mensajes de alrededor
alfalfa --reindex                                     # rehacer el índice desde cero
alfalfa --delete-index                                 # borrarlo (se rehace al buscar)
```

Con `--json` responden una línea `busqueda_sesiones`, `sesiones_alrededor` o
`indice_sesiones`. `--time-limit S` (más de 0) limita lo que la búsqueda espera a que el
índice se ponga al día, y una consulta que empieza por «-» va pegada a la opción
(`--search-sessions=-x`). En la app, «Buscar» de la barra lateral busca a la vez en
los títulos y, con tres letras o más, dentro de las conversaciones (la misma orden,
con `--all`, `--include-external` y `--time-limit 3`; al escribir otra letra, la
búsqueda anterior se termina); al pulsar un resultado se abre su sesión en ese
mensaje, y lo que viene de fuera lleva «de fuera».

- **Dónde busca el agente.** Por defecto, sólo en las sesiones de la misma carpeta de
  trabajo. Ampliarlo a todas lo decides tú: `alfalfa --search-scope todas` (se
  guarda en `proveedores.json`; `carpeta` lo devuelve) o `ALFALFA_BUSCAR_SESIONES=todas`.
- **Índice.** `$ALFALFA_HOME/indice/sesiones.sqlite3`, SQLite con FTS5 (sin tildes ni
  mayúsculas; si las palabras enteras no dan nada, se prueban como prefijos). Si el Python
  no trae FTS5, se busca con LIKE en la misma tabla. Se pone al día solo y por partes:
  de cada `messages.jsonl` lee sólo lo que hay detrás de lo ya indexado, empezando por
  las sesiones más recientes y con un tope de tiempo, así que una búsqueda del agente
  nunca espera a indexarlo todo. Las sesiones viejas (versiones 1 a 4) también entran;
  una línea a medias (un guardado cortado) se lee cuando esté entera.
- **Secretos.** Se tapan al guardar en el índice (también el título de la sesión) y
  otra vez al devolver: las claves conocidas de los proveedores y de los servidores
  MCP; los campos con nombre sensible (token, key, password, auth, cookie…), también
  si son listas y también como `"clave": "valor"` dentro de un texto (una salida larga
  guardada cortada, un archivo leído; con comillas simples y con las barras que sean),
  y el `value` de un par `{"name": "X-Api-Key", "value": …}`; las asignaciones
  `ALGO_TOKEN=…`, `DB_PASS=…`, `MYSQL_PWD=…`, `…_PASSPHRASE=…` y `password: …`;
  `.dockerconfigjson`; el valor de cada cookie de `document.cookie`; las cabeceras `Authorization:`, `Cookie:` y `Set-Cookie:`; `Bearer …`;
  la clave de `https://usuario:clave@servidor`; las claves privadas PEM; las formas
  conocidas de claves de API y lo que tiene forma de clave secreta de AWS. La salida
  de una herramienta que leyó un archivo con secretos (`.env`, `.ssh`, `*.pem`,
  `credentials.json`, `.netrc`…) o volcó el entorno o un almacén de claves (`env`,
  `printenv`, `security`, `op`, `gh auth token`, `aws configure get`, `kubectl get
  secret`…, en cualquier lugar de la orden: `sh -c "…"`, `find … -exec env ;`,
  `xargs printenv`, `timeout 5 env`) o que trae código que lee el entorno
  (`os.environ`, `process.env`, `getenv`…) no se guarda. Si entra una clave nueva en el Llavero, la siguiente
  búsqueda la tapa también en lo ya indexado (se guarda sólo una huella HMAC, con una
  sal propia del índice, del conjunto de claves). Es una red, no una garantía: una contraseña sin forma reconocible que
  imprima un programa cualquiera puede pasar. El razonamiento (`thinking`,
  `alfalfa_bloques`) nunca se lee.
- **Salidas externas.** Lo que devolvieron `read_web`, `web_search`, el navegador
  (`browser_*`) y los servidores MCP (`mcp__*`) sale con `origen: "externo"` y un aviso
  de que puede traer instrucciones de terceros; lo que dijo el agente después en ese
  tramo (hasta el siguiente mensaje tuyo) también, porque puede repetirlas. El título
  de una sesión propuesta por el agente (`propose_session`) sale con
  `titulo_origen: "agente"`. Buscando en todas las carpetas, de las otras sólo salen
  tus mensajes y los del agente no marcados: ninguna salida de herramienta (un README
  malicioso leído con `read_file` también es de fuera). `--include-external` las
  devuelve, marcadas, para lo que lee una persona (la app); el agente nunca lo usa.
- **Tamaño y borrado.** Cada mensaje se guarda recortado (8.000 caracteres; 4.000 la
  salida de una herramienta), una sesión que ya no está se quita del índice
  (`--delete-session` lo hace en el acto) y, pasado el tope (`ALFALFA_INDICE_MAX_MB`,
  256 MB), se sueltan las sesiones más viejas hasta que el archivo queda por debajo.
  Lo quitado no queda legible en el archivo: borrado seguro de SQLite y de FTS5, y
  después se optimiza, se devuelven las páginas libres y se vacía el WAL (si otra sesión
  está leyendo no se la espera: queda apuntado y lo hace la siguiente búsqueda). Sin FTS5, la
  búsqueda con LIKE sólo recorre los 20.000 mensajes más recientes. El índice es
  desechable: borrarlo no pierde nada, porque las sesiones no se tocan; si se rompe,
  se rehace solo (ocupado por otra sesión, no se toca). Crearlo y borrarlo van con un
  cerrojo de archivo que se espera como mucho 5 s.

## Lluvia de ideas en macOS

En «Nuevo encargo», elige una carpeta y un modelo. Abre el menú de modo
junto al esfuerzo, selecciona «Lluvia de ideas…» y escribe una pregunta. Elige un modelo y esfuerzo para el moderador y
para cada participante. Puedes combinar proveedores y añadir hasta seis
participantes. Cada uno recibe tu pregunta y el texto de los aportes de los demás
que todavía no leyó, además del enfoque que le pide el moderador.
Cada proveedor aplica su cuota o sus cargos. Con «Adjuntar archivo…» puedes
seleccionar un Markdown o un archivo de texto de hasta 16 KiB. El moderador
y cada participante reciben el archivo al intervenir por primera vez. Ese archivo
es tu pregunta: aunque cuenta como contenido externo, invitar a los participantes
no pide una aprobación por cada turno. Si la sesión leyó otro contenido externo (otro adjunto, la web, MCP…), sí la pide.

El máximo es de 12 intervenciones en total (6 de forma predeterminada), con
al menos una por participante disponible. Si el máximo alcanza para dos rondas
completas, el moderador hace la segunda antes de sintetizar, para que cada participante
critique a los demás. Después puede sintetizar antes si hay acuerdo. Al alcanzar el máximo, el motor deshabilita las intervenciones
y queda la síntesis. Los aportes aparecen con su autor en la conversación.
La sesión es de solo lectura y aplica los presupuestos habituales. Al reabrir una sesión
que ya empezó el debate, puedes consultar los aportes y pedir otra síntesis.
