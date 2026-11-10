# Permisos de sitios del navegador

El motor decide los permisos. Antes de visitar un origen por primera vez ofrece:

- **Una vez:** ese origen durante esta visita. Se retira al entrar en otro origen.
- **Este sitio · sesión:** ese origen mientras vive la sesión del motor.
- **Siempre · este proyecto:** ese origen, guardado sólo para la carpeta elegida.
- **Rechazar:** no se contacta con el sitio ni se repite la acción por otra vía.

Un origen incluye esquema, servidor y puerto: `https://ejemplo.com:443` es distinto
de `http://ejemplo.com:80` y de `https://ejemplo.com:8443`. Los nombres internacionales
se normalizan con IDNA 2008; no hay comodines ni permiso global implícito.

La confianza no concede permisos de formulario, contraseña, descarga, subida o
JavaScript, ni acceso a este Mac o a la red local. Esos permisos siguen separados.
Plan permite navegar y leer; conserva la prohibición de escribir. Omitir concede
sólo una visita sin preguntar y respeta los bloqueados; nunca guarda confianza
duradera por sí solo.

## Ajustes y configuración

En **Ajustes ▸ Navegador** hay listas de permitidos y bloqueados por proyecto. Los
bloqueados tienen prioridad. El motor muestra el antes y después y guarda sólo
tras confirmar esa propuesta. El archivo vive bajo el hogar protegido de Alfalfa,
en `navegador/sitios/<huella de la carpeta>.json`; la app no escribe confianza.
El canal de Ajustes no inicia modelos, proveedores ni conversaciones.

Un proyecto puede restringir el resultado desde `.alfalfa/ajustes.json`:

```json
{
  "navegador": {
    "sitios": {
      "permitidos": ["http://127.0.0.1:3000", "https://ejemplo.com:443"],
      "bloqueados": ["https://externo.example:443"],
      "estricto": false
    }
  }
}
```

`permitidos` en el proyecto sólo limita el conjunto posible: **no concede confianza**.
Omitir esa clave permite preguntar por otros orígenes; una lista vacía los bloquea
todos. Los bloqueados se suman a los de Ajustes. `estricto: true` puede exigir el
modo estricto; `false` no desactiva uno exigido desde Ajustes. Se rechazan reglas
inválidas y enlaces simbólicos; se vuelven a leer tras cada aprobación pendiente.

Por defecto se controlan las navegaciones y redirecciones. El modo estricto,
opcional, también bloquea subrecursos HTTP(S) de orígenes sin permiso. Puede romper
sitios que dependan de servicios externos. Las excepciones de sitios se aplican
antes de la barrera existente de red local y no pueden desactivarla. Esa barrera
sigue mirando las URL de subrecursos, no sus resoluciones DNS; no es un cortafuegos.

## Contrato entre motor y ejecutor

Cada petición lleva `sitios` con `version: 1`, `permitidos`, `bloqueados`,
`estricto` y `visita`; puede incluir `limite_proyecto`. `permitidos` contiene sólo
las concesiones de sesión y proyecto. Una visita tiene `{id, origen}` y no se
convierte en un permiso permanente. También se envían `visita_sitio` y `visita_id`.
`politica_sitios(runtime)` expone la misma política
para otros ejecutores. `origenes_red_local(runtime)` devuelve aparte los orígenes
exactos cuyo acceso local aprobó la persona; dar permiso de sitio no los añade.

La visita se consume al confirmar un documento de otro origen, no al intentar
una navegación que luego se rechaza. El ejecutor recuerda los IDs consumidos y
rechaza fotos antiguas que intenten reintroducirlos; la respuesta comunica
`visita_consumida` para retirar también el permiso en el motor. Sus excepciones
de subrecursos sólo se aplican mientras el origen superior sea el de esa visita.

WebKit cancela una navegación desconocida antes del contacto y devuelve un permiso
`sitio` con URL y token. Tras la decisión, el motor manda `reanudar_sitio` con ese
token y la política actual. El token se vincula a la solicitud, al documento y a
la URL de origen. Rechazar o cancelar manda `cancelar_sitio`; no vuelve a pulsar ni
a enviar el formulario. Sólo se reanudan GET y HEAD. Para POST y cualquier otro
método se concede el sitio para el siguiente intento y se devuelve un error
explícito, aunque WebKit conserve su cuerpo: visitar B no autoriza a entregarle
un cuerpo aprobado para A. No se convierte en GET ni se envía vacío.
Si una espera se interrumpe por una navegación de fondo, no se afirma que su
condición se cumplió. Si la acción no llegó a completarse (`iniciada:false`),
se informa de ello para releer la página antes de repetirla.

La consulta `alfalfa --navegador-sitios --json` no modifica nada. El canal interactivo
`--navegador-sitios --stdio` sólo guarda tras una respuesta de permiso con el ID
de la propuesta pendiente. No hay herramienta del modelo para ampliar las listas.
