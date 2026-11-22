# Abrir Antigravity desde AlfalfaCode

La app de macOS puede abrir una sesión de **Antigravity** en su barra lateral.
[Instala Antigravity CLI](https://antigravity.google/docs/getting-started?tab=cli)
con una versión que incluya `agy acp`. Si se instaló en `~/.local/bin/agy`, AlfalfaCode
lo detecta. Para otra ubicación, usa **Elegir ejecutable agy…**. Selecciona una
carpeta que quieras compartir con Antigravity y pulsa **Conectar**.

AlfalfaCode abre el CLI oficial como un proceso local con
[`agy acp --sandbox`](https://antigravity.google/docs/ide/extensions/acp/).
Puedes tener cerrada la app de escritorio de Antigravity. La primera vez, el
botón **Iniciar sesión con Google** delega la autorización al CLI. Si el
agente no completa el inicio de sesión desde ACP, abre `agy` en Terminal,
inicia sesión ahí y vuelve a conectar. Las solicitudes elegibles usan la cuenta
y los límites configurados en Antigravity.

La vista muestra mensajes, estado de herramientas y solicitudes de permiso.
Ofrece permitir una vez, denegar y cancelar el turno. El contador de contexto
aparece si el agente envía su uso por ACP. Los archivos y comandos los maneja
Antigravity con su sandbox y sus permisos. El cliente ACP de AlfalfaCode no
acepta peticiones para que AlfalfaCode escriba archivos o ejecute comandos por
cuenta del agente.

## Servidores MCP

La sesión puede usar los servidores que ya configuraste en Antigravity. Para
reutilizar la configuración de AlfalfaCode, pulsa **Importar servidores MCP…**,
elige su `mcp.json` (por defecto `~/.alfalfa/mcp.json`) y marca los servidores
que quieras pasar a esta sesión. El selector muestra el comando y sus argumentos
o la URL que se compartirá. Revisa que no incluyan credenciales antes de marcar
cada servidor.

Se admiten entradas sencillas con comando absoluto, acceso de red habilitado
y sin entorno ni filtros propios, y entradas HTTPS simples sin cabeceras
o credenciales cuando el CLI anuncia soporte HTTP por ACP. Para los demás,
usa [la configuración de MCP de Antigravity](https://antigravity.google/docs/cli/mcp/).
Las herramientas nativas de AlfalfaCode, como su navegador interno y Control
de apps, conservan su sesión y su flujo de permisos propios.

## Alcance de la sesión

La vista mantiene una conversación de Antigravity abierta mientras navegas por
la app. El historial de la sesión ACP se conserva en Antigravity. La app todavía
no reabre esos hilos después de cerrar la sesión ni los añade al historial
de sus sesiones nativas. Antigravity puede delegar en sus propios subagentes.
La vista ACP actual no abre las conversaciones hijas ni delega encargos entre
motores.

## Delegar una consulta entre agentes

- **AlfalfaCode → Antigravity.** Instala `agy` en `~/.local/bin`, `~/.antigravity/bin`,
  `/opt/homebrew/bin` o `/usr/local/bin` e inicia sesión en Antigravity. En un chat nativo
  de AlfalfaCode, pide «Delega a Antigravity una revisión de este fragmento». Revisa y aprueba
  el texto en la tarjeta. El motor abre un encargo ACP nuevo en una carpeta temporal, exige
  modo Plan, rechaza las solicitudes de permisos del agente y devuelve el texto al chat.
- **Antigravity → AlfalfaCode.** En la pantalla de conexión de Antigravity elige un modelo
  en **Delegar a un modelo de AlfalfaCode** y conecta. Pide «Consulta al modelo de AlfalfaCode
  por MCP sobre este fragmento». El modelo queda fijado para esa conexión. Puede recibir hasta
  tres consultas, una a la vez, con la cuota y los cargos normales de su proveedor.
  Utiliza la configuración guardada de AlfalfaCode. Las credenciales de la app no viajan
  al proceso de Antigravity.
  Para conectar el mismo servidor desde otro cliente MCP usa la ruta absoluta de
  `alfalfa` y los argumentos `--delegacion-mcp MODELO`.

La delegación está desactivada en proyectos con política de equipo. Cada consulta dura
hasta 90 segundos. Comparte sólo el texto que haga falta para la tarea.
Las consultas no llevan automáticamente archivos, historial ni herramientas del chat de
origen. El modelo de AlfalfaCode consultado por MCP responde sin herramientas. Los
encargos a Antigravity no reciben el servidor MCP de vuelta. Al cerrar la conexión
de Antigravity se cierra también el servidor MCP.
