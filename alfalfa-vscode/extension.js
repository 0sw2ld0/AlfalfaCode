'use strict';
const vscode = require('vscode');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { ClientePacai } = require('./lib/pacai');
const { buscarAlfalfa, entorno, LUGARES } = require('./lib/ejecutable');
const { listarSesiones, leerHistorial } = require('./lib/sesiones');

// El mismo chat a los dos lados, como Claude Code: en la barra de actividad de la izquierda y, donde el editor la
// admite para extensiones (VS Code 1.106+, no Cursor), en la barra lateral secundaria de la derecha.
const VISTA_IZQUIERDA = 'alfalfa.chat';
const VISTA_DERECHA = 'alfalfa.chatDerecha';
const SIN_SECUNDARIA = 'alfalfa.sinBarraSecundaria';

function admiteBarraSecundaria() {
    if (/cursor/i.test(vscode.env.appName)) return false;
    const [mayor, menor] = vscode.version.split('.').map(Number);
    return mayor > 1 || (mayor === 1 && menor >= 106);
}

function vista() {
    return admiteBarraSecundaria() ? VISTA_DERECHA : VISTA_IZQUIERDA;
}
const CLAVE_ULTIMA = 'alfalfa.ultimaSesion';
// Estados de `--sesiones` cuando otro proceso tiene la sesión abierta con un turno en marcha.
const TRABAJANDO = new Set(['running', 'waiting', 'starting']);
const CLAVE_MODOS = 'alfalfa.modos';
const { CLAVE_HEREDADA, yaSeUsaba, vistaEnriquecida } = require('./lib/vista');
// Los esfuerzos que acepta `alfalfa` (terminal.EFFORTS); un modelo puede anunciar otros (`none`, `minimal`).
const ESFUERZOS = ['low', 'medium', 'high', 'xhigh', 'max'];

/** Guarda un ajuste donde ya esté definido (la carpeta manda sobre el usuario); si no, en el del usuario. */
async function guardarAjuste(clave, valor) {
    const ajustes = vscode.workspace.getConfiguration('alfalfa');
    const donde = ajustes.inspect(clave);
    const destino = donde?.workspaceFolderValue !== undefined ? vscode.ConfigurationTarget.WorkspaceFolder
        : donde?.workspaceValue !== undefined ? vscode.ConfigurationTarget.Workspace
            : vscode.ConfigurationTarget.Global;
    await ajustes.update(clave, valor, destino);
}

/** `alfalfa --modos --json`: los modos del motor con su id de PACAI. null si este `alfalfa` no lo admite (anterior). */
/** «hace 5 min», «ayer»… de una fecha en segundos. */
function haceCuanto(segundos) {
    if (!segundos) return '';
    const s = Math.max(0, Date.now() / 1000 - segundos);
    if (s < 60) return 'hace un momento';
    if (s < 3600) return `hace ${Math.round(s / 60)} min`;
    if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
    if (s < 2 * 86400) return 'ayer';
    if (s < 30 * 86400) return `hace ${Math.round(s / 86400)} días`;
    return new Date(segundos * 1000).toLocaleDateString();
}

function listarModos(ruta) {
    return new Promise(resolver => {
        execFile(ruta, ['--modos', '--json'], { env: entorno(), timeout: 15000 }, (error, salida) => {
            if (error) return resolver(null);
            try {
                const modos = (JSON.parse(salida).modos || [])
                    .filter(m => m.pacai && m.nombre)
                    .map(m => ({ mode: m.pacai, name: m.nombre, summary: m.resumen || '', ...(m.confirmar && { confirm: true }) }));
                resolver(modos.length ? modos : null);
            } catch {
                resolver(null);
            }
        });
    });
}

/** `alfalfa --modelos --json`: los modelos con herramientas, con su proveedor y los esfuerzos válidos. */
function listarModelos(ruta) {
    return new Promise(resolver => {
        execFile(ruta, ['--modelos', '--json'], { env: entorno(), timeout: 30000, maxBuffer: 16 * 1024 * 1024 },
            (error, salida) => {
                if (error) return resolver({ error: error.message.split('\n')[0] });
                try {
                    const modelos = (JSON.parse(salida).modelos || [])
                        .filter(m => m.herramientas !== false && m.name)
                        .map(m => ({ nombre: m.name, proveedor: m.proveedor || 'otros',
                            esfuerzos: (m.esfuerzos || []).filter(e => ESFUERZOS.includes(e)) }));
                    resolver({ modelos });
                } catch (e) {
                    resolver({ error: 'No pude leer la lista de modelos: ' + e.message });
                }
            });
    });
}

