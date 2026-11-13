# La primera vez, sin claves

Puede que todavía no haya ningún proveedor: ni claves, ni un proveedor elegido,
ni Ollama en el Mac. Entonces `alfalfa` y la app no dan un error de conexión.
Enseñan los modelos de **Gratis ahora** (ver [Catálogo, coste y presupuesto](catalogo.md)), con su aviso de datos.

OpenRouter se conecta con un clic, sin pegar ninguna clave:

```bash
alfalfa --conectar openrouter
```

En la app, el botón está en Nuevo encargo («Empieza gratis») y en la hoja que pide
la clave de OpenRouter. Esto es lo que pasa:

1. Se abre OpenRouter en el navegador, donde entras o creas tu cuenta gratis y
   autorizas AlfalfaCode (OAuth con PKCE).
2. La vuelta llega a un servidor efímero en `localhost`. Sólo acepta la que trae
   el `state` correcto, y cualquier otra no corta la espera.
3. La clave pasa al Llavero sin que la veas.
4. Si no había ningún proveedor, queda como modelo por defecto un gratis probado
   con herramientas: Nemotron 3.5 Super VL. Lo eliges tú, después de ver el aviso
   de datos.

Si ya tenías un proveedor, `--conectar` sólo guarda la clave. Lo mismo pasa con
`--solo-clave`, que es lo que usa la hoja de la clave de la app: ni el proveedor
ni el modelo por defecto cambian. Un modelo gratis nunca se elige por su cuenta:
con OpenRouter por defecto y sin modelo elegido, `alfalfa "encargo"` pide
`--modelo`.

En una terminal, `alfalfa "encargo"` lo ofrece directamente y sigue con el
encargo. La pregunta lleva el aviso de datos, y hay que contestar «s»: Enter es
que no. Los demás proveedores con
modelos gratis dicen dónde se saca su clave gratis
(`alfalfa --guardar-clave nvidia`). `alfalfa --primera-vez [--json]` da el
mismo resumen. Para verlo en un Mac que ya tiene claves, usa
`ALFALFA_FORZAR_PRIMERA_VEZ=1`.

Las sesiones, reglas, complementos y hooks del usuario viven en `$ALFALFA_HOME`
(por defecto `~/.alfalfa`).
