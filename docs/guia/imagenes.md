# Imágenes

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud --imagen captura.png "¿qué falla aquí?"
```

Como en Codex, cada imagen va al texto con `<image name=[Image #N] path="...">`, se
copia a la sesión reducida a 2048 px como mucho y viaja a Ollama en el campo
`images` de `/api/chat`. Hace falta un modelo con visión. En `--stdio` el mensaje
lleva `"imagenes": ["/ruta/absoluta.png"]`. En la app de Mac se adjuntan con el
botón «+», pegándolas con ⌘V o soltándolas sobre el cuadro de texto, y salen como
miniatura, como en Codex: si el campo de texto escribe la ruta al soltarla, la app la
quita y la adjunta. Una captura arrastrada desde su miniatura flotante se copia a
los adjuntos, porque macOS borra enseguida su archivo temporal.

Cada modelo de `--modelos --json` dice si ve imágenes (`vision`: true, false o null si
no se sabe), de su `/api/show` en Ollama, de lo que dice cada proveedor o el servicio
Alfalfa, o del catálogo de models.dev. Un mensaje con imágenes para un modelo que no las
ve se rechaza antes de empezar el turno, con un error que nombra modelos con visión que
tienes de verdad; si quien integra AlfalfaCode configuró una visión delegada, las imágenes
se describen con ella en vez de fallar. Las imágenes que ya estaban en la conversación (o
que devuelve una herramienta) no rompen el turno de un modelo sin visión: se describen
con la visión delegada o el mensaje dice que no se envían. Si no se sabe si las ve, se le
mandan y el proveedor dirá. En la app, con un modelo sin visión el botón de adjuntar queda
desactivado («Este modelo no ve imágenes»), pegar o soltar una imagen lo avisa, las
miniaturas ya adjuntas se marcan y al enviar se ofrece cambiar a un modelo con visión o
quitarlas.

Si el modelo tiene visión, el agente también puede mirar por su cuenta una imagen
del proyecto con `inspect_image`, como `view_image` en Codex: el mismo modelo de
la sesión recibe la imagen y responde a la pregunta. Sin visión, la herramienta
no se ofrece. Una aplicación que integra AlfalfaCode puede delegarlo en otra
sesión con `alfalfa.ajustes.vision_delegada`.

Al revés, el agente también puede enseñarte imágenes y vídeos, como en la app de Codex.
Si responde con una línea `![Tolgrenn — caminar](/ruta/lamina.png)`, la app de Mac
pinta ahí la imagen con el texto como pie de foto, y varias seguidas forman una
cuadrícula. Valen PNG, JPG, HEIC, WebP, GIF (animado) y, en un reproductor sin sonido
y en bucle si es corto, MP4 y MOV. Sólo archivos locales que existan, con ruta
absoluta, `~/` o relativa a la carpeta de la sesión: una URL web se queda como enlace y
nunca se descarga nada. Un clic la abre en Vista rápida y el menú contextual la muestra
en Finder o la copia. Copiar o exportar la respuesta sigue dando el Markdown original, y
en la terminal se ve la ruta.
