# Venir de OpenCode, Codex o Claude Code

Un proyecto preparado para otra herramienta funciona en AlfalfaCode sin tocar nada.
`alfalfa --importar` dice qué encontró en la carpeta y en qué estado está; en la app
está en Ajustes ▸ Importar.

| Herramienta | Qué se lee | Qué se usa |
|---|---|---|
| Estándar AGENTS.md | `AGENTS.md` / `AGENTS.override.md` de la raíz del repositorio a la carpeta de trabajo | Instrucciones |
| Claude Code | `CLAUDE.md`, `.claude/CLAUDE.md` y `CLAUDE.local.md` de esas mismas carpetas; `.mcp.json` del proyecto; `.claude/commands/*.md` (del proyecto y de `~/.claude/commands`) | Instrucciones, servidores MCP, órdenes «/» |
| OpenCode | `opencode.json(c)` y `.opencode/opencode.json(c)` del proyecto, `~/.config/opencode/opencode.json(c)`, `.opencode/agent(s)/*.md` y `.opencode/command(s)/*.md` | `mcp`, `agent`/`agents`, `command`/`commands` e `instructions` (formatos V1 y V2) |
| Codex | `~/.codex/config.toml` y `.codex/config.toml` del proyecto | `[mcp_servers.*]`, `developer_instructions` y `project_doc_fallback_filenames` |

**Instrucciones.** Entran en este orden y en el mismo tope de 32 KiB:
`developer_instructions` de Codex (como hace Codex, delante); en cada carpeta, de la
raíz a la de trabajo, `AGENTS.override.md`, `AGENTS.md` o un nombre alternativo de
Codex, y después los CLAUDE.md; al final, los archivos de `instructions` de OpenCode
(nunca URL). Un archivo que ya entró no se repite: ni el mismo, ni un enlace a él,
ni otro con el mismo texto, y un CLAUDE.md que sólo dice `@AGENTS.md` no añade nada.
Un archivo que enlaza fuera del proyecto no se lee. El `AGENTS.md` de `~/.alfalfa`
sigue yendo primero.

**Servidores MCP, agentes y órdenes «/»: se autorizan una vez, viendo qué hacen.**
Como los complementos, lo que viene de una configuración ajena no se activa hasta
que lo autorizas. La lista enseña de qué herramienta y de qué archivo viene cada
uno y su definición sin resolver. De un servidor stdio enseña la orden entera con
sus argumentos, sin recortar; una de más de 8000 caracteres (contados en crudo) no se
usa. La de un servidor del proyecto se ve tal cual se ejecutará. En la de uno tuyo se
tapan sólo los valores que son claramente una credencial: `--token=…`,
`NOMBRE_TOKEN=…` (como `docker run -e`), el valor que sigue a `--api-key`, una cabecera
(`Authorization: …`, `Cookie: …`, también tras `--header=` o `-H`), un token con prefijo
conocido (`ghp_`, `github_pat_`, `sk-`, `xoxb-`, `glpat-`…), las claves con nombre de
credencial de un JSON (y el `value` de un `{"name": "Authorization", …}`), y de una URL de
cualquier esquema (también `postgresql://`, `mongodb+srv://`, `redis://`) o sin él, la
contraseña de `usuario:contraseña@` (siempre, aunque sea corta), los valores secretos
de la consulta o del fragmento y un tramo con forma de token. Los caracteres
de control salen escritos. También enseña la ruta del ejecutable, avisando si es del
repositorio, también si un servidor tuyo acaba lanzando un binario del repositorio
abierto. Pasa con un `npx`, `npm exec`, `pnpm dlx` o `bunx` (aunque sean los globales),
que ejecutan el `node_modules/.bin/<paquete>` del proyecto si existe, buscándolo desde la
carpeta del proceso hacia arriba. Entonces la huella incluye ese binario y todo su
contenido, y la autorización no vale en otro repositorio ni si el binario cambia. Si
la definición declara un PATH, se busca con él, y un tramo vacío cuenta como la
carpeta del proceso. Por último, la carpeta que podrá escribir, si
tendrá internet y qué variables lee. De uno HTTP enseña sólo el host. De una orden «/» o un agente enseña el principio de su texto.
Al empezar una sesión, un aviso dice qué queda sin autorizar.

```bash
alfalfa --importar                                # qué hay, su definición y su huella
alfalfa --importar --autorizar mcp:docs@HUELLA    # lo que viste (la clave corta vale si no hay dudas)
alfalfa --importar --autorizar todo               # en una terminal: todos los pendientes
alfalfa --importar --autorizar todo --sin-internet
alfalfa --importar --retirar agente:revisor
```

