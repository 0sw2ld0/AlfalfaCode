# AlfalfaCode

Español · [English](README.en.md)

Arnés de agentes de programación para modelos de Ollama (local o Ollama Cloud), de
OpenAI, de Anthropic (Claude), OpenRouter, OpenCode Zen u otro proveedor compatible
con OpenAI con tu propia clave, o de un servicio Alfalfa. Recibe un encargo, trabaja
en la carpeta actual y pregunta antes de ejecutar lo que no sea una lectura. La orden
es `alfalfa`.

Se usa desde la terminal, con `--stdio` (JSONL) para integrarlo en otra aplicación o
con la app de Mac de `alfalfa-mac/` (desde el repositorio,
`cd alfalfa-mac && swift run AlfalfaCode`). La app también se instala sin el repositorio:
`alfalfa-mac/build-app.sh` la empaqueta con su propio Python y el motor dentro, y
`alfalfa-mac/publicar.sh` hace el DMG firmado y notarizado. Detalles en
[alfalfa-mac/EMPAQUETADO.md](alfalfa-mac/EMPAQUETADO.md).

## Instalar

En un Mac con Apple Silicon y macOS 14 o posterior, sin Python, git ni Xcode:

```bash
brew install --cask 0sw2ld0/tap/alfalfacode   # la app de Mac
brew install 0sw2ld0/tap/alfalfa              # la CLI
```

o, sólo la CLI y sin Homebrew ni sudo:

```bash
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh
```

