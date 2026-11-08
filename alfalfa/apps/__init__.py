"""Control de apps del Mac para probar lo que el agente construye, como Computer Use de Codex.

Codex lo hace con un servicio nativo («Codex Computer Use.app») y un servidor MCP
en Node (`cua_repl`) con una sola herramienta, `js`: el modelo escribe JavaScript
que llama a `sky.get_window_state`, `sky.click`, `sky.type_text`… El estado de una
ventana es una captura más su árbol de accesibilidad con elementos numerados, y
cada acción apunta a un número de ese árbol o a un punto de la ventana. El acceso
se decide por app (`bundle_ids` con permitir/denegar).

AlfalfaCode toma esa idea con herramientas normales en vez de un intérprete:

- `app_list`: las apps abiertas y sus ventanas. Los títulos de las ventanas sólo se
  enseñan de las apps ya aprobadas, porque pueden llevar datos del usuario.
- `app_state`: el árbol de una ventana con números, su tamaño y una captura
  reducida a puntos (así un punto de la captura es un punto de la ventana). Con
  `question`, la captura se mira con la visión del modelo y vuelve la respuesta.
- `app_action`: clic (por número, con AXPress si el elemento lo tiene; o por
  coordenadas), doble clic, clic derecho, escribir, teclas, desplazar, cambiar el
  valor de un campo, arrastrar y traer al frente.

Ratón propio, como Codex: los clics, arrastres, desplazamientos y teclas se fabrican
como eventos de la ventana (NSEvent con su número) y se mandan al proceso de la app,
y a esa app sola se le avisa de que tiene el foco (SLPSPostEventRecordTo). No se mueve
el ratón del usuario ni cambia la app de delante, y un cursor dibujado enseña dónde
actúa (`ALFALFA_SIN_CURSOR=1` lo apaga). `real_mouse: true` usa el ratón de verdad
para una app que ignore los eventos de fondo.

Mejoras sobre Codex:
- El ratón propio no le quita al usuario ni el ratón ni el teclado.
- Un número de elemento se resuelve con la ruta y el rol guardados de la última
  lectura; si la ventana cambió, la acción falla en vez de pulsar otra cosa.
- Cada acción devuelve la ventana ya cambiada (árbol nuevo y, si se pide,
  captura), así el modelo comprueba el efecto sin otra llamada.
- La primera vez que se toca una app se pregunta al usuario, con «Permitir en esta
  sesión»; las terminales, los editores con terminal, los gestores de contraseñas,
  Acceso a Llaveros, Ajustes del Sistema y los propios agentes nunca se manejan:
  escribir en una terminal sería salir del sandbox sin preguntar.

El ayudante nativo (`ayudante.swift`) se compila con `swiftc` la primera vez y se
guarda por su huella en `$ALFALFA_HOME/bin`. Usa los permisos de Accesibilidad y de
Grabación de pantalla de la app que lanzó AlfalfaCode; si faltan, lo dice.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

FUENTE = Path(__file__).with_name('ayudante.swift')
MAX_SALIDA = 24000
MAX_ELEMENTOS = 400
COMPILAR_SEGUNDOS = 240
ORDEN_SEGUNDOS = 30
MOTIVO = 'app'
MAX_PASOS = 20
MAX_ESPERA_TEXTO = 30
ACCIONES = ('click', 'double_click', 'right_click', 'type', 'key', 'scroll', 'set_value', 'press', 'drag', 'focus',
            'hover', 'menu')
_TIPO = {'click': 'clic', 'double_click': 'doble', 'right_click': 'derecho', 'type': 'escribir', 'key': 'tecla',
         'scroll': 'desplazar', 'set_value': 'valor', 'press': 'pulsar', 'drag': 'arrastrar', 'focus': 'activar',
         'hover': 'pasar', 'menu': 'menu'}
_NOMBRE_ACCION = {'click': 'hacer clic', 'double_click': 'hacer doble clic', 'right_click': 'hacer clic derecho',
                  'type': 'escribir', 'key': 'pulsar teclas', 'scroll': 'desplazar', 'set_value': 'cambiar un valor',
                  'press': 'pulsar un elemento', 'drag': 'arrastrar', 'focus': 'traer al frente',
                  'hover': 'pasar por encima', 'menu': 'elegir en su menú'}

# Niveles de acceso por app, como los de Computer Use de Claude: un navegador sólo se
# mira; una terminal o un editor se miran y admiten clics sueltos, nunca texto, teclas,
# clic derecho (su menú tiene Pegar) ni arrastrar texto encima; el resto, completo.
BLOQUEADA, LECTURA, CLIC, COMPLETA = 'bloqueada', 'lectura', 'clic', 'completa'
DESCRIPCION_NIVEL = {LECTURA: 'Sólo se puede mirar', CLIC: 'Sólo se puede mirar y hacer clics sueltos'}
ACCIONES_POR_NIVEL = {LECTURA: (), CLIC: ('click', 'press', 'scroll', 'hover')}

# Terminales y editores: sólo mirar y clics sueltos (nivel CLIC). Escribir en ellos
# ejecutaría órdenes fuera del sandbox sin preguntar.
SOLO_CLIC = {
    'com.apple.Terminal': 'una terminal',
    'com.googlecode.iterm2': 'una terminal',
    'dev.warp.': 'una terminal',
    'com.mitchellh.ghostty': 'una terminal',
    'net.kovidgoyal.kitty': 'una terminal',
    'io.alacritty': 'una terminal',
    'org.alacritty': 'una terminal',
    'com.github.wez.wezterm': 'una terminal',
    'co.zeit.hyper': 'una terminal',
    'com.microsoft.VSCode': 'un editor con terminal',
    'com.todesktop.230313mzl4w4u92': 'un editor con terminal',  # Cursor
    'com.exafunction.windsurf': 'un editor con terminal',
    'dev.zed.Zed': 'un editor con terminal',
    'com.jetbrains.': 'un editor con terminal',
    'com.apple.dt.Xcode': 'un editor que ejecuta scripts',
}
# Navegadores: sólo mirar (nivel LECTURA); para manejar una web hay herramientas de navegador.
SOLO_LECTURA = {
    'com.apple.Safari': 'un navegador',
    'com.google.Chrome': 'un navegador',
    'org.mozilla.firefox': 'un navegador',
    'com.microsoft.edgemac': 'un navegador',
    'company.thebrowser.': 'un navegador',
    'com.brave.Browser': 'un navegador',
    'com.operasoftware.': 'un navegador',
    'com.vivaldi.Vivaldi': 'un navegador',
}
# Nunca se manejan ni se miran: guardan secretos, cambian la seguridad del Mac, ejecutan
# scripts o son otros agentes (sus conversaciones son del usuario).
BLOQUEADAS = {
    'com.apple.ScriptEditor2': 'un editor de scripts',
    'com.apple.Automator': 'un editor de scripts',
    'com.apple.shortcuts': 'Atajos, que ejecuta acciones del sistema',
    'com.apple.keychainaccess': 'Acceso a Llaveros',
    'com.apple.Passwords': 'un gestor de contraseñas',
    'com.1password.': 'un gestor de contraseñas',
    'com.agilebits.': 'un gestor de contraseñas',
    'com.bitwarden.': 'un gestor de contraseñas',
    'com.lastpass.': 'un gestor de contraseñas',
    'com.apple.systempreferences': 'Ajustes del Sistema',
    'com.apple.SystemSettings': 'Ajustes del Sistema',
    'com.apple.securityagent': 'un diálogo de seguridad',
    'com.apple.LocalAuthentication': 'un diálogo de seguridad',
    'com.anthropic.': 'otro agente',
    'com.openai.': 'otro agente',
    'com.alfalfacode.app': 'AlfalfaCode',
}
# Por nombre, para las que se lanzan sin bundle (swift run AlfalfaCode).
NOMBRES_BLOQUEADOS = {'alfalfacode': 'AlfalfaCode', 'alfalfamac': 'AlfalfaCode', 'claude': 'otro agente',
                      'codex': 'otro agente', 'chatgpt': 'otro agente'}


def _por_bundle(tabla, bundle):
    for prefijo, motivo in tabla.items():
        if bundle == prefijo or (prefijo.endswith('.') and bundle.startswith(prefijo)):
            return motivo
    return None


def nivel(app):
    """(nivel, motivo): BLOQUEADA, LECTURA, CLIC o COMPLETA."""
    bundle = str(app.get('bundle') or '')
    nombre = str(app.get('nombre') or '').strip().lower()
    ejecutable = os.path.basename(str(app.get('ejecutable') or '')).lower()
    motivo = (_por_bundle(BLOQUEADAS, bundle) or NOMBRES_BLOQUEADOS.get(nombre)
              or NOMBRES_BLOQUEADOS.get(ejecutable))
    if motivo:
        return BLOQUEADA, motivo
    motivo = _por_bundle(SOLO_CLIC, bundle) or ('una terminal' if nombre == 'terminal' else None)
    if motivo:
        return CLIC, motivo
    motivo = _por_bundle(SOLO_LECTURA, bundle)
    if motivo:
        return LECTURA, motivo
    return COMPLETA, None


def bloqueo(app):
    """Por qué no se puede ni mirar esta app, o None."""
    grado, motivo = nivel(app)
    return motivo if grado == BLOQUEADA else None


def clave_de_app(app):
    identidad = app.get('bundle') or app.get('ejecutable') or app.get('nombre')
    return json.dumps(['app', str(identidad)], ensure_ascii=False, separators=(',', ':'))


def huella():
    return hashlib.sha256(FUENTE.read_bytes()).hexdigest()[:12]


class Ayudante:
    """El binario nativo, compilado una vez por versión de su fuente."""

    def __init__(self, hogar):
        self.carpeta = Path(hogar) / 'bin'
        self._candado = asyncio.Lock()

    @property
    def ruta(self):
        return self.carpeta / ('alfalfa-apps-' + huella())

    async def asegurar(self):
        async with self._candado:
            if self.ruta.is_file() and os.access(self.ruta, os.X_OK):
                return self.ruta
            swiftc = shutil.which('swiftc') or ('/usr/bin/swiftc' if os.path.exists('/usr/bin/swiftc') else None)
            if not swiftc:
                raise ValueError('El control de apps necesita swiftc (Xcode o sus herramientas de línea de órdenes).')
            self.carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporal = self.ruta.with_name(self.ruta.name + '.' + str(os.getpid()) + '.tmp')
            proc = await asyncio.create_subprocess_exec(
                swiftc, '-O', str(FUENTE), '-o', str(temporal), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            try:
                salida, _ = await asyncio.wait_for(proc.communicate(), COMPILAR_SEGUNDOS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ValueError('No se pudo compilar el ayudante de control de apps a tiempo.') from None
            if proc.returncode != 0 or not temporal.is_file():
                temporal.unlink(missing_ok=True)
                raise ValueError('No se pudo compilar el ayudante de control de apps: '
                                 + salida.decode(errors='replace')[-800:])
            os.chmod(temporal, 0o700)
            os.replace(temporal, self.ruta)
            for viejo in self.carpeta.glob('alfalfa-apps-*'):
                if viejo != self.ruta and not viejo.name.endswith('.tmp'):
                    viejo.unlink(missing_ok=True)
            return self.ruta

    async def llamar(self, orden, **argumentos):
        ruta = await self.asegurar()
        argv = [str(ruta), orden]
        for clave, valor in argumentos.items():
            if valor is True:
                argv.append('--' + clave)
            elif valor is not None and valor is not False:
                argv.append('--' + clave + '=' + str(valor))
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=True)
        try:
            salida, errores = await asyncio.wait_for(proc.communicate(), ORDEN_SEGUNDOS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ValueError('La app no respondió a tiempo (' + str(ORDEN_SEGUNDOS) + ' s); puede estar colgada.') from None
        try:
            datos = json.loads(salida.decode(errors='replace').strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise ValueError('Respuesta inválida del ayudante de apps: '
                             + (errores or salida).decode(errors='replace')[-300:]) from None
        if isinstance(datos, dict) and datos.get('error'):
            raise ValueError(str(datos['error']))
        return datos


class ControlDeApps:
    """Lo que hay detrás de app_list, app_state y app_action en una sesión."""

    def __init__(self, runtime, ayudante=None):
        self.runtime = runtime
        hogar = getattr(runtime.manager, 'home', None) or Path.home() / '.alfalfa'
        self.directorio = getattr(runtime, 'directory', None)
        self.ayudante = ayudante or Ayudante(hogar)
        # (pid, ventana) → {número: (ruta, rol)} y el árbol de la última lectura.
        self._elementos = {}
        self._filas = {}
        self._arboles = {}

    # --- apps

    async def _apps(self):
        datos = await self.ayudante.llamar('lista')
        return datos.get('apps') or [], datos.get('accesibilidad')

    async def lista(self):
        from ..permisos import sesion
        apps, accesibilidad = await self._apps()
        filas = []
        for app in apps:
            fila = {k: app[k] for k in ('nombre', 'bundle', 'pid', 'activa', 'oculta') if k in app}
            grado, motivo = nivel(app)
            if grado == BLOQUEADA:
                fila['bloqueada'] = 'No se maneja: es ' + motivo + '.'
            elif grado != COMPLETA:
                fila['nivel'] = grado
                fila['nota'] = DESCRIPCION_NIVEL[grado] + ' (es ' + motivo + ').'
            ventanas = app.get('ventanas') or []
            if sesion.aprobadas(self.runtime.task, [clave_de_app(app)]):
                fila['ventanas'] = ventanas
            else:
                fila['ventanas'] = [{k: v for k, v in v.items() if k != 'titulo'} for v in ventanas]
            filas.append(fila)
        resultado = {'apps': filas}
        if accesibilidad is False:
            resultado['aviso'] = ('Falta el permiso de Accesibilidad para la app que lanzó AlfalfaCode: el usuario '
                                  'lo da en Ajustes del Sistema → Privacidad y seguridad → Accesibilidad.')
        return resultado

    async def _resolver(self, nombre):
        apps, _ = await self._apps()
        texto = str(nombre or '').strip()
        if not texto:
            raise ValueError('Falta app: su nombre, su bundle id o su pid (de app_list).')
        bajo = texto.lower()
        exactas = [a for a in apps if str(a.get('pid')) == texto or str(a.get('bundle') or '').lower() == bajo
                   or str(a.get('nombre') or '').lower() == bajo]
        parecidas = exactas or [a for a in apps if bajo in str(a.get('nombre') or '').lower()]
        if not parecidas:
            raise ValueError('No hay ninguna app abierta que se llame «' + texto + '». Ábrela primero (por ejemplo '
                             'con run_command background: true o con open) y mira app_list.')
        if len(parecidas) > 1:
            raise ValueError('Hay varias apps que encajan con «' + texto + '»: '
                             + ', '.join(str(a.get('nombre')) + ' (pid ' + str(a.get('pid')) + ')' for a in parecidas[:8])
                             + '. Usa su bundle id o su pid.')
        app = parecidas[0]
        grado, motivo = nivel(app)
        if grado == BLOQUEADA:
            raise ValueError('AlfalfaCode no maneja ' + str(app.get('nombre')) + ': es ' + motivo
                             + '. No lo intentes por otra vía; si hace falta, pídeselo al usuario.')
        return app

    async def _permiso(self, app, que):
        from ..permisos import sesion
        clave = clave_de_app(app)
        if sesion.aprobadas(self.runtime.task, [clave]):
            return
        nombre = str(app.get('nombre') or app.get('bundle'))
        grado, motivo = nivel(app)
        detalle = {'nombre': nombre, 'bundle': app.get('bundle'), 'pid': app.get('pid'), 'accion': que}
        alcance = ('Puede ver su ventana (capturas) y usar su propio ratón y teclado en ella, sin mover los del '
                   'usuario.')
        if grado != COMPLETA:
            detalle['nivel'] = grado
            alcance = DESCRIPCION_NIVEL[grado] + ' porque es ' + motivo + '.'
        solicitud = {'argv': ['app', nombre], 'purpose': 'Controlar ' + nombre + ': ' + que + '. ' + alcance,
                     'motivo': MOTIVO, 'motivos': [MOTIVO], 'app': detalle, 'para_sesion': True}
        try:
            respuesta = await self.runtime.approve(solicitud)
        except ValueError:
            raise ValueError('El usuario no dejó controlar ' + nombre + '. No lo intentes por otra vía.') from None
        if respuesta == sesion.SESION:
            sesion.recordar(self.runtime.task, [clave])

    # --- estado

    def _carpeta_de_capturas(self):
        base = self.directorio
        carpeta = Path(base) / 'capturas' if base else Path(os.environ.get('TMPDIR', '/tmp')) / 'alfalfa-capturas'
        carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
        return carpeta

    async def _leer(self, app, ventana, captura=True, maximo=MAX_ELEMENTOS, zoom=None):
        ruta = None
        if captura or zoom:
            ruta = self._carpeta_de_capturas() / (str(app.get('pid')) + '-' + str(int(time.time() * 1000)) + '.png')
        datos = await self.ayudante.llamar('estado', pid=app['pid'], ventana=ventana, captura=ruta, max=maximo)
        clave = (app['pid'], datos.get('ventana', 0))
        self._elementos[clave] = {e['i']: (e.get('ruta', ''), e.get('rol')) for e in datos.get('elementos') or []
                                  if 'i' in e}
        self._filas[clave] = [e for e in datos.get('elementos') or [] if 'i' in e]
        arbol = str(datos.get('arbol') or '')
        self._arboles[clave] = arbol
        resultado = {'app': app.get('nombre'), 'pid': app.get('pid'), 'ventana': datos.get('ventana', 0),
                     'titulo': datos.get('titulo', ''), 'ancho': datos.get('ancho'), 'alto': datos.get('alto'),
                     'arbol': arbol[:MAX_SALIDA]}
        grado, _ = nivel(app)
        if grado != COMPLETA:
            resultado['nivel'] = grado
        if len(arbol) > MAX_SALIDA or datos.get('recortado'):
            resultado['recortado'] = True
        if datos.get('captura'):
            if zoom:
                await _recortar(Path(datos['captura']), datos.get('ancho'), zoom)
                resultado['zoom'] = {'x': zoom[0], 'y': zoom[1], 'ancho': zoom[2], 'alto': zoom[3],
                                     'nota': 'Recorte a la resolución completa de la pantalla.'}
            else:
                await _a_puntos(Path(datos['captura']), datos.get('ancho'))
            resultado['captura'] = datos['captura']
        elif datos.get('captura_error'):
            resultado['captura_error'] = datos['captura_error']
        return resultado

    async def estado(self, args):
        app = await self._resolver(args.get('app'))
        await self._permiso(app, 'mirar su ventana')
        ventana = _numero(args, 'window')
        maximo = _numero(args, 'max_elements') or MAX_ELEMENTOS
        zoom = _zoom(args.get('zoom'))
        esperar = args.get('wait_for')
        if isinstance(esperar, str) and esperar.strip():
            # Mejor que mirar una y otra vez: se espera a que el texto salga en la ventana.
            plazo = min(float(_numero(args, 'timeout_seconds') or 10), MAX_ESPERA_TEXTO)
            fin = time.monotonic() + plazo
            while True:
                previo = await self._leer(app, ventana, captura=False, maximo=maximo)
                if esperar.strip().lower() in previo['arbol'].lower():
                    break
                if time.monotonic() >= fin:
                    previo['espera'] = ('«' + esperar.strip() + '» no apareció en ' + str(int(plazo)) + ' s.')
                    break
                await asyncio.sleep(0.4)
        resultado = await self._leer(app, ventana, maximo=maximo, zoom=zoom)
        if isinstance(esperar, str) and esperar.strip():
            if esperar.strip().lower() in resultado['arbol'].lower():
                resultado['espera'] = '«' + esperar.strip() + '» ya aparece.'
            else:
                resultado['espera'] = previo.get('espera') or '«' + esperar.strip() + '» no apareció.'
        await self._mirar(resultado, args.get('question'))
        resultado['nota'] = ('Los números [i] del árbol sirven como element en app_action; target busca por texto. '
                             'x e y son puntos desde la esquina superior izquierda de la ventana; la captura está a '
                             'esa misma escala.')
        return resultado

    async def _mirar(self, resultado, pregunta):
        if not pregunta or not resultado.get('captura'):
            return
        try:
            resultado['respuesta'] = await self.runtime.vision(Path(resultado['captura']), str(pregunta)[:2000])
        except Exception as exc:  # la visión es opcional: el árbol ya está
            resultado['respuesta_error'] = str(exc)[:300] or type(exc).__name__

    # --- acciones

    async def accion(self, args):
        pasos = args.get('steps')
        if pasos is not None:
            return await self._lote(args, pasos)
        if not args.get('action'):
            raise ValueError('Falta action (o steps, una lista de acciones).')
        app = await self._resolver(args.get('app'))
        return await self._una(app, args, final=True)

    async def _lote(self, args, pasos):
        """Varias acciones seguidas en una llamada; para en la primera que falla."""
        if not isinstance(pasos, list) or not pasos or len(pasos) > MAX_PASOS:
            raise ValueError('steps debe ser una lista de 1 a ' + str(MAX_PASOS) + ' acciones.')
        app = await self._resolver(args.get('app'))
        comunes = {k: v for k, v in args.items() if k in ('window', 'real_mouse')}
        hechos = []
        ultimo = None
        for n, paso in enumerate(pasos, 1):
            if not isinstance(paso, dict) or 'steps' in paso:
                raise ValueError('Cada paso es un objeto con su action (sin steps dentro).')
            final = n == len(pasos)
            datos = dict(comunes)
            datos.update(paso)
            datos['app'] = args.get('app')
            if final:
                datos.setdefault('screenshot', args.get('screenshot'))
                datos.setdefault('question', args.get('question'))
            try:
                ultimo = await self._una(app, datos, final=final)
            except ValueError as exc:
                resultado = {'hechos': hechos, 'fallo': {'paso': n, 'action': paso.get('action'), 'error': str(exc)},
                             'nota': 'Se pararon los pasos en el ' + str(n) + '; los anteriores sí se hicieron.'}
                try:
                    resultado.update(await self._leer(app, _numero(args, 'window') or 0, captura=False))
                except ValueError:
                    pass
                return resultado
            hechos.append({'paso': n, 'action': paso.get('action'), 'como': ultimo.get('como')})
            espera = paso.get('pause_seconds')
            if isinstance(espera, (int, float)) and not isinstance(espera, bool) and 0 < espera <= 5:
                await asyncio.sleep(float(espera))
        ultimo['hechos'] = hechos
        return ultimo

    async def _objetivo(self, app, ventana, args):
        """El número de elemento: el dado, o el que tiene ese texto (target), leído al momento."""
        elemento = _numero(args, 'element')
        objetivo = args.get('target')
        if elemento is not None or not isinstance(objetivo, str) or not objetivo.strip():
            return elemento
        await self._leer(app, ventana, captura=False)
        filas = self._filas.get((app['pid'], ventana)) or []
        buscado = objetivo.strip().lower()
        def textos(fila):
            return [str(fila.get(k) or '').strip().lower() for k in ('titulo', 'descripcion', 'valor', 'id', 'ayuda')]
        exactas = [f for f in filas if buscado in textos(f)]
        candidatas = exactas or [f for f in filas if any(buscado in t for t in textos(f) if t)]
        # Entre iguales, el que se puede pulsar (un botón antes que su texto).
        pulsables = [f for f in candidatas if f.get('acciones')]
        candidatas = pulsables or candidatas
        if not candidatas:
            raise ValueError('No hay ningún elemento con el texto «' + objetivo.strip() + '» en la ventana. Mírala '
                             'con app_state.')
        if len(candidatas) > 1:
            lista = '; '.join('[' + str(f['i']) + '] ' + str(f.get('rol', '')).replace('AX', '') + ' '
                              + str(f.get('titulo') or f.get('descripcion') or f.get('valor') or '')[:40]
                              for f in candidatas[:6])
            raise ValueError('Varios elementos tienen «' + objetivo.strip() + '»: ' + lista + '. Usa element.')
        return candidatas[0]['i']

    async def _una(self, app, args, final=True):
        accion = args.get('action')
        if accion not in ACCIONES:
            raise ValueError('action debe ser una de: ' + ', '.join(ACCIONES) + '.')
        grado, motivo = nivel(app)
        permitidas = ACCIONES_POR_NIVEL.get(grado)
        if permitidas is not None and accion not in permitidas:
            raise ValueError(str(app.get('nombre')) + ' es ' + motivo + ': ' + DESCRIPCION_NIVEL[grado].lower()
                             + '. ' + accion + ' no se permite ahí.')
        await self._permiso(app, _NOMBRE_ACCION[accion])
        ventana = _numero(args, 'window') or 0
        parametros = {'pid': app['pid'], 'ventana': ventana, 'tipo': _TIPO[accion]}
        if accion == 'menu':
            ruta = args.get('menu') or args.get('text')
            if not isinstance(ruta, str) or not ruta.strip():
                raise ValueError('menu necesita menu, la ruta en la barra de menús: "Archivo > Guardar".')
            parametros['texto'] = ruta
        elemento = await self._objetivo(app, ventana, args) if accion != 'menu' else None
        if elemento is not None:
            conocidos = self._elementos.get((app['pid'], ventana))
            if not conocidos:
                raise ValueError('Mira antes la ventana con app_state: element es un número de su árbol.')
            if elemento not in conocidos:
                raise ValueError('No hay un elemento ' + str(elemento) + ' en la última lectura de la ventana.')
            parametros['ruta'], parametros['rol'] = conocidos[elemento]
        for clave in ('x', 'y', 'x2', 'y2', 'dx', 'dy'):
            valor = _numero(args, clave)
            if valor is not None:
                parametros[clave] = valor
        if accion in ('type', 'set_value'):
            if not isinstance(args.get('text'), str):
                raise ValueError(accion + ' necesita text.')
            parametros['texto'] = args['text']
        if accion == 'key':
            if not isinstance(args.get('key'), str) or not args['key'].strip():
                raise ValueError('key necesita key, por ejemplo "return", "tab", "cmd+s" o "shift+tab".')
            parametros['tecla'] = args['key']
        if accion in ('press', 'set_value') and elemento is None:
            raise ValueError(accion + ' necesita element o target.')
        if accion in ('click', 'double_click', 'right_click', 'scroll', 'drag', 'hover') and elemento is None and (
                'x' not in parametros or 'y' not in parametros):
            raise ValueError(accion + ' necesita element, target o x e y.')
        if accion == 'drag' and ('x2' not in parametros or 'y2' not in parametros):
            raise ValueError('drag necesita x2 e y2, el punto donde se suelta.')
        if accion == 'scroll' and 'dy' not in parametros and 'dx' not in parametros:
            raise ValueError('scroll necesita dy (positivo baja) o dx.')
        if accion == 'press' and isinstance(args.get('ax_action'), str):
            parametros['accion'] = args['ax_action']
        if args.get('real_mouse') is True:
            parametros['raton'] = True
        if os.environ.get('ALFALFA_SIN_CURSOR') == '1':
            parametros['sin-cursor'] = True
        antes = self._arboles.get((app['pid'], ventana))
        hecho = await self.ayudante.llamar('accion', **parametros)
        self.runtime.manager.event(self.runtime.task, 'system', 'App ' + str(app.get('nombre')) + ': '
                                   + _NOMBRE_ACCION[accion] + _resumen(dict(args, element=elemento)))
        if not final:
            # En un lote sólo se vuelve a mirar al final; aquí basta con el árbol para el siguiente paso.
            despues = await self._leer(app, ventana, captura=False)
            despues['como'] = hecho.get('como')
            return despues
        # Como mejora sobre Codex: la ventana ya cambiada, para comprobar el efecto sin otra llamada.
        try:
            despues = await self._leer(app, ventana, captura=args.get('screenshot') is True or bool(args.get('question')))
        except ValueError as exc:
            despues = {'aviso': 'Hecho, pero no se pudo volver a leer la ventana: ' + str(exc)}
        await self._mirar(despues, args.get('question'))
        if antes is not None and 'arbol' in despues:
            despues['cambios'] = cambios(antes, despues['arbol'])
            if not despues['cambios']['aparece'] and not despues['cambios']['desaparece']:
                despues['nota'] = ('La ventana no cambió. Si esperabas un efecto, compruébalo con question o '
                                   'screenshot; si la app ignoró el evento en fondo, prueba real_mouse: true.')
        despues['hecho'] = True
        if hecho.get('como'):
            despues['como'] = hecho['como']
        return despues


def cambios(antes, despues, maximo=40):
    """Qué líneas del árbol aparecen y desaparecen, sin contar los números [i], que se mueven."""
    def lineas(arbol):
        return [re.sub(r'^\s*\[\d+\]\s*', '', linea) for linea in arbol.splitlines() if linea.strip()]
    previas, nuevas = lineas(antes), lineas(despues)
    quedan = list(previas)
    aparece = []
    for linea in nuevas:
        if linea in quedan:
            quedan.remove(linea)
        else:
            aparece.append(linea)
    return {'aparece': aparece[:maximo], 'desaparece': quedan[:maximo]}


def _zoom(valor):
    if valor is None:
        return None
    if isinstance(valor, dict):
        partes = [valor.get(k) for k in ('x', 'y', 'width', 'height')]
    elif isinstance(valor, (list, tuple)):
        partes = list(valor)
    else:
        raise ValueError('zoom es {x, y, width, height} en puntos de la ventana.')
    try:
        x, y, ancho, alto = (int(float(v)) for v in partes)
    except (TypeError, ValueError):
        raise ValueError('zoom es {x, y, width, height} en puntos de la ventana.') from None
    if ancho <= 0 or alto <= 0 or x < 0 or y < 0:
        raise ValueError('zoom necesita x e y desde 0 y un ancho y alto positivos.')
    return x, y, ancho, alto


def _numero(args, clave):
    valor = args.get(clave)
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    try:
        return int(float(str(valor).strip()))
    except ValueError:
        raise ValueError(clave + ' debe ser un número.') from None


def _resumen(args):
    partes = []
    if args.get('element') is not None:
        partes.append('elemento ' + str(args['element']))
    if args.get('x') is not None and args.get('y') is not None:
        partes.append('(' + str(args['x']) + ', ' + str(args['y']) + ')')
    if args.get('key'):
        partes.append(str(args['key']))
    if isinstance(args.get('text'), str):
        partes.append('«' + args['text'][:60] + ('…' if len(args['text']) > 60 else '') + '»')
    return (' · ' + ' '.join(partes)) if partes else ''


async def _recortar(ruta, ancho, zoom):
    """Recorta la zona pedida a la resolución completa (sin reducir a puntos), para leer detalles."""
    if not shutil.which('sips'):
        return
    proc = await asyncio.create_subprocess_exec('sips', '-g', 'pixelWidth', str(ruta), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    salida, _ = await proc.communicate()
    encontrado = re.search(rb'pixelWidth: (\d+)', salida)
    escala = int(encontrado.group(1)) / ancho if encontrado and isinstance(ancho, int) and ancho > 0 else 1
    x, y, w, h = (int(v * escala) for v in zoom)
    proc = await asyncio.create_subprocess_exec('sips', '-c', str(h), str(w), '--cropOffset', str(y), str(x), str(ruta),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), 20)
    except asyncio.TimeoutError:
        proc.kill()


async def _a_puntos(ruta, ancho):
    """La captura sale en píxeles de pantalla (el doble en Retina): se deja en puntos."""
    if not isinstance(ancho, int) or ancho <= 0 or not shutil.which('sips'):
        return
    proc = await asyncio.create_subprocess_exec('sips', '--resampleWidth', str(ancho), str(ruta),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), 20)
    except asyncio.TimeoutError:
        proc.kill()