`CLAVE@HUELLA` sólo autoriza si el archivo sigue diciendo lo que viste: si cambió
entre la lista y la autorización, se rechaza. Sin huella, o con `todo`, hace falta
una terminal de verdad. Dentro de una sesión de AlfalfaCode no se autoriza nada,
porque un agente no se da permisos. La app autoriza siempre con la huella, y
«Autorizar todo» pide confirmación.

La autorización queda en `~/.alfalfa/importadas.json` como una huella de lo que se
usará (HMAC con una clave de este Mac). Si cambia algo (otra orden, otros argumentos,
otro entorno, el archivo `{file:…}` del prompt de un agente), vuelve a «sin
autorizar». Otro proyecto con un servidor del mismo nombre no hereda la autorización.

- Los servidores corren como los de `mcp.json`: stdio dentro del sandbox Seatbelt
  (con internet salvo `--sin-internet`) y cada llamada pide permiso. Sólo reciben el
  entorno que declaran. Las referencias (`{env:X}` de OpenCode, `${X}` de Claude Code,
  `env_vars` y `bearer_token_env_var` de Codex) se resuelven en memoria al abrir la
  sesión y nunca entregan una clave de proveedor. Lo que sale de ellas, también en la
  URL, se redacta como el entorno del servidor. Ninguna clave se copia a ningún
  sitio. La lista enseña los valores literales que puso el proyecto y oculta los de
  tu configuración.
- La carpeta de un servidor stdio es la única que puede escribir. La de uno del
  proyecto queda dentro del proyecto; si la raíz del repositorio es tu carpeta
  personal (dotfiles), dentro de la carpeta de trabajo. Ninguna puede ser tu carpeta
  personal ni una que la contenga. Tampoco puede estar dentro de `~/.alfalfa` ni
  contenerla, ni estar dentro de un `.git` o de un repositorio desnudo, cuyos hooks
  correrían fuera del sandbox. Las comparaciones no distinguen mayúsculas, como el
  disco del Mac (`.GIT`, `~/.ALFALFA`). Si lo es, el servidor no se usa. El sandbox
  tampoco abre para escribir una carpeta de trabajo que esté dentro de un `.git`.
- `mcp.json` y los complementos ganan. Las capas de OpenCode se fusionan campo a
  campo como en OpenCode: la más cercana gana, así que `{"gh": {"disabled": true}}`
  en el proyecto apaga el `gh` del usuario. Luego va lo del proyecto (de la carpeta
  de trabajo a la raíz, también las intermedias) y por último lo del usuario. Un
  nombre repetido sale como «duplicado». Si lo tapado es algo que ya autorizaste, se
  avisa.
- Diferencias con las otras herramientas:
  - OpenCode deja vacía una `{env:X}` que no existe; aquí el servidor no se inicia sin ella.
  - Codex sigue sin una variable de `env_vars` que no existe; aquí también, con un aviso.
  - OAuth no se usa, sólo las cabeceras. Un servidor remoto de OpenCode que dependa
    de su OAuth por defecto lo avisa.
  - Tampoco se usan `{file:…}` en un servidor, SSE ni `http_headers_helper` de Codex.
- Un agente de OpenCode se usa como subagente (`spawn_agent`). Denegar editar
  (`permission.edit`, `tools.write/edit: false` o las reglas V2 en orden, la última
  gana) lo deja de sólo lectura, y denegar `bash` le quita las órdenes. Un deny
  limitado a unas rutas no se puede limitar aquí: se aplica entero y se avisa. Su
  modelo `proveedor/modelo` no es de AlfalfaCode, así que usa el de la sesión, y la
  variante (`#high`) pasa a esfuerzo. Un `.alfalfa/agents` o `.claude/agents` con el
  mismo nombre gana.

**Órdenes «/».** Las de OpenCode y Claude Code salen en el menú «/» de la app; las que
faltan por autorizar, atenuadas. Una autorizada, `/probar unidad` (o `alfalfa "/probar
unidad"`), le manda al agente su plantilla con `$ARGUMENTS` y `$1`, `$2`… sustituidos,
en una sola pasada. La plantilla va marcada como texto del repositorio, no como
palabras tuyas. Una sin autorizar se manda tal cual, con un aviso. `` !`orden` `` no
se ejecuta sola: queda como petición al agente, que la corre con los permisos de
siempre. Si el comando pide un subagente (`subtask`/`subagent`), el mensaje lo dice.
Las órdenes propias de la app ganan.
