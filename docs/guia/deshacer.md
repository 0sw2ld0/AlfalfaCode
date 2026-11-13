# Volver a un mensaje y deshacer el código

Antes y después de cada turno, AlfalfaCode guarda una instantánea del contenido de la
carpeta de trabajo en un git aparte, fuera del proyecto:
`$ALFALFA_HOME/instantaneas/<huella de la carpeta>` (`--git-dir` ahí, `--work-tree`
en la carpeta). El `.git` del proyecto no se toca y funciona igual en una carpeta que
no es repositorio. Como guarda la carpeta entera, cubre también lo que cambian las
órdenes (`run_command`), no sólo las ediciones. Guarda los bytes tal cual (sin los
`text`, `eol` ni `working-tree-encoding` de los `.gitattributes`). Respeta los
`.gitignore`, el `.git/info/exclude` del proyecto y el ignore global de git; salta las
cachés que ya conocen los permisos (`__pycache__`, `.pytest_cache`…), las carpetas
`.tmp-…/`, `node_modules/` y los entornos de Python, los repositorios anidados, los
archivos que no se pueden leer y los de más de 5 MB, y apunta todo lo que deja fuera.
Las instantáneas de más de 30 días se borran, y también las más viejas si el almacén
pasa de 2 GB (nunca las de los dos últimos días ni las de deshacer y rehacer).
`ALFALFA_INSTANTANEAS=0` las apaga; un fallo al guardar una nunca para el turno.

En un repositorio git no copia lo que el proyecto ya tiene: el almacén lee sus objetos
(`objects/info/alternates` al almacén común, también desde un worktree) y la primera
instantánea parte del árbol de HEAD, así que sólo escribe lo cambiado y lo nuevo. Se
toma mientras el modelo ya responde: sólo la esperan las herramientas que pueden
cambiar archivos (editar, órdenes, parches, subagentes, MCP…). Si tarda, la app y la
terminal dicen «Preparando deshacer…» con el tamaño. Si no termina en 90 s, avisa una
vez de que deshacer no está disponible en esa carpeta y no lo reintenta hasta el día
siguiente. Si el proyecto reescribe su historia y un `git gc` borra objetos que una
instantánea tomaba prestados, esos archivos no se restauran (salen como «perdido»).

Al pulsar **volver** la app pregunta qué vuelve a antes del mensaje:

- **Conversación y código**: la conversación sigue en una copia justo antes del
  mensaje, con su texto en el cuadro, y los archivos vuelven a como estaban.
- **Sólo conversación**: como hasta ahora; los archivos no cambian.
- **Sólo código**: los archivos vuelven y la conversación sigue igual.

Antes de tocar nada enseña qué archivos vuelven y cuáles no:

- Sólo se restaura sin preguntar lo que el agente apuntó en la evidencia durante esos
  turnos y sigue como lo dejó. Lo que cambió sin huella durante un turno con órdenes o
  subagentes viene marcado como «probablemente una orden», para que lo confirmes. Lo
  que cambiaste después del turno, lo que otro cambió durante un turno sin órdenes y
  lo que el agente no tocó se queda como está, salvo que lo marques uno por uno.
- Nunca borra algo que ya existía al empezar el turno sólo porque las instantáneas no
  lo guardaban (un `.env` ignorado, un archivo grande, un repositorio anidado): se
  queda y se avisa.
- Restaurar no sigue enlaces ni escribe fuera de la carpeta, conserva los permisos y
  atributos de los archivos que reescribe (sólo cambia su bit de ejecución) y sólo
  quita carpetas vacías que creó el turno.
- Dice con honestidad lo que no se deshace, leyendo las órdenes de esos turnos con el
  clasificador de los permisos: `git push` y otras publicaciones, commits y ramas
  (el `.git` no entra en las instantáneas), instalaciones, archivos fuera de la
  carpeta, llamadas de red, cambios del sistema, herramientas MCP o del navegador,
  las órdenes de los subagentes y los archivos que cambió el agente fuera de lo que
  guardan las instantáneas.

Al deshacer se guarda el estado que se quita: la conversación muestra una marca
«Código deshecho» con **Rehacer**, que lo devuelve con la misma regla (lo que
cambiaste después de deshacer no se pisa). Los archivos restaurados reciben una
huella nueva en la evidencia (`changes`, `via: "deshacer"`) y la revisión del espacio
sube, así que ninguna comprobación anterior sigue valiendo.

Desde la terminal:

```bash
alfalfa --volver ID --turno T --vista-previa        # qué haría, sin tocar nada
alfalfa --volver ID --turno T                       # conversación y código
alfalfa --volver ID --turno T --alcance codigo      # sólo el código, en la misma sesión
alfalfa --volver ID --turno T --alcance conversacion
alfalfa --rehacer ID [--deshecho D]
```

En una terminal interactiva pregunta archivo por archivo por los que cambiaste
después; si no, `--forzar RUTA` (se repite) los restaura igualmente. Con `--json`
responde con líneas del protocolo de `--stdio`: `deshacer_vista` y `rehacer_vista`
(vista previa), `deshecho` y, si la conversación sigue en otra sesión,
`sesion_cambiada`. El historial (`--historial ID --json`) trae la marca como el campo
`deshacer` de su evento.
