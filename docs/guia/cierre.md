# Cierre de cada turno

Como la caja «siguiente paso» de Claude Code: cuando un turno cambió archivos o
ejecutó órdenes, el agente llama a `wrap_up` antes de su respuesta. Da un resumen
corto de cómo queda el trabajo y, si lo siguiente lo decide el usuario, una
pregunta con una o dos respuestas. En `--stdio` llega como `cierre` dentro de
`turno_terminado` (`resumen`, `pregunta`, `opciones`). La app de Mac lo pinta como
una tarjeta, y cada botón manda su texto como el siguiente mensaje; el primero
también responde con ⌘↩. En la terminal sale como «Siguiente paso». Sólo lo
ofrece el catálogo de AlfalfaCode; los agentes del integrador no lo ven.

Como las tarjetas «Nueva sesión» de Claude Code, el agente puede proponer una
tarea aparte con `propose_session` (`title` y `prompt`, el encargo completo,
porque la sesión nueva no ve la conversación). Sirve para algo fuera del
encargo o para el siguiente paso que recomienda y no va a hacer ahora. Puede
proponer hasta tres por turno, y no ejecuta nada: viaja en el mismo `cierre`, como
`sesiones: [{titulo, encargo}]`, y se guarda con el turno. La app enseña una
tarjeta por propuesta. «Nueva sesión» empieza el encargo aparte, en la misma
carpeta y con el mismo modelo, esfuerzo y modo, sin cambiar de pantalla: la
sesión sale en la barra lateral. «Hacerlo aquí» se lo pide a la sesión actual. En
la terminal se imprime el encargo para abrir otra sesión a mano.

La sesión que nace así es hija de la que la propuso: se crea con `--padre ID`, que
queda guardado en ella y sale como `padre` en `--sesiones --json`, y con `--titulo`
y el título de la propuesta, que la barra lateral enseña en vez del principio del
encargo. Si el padre vuelve a abrirse, su tarjeta ya no ofrece «Nueva sesión». En la barra
lateral va debajo de su padre, con sangría. Cada vez que la hija termina un turno, la
app le manda al padre una nota con los primeros 400 caracteres de su informe: el
resumen de `wrap_up` o, si no lo hay, su última respuesta. Si el padre está
trabajando, la nota entra al acabar su turno. Si está dormido o cerrado, se despierta
sin cambiarte de pantalla. Un turno que corta el usuario no manda nota.