// La hoja trifoliada del icono de AlfalfaCode (alfalfa-mac/Design/AppIcon.svg), sin el fondo. Cada foliolo lleva su
// clase para que el panel los haga respirar mientras trabaja, como el indicador de la app de Mac (LeafMotion).
function hoja(clase) {
    return `<svg class="hoja ${clase}" viewBox="250 250 524 510" aria-hidden="true">
  <defs><linearGradient id="g-${clase}" x1="372" y1="270" x2="626" y2="603" gradientUnits="userSpaceOnUse">
    <stop stop-color="#A2DB9C"/><stop offset="0.32" stop-color="#8FD08F"/><stop offset="0.72" stop-color="#6FBF73"/><stop offset="1" stop-color="#55A761"/>
  </linearGradient></defs>
  <path class="tallo" d="M481 741C516 676 520 584 512 473M514 613L433 548M515 613L591 548" stroke="#6FBF73" stroke-width="32" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <g fill="url(#g-${clase})">
    <path class="f f0" transform="translate(-36 24)" d="M486 544C377 565 287 491 282 407C280 377 300 356 328 362C335 337 365 335 390 352C469 390 508 465 486 544Z"/>
    <path class="f f1" d="M512 499C449 450 417 356 450 296C465 268 491 266 512 278C533 266 559 268 574 296C607 356 575 450 512 499Z"/>
    <path class="f f2" transform="translate(36 24)" d="M538 544C647 565 737 491 742 407C744 377 724 356 696 362C689 337 659 335 634 352C555 390 516 465 538 544Z"/>
  </g>
</svg>`;
}

