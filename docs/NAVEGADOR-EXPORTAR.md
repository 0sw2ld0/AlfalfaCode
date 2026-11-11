# Guardar un flujo como prueba de Playwright

En la app de Mac, al terminar un turno que comprobó un flujo en WebKit, el
cierre puede ofrecer **Guardar prueba de Playwright**. El botón guarda un archivo
nuevo en `tests/e2e/*.spec.ts`; puedes cambiar el nombre dentro de esa carpeta.
Manual pide el permiso de edición habitual. Plan y solo lectura no guardan.
La oferta pertenece al último turno del proceso que lo ejecutó: un cierre
reabierto puede mostrarla, pero no puede guardarla desde el historial.

El motor registra sólo operaciones con un resultado comprobable. El flujo
empieza con una navegación HTTP(S) explícita, usa lecturas completas del árbol
para relacionar cada clic o relleno simple con un rol y nombre únicos, y acaba
con una espera que confirmó un texto visible y único. Los roles, nombres y
textos exactos se convierten en localizadores de Playwright; las referencias
`[eN]` no quedan en el archivo. Cada localizador se verifica como único al correr
la prueba, y el texto final se comprueba con una aserción de visibilidad.

La tarjeta enumera las claves de entorno y su paso, sin valores. Configura
`ALFALFA_E2E_BASE_URL` con la URL inicial del entorno de ensayo, y completa
las claves `ALFALFA_E2E_NOMBRE_n`, `ALFALFA_E2E_VALOR_n`,
`ALFALFA_E2E_TEXTO_n` y `ALFALFA_E2E_RUTA_n` que pida la tarjeta. Las rutas
posteriores son relativas al mismo origen. No añadas valores privados al
repositorio; usa el mecanismo de secretos de tu CI si hace falta. El guardado
no ejecuta la prueba. Para correrla con Playwright instalado en el proyecto:

```sh
npx playwright test tests/e2e/tu-prueba.spec.ts
```

Si faltó una lectura completa o hay un paso que no se puede reproducir, la
tarjeta explica el motivo. Relevos, JavaScript, contraseñas, envíos de
formularios, subidas, descargas, flujos entre orígenes y otros pasos sin
evidencia quedan fuera. El adaptador CuyBrowser actual no confirma el elemento
efectivo del clic o relleno ni todas las condiciones de espera; esos turnos
no generan un archivo parcial. Un diff o una lectura recortada no bastan
para resolver un localizador.

El spec corre en un contexto nuevo de Playwright, sin copiar sesiones ni
cookies. Comprueba el origen de la página al navegar, pulsar y confirmar. Usa
un entorno de prueba bajo tu control; el filtro de peticiones de Playwright
no sustituye un aislamiento de red. La prueba no guarda estado del navegador
ni vuelve a pedir permisos del arnés al ejecutarse fuera de Alfalfa.

El [ensayo local](demos/navegador-a11.md) muestra cómo comprobar el flujo
completo con una página ficticia.
