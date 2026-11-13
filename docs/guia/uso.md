# Uso diario

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud "corrige la prueba que falla"
alfalfa --sesiones
alfalfa --reanudar ID "sigue"
alfalfa --bifurcar ID --turno T "prueba otra solución"
alfalfa --volver ID --turno T          # conversación y código a antes de ese mensaje
alfalfa --rehacer ID                   # devuelve el código que quitó el último deshacer
alfalfa --comprobar-orden git push
```

## Opciones de cada mensaje

Bajo cada mensaje tuyo la app muestra hace cuánto lo enviaste y tres botones, como
en Claude: copiarlo; **volver** a él; y **bifurcar**, que abre una sesión nueva con
la conversación hasta ese mensaje y su respuesta (`--solo-bifurcar ID --turno T`; los
archivos no cambian).

## Órdenes con «/»

Como en Codex, escribir «/» en el cuadro de la app abre un menú de órdenes que se
filtra por prefijo: ↑↓ para moverse, ↩ para ejecutar, ⇥ para completar y Esc para
cerrar. Las que no valen en ese momento salen atenuadas, por ejemplo `/revisar`
con un turno en marcha. También aceptan su nombre en inglés (`/model`, `/compact`,
`/init`…).

`/nuevo`, `/modelo`, `/esfuerzo`, `/compactar`, `/contexto`, `/estado`, `/revisar`,
`/inicializar` (crea un AGENTS.md, como `/init` de Codex), `/renombrar`, `/fijar`,
`/bifurcar`, `/archivar`, `/copiar`, `/exportar` (la conversación en Markdown),
`/aprender` (ver [Habilidades y memoria](habilidades.md)) y `/detener`. Lo que empieza por «/» y no es una orden, como una ruta, se envía como
mensaje normal. Las órdenes del proyecto de OpenCode y Claude Code también salen en el
menú (ver [Venir de OpenCode, Codex o Claude Code](migrar.md)).

## Cargar un chat de Ollama

Como `/import` de Codex, que trae chats de Claude Code: en la app, «Cargar chat de
Ollama» (en Nuevo encargo) lista los chats de la app de Ollama y copia el elegido
en una sesión nueva de la carpeta actual. Sus mensajes de usuario y asistente
quedan como contexto, y el siguiente mensaje la continúa. Desde la terminal:

```bash
alfalfa --chats-ollama
alfalfa --importar-ollama ID_DEL_CHAT
alfalfa --reanudar SESION "sigue desde aquí"
```

La base de Ollama (`~/Library/Application Support/Ollama/db.sqlite`) sólo se lee:
conexión de solo lectura, `query_only` y una transacción que se deshace al final.
La lista sólo enseña títulos y recuentos. No se traen imágenes adjuntas,
herramientas ni respuestas a medio escribir.