const svg = cuerpo => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${cuerpo}</svg>`;
const ICONOS = {
    reloj: svg('<circle cx="8" cy="8" r="6"/><path d="M8 4.8V8l2.2 1.4"/>'),
    nueva: svg('<path d="M8 3.5v9M3.5 8h9"/>'),
    flecha: svg('<path d="M8 12.5v-9M4 7.5l4-4 4 4"/>'),
    parar: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor"/></svg>',
    archivo: svg('<path d="M4 2h5l3 3v9H4z"/><path d="M9 2v3h3"/>'),
    cerrar: svg('<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>'),
    reset: svg('<path d="M3 8a5 5 0 1 0 1.5-3.5"/><path d="M3 2.5V5h2.5"/>'),
    medidor: svg('<path d="M2.5 11.5a5.5 5.5 0 1 1 11 0"/><path d="M8 11.5l2.6-3.4"/>'),
    abajo: svg('<path d="M5 6.5l3 3 3-3"/>'),
    escudo: svg('<path d="M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z"/>'),
};

/** El chat: una o dos webviews (izquierda y derecha) que muestran la misma sesión de `alfalfa --pacai`. */
class ProveedorChat {
    constructor(contexto) {
        this.contexto = contexto;
        this.vistas = new Set();     // las webviews abiertas; todas reciben lo mismo
        this.cliente = null;
        this.historial = [];        // lo enviado a la webview, para repintarla si se recrea
        this.sesionAlfalfa = null;  // id de la sesión de AlfalfaCode (`alfalfa.session`)
        this.haySesiones = false;   // ¿hay sesiones guardadas de esta carpeta (de aquí o de la app de Mac)?
        this.salida = vscode.window.createOutputChannel('AlfalfaCode', { log: true });
    }

    resolveWebviewView(vista) {
        this.vistas.add(vista);
        const media = vscode.Uri.joinPath(this.contexto.extensionUri, 'media');
        // Primero las opciones y después el HTML, el orden que esperan VS Code y sus derivados (Cursor): con el
        // orden contrario, un editor que no vuelve a pintar al cambiar las opciones deja el panel sin scripts.
        vista.webview.options = { enableScripts: true, localResourceRoots: [media] };
        vista.webview.html = this._html(vista.webview, media);
        vista.webview.onDidReceiveMessage(m => this._deLaVista(m, vista).catch(error => this._aviso('error', error.message)));
        vista.onDidDispose(() => this.vistas.delete(vista));
    }

    // -- webview → extensión ------------------------------------------------
    async _deLaVista(m, vista) {
        switch (m.tipo) {
            case 'listo': {
                // Sólo a la que acaba de cargarse: la otra ya lo tiene pintado.
                const enviar = x => vista.webview.postMessage(x);
                enviar({ tipo: 'reinicio' });
                for (const anterior of this.historial) enviar(anterior);
                enviar(this._config());
                enviar(archivoActivo());
                this._modelos().then(lista => vista.webview.postMessage({ tipo: 'modelos', ...lista }));
                // Los modos, antes de abrir sesión: así uno nuevo del motor (Turbo…) sale ya en el menú.
                this._modos().then(modos => { if (modos) vista.webview.postMessage({ tipo: 'modos', modos }); });
                this._comprobarSesiones();
                break;
            }
            case 'enviar':
                await this.enviar(m.texto, { encauzar: m.encauzar, archivo: m.archivo });
                break;
            case 'aprobar':
                if (!this.cliente) break;
                this.cliente.aprobar(m.id, m.alcance === 'session' ? 'session' : 'once');
                // La otra vista también tiene la tarjeta abierta: se cierra en las dos.
                this._post({ tipo: 'resuelta', id: m.id,
                    texto: m.alcance === 'session' ? 'Permitido en esta sesión' : 'Permitido' });
                break;
            case 'denegar':
                if (!this.cliente) break;
                this.cliente.denegar(m.id, m.motivo || 'Denegado desde el editor.');
                this._post({ tipo: 'resuelta', id: m.id, texto: 'Denegado' });
                break;
            case 'detener':
                this.detener();
                break;
            case 'nueva':
                this.nuevaSesion();
                break;
            case 'reanudar':
                await this.reanudar(m.id);
                break;
            case 'modo': {
                // Un modo que se confirma (Omitir permisos) pregunta antes, como la app de Mac.
                if (m.confirmar) {
                    const si = await vscode.window.showWarningMessage(`¿Activar «${m.nombre || m.modo}»?`,
                        { modal: true, detail: m.resumen || 'AlfalfaCode dejará de pedir permiso.' }, 'Habilitar');
                    if (si !== 'Habilitar') {
                        this._post(this._config(), false);
                        break;
                    }
                }
                await guardarAjuste('modo', m.modo);
                this.cliente?.ajustes({ modo: m.modo });
                if (this.cliente?.ocupado) this._aviso('info', 'El cambio de modo vale desde el próximo turno.');
                break;
            }
            case 'modelo': {
                // Vacío = el predeterminado de AlfalfaCode: vale para la próxima sesión, no para la abierta.
                await guardarAjuste('modelo', m.modelo || '');
                if (m.esfuerzo !== undefined) await guardarAjuste('esfuerzo', m.esfuerzo || '');
                const cambio = {};
                if (m.modelo) cambio.modelo = m.modelo;
                if (m.esfuerzo) cambio.esfuerzo = m.esfuerzo;
                if (this.cliente && !this.cliente.cerrado && Object.keys(cambio).length) {
                    this.cliente.ajustes(cambio);
                    if (this.cliente.ocupado) this._aviso('info', 'El cambio de modelo vale desde el próximo turno.');
                } else if (this.cliente && !m.modelo) {
                    this._aviso('info', 'El modelo predeterminado se usará en la próxima sesión (＋).');
                }
                this._post(this._config(), false);
                break;
            }
            case 'verParche':
                await this._verParche(m.parche, m.titulo);
                break;
            case 'abrirArchivo':
                await this._abrirArchivo(m.ruta);
                break;
            case 'registro':
                this.salida.show(true);
                break;
            case 'accionPantalla':
                await this.accionDePantalla(m.data, m.resumen);
                break;
            case 'abrirEnlace':
                await this._abrirEnlace(m.url);
                break;
        }
    }

    // -- acciones -------------------------------------------------------------
    async enviar(texto, { encauzar = false, archivo } = {}) {
        texto = String(texto || '').trim();
        if (!texto) return;
        if (!this.cliente || this.cliente.cerrado) {
            if (!(await this._arrancar())) return;
        }
        // El archivo abierto va como referencia al principio del mensaje (no su contenido: el agente lo lee si
        // le hace falta), como el chip de archivo de Claude Code.
        const ref = archivo?.ruta ? `[Archivo abierto en el editor: ${archivo.ruta}${archivo.lineas ? ', líneas ' + archivo.lineas : ''}]\n\n` : '';
        const id = this.cliente.enviarTexto(ref + texto, { encauzar: encauzar && this.cliente.ocupado });
        this._post({ tipo: 'usuario', id, texto, archivo: archivo?.ruta ? archivo : undefined,
            hora: Date.now(), encauzado: encauzar && this.cliente.ocupado });
    }

    /** Un botón o formulario de una pantalla del agente (perfil `ui`): va como mensaje del usuario. */
    async accionDePantalla(data, resumen) {
        if (!this.cliente || this.cliente.cerrado || !data || typeof data !== 'object') return;
        const texto = String(resumen || data.name || '').slice(0, 500);
        const id = this.cliente.accionDePantalla(data, texto);
        this._post({ tipo: 'usuario', id, texto, hora: Date.now(), accion: true });
    }

    detener() {
        if (this.cliente?.interrumpir()) this._aviso('info', 'Deteniendo el turno…');
    }

    nuevaSesion() {
        this._parar();
        this.historial = [];
        this.sesionAlfalfa = null;
        this._post({ tipo: 'reinicio' }, false);
        this._post(this._config(), false);
    }

    /**
     * Reanuda una sesión guardada de esta carpeta: la de `id` o la que se elija en la lista. La lista es la del
     * motor, así que trae también las sesiones abiertas desde la app de Mac. El panel pinta su conversación.
     */
    async reanudar(id) {
        const carpeta = vscode.workspace.workspaceFolders?.[0];
        const ruta = await buscarAlfalfa(vscode.workspace.getConfiguration('alfalfa').get('ejecutable'));
        if (!ruta) return this.sinAlfalfa();
        if (!carpeta || carpeta.uri.scheme !== 'file') {
            this._aviso('error', 'Abre una carpeta local: AlfalfaCode trabaja dentro de ella.');
            return;
        }
        if (!id) {
            const elegida = await this._elegirSesion(ruta, carpeta.uri.fsPath);
            if (!elegida) return;
            id = elegida.id;
        }
        if (id === this.sesionAlfalfa && this.cliente && !this.cliente.cerrado) return;
        const historial = await leerHistorial(ruta, id, entorno());
        this.nuevaSesion();
        if (historial) this._post({ tipo: 'historial', ...historial });
        else this._aviso('info', 'No pude leer la conversación guardada; la sesión sigue igualmente.');
        if (await this._arrancar({ reanudar: id })) {
            this._aviso('info', 'Sesión reanudada. Escribe para seguir.');
        }
    }

    async _elegirSesion(ruta, carpeta) {
        const sesiones = await listarSesiones(ruta, carpeta, entorno());
        if (sesiones === null) {
            vscode.window.showErrorMessage('AlfalfaCode no pudo listar las sesiones guardadas.');
            return null;
        }
        if (!sesiones.length) {
            vscode.window.showInformationMessage('No hay sesiones guardadas en esta carpeta.');
            return null;
        }
        const ultima = this.contexto.workspaceState.get(CLAVE_ULTIMA);
        const elementos = sesiones.map(s => {
            const titulo = String(s.titulo || s.encargo || s.id).split('\n')[0].trim();
            const turnos = s.turnos === 1 ? '1 turno' : `${s.turnos || 0} turnos`;
            const detalle = [haceCuanto(s.fecha), turnos];
            if (TRABAJANDO.has(s.estado)) detalle.push('trabajando en otro sitio');
            if (s.id === this.sesionAlfalfa) detalle.push('abierta aquí');
            else if (s.id === ultima) detalle.push('la última de esta ventana');
            return {
                label: titulo.length > 90 ? titulo.slice(0, 89) + '…' : titulo,
                description: s.modelo || '', detail: detalle.join(' · '), sesion: s,
            };
        });
        const elegido = await vscode.window.showQuickPick(elementos, {
            title: 'Reanudar una sesión de AlfalfaCode', placeHolder: 'Las de esta carpeta, también las de la app de Mac',
            matchOnDescription: true, matchOnDetail: true,
        });
        if (elegido && TRABAJANDO.has(elegido.sesion.estado) && elegido.sesion.id !== this.sesionAlfalfa) {
            // Como la app: dos procesos sobre la misma sesión se pisarían. Se puede reanudar cuando termine.
            vscode.window.showWarningMessage('Esa sesión está trabajando ahora en otro sitio (la app de Mac u otra '
                + 'ventana). Reanúdala cuando termine.');
            return null;
        }
        return elegido?.sesion || null;
    }

    /** ¿Hay sesiones guardadas en esta carpeta? Para enseñar el botón de reanudar aunque no sean de esta ventana. */
    async _comprobarSesiones() {
        const carpeta = vscode.workspace.workspaceFolders?.[0];
        if (!carpeta || carpeta.uri.scheme !== 'file') return;
        const ruta = await buscarAlfalfa(vscode.workspace.getConfiguration('alfalfa').get('ejecutable'));
        const sesiones = ruta ? await listarSesiones(ruta, carpeta.uri.fsPath, entorno()) : null;
        const hay = Boolean(sesiones?.length);
        if (hay !== this.haySesiones) {
            this.haySesiones = hay;
            this._post(this._config(), false);
        }
    }

    /** Pone texto en el cuadro de mensaje y enfoca el panel (p. ej. la selección del editor). */
    async insertar(texto) {
        const id = vista();
        await vscode.commands.executeCommand(id + '.focus');
        // Al borrador de la vista que se enfoca, no a las dos.
        for (const v of this.vistas) if (v.viewType === id) v.webview.postMessage({ tipo: 'insertar', texto });
    }

    dispose() {
        this._parar();
        this.salida.dispose();
    }

    /** No aparece `alfalfa`: lo dice, dónde se buscó, y ofrece elegirlo a mano o ver cómo instalarlo. */
    async sinAlfalfa() {
        const texto = 'No encuentro AlfalfaCode. Busqué en ' + LUGARES.join(', ') + '.';
        this._aviso('error', texto + ' Instala la app o la orden `alfalfa`, o elige el ejecutable.');
        const eleccion = await vscode.window.showErrorMessage(texto, 'Elegir ejecutable…', 'Cómo instalar');
        if (eleccion === 'Elegir ejecutable…') await this.elegirEjecutable();
        if (eleccion === 'Cómo instalar') vscode.env.openExternal(vscode.Uri.parse('https://alfalfacode.cuycoders.dev'));
    }

    /** Elegir `alfalfa` o la app AlfalfaCode (se usa el motor que lleva dentro) y guardarlo en el ajuste. */
    async elegirEjecutable() {
        const elegidos = await vscode.window.showOpenDialog({
            canSelectFiles: true, canSelectFolders: false, canSelectMany: false,
            defaultUri: vscode.Uri.file('/Applications'), openLabel: 'Usar este',
            title: 'Elige la app AlfalfaCode o la orden alfalfa',
        });
        if (!elegidos?.length) return;
        let ruta = elegidos[0].fsPath;
        if (ruta.endsWith('.app')) ruta = path.join(ruta, 'Contents/Resources/motor/alfalfa');
        if (!(await buscarAlfalfa(ruta))) {
            vscode.window.showErrorMessage('Ahí no hay un `alfalfa` que se pueda ejecutar: ' + ruta);
            return;
        }
        await vscode.workspace.getConfiguration('alfalfa').update('ejecutable', ruta, vscode.ConfigurationTarget.Global);
        this._aviso('info', 'Usaré ' + ruta + '.');
    }

    /** Vuelve a pedir modelos y modos (cambió el ejecutable) y se los manda a las vistas abiertas. */
    refrescarListas() {
        this.modelos = null;
        this.modos = null;
        this._modelos().then(lista => this._post({ tipo: 'modelos', ...lista }, false));
        this._modos().then(modos => { if (modos) this._post({ tipo: 'modos', modos }, false); });
    }

    // -- proceso ----------------------------------------------------------------
    async _arrancar({ reanudar } = {}) {
        const carpeta = vscode.workspace.workspaceFolders?.[0];
        if (!carpeta || carpeta.uri.scheme !== 'file') {
            this._aviso('error', 'Abre una carpeta local: AlfalfaCode trabaja dentro de ella.');
            return false;
        }
        const ajustes = vscode.workspace.getConfiguration('alfalfa');
        const ruta = await buscarAlfalfa(ajustes.get('ejecutable'));
        if (!ruta) {
            this.sinAlfalfa();
            return false;
        }
        const cliente = new ClientePacai({
            ejecutable: ruta, cwd: carpeta.uri.fsPath, env: entorno(), reanudar,
            modo: ajustes.get('modo') || undefined, modelo: ajustes.get('modelo') || undefined,
            esfuerzo: ajustes.get('esfuerzo') || undefined,
            version: this.contexto.extension.packageJSON.version,
        });
        this.salida.info(`Lanzando ${ruta} --pacai en ${carpeta.uri.fsPath}`);
        cliente.on('mensaje', m => this._delAgente(cliente, m));
        cliente.on('enviado', m => this.salida.trace('→ ' + JSON.stringify(m)));
        cliente.on('estado', e => { if (this.cliente === cliente) this._post({ tipo: 'estado', ...e }, false); });
        cliente.on('fallo', error => {
            this.salida.error(error.message);
            if (this.cliente === cliente) this._aviso('error', error.message);
        });
        cliente.on('salida', ({ codigo, senal, stderr }) => {
            if (stderr) this.salida.info(stderr.trim());
            if (this.cliente !== cliente) return;
            if (codigo !== 0 && !cliente.cerradoAdrede) {
                const ultima = stderr.trim().split('\n').slice(-3).join('\n');
                this._aviso('error', `AlfalfaCode terminó (${senal ?? 'código ' + codigo}).`
                    + (ultima ? '\n' + ultima : ''));
            }
            this.cliente = null;
            this._post({ tipo: 'estado', ocupado: false, vivo: false }, false);
        });
        this.cliente = cliente;
        try {
            cliente.iniciar();
        } catch (error) {
            this._aviso('error', error.message);
            this.cliente = null;
            return false;
        }
        return true;
    }

    _parar() {
        if (!this.cliente) return;
        this.cliente.cerradoAdrede = true;
        this.cliente.cerrar();
        this.cliente = null;
    }

    _delAgente(cliente, m) {
        if (this.cliente !== cliente) return;
        this.salida.trace('← ' + JSON.stringify(m).slice(0, 2000));
        if (m.type === 'alfalfa.session' && m.payload?.id) {
            this.sesionAlfalfa = m.payload.id;
            this.contexto.workspaceState.update(CLAVE_ULTIMA, m.payload.id);
            // Los modos que anuncia el motor: se recuerdan para pintarlos antes de abrir la próxima sesión.
            if (Array.isArray(m.payload.modes) && m.payload.modes.length) {
                this.contexto.globalState.update(CLAVE_MODOS, m.payload.modes);
            }
            // El motor anuncia la «Vista enriquecida» (`rich_view`): si no es la del ajuste, se le manda la del ajuste.
            const vista = this.vistaEnriquecida();
            if (typeof m.payload.rich_view === 'boolean' && m.payload.rich_view !== vista) cliente.vistaEnriquecida(vista);
        }
        // `alfalfa.context`, `alfalfa.awake`… no hacen falta en el panel y llenarían el historial.
        if (['alfalfa.context', 'alfalfa.awake', 'heartbeat'].includes(m.type)) return;
        this._post({ tipo: 'pacai', m });
    }

    // -- utilidades ---------------------------------------------------------------
    _post(mensaje, guardar = true) {
        if (guardar) this.historial.push(mensaje);
        for (const v of this.vistas) v.webview.postMessage(mensaje);
    }

    _aviso(nivel, texto) {
        this._post({ tipo: 'aviso', nivel, texto });
    }

    async _modos() {
        if (!this.modos) {
            const ruta = await buscarAlfalfa(vscode.workspace.getConfiguration('alfalfa').get('ejecutable'));
            this.modos = ruta ? listarModos(ruta) : Promise.resolve(null);
            this.modos.then(modos => {
                if (modos) this.contexto.globalState.update(CLAVE_MODOS, modos);
                else this.modos = null;
            });
        }
        return this.modos;
    }

    async _modelos() {
        if (!this.modelos) {
            const ruta = await buscarAlfalfa(vscode.workspace.getConfiguration('alfalfa').get('ejecutable'));
            this.modelos = ruta ? listarModelos(ruta) : Promise.resolve({ error: 'No encuentro la orden `alfalfa`.' });
            this.modelos.then(r => { if (r.error) this.modelos = null; });
        }
        return this.modelos;
    }

    /** La «Vista enriquecida» que vale: la del ajuste si se puso; si no, la heredada de antes de 0.99.57. */
    vistaEnriquecida() {
        return vistaEnriquecida(vscode.workspace.getConfiguration('alfalfa').inspect('vistaEnriquecida'),
            this.contexto.globalState.get(CLAVE_HEREDADA));
    }

    _config() {
        const ajustes = vscode.workspace.getConfiguration('alfalfa');
        return {
            tipo: 'config', modo: ajustes.get('modo') || 'auto',
            modos: this.contexto.globalState.get(CLAVE_MODOS) || null,
            modelo: ajustes.get('modelo') || '', esfuerzo: ajustes.get('esfuerzo') || '',
            hayAnterior: this.haySesiones || Boolean(this.contexto.workspaceState.get(CLAVE_ULTIMA)),
            vivo: Boolean(this.cliente && !this.cliente.cerrado), ocupado: Boolean(this.cliente?.ocupado),
        };
    }

    async _verParche(parche, titulo) {
        const doc = await vscode.workspace.openTextDocument({ content: String(parche || ''), language: 'diff' });
        await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Active });
        if (titulo) this.salida.info('Parche: ' + titulo);
    }

    /** Un enlace de una pantalla del agente: sólo `https:`, y antes se pregunta. */
    async _abrirEnlace(url) {
        let destino;
        try {
            destino = vscode.Uri.parse(String(url), true);
        } catch {
            return;
        }
        if (destino.scheme !== 'https') return;
        const si = await vscode.window.showInformationMessage('¿Abrir este enlace del agente?',
            { modal: true, detail: destino.toString(true) }, 'Abrir');
        if (si === 'Abrir') await vscode.env.openExternal(destino);
    }

    async _abrirArchivo(ruta) {
        const carpeta = vscode.workspace.workspaceFolders?.[0];
        if (!ruta || !carpeta) return;
        const absoluta = path.isAbsolute(ruta) ? ruta : path.join(carpeta.uri.fsPath, ruta);
        try {
            await vscode.window.showTextDocument(vscode.Uri.file(absoluta), { preview: true });
        } catch {
            this._aviso('error', 'No se puede abrir ' + ruta);
        }
    }

    _html(webview, media) {
        const nonce = Buffer.from(require('node:crypto').randomBytes(16)).toString('base64');
        const js = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.js'));
        const ui = webview.asWebviewUri(vscode.Uri.joinPath(media, 'ui-global.js'));
        const css = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.css'));
        return `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}' ${webview.cspSource}; img-src ${webview.cspSource} data:;">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${css}">