También vale bajar el DMG de la
[última release](https://github.com/0sw2ld0/AlfalfaCode/releases/latest) y
arrastrar la app a Aplicaciones.

**En un Mac con Intel** (también macOS 14 o posterior), Homebrew todavía no sirve: usa
`instalar.sh`, que baja solo la versión para Intel, o el DMG `AlfalfaCode-<versión>-intel.dmg`
de la release. La app de Intel se actualiza sola con los DMG de Intel. Sin chip T2 no hay
Secure Enclave, así que el código de cortesía del proveedor Alfalfa no se puede activar;
los demás proveedores funcionan igual.

Las tres formas usan el mismo DMG de la release, firmado y notarizado:

- **El cask** instala la app. Se actualiza sola, así que `brew upgrade` no la toca.
- **La fórmula** instala la CLI desde un bottle con el motor de ese DMG, sin compilar.
- **`instalar.sh`** baja el DMG, comprueba su huella SHA-256 y la firma de la app, y
  deja la CLI en `~/.local/bin/alfalfa`.

Para desinstalar:

```bash
brew uninstall alfalfa && brew uninstall --cask alfalfacode
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh -s -- --desinstalar
```

Ninguna de las dos borra `~/.alfalfa`, donde viven tus sesiones y ajustes. El paso a
paso, las variables de `instalar.sh` y la instalación desde el código están en la
[guía de instalación](docs/guia/instalar.md).

En Linux (Ubuntu 24.04, también en WSL2) el motor se instala con pipx y aísla sus órdenes
con bubblewrap; los pasos, el perfil de AppArmor y las claves están en
[Linux](docs/USO.md#linux).

## Primeros pasos

1. Instala y abre la app, o en la terminal:
   `alfalfa "corrige la prueba que falla"`.
2. Si no hay claves ni proveedor, no hay error de conexión: se ofrecen los modelos de
   **Gratis ahora**, con su aviso de datos.
3. Para empezar gratis, conecta OpenRouter con un clic, sin pegar ninguna clave:

   ```bash
   alfalfa --conectar openrouter
   ```

   En la app, el botón «Empieza gratis» está en Nuevo encargo. Abre OpenRouter en el
   navegador, donde autorizas AlfalfaCode, y la clave queda en el Llavero.
4. Con tu clave de un proveedor de pago: `alfalfa --guardar-clave openai`
   (o `anthropic`). El modelo se elige con `alfalfa --modelo …` o en el selector.

Todo lo dicho, paso a paso, está en la [primera vez, sin claves](docs/guia/primera-vez.md).
Las sesiones, reglas, complementos y hooks del usuario viven en `$ALFALFA_HOME`
(por defecto `~/.alfalfa`).

## Generar imágenes con OpenAI

Configura tu clave de OpenAI en Ajustes, con `alfalfa --guardar-clave openai`
en macOS o mediante `OPENAI_API_KEY`. No pegues la clave en el chat. El modelo
que usa el agente para conversar puede ser de otro proveedor.

Ejemplo: «Genera una acuarela de un zorro en la nieve y guárdala como `zorro.png`».
El agente principal usa `generate_image` con `gpt-image-1.5`: puedes elegir tamaño
cuadrado, vertical u horizontal y calidad baja, media o alta. Guarda un PNG por
llamada, en una carpeta existente y sin sobrescribir archivos.

Antes de enviar el prompt, AlfalfaCode pide aprobación y muestra el precio estimado
(por ejemplo, USD 0.034 para 1024 × 1024 en calidad media, más el texto de entrada).
Después devuelve la ruta, dimensiones, tamaño, uso disponible y costo estimado.
El importe no es un cargo confirmado: consulta la [tarifa de OpenAI](https://platform.openai.com/docs/pricing)
y tu facturación. No está disponible en Plan, solo lectura, revisión, subagentes
ni proyectos con reglas de equipo (todavía no se integra en su presupuesto).

## Proveedores

El modelo dice con quién se habla:

| Modelo | Proveedor |
|---|---|
| `deepseek-v4.1-flash:cloud`, `ollama/…` | Ollama, local o Ollama Cloud |
| `chatgpt/<modelo>` | Plan de ChatGPT en macOS, con tu cuenta y la cuota disponible |
| `openai/gpt-6-luna`, `openai/<modelo>` | OpenAI, con tu clave de la API (Responses API) |
| `anthropic/claude-sonnet-5-5`, `anthropic/<modelo>` | Anthropic (Claude), con tu clave de la API (Messages API) |
| `openrouter/…`, `opencode/…`, `<proveedor>/…` | un proveedor compatible con OpenAI del catálogo, con tu clave |
| `alfalfa/…` | un servicio Alfalfa, con un código de cortesía |

```bash
alfalfa --proveedores                         # estado, sin secretos
alfalfa --guardar-clave anthropic             # pide la clave sin enseñarla
alfalfa --proveedor-por-defecto openai
```

Las claves van al Llavero de macOS (servicio `AlfalfaCode`), nunca a un archivo.
También valen las variables estándar `OLLAMA_API_KEY`, `OPENAI_API_KEY` y
`ANTHROPIC_API_KEY`. Lo que no es secreto queda en `$ALFALFA_HOME/proveedores.json`.
Cada proveedor, sus claves y los códigos de cortesía están en la guía de
[proveedores](docs/guia/proveedores.md); el catálogo de modelos, lo que sale gratis
y el coste estimado, en [catálogo, coste y presupuesto](docs/guia/catalogo.md).

## Funciones principales

Cada función, con la guía que la detalla:

- **Sesiones**: el historial se guarda por turnos; se reanuda, bifurca y
  [se busca](docs/guia/sesiones.md) con un índice local.
- **Contexto y compactación**: se compacta sola al llenarse, también a mano, y los
  modelos de tarifa larga tienen modo Ahorro ([Contexto y compactación](docs/guia/contexto.md)).
- **Deshacer**: cada turno toma una instantánea del proyecto en un git aparte; se
  vuelve a un mensaje con su código y se rehace
  ([Volver a un mensaje y deshacer el código](docs/guia/deshacer.md)).
- **Cierre de cada turno**: resumen con «siguiente paso» y sesiones nuevas a un clic
  ([Cierre de cada turno](docs/guia/cierre.md)).
- **Permisos con sandbox**: cuatro modos, sandbox de macOS y «Permitir en esta
  sesión» ([Permisos](docs/guia/permisos.md)); órdenes de fondo y Mac despierto
  mientras trabaja ([Procesos en segundo plano](docs/guia/procesos.md)).
- **Edición revisada**: errores de servidores de lenguaje y linters tras cada edición
  ([Errores al editar](docs/guia/diagnosticos.md)).
- **Imágenes y archivos**: se adjuntan imágenes (con visión), PDF, texto, código,
  Word y Excel ([Imágenes](docs/guia/imagenes.md) y [Archivos](docs/guia/archivos.md)).
- **Habilidades y memoria**: lee `SKILL.md` sueltas y aprende de cada sesión, siempre
  con tu aprobación ([Habilidades y memoria](docs/guia/habilidades.md)).
- **Antigravity**: abre su CLI como agente externo por ACP en macOS. Desde un
  chat de AlfalfaCode puedes delegarle una consulta de texto con tu aprobación.
  Antigravity también puede consultar por MCP el modelo de AlfalfaCode que elijas
  al conectar. [Configurar Antigravity y la delegación](docs/guia/antigravity.md).
- **Subagentes**: tareas acotadas en paralelo, con los permisos de la sesión
  ([Subagentes](docs/guia/subagentes.md)).
- **Mensajes con el agente ocupado**: cola de mensajes, redirigir y enviar ahora
  ([Mensajes con el agente ocupado](docs/guia/ocupado.md)).
- **Control de apps del Mac**: prueba como un usuario la app que construyó
  ([Probar apps del Mac](docs/guia/apps-mac.md)).
- **Navegador propio**: WebKit dentro de la app, con las herramientas `browser_*`;
  opcional Chromium con AlfalfaBrowser ([Navegador propio](docs/guia/navegador.md)).
- **Venir de otra herramienta**: lee `AGENTS.md`, `CLAUDE.md`, la configuración de
  OpenCode y de Codex, servidores MCP, agentes y órdenes «/»
  ([Venir de OpenCode, Codex o Claude Code](docs/guia/migrar.md)).
- **Pruebas**: pytest del motor y `swift test` de la app ([Pruebas](docs/guia/pruebas.md)).
- **Actualizar**: buscar y aplicar la versión nueva ([Actualizar](docs/guia/actualizar.md)).
- **Integrarlo en otra aplicación**: cómo cambia el arnés al integrarse
  ([Integrarlo en otra aplicación](docs/guia/integrar.md)).

## Documentación

- [docs/USO.md](docs/USO.md): el protocolo `--stdio` (JSONL) para integrar
  AlfalfaCode en otra aplicación, con sus reglas de seguridad.
- [docs/ESTILO.md](docs/ESTILO.md): las reglas de estilo del código, con SwiftLint y ruff.
- [docs/guia/README.md](docs/guia/README.md): el índice de la guía, un archivo por tema.
- Del navegador: [NAVEGADOR-ACTIVIDAD.md](docs/NAVEGADOR-ACTIVIDAD.md) (qué hizo el
  agente), [NAVEGADOR-EXPORTAR.md](docs/NAVEGADOR-EXPORTAR.md),
  [NAVEGADOR-LECTURAS.md](docs/NAVEGADOR-LECTURAS.md),
  [NAVEGADOR-SITIOS.md](docs/NAVEGADOR-SITIOS.md) y
  [navegador-comparar.md](docs/navegador-comparar.md).
- [Qué incluye](docs/guia/que-incluye.md) describe los módulos del motor.

## Licencia

AlfalfaCode es MIT; ver [LICENSE](LICENSE). Las piezas adaptadas de terceros
conservan la suya, como se cuenta en «Procedencia».

## Procedencia

Varias piezas son adaptaciones de [OpenAI Codex](https://github.com/openai/codex)
(Apache-2.0). Cada una lleva junto a su código `LICENSE`, `NOTICE` y
`PROCEDENCIA.md` con lo que se tomó y cómo se cambió. El código de terceros de la
app de Mac está en [alfalfa-mac/TERCEROS.md](alfalfa-mac/TERCEROS.md).

La búsqueda web usa la librería [CuySearch](https://github.com/0sw2ld0/CuySearch)
(MIT) como dependencia.

«Aprender de cada sesión» toma ideas, no código, de
[Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research, MIT): sus
almacenes de memoria con tope y escaneo, `skill_manage` y su repaso en segundo plano.

La idea de `buscar_sesiones` viene de `session_search` de
[Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research); no se tomó
código.

El catálogo de modelos viene de [models.dev](https://models.dev) (MIT); ver
[alfalfa/catalogo/PROCEDENCIA.md](alfalfa/catalogo/PROCEDENCIA.md).

