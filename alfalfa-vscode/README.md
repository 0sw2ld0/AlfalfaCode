# AlfalfaCode para VS Code y Cursor

Esta extensión añade un chat de AlfalfaCode en la barra lateral del editor. Funciona igual en VS Code y en Cursor. No entra en el chat de Copilot ni en el de Cursor: el panel es suyo. En VS Code 1.106 o posterior está a los dos lados (barra de actividad y barra lateral secundaria); en Cursor, a la izquierda.

La extensión no trae ningún motor. Lanza la orden `alfalfa --pacai` en la carpeta abierta y habla con ella por PACAI 0.3, el mismo protocolo que usa la app de Mac. Los modelos, las claves, el sandbox y los permisos son los de tu instalación de AlfalfaCode.

## Qué hace

- **Chat en la barra lateral.** Las respuestas aparecen con formato y cada herramienta queda en una línea que se despliega para ver sus argumentos y su salida.
- **Permisos.** Cada petición sale en una tarjeta con su motivo y, si es una edición, el parche. Puedes permitir una vez, permitir en la sesión o denegar. *Abrir parche* lo muestra en el editor.
- **Modo de permisos** junto al botón de enviar, con un menú como el de la app de Mac: cada modo con su descripción y su número (1…), y Omitir permisos aparte, con confirmación. Por defecto, Auto. El cambio vale desde el turno siguiente. Si `alfalfa` anuncia sus modos (`alfalfa --modos --json` y `modes` en `alfalfa.session`), el menú sale de ahí y un modo nuevo del motor aparece sin actualizar la extensión; con un `alfalfa` que aún no los anuncia, el menú usa la lista que trae la extensión.
- **Modelo y esfuerzo** bajo el cuadro de texto. La píldora del modelo abre un buscador sobre `alfalfa --modelos` (solo modelos con herramientas, agrupados por proveedor); el selector de esfuerzo, a su lado, muestra los niveles que admite el modelo elegido. Con una sesión abierta, el cambio vale desde el turno siguiente.
- **Plan del turno** fijo encima del cuadro de texto, y **cambios del turno** con los archivos tocados (se abren con un clic) y el parche completo.
- **Encauzar.** Lo que escribes mientras AlfalfaCode trabaja entra en el turno en curso sin cortarlo. **Detener** (o Esc) lo interrumpe.
- **Preguntar sobre la selección** (menú contextual o ⌘⌥A): pega la ruta, las líneas y el código seleccionado en el cuadro.
- **Reanudar una sesión** de esa carpeta, también las abiertas desde la app de Mac: el reloj de la cabecera lista las sesiones guardadas (`alfalfa --sesiones`) y, al elegir una, el panel pinta su conversación (`alfalfa --historial`) y sigue en ella (`alfalfa --reanudar`). Las sesiones de la extensión salen igualmente en la app. Una sesión que está trabajando en otro sitio no se reanuda hasta que termine.
- Uso de contexto y gasto de la sesión en la cabecera.

## Instalar

Descarga [AlfalfaCode.vsix](https://alfalfacode.cuycoders.dev/vscode/AlfalfaCode.vsix) desde la web y, en la paleta de órdenes (⌘⇧P), elige **Extensions: Install from VSIX…**. Desde el terminal:

```sh
code --install-extension AlfalfaCode.vsix      # VS Code
cursor --install-extension AlfalfaCode.vsix    # Cursor
```

## Requisitos

- La app de Mac de AlfalfaCode o la orden `alfalfa` (0.99.28 o posterior), con un modelo configurado que admita herramientas.
- VS Code 1.85 o posterior, o una versión de Cursor basada en él.

Basta con la app de Mac: si no tienes la orden `alfalfa`, la extensión usa el motor que la app lleva dentro (`AlfalfaCode.app/Contents/Resources/motor/alfalfa`). Antes busca la orden en el PATH del editor, en el de tu shell y en `/opt/homebrew/bin`, `/usr/local/bin` y `~/.local/bin`. Si no aparece, el aviso trae **Elegir ejecutable…** (vale elegir la app entera), o usa la orden **AlfalfaCode: Elegir el ejecutable de AlfalfaCode**.

## Ajustes

| Ajuste | Para qué |
| --- | --- |
| `alfalfa.ejecutable` | Ruta de `alfalfa`. Vacío: se busca. |
| `alfalfa.modo` | Modo de permisos al abrir la sesión. Por defecto, `auto`. |
| `alfalfa.modelo` | Modelo (`alfalfa --modelos` los lista). Vacío: el configurado. |
| `alfalfa.esfuerzo` | `low` … `max`. Vacío: el configurado. |

El tráfico PACAI completo se ve en **AlfalfaCode: Ver el registro** con el nivel *Trace* (Salida → AlfalfaCode → nivel de registro).

## Probarla desde el repositorio

Sin instalar nada, desde la raíz de AlfalfaCode:

```sh
code --extensionDevelopmentPath="$PWD/alfalfa-vscode" .
```

En Cursor, la orden es la misma cambiando `code` por `cursor`.

Para empaquetarla e instalar el `.vsix`:

```sh
cd alfalfa-vscode
npm run package
code --install-extension alfalfa-vscode-0.1.4.vsix
```

Las pruebas del cliente PACAI usan un agente falso y no gastan modelo:

```sh
cd alfalfa-vscode
npm test
```

## Límites de esta versión

- Una sesión por ventana, en la primera carpeta del espacio de trabajo.
- No adjunta imágenes ni archivos (el cliente ya sabe enviarlos como bloques `file`; falta el botón).
- No hay sincronía en vivo con la app de Mac: cada lado ve lo que hizo el otro al reanudar la sesión, no mientras trabaja.
- Se reparte como `.vsix`; aún no está en el Marketplace de VS Code ni en Open VSX.
- El navegador propio de AlfalfaCode (`--navegador`) no está disponible en el editor.