<title>AlfalfaCode</title>
</head>
<body>
<header id="barra">
  ${hoja('hoja-estado')}
  <span id="titulo" title="Sesión">Nueva sesión</span>
  <span id="uso"></span>
  <button id="historial" class="icono" title="Reanudar una sesión" aria-label="Reanudar una sesión">${ICONOS.reloj}</button>
  <button id="nueva" class="icono" title="Nueva sesión" aria-label="Nueva sesión">${ICONOS.nueva}</button>
</header>
<main id="conversacion" aria-live="polite"></main>
<div id="plan" hidden></div>
<form id="redactor">
  <div id="tarjeta">
    <textarea id="texto" rows="1" placeholder="Pide algo a AlfalfaCode…" aria-label="Mensaje"></textarea>
    <div id="adjuntos">
        <span id="contexto" class="chip" title="Archivo del editor que se envía como contexto" hidden>${ICONOS.archivo}<span id="contexto-nombre"></span><button type="button" id="quitar-contexto" class="icono" title="No enviar este archivo" aria-label="No enviar este archivo">${ICONOS.cerrar}</button></span>
    </div>
    <div id="herramientas">
      <div id="selector">
        <button type="button" id="pildora" aria-haspopup="listbox" aria-expanded="false" title="Modelo y esfuerzo">
          <span id="pildora-modelo">Predeterminado</span><span id="pildora-esfuerzo"></span>
        </button>
        <div id="menu" role="dialog" aria-label="Modelo y esfuerzo" hidden>
          <div class="menu-cab">Elige un modelo</div>
          <input id="buscar" type="search" placeholder="Buscar modelo…" aria-label="Buscar modelo">
          <div id="lista" role="listbox" aria-label="Modelos"></div>
          <div id="fila-esfuerzo">
            <span id="esfuerzo-etiqueta">Esfuerzo</span>
            <button type="button" id="esfuerzo-reset" class="icono" title="Volver al esfuerzo predeterminado del modelo" aria-label="Esfuerzo predeterminado">${ICONOS.reset}</button>
            <div id="deslizador" role="slider" tabindex="0" aria-label="Esfuerzo de razonamiento"></div>
          </div>
        </div>
      </div>
      <span id="pista"></span>
      <div id="modo-caja">
        <button type="button" id="modo" aria-haspopup="menu" aria-expanded="false" title="Modo de permisos">${ICONOS.escudo}<span id="modo-nombre">Auto</span>${ICONOS.abajo}</button>
        <div id="menu-modos" role="menu" aria-label="Modo de permisos" hidden></div>
      </div>
      <button type="submit" id="enviar" title="Enviar (Enter)" aria-label="Enviar">${ICONOS.flecha}</button>
      <button type="button" id="detener" title="Detener (Esc)" aria-label="Detener" hidden>${ICONOS.parar}</button>
    </div>
  </div>
