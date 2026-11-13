# Contexto y compactación

## Modelo, esfuerzo y contexto

El modelo y el esfuerzo se pueden cambiar en una sesión ya empezada, como `/model`
en Codex: la app manda `{"tipo":"ajustes","modelo":…,"esfuerzo":…}` por `--stdio`
y el cambio vale desde el siguiente turno. Cada `contexto` trae, además del total
que mide Ollama, un `desglose` estimado (mensajes, instrucciones, herramientas,
MCP y, si los hay, archivos de memoria, instrucciones MCP y complementos), el uso
`estimado` con lo añadido desde la última medida, el `umbral` de la compactación
automática y la lista de archivos de `memoria` (`AGENTS.md`). Con servidores MCP
también trae las herramientas cargadas con su peso y cuántas siguen diferidas. La
última medición se guarda con la sesión, y el panel la enseña al abrirla, como el
de Claude Code: % usado, cada parte con sus tokens, lo añadido desde la última
medida, la reserva de compactación, el espacio libre y cuánto falta para compactar.

## Compactar

La compactación automática salta al 90 % de la ventana (o en `--compactar-en`).
Además se puede compactar a mano en cualquier momento, como `/compact` de Claude
Code: el botón **Compactar sesión** del panel o `/compactar`, con instrucciones
detrás si quieres centrar el resumen (`/compactar conserva las rutas y los
errores`). Entre turnos se compacta al momento sin abrir turno; con un turno en
marcha, antes de la siguiente respuesta del modelo. Por `--stdio` es
`{"tipo":"compactar","instrucciones":"…"}`. Una sesión guardada se abre sin turno
con `--reanudar ID --esperar` para compactarla.

La conversación enseña la marca «Conversación compactada · 120k → 9k» con el
resumen que ahora lee el modelo, plegado; el historial completo sigue guardado.
El evento es un `evento` de sistema con `compactacion`
(`estado` `en_curso`/`hecha`/`fallida`, `motivo`, `antes`, `despues`, `resumen`).
Cerca del umbral, el pie del cuadro avisa de cuánto falta («12 % hasta compactar»).
`compactacion` lleva también `composicion`: cuántos tokens había por tipo de
mensaje (`tool.content`, `assistant.tool_calls`…), sin su texto.

## Modelos que cobran más por contexto largo

Algunos modelos cobran más toda la petición pasado un umbral: GPT-6 Luna, por
encima de 272K tokens de entrada, 2× la entrada y la caché y 1,5× la salida (su
ventana es de 1.050.000); Claude Haiku 5.5, si el prompt pasa de 100.000 tokens
contando la caché, 5× (ventana de 1M). El umbral sale de los tramos de precio de
models.dev (`tiers`, o `context_over_200k` en el formato antiguo), de una tabla
propia comprobada en las páginas oficiales, o de lo que diga el proveedor
(Alfalfa Cloud lo manda en `/api/show` como `alfalfa_tarifa`). `--modelos --json`
lo da junto a la ventana (`barato_hasta`, y `tarifa_larga` con los multiplicadores),
y el selector lo enseña: «1M · barato hasta 100K».

Cada sesión elige cómo usarlo, y se guarda con ella:

- **Ahorro** (por defecto): la compactación automática salta también al 90 % del
  umbral. Al 80 % la conversación avisa («A partir de 100K tokens, Claude Haiku 5.5
  cobra 5× toda la petición. Compactaré en unos 10K») con tres botones: **Compactar
  ahora**, **Seguir con contexto completo** (no vuelve a preguntar en esa sesión) y
  **Seguir en una sesión nueva**, que compacta y abre otra que empieza sólo con el
  resumen (con un turno en marcha, espera a que termine; hereda los cambios del
  agente pero no las incidencias, que señalan una conversación que ya no ve). Si una
  salida de herramienta enorme llevaría una sola petición por encima del umbral, se
  recorta antes de enviarla (por el medio, como las salidas viejas), empezando por
  las que aún no están en la caché del proveedor, de la más nueva a la más vieja;
  se guarda sólo hasta cuántos tokens se recortó, para que las peticiones siguientes
  sean idénticas y la caché siga valiendo, y el historial completo no cambia. Si tras
  compactar lo fijo (instrucciones, herramientas y resumen) sigue por encima del
  umbral, lo dice una vez y no vuelve a compactar sola hasta que la conversación crezca.
  Los agentes que usan el motor sin la compactación de Codex también compactan antes
  del umbral, y los subagentes siguen el modo de la sesión que los lanza.
- **Contexto completo**: usa la ventana entera y avisa una vez al pasar el umbral.

Se cambia con `/contexto ahorro` o `/contexto completo`, en el panel de contexto,
con `--contexto ahorro|completo` en la terminal, o con
`{"tipo":"contexto","modo":"completo"}` por stdio (vale ya, también a mitad de
turno). El evento `sesion` dice el modo (`contexto`), el `contexto` lleva `tarifa`
(`desde`, `entrada`, `salida`, `modo`) y el aviso es un `evento` de sistema con
`tarifa` (también en `--historial`, donde la app lo pinta ya contestado). El coste
estimado cobra el tramo aunque models.dev no lo traiga (GPT-6 Luna). «Seguir en una sesión nueva» es `{"tipo":"continuar_en_nueva"}`: el
motor responde `sesion_cambiada` con `continuada_de` y se cierra. Con un código de
Alfalfa Cloud el servicio anuncia como ventana el umbral barato, salvo a los
códigos con contexto completo, que reciben la ventana real y el umbral.

Lo que lee el modelo tras compactar junta Codex y Claude Code, para que no rehaga
lo que ya hizo ni tome por suyos cambios ajenos:

- El resumen va por apartados, como en Claude Code: encargo actual, restricciones,
  lo hecho, errores, pedidos anteriores (atendidos o no), lo pendiente, en qué
  estaba y un siguiente paso que sólo continúa el encargo actual.
- Tus mensajes anteriores van juntos y marcados como historia ya atendida; sólo
  el último va como mensaje tuyo. Al compactar entre turnos también el último es
  historia.
- Antes de tu último mensaje, como el contexto que Codex vuelve a dar, va la lista
  de archivos que cambió el propio agente: el resto no lo mete en un commit ni lo
  revierte sin preguntar.

Al empezar cada turno el motor da al modelo un informe breve de estado (archivos
que cambió, cambiados por fuera, últimos fallos), con tope, y sólo cuenta el
último: antes iba entero en cada turno y llenaba el contexto.

Las instrucciones base piden, como Codex y Claude Code, que un commit añada por
nombre sólo los archivos del agente (nunca `git add -A` con otros cambios) y que
pregunte si encuentra cambios inesperados.