</form>
<template id="hoja-grande">${hoja('hoja-marca')}</template>
<script nonce="${nonce}" src="${js}"></script>
<script nonce="${nonce}" type="module" src="${ui}"></script>
</body>
</html>`;
    }
}

/** El archivo del editor activo (ruta relativa y líneas seleccionadas), para el chip de contexto. */
function archivoActivo() {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== 'file') return { tipo: 'archivoActivo', ruta: null };
    const sel = editor.selection;
    const lineas = sel.isEmpty ? '' : sel.start.line === sel.end.line ? `${sel.start.line + 1}`
        : `${sel.start.line + 1}-${sel.end.line + 1}`;
    return { tipo: 'archivoActivo', ruta: vscode.workspace.asRelativePath(editor.document.uri), lineas };
}

function activate(contexto) {
    // La primera vez con 0.99.57: si la extensión ya se usaba, la «Vista enriquecida» sigue encendida (lib/vista.js).
    if (contexto.globalState.get(CLAVE_HEREDADA) === undefined) {
        contexto.globalState.update(CLAVE_HEREDADA, yaSeUsaba(contexto.globalState.keys()));
    }
    const proveedor = new ProveedorChat(contexto);
    vscode.commands.executeCommand('setContext', SIN_SECUNDARIA, !admiteBarraSecundaria());
    const opciones = { webviewOptions: { retainContextWhenHidden: true } };
    contexto.subscriptions.push(
        proveedor,
        vscode.window.registerWebviewViewProvider(VISTA_IZQUIERDA, proveedor, opciones),
        vscode.window.registerWebviewViewProvider(VISTA_DERECHA, proveedor, opciones),
        vscode.commands.registerCommand('alfalfa.abrir', () => vscode.commands.executeCommand(vista() + '.focus')),
        vscode.commands.registerCommand('alfalfa.nuevaSesion', () => proveedor.nuevaSesion()),
        vscode.commands.registerCommand('alfalfa.detener', () => proveedor.detener()),
        vscode.commands.registerCommand('alfalfa.reanudar', () => proveedor.reanudar()),
        vscode.commands.registerCommand('alfalfa.elegirEjecutable', () => proveedor.elegirEjecutable()),
        vscode.commands.registerCommand('alfalfa.registro', () => proveedor.salida.show(true)),
        vscode.commands.registerCommand('alfalfa.preguntarSeleccion', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;
            const relativa = vscode.workspace.asRelativePath(editor.document.uri);
            const sel = editor.selection;
            const lineas = sel.isEmpty ? '' : `:${sel.start.line + 1}-${sel.end.line + 1}`;
            const codigo = sel.isEmpty ? '' : '\n```' + editor.document.languageId + '\n'
                + editor.document.getText(sel) + '\n```\n';
            await proveedor.insertar(`En \`${relativa}${lineas}\`:${codigo}\n`);
        }),
        vscode.window.onDidChangeActiveTextEditor(() => proveedor._post(archivoActivo(), false)),
        vscode.window.onDidChangeTextEditorSelection(e => {
            if (e.textEditor === vscode.window.activeTextEditor) proveedor._post(archivoActivo(), false);
        }),
        vscode.workspace.onDidChangeConfiguration(e => {
            if (e.affectsConfiguration('alfalfa.ejecutable')) proveedor.refrescarListas();
            if (e.affectsConfiguration('alfalfa.vistaEnriquecida')) {
                proveedor.cliente?.vistaEnriquecida(proveedor.vistaEnriquecida());
            }
            if (['modo', 'modelo', 'esfuerzo'].some(k => e.affectsConfiguration('alfalfa.' + k))) {
                proveedor._post(proveedor._config(), false);
            }
        }),
    );
}

function deactivate() {}

module.exports = { activate, deactivate };
