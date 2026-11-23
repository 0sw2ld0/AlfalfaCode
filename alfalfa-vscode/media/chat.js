// Panel del chat de AlfalfaCode. Recibe de la extensión los mensajes PACAI tal cual y los pinta.
(function () {
    'use strict';
    const vscode = acquireVsCodeApi();
    const $ = id => document.getElementById(id);
    const conversacion = $('conversacion');
    const texto = $('texto');
    const plan = $('plan');

    const MOTIVOS = {
        dangerous: 'Orden peligrosa', network: 'Necesita red', outside_sandbox: 'Fuera del sandbox',
        sandbox_denied_retry: 'El sandbox la bloqueó: reintentar fuera', external_tool: 'Herramienta externa (MCP)',
        policy: 'Lo pide la política', 'alfalfa.edit': 'Editar archivos', 'alfalfa.mode': 'Lo pide el modo',
    };
    const MODOS_V2 = { ediciones: 'accept_edits', omitir: 'bypass_permissions' };
    // Reserva para un `alfalfa` que aún no anuncia sus modos: lo normal es que lleguen en `alfalfa.session`
    // (`modes`), y así un modo nuevo del motor aparece aquí sin tocar la extensión.
    const MODOS_RESERVA = [
        { mode: 'auto', name: 'Auto', summary: 'Las órdenes normales corren en el sandbox; pregunta las peligrosas' },
        { mode: 'manual', name: 'Manual', summary: 'Pregunta antes de cada cambio: órdenes y ediciones' },
        { mode: 'accept_edits', name: 'Aceptar ediciones', summary: 'Edita sin preguntar; pregunta antes de cada orden' },
        { mode: 'plan', name: 'Plan', summary: 'Sólo lee y propone un plan antes de cambiar nada' },
        { mode: 'bypass_permissions', name: 'Omitir permisos', summary: 'Aprueba todo sin preguntar, también fuera del sandbox', confirm: true },
    ];
    let modos = MODOS_RESERVA;
    let modoSel = 'auto';

    let ocupado = false;
    let vivo = false;
    const mensajes = new Map();     // message_id → {el, texto, rol}
    const herramientas = new Map(); // tool_call_id → elemento
    const aprobaciones = new Map(); // approval id → tarjeta
    const ejecuciones = new Map();  // run_id → nombre del subagente
    const propios = new Set();      // ids de input enviados desde aquí
    let avisosSistema = null;       // <details> que agrupa los mensajes de sistema seguidos
    let gasto = { sesion: null, contexto: null, limite: null };
    let modelos = [];               // [{nombre, proveedor, esfuerzos}] de `alfalfa --modelos`
    let modeloSel = '';             // '' = el predeterminado de AlfalfaCode
    let esfuerzoSel = 'medium';     // siempre un nivel concreto; de partida, Medio
    const ESFUERZO_BASE = 'medium';
    let archivo = null;             // {ruta, lineas} del editor activo
    let archivoQuitado = null;      // ruta que el usuario quitó del contexto
    let ultimaFecha = null;
    let horaPendiente = false;      // el primer mensaje del asistente tras uno del usuario lleva su hora
    let titulado = false;
    const ESFUERZOS = { low: 'Bajo', medium: 'Medio', high: 'Alto', xhigh: 'Muy alto', max: 'Máximo' };

    // -- utilidades --------------------------------------------------------------
    function el(etiqueta, clase, contenido) {
        const e = document.createElement(etiqueta);
        if (clase) e.className = clase;
        if (contenido !== undefined) e.textContent = contenido;
        return e;
    }

    function escapar(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    }

    /** Markdown mínimo y seguro: todo se escapa primero; luego bloques de código, código, negrita, enlaces, listas. */
    function markdown(fuente) {
        const bloques = [];
        let s = String(fuente).replace(/```([\w+-]*)\n?([\s\S]*?)(```|$)/g, (_, lengua, codigo) => {
            bloques.push(`<pre><code class="lengua-${escapar(lengua)}">${escapar(codigo.replace(/\n$/, ''))}</code></pre>`);
            return `\u0000${bloques.length - 1}\u0000`;
        });
        s = escapar(s)
            .replace(/`([^`\n]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
            .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>')
            .replace(/^#{1,6} (.+)$/gm, '<strong class="titulo">$1</strong>')
            .replace(/^(?:[-*]|\d+\.) (.+)$/gm, '<span class="item">• $1</span>');
        s = s.split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
        return s.replace(/<p>\u0000(\d+)\u0000<\/p>|\u0000(\d+)\u0000/g, (_, a, b) => bloques[Number(a ?? b)]);
    }

    function alFinal() {
        const cerca = conversacion.scrollHeight - conversacion.scrollTop - conversacion.clientHeight < 120;
        return () => { if (cerca) conversacion.scrollTop = conversacion.scrollHeight; };
    }

    function añadir(nodo) {
        const bajar = alFinal();
        if (!nodo.classList.contains('sistema')) avisosSistema = null;
        conversacion.appendChild(nodo);
        $('vacio')?.remove();
        bajar();
        return nodo;
    }

    function etiquetaDe(runId) {
        const nombre = ejecuciones.get(runId);
        return nombre ? el('span', 'subagente', nombre) : null;
    }

    function textoDe(bloques) {
        return (bloques || []).filter(b => b.type === 'text').map(b => b.text).join('');
    }

    function boton(etiqueta, clase, accion) {
        const b = el('button', clase, etiqueta);
        b.type = 'button';
        b.addEventListener('click', accion);
        return b;
    }

    function vacio(hayAnterior) {
        if (conversacion.children.length && !$('vacio')) return;
        $('vacio')?.remove();
        const caja = el('div', 'vacio');
        caja.id = 'vacio';
        caja.appendChild($('hoja-grande').content.cloneNode(true));
        const marca = el('div', 'marca', 'Alfalfa');
        marca.appendChild(el('span', null, 'Code'));
        caja.appendChild(marca);
        caja.appendChild(el('p', null, 'Pide un cambio, una revisión o una explicación. Trabaja en la carpeta abierta y te pide permiso según el modo.'));
        if (hayAnterior) caja.appendChild(boton('Reanudar una sesión…', 'enlace', () => vscode.postMessage({ tipo: 'reanudar' })));
        conversacion.appendChild(caja);
    }

    function pintarUso() {
        const partes = [];
        if (gasto.contexto != null && gasto.limite) partes.push(`${Math.round(100 * gasto.contexto / gasto.limite)}% ctx`);
        if (gasto.sesion) partes.push(`$${gasto.sesion.toFixed(gasto.sesion < 1 ? 4 : 2)}`);
        $('uso').textContent = partes.join(' · ');
        $('uso').title = gasto.contexto != null ? `Contexto: ${gasto.contexto} de ${gasto.limite} tokens` : '';
    }

    function pintarEstado() {
        const hay = texto.value.trim().length > 0;
        // Trabajando: Detener; si escribes, Enviar (entra en el turno en curso).
        $('detener').hidden = !ocupado || hay;
        $('enviar').hidden = ocupado && !hay;
        $('enviar').disabled = !hay;
        const hoja = document.querySelector('.hoja-estado');
        hoja.classList.toggle('trabajando', ocupado);
        hoja.classList.toggle('apagada', !vivo && !ocupado);
        $('barra').title = ocupado ? 'Trabajando' : vivo ? 'Sesión abierta' : 'Sin sesión';
        $('pista').textContent = ocupado ? (hay ? 'Entra en el turno en curso' : 'Trabajando…') : '';
    }

    function pintarContexto() {
        const chip = $('contexto');
        chip.hidden = !archivo?.ruta;
        if (!archivo?.ruta) return;
        const nombre = archivo.ruta.split('/').pop() + (archivo.lineas ? ':' + archivo.lineas : '');
        $('contexto-nombre').textContent = nombre;
        chip.title = (archivoQuitado === archivo.ruta ? 'No se enviará: ' : 'Se envía como contexto: ') + archivo.ruta;
        chip.classList.toggle('quitado', archivoQuitado === archivo.ruta);
        $('quitar-contexto').title = archivoQuitado === archivo.ruta ? 'Volver a enviar este archivo' : 'No enviar este archivo';
    }

    function marcaDeTiempo(ms) {
        const d = new Date(ms || Date.now());
        const dia = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
        if (dia !== ultimaFecha) {
            ultimaFecha = dia;
            añadir(el('div', 'fecha', dia));
        }
        añadir(el('div', 'hora', d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })));
    }

    // -- mensajes PACAI --------------------------------------------------------
    function pacai(m) {
        const p = m.payload || {};
        const a = p.alfalfa || {};
        switch (m.type) {
            case 'alfalfa.session': {
                vivo = true;
                if (Array.isArray(p.modes) && p.modes.length) modos = p.modes;
                if (p.mode) modoSel = MODOS_V2[p.mode] || p.mode;
                pintarModo();
                if (p.model) elegirModelo(p.model, p.effort);
                $('titulo').title = `Sesión ${p.id} · ${p.cwd || ''}`;
                pintarEstado();
                break;
            }
            case 'alfalfa.user_message':
                if (!propios.has(p.input_id) && p.text) usuario({ texto: p.text });
                break;
            case 'run_started':
                if (p.parent_run_id) {
                    ejecuciones.set(m.id, p.agent?.name || 'subagente');
                }
                break;
            case 'message_start': {
                if (p.role === 'user') break;
                let nodo;
                if (p.role === 'system') {
                    nodo = lineaSistema();
                } else {
                    if (horaPendiente && p.role === 'assistant' && !ejecuciones.has(p.run_id)) {
                        horaPendiente = false;
                        marcaDeTiempo(Date.parse(m.time));
                    }
                    nodo = añadir(el('div', 'mensaje asistente'));
                    const etiqueta = etiquetaDe(p.run_id);
                    if (etiqueta) nodo.appendChild(etiqueta);
                    nodo.appendChild(el('div', 'cuerpo'));
                }
                const entrada = { el: nodo, texto: textoDe(p.blocks), rol: p.role };
                mensajes.set(m.id, entrada);
                pintarMensaje(entrada);
                break;
            }
            case 'message_delta':
            case 'message_end': {
                const entrada = mensajes.get(p.message_id);
                if (!entrada) break;
                if (m.type === 'message_delta') entrada.texto += textoDe(p.blocks);
                else if (p.blocks?.length) entrada.texto = textoDe(p.blocks);
                pintarMensaje(entrada);
                break;
            }
            case 'tool_start':
                herramienta(m);
                break;
            case 'tool_end': {
                const fila = herramientas.get(p.tool_call_id);
                if (!fila) break;
                if (p.summary) fila.querySelector('.resumen').textContent = p.summary;
                cerrarHerramienta(fila, p.status, textoDe(p.output));
                break;
            }
            case 'approval_request':
                aprobacion(m);
                break;
            case 'approval_result': {
                const tarjeta = aprobaciones.get(m.reply_to);
                if (tarjeta) cerrarAprobacion(tarjeta, p.decision === 'expired' ? 'Caducó' : 'Cancelada');
                break;
            }
            case 'plan_updated':
                pintarPlan(p.steps || [], a.explanation);
                break;
            case 'diff_updated':
                cambios(p);
                break;
            case 'usage':
                if (p.context_tokens != null) gasto.contexto = p.context_tokens;
                if (p.context_limit) gasto.limite = p.context_limit;
                if (p.cost?.amount != null) gasto.sesion = p.cost.amount;
                pintarUso();
                break;
            case 'alfalfa.budget':
                if (typeof p.session?.spent === 'number') { gasto.sesion = p.session.spent; pintarUso(); }
                break;
            case 'run_finished':
                if (!p.parent_run_id && ejecuciones.has(p.run_id)) break;
                if (p.outcome === 'interrupted') aviso('info', 'Turno detenido.');
                else if (p.outcome === 'failed') aviso('error', 'El turno falló' + (p.detail ? ': ' + p.detail : '.'));
                else if (p.outcome === 'blocked') aviso('info', 'Turno bloqueado' + (p.detail ? ': ' + p.detail : '.'));
                for (const tarjeta of aprobaciones.values()) cerrarAprobacion(tarjeta, 'Sin respuesta');
                break;
            case 'error':
                aviso('error', p.message || p.code);
                break;
            case 'close':
                vivo = false;
                pintarEstado();
                break;
        }
    }

    // -- modo de permisos ------------------------------------------------------
    function modoDe(id) {
        return modos.find(m => m.mode === id);
    }

    function pintarModo() {
        const m = modoDe(modoSel);
        $('modo-nombre').textContent = m?.name || modoSel;
        $('modo').title = `Modo de permisos: ${m?.name || modoSel}` + (m?.summary ? ` — ${m.summary}` : '');
        $('modo-caja').classList.toggle('peligro', Boolean(m?.confirm));
        if (!$('menu-modos').hidden) pintarModos();
    }

    function elegirModo(m) {
        cerrarModos(true);
        if (m.mode === modoSel) return;
        // Los que se confirman los cambia la extensión tras su diálogo; el resto, ya.
        if (!m.confirm) { modoSel = m.mode; pintarModo(); }
        vscode.postMessage({ tipo: 'modo', modo: m.mode, confirmar: Boolean(m.confirm), nombre: m.name, resumen: m.summary });
    }

    function pintarModos() {
        const menu = $('menu-modos');
        menu.replaceChildren(el('div', 'menu-cab', 'Modo'));
        const normales = modos.filter(m => !m.confirm);
        const confirmar = modos.filter(m => m.confirm);
        normales.forEach((m, i) => {
            const b = el('button', 'modo-item');
            b.type = 'button';
            b.setAttribute('role', 'menuitemradio');
            b.setAttribute('aria-checked', String(m.mode === modoSel));
            if (i < 9) b.dataset.atajo = String(i + 1);
            const texto = el('span', 'modo-texto');
            texto.appendChild(el('span', 'modo-titulo', m.name || m.mode));
            if (m.summary) texto.appendChild(el('span', 'modo-resumen', m.summary));
            b.appendChild(texto);
            b.appendChild(el('span', 'modo-marca', m.mode === modoSel ? '✓' : ''));
            b.appendChild(el('span', 'modo-atajo', i < 9 ? String(i + 1) : ''));
            b.addEventListener('click', () => elegirModo(m));
            menu.appendChild(b);
        });
        if (confirmar.length) menu.appendChild(el('div', 'menu-separador'));
        for (const m of confirmar) {
            const activo = m.mode === modoSel;
            const b = el('button', 'modo-item modo-confirmar');
            b.type = 'button';
            b.setAttribute('role', 'menuitemradio');
            b.setAttribute('aria-checked', String(activo));
            b.title = m.summary || '';
            b.appendChild(el('span', 'modo-titulo', m.name || m.mode));
            b.appendChild(el('span', 'modo-accion', activo ? '✓' : 'Habilitar'));
            b.addEventListener('click', () => elegirModo(m));
            menu.appendChild(b);
        }
    }

    function abrirModos() {
        cerrarMenu(false);
        pintarModos();
        $('menu-modos').hidden = false;
        $('modo').setAttribute('aria-expanded', 'true');
        ($('menu-modos').querySelector('[aria-checked="true"]') || $('menu-modos').querySelector('button'))?.focus();
    }

    function cerrarModos(enfocar) {
        if ($('menu-modos').hidden) return;
        $('menu-modos').hidden = true;
        $('modo').setAttribute('aria-expanded', 'false');
        if (enfocar) $('modo').focus();
    }

    // -- modelo y esfuerzo ------------------------------------------------------
    let errorModelos = '';

    function corto(nombre) {
        return nombre ? nombre.split('/').pop() : 'Predeterminado';
    }

    /** `e` si el modelo lo admite; si no, el nivel admitido más cercano (con empate, el más alto). */
    function ajustarEsfuerzo(e, validos) {
        if (validos.includes(e)) return e;
        const orden = Object.keys(ESFUERZOS);
        const objetivo = orden.indexOf(orden.includes(e) ? e : ESFUERZO_BASE);
        let mejor = validos[0], distancia = Infinity;
        for (const v of validos) {
            const d = Math.abs(orden.indexOf(v) - objetivo);
            if (d < distancia || (d === distancia && orden.indexOf(v) > orden.indexOf(mejor))) { mejor = v; distancia = d; }
        }
        return mejor || '';
    }

    function esfuerzosDe(nombre) {
        // Los niveles que declara el catálogo; si no declara ninguno, los cinco, como la app de Mac.
        const modelo = modelos.find(m => m.nombre === nombre);
        return modelo?.esfuerzos?.length ? modelo.esfuerzos : Object.keys(ESFUERZOS);
    }

    function pintarPildora() {
        $('pildora-modelo').textContent = corto(modeloSel);
        $('pildora-esfuerzo').textContent = ESFUERZOS[esfuerzoSel] || esfuerzoSel;
        $('pildora').title = `Modelo: ${modeloSel || 'el predeterminado de AlfalfaCode'} · esfuerzo ${ESFUERZOS[esfuerzoSel] || esfuerzoSel}`;
        pintarDeslizador();
    }

    /** El esfuerzo, como en Claude Code y Codex: un deslizador con los niveles que admite el modelo. */
    function pintarDeslizador() {
        const validos = esfuerzosDe(modeloSel);
        const fila = $('fila-esfuerzo');
        fila.hidden = !validos.length;
        const actual = validos.indexOf(esfuerzoSel);
        $('esfuerzo-etiqueta').replaceChildren('Esfuerzo ',
            el('span', 'suave', `(${ESFUERZOS[esfuerzoSel] || esfuerzoSel})`));
        // ↺ vuelve a Medio (o al más cercano que admita el modelo); no hace falta si ya está ahí.
        const base = ajustarEsfuerzo(ESFUERZO_BASE, validos);
        $('esfuerzo-reset').hidden = esfuerzoSel === base;
        $('esfuerzo-reset').title = `Volver a ${ESFUERZOS[base] || base}`;
        const pista = $('deslizador');
        pista.replaceChildren();
        pista.style.setProperty('--n', String(Math.max(validos.length - 1, 1)));
        pista.style.setProperty('--i', String(Math.max(actual, 0)));
        pista.classList.toggle('sin-valor', actual < 0);
        validos.forEach((e, i) => {
            const punto = el('button', 'punto' + (i === actual ? ' actual' : ''));
            punto.type = 'button';
            punto.tabIndex = -1;
            punto.title = ESFUERZOS[e] || e;
            punto.addEventListener('click', () => fijarEsfuerzo(e));
            pista.appendChild(punto);
        });
        pista.setAttribute('aria-valuemin', '0');
        pista.setAttribute('aria-valuemax', String(validos.length - 1));
        pista.setAttribute('aria-valuenow', String(Math.max(actual, 0)));
        pista.setAttribute('aria-valuetext', ESFUERZOS[esfuerzoSel] || esfuerzoSel);
    }

    function fijarEsfuerzo(e) {
        if (e === esfuerzoSel) return;
        esfuerzoSel = e;
        pintarPildora();
    pintarModo();
        vscode.postMessage({ tipo: 'modelo', modelo: modeloSel, esfuerzo: esfuerzoSel });
    }

    /** Pone el modelo (aunque no esté en la lista: el de la sesión o el de los ajustes) y su esfuerzo. */
    function elegirModelo(nombre, esfuerzo) {
        modeloSel = nombre || '';
        const validos = esfuerzosDe(modeloSel);
        const e = esfuerzo === undefined ? esfuerzoSel : esfuerzo;
        esfuerzoSel = ajustarEsfuerzo(e || ESFUERZO_BASE, validos);
        pintarPildora();
    pintarModo();
        if (!$('menu').hidden) pintarMenu();
    }

    function pintarModelos(error) {
        errorModelos = error || '';
        elegirModelo(modeloSel);
    }

    function pintarMenu() {
        const filtro = $('buscar').value.trim().toLowerCase();
        const lista = $('lista');
        lista.replaceChildren();
        const opcion = (valor, etiqueta) => {
            const b = boton(etiqueta, null, () => {
                const antes = esfuerzoSel;
                elegirModelo(valor, antes);
                vscode.postMessage({ tipo: 'modelo', modelo: modeloSel, esfuerzo: esfuerzoSel });
                cerrarMenu(true);
            });
            b.setAttribute('role', 'option');
            b.setAttribute('aria-selected', String(modeloSel === valor));
            b.title = valor || 'El modelo configurado en AlfalfaCode';
            return b;
        };
        if (!filtro) lista.appendChild(opcion('', 'Modelo predeterminado'));
        const grupos = new Map();
        for (const m of modelos) {
            if (filtro && !(m.nombre.toLowerCase().includes(filtro) || m.proveedor.toLowerCase().includes(filtro))) continue;
            if (!grupos.has(m.proveedor)) grupos.set(m.proveedor, []);
            grupos.get(m.proveedor).push(m);
        }
        for (const [proveedor, ms] of [...grupos].sort((a, b) => a[0].localeCompare(b[0]))) {
            lista.appendChild(el('div', 'grupo', proveedor));
            for (const m of ms) lista.appendChild(opcion(m.nombre, m.nombre));
        }
        if (errorModelos) lista.appendChild(el('div', 'nada', 'No se pudo listar los modelos: ' + errorModelos));
        else if (!modelos.length) lista.appendChild(el('div', 'nada', 'Cargando modelos…'));
        else if (filtro && !grupos.size) lista.appendChild(el('div', 'nada', 'Ningún modelo coincide.'));
    }

    function abrirMenu() {
        cerrarModos(false);
        $('menu').hidden = false;
        $('pildora').setAttribute('aria-expanded', 'true');
        $('buscar').value = '';
        pintarMenu();
        $('buscar').focus();
        $('lista').querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'center' });
    }

    function cerrarMenu(enfocar) {
        $('menu').hidden = true;
        $('pildora').setAttribute('aria-expanded', 'false');
        if (enfocar) texto.focus();
    }

    function pintarMensaje(entrada) {
        const bajar = alFinal();
        if (entrada.rol === 'system') entrada.el.textContent = entrada.texto;
        else entrada.el.querySelector('.cuerpo').innerHTML = markdown(entrada.texto);
        bajar();
    }

    /** Una línea dentro del grupo «Avisos» de los mensajes de sistema seguidos. */
    function lineaSistema(t) {
        if (!avisosSistema) {
            avisosSistema = el('details', 'sistema');
            avisosSistema.appendChild(el('summary', null, 'Avisos'));
            añadir(avisosSistema);
        }
        const nodo = el('div', 'linea-sistema', t);
        avisosSistema.appendChild(nodo);
        avisosSistema.querySelector('summary').textContent = `Avisos (${avisosSistema.children.length - 1})`;
        return nodo;
    }

    function filaHerramienta(titulo, etiqueta, argumentos) {
        const fila = añadir(el('div', 'herramienta'));
        fila.dataset.estado = 'running';
        const resumen = el('details');
        const cabecera = el('summary');
        cabecera.appendChild(el('span', 'estado', '◌'));
        if (etiqueta) cabecera.appendChild(etiqueta);
        cabecera.appendChild(el('span', 'titulo', titulo));
        cabecera.appendChild(el('span', 'resumen'));
        resumen.appendChild(cabecera);
        resumen.appendChild(el('pre', 'argumentos', argumentos.length > 3000 ? argumentos.slice(0, 3000) + '\n…' : argumentos));
        fila.appendChild(resumen);
        return fila;
    }

    function cerrarHerramienta(fila, estado, salida) {
        fila.dataset.estado = estado;
        fila.querySelector('.estado').textContent = { success: '✓', error: '✗', cancelled: '⊘' }[estado] || '•';
        if (salida) {
            const pre = el('pre', 'salida', salida.length > 4000 ? salida.slice(0, 4000) + '\n…' : salida);
            fila.querySelector('details').appendChild(pre);
        }
    }

    function herramienta(m) {
        const p = m.payload;
        const args = JSON.stringify(p.arguments ?? {}, null, 2);
        herramientas.set(m.id, filaHerramienta(p.title || p.name, etiquetaDe(p.run_id), `${p.name} ${args}`));
    }

    function mensajeAsistente(t) {
        const nodo = añadir(el('div', 'mensaje asistente'));
        const cuerpo = el('div', 'cuerpo');
        cuerpo.innerHTML = markdown(t);
        nodo.appendChild(cuerpo);
    }

    /**
     * La conversación guardada de una sesión que se reanuda (`alfalfa --historial`), con el formato de la app:
     * mensajes, cada herramienta con su resultado y los avisos agrupados. Una herramienta cierra la última
     * abierta con su mismo nombre.
     */
    function pintarHistorial({ eventos, recortado }) {
        if (recortado) aviso('info', 'Sólo se muestra lo más reciente de esta sesión.');
        const abiertas = [];
        // El encargo de la sesión sale sin turno cuando hay avisos sueltos; en una sesión del panel es el primer
        // mensaje, que también sale con su turno: no se pinta dos veces.
        const conTurno = new Set((eventos || []).filter(e => e.rol === 'user' && e.turno).map(e => e.texto));
        for (const e of eventos || []) {
            const hora = e.hora ? e.hora * 1000 : null;
            const t = typeof e.texto === 'string' ? e.texto : '';
            if (e.rol === 'user') {
                if (!e.turno && (t === 'Sesión PACAI' || conTurno.has(t))) continue;
                // La referencia al archivo del editor que añade el panel vuelve a ser su chip.
                const ref = /^\[Archivo abierto en el editor: (.+?)(?:, líneas ([\d-]+))?\]\n\n/.exec(t);
                usuario({ texto: ref ? t.slice(ref[0].length) : t, hora,
                    archivo: ref ? { ruta: ref[1], lineas: ref[2] } : undefined });
            } else if (e.rol === 'agent' || e.rol === 'assistant') {
                if (!t.trim()) continue;
                if (horaPendiente) { horaPendiente = false; marcaDeTiempo(hora); }
                mensajeAsistente(t);
            } else if (e.rol === 'tool' && e.herramienta) {
                const h = e.herramienta;
                if (h.fase === 'inicio') {
                    abiertas.push({ nombre: h.nombre, fila: filaHerramienta(h.descripcion || h.nombre, null, t) });
                } else if (h.fase === 'fin') {
                    const i = abiertas.map(a => a.nombre).lastIndexOf(h.nombre);
                    if (i < 0) continue;
                    const [{ fila }] = abiertas.splice(i, 1);
                    const fallo = h.error || h.clasificacion?.disposition === 'failure';
                    cerrarHerramienta(fila, fallo ? 'error' : 'success', t);
                }
            } else if (e.rol === 'system' && t.trim()) {
                lineaSistema(t);
            }
        }
        // Las que se quedaron sin resultado (el turno se cortó): sin marca de éxito ni de fallo.
        for (const { fila } of abiertas) cerrarHerramienta(fila, 'cancelled');
        conversacion.scrollTop = conversacion.scrollHeight;
    }

    function aprobacion(m) {
        const p = m.payload;
        const a = p.alfalfa || {};
        const tarjeta = añadir(el('div', 'aprobacion'));
        tarjeta.appendChild(el('div', 'motivo', MOTIVOS[p.reason] || (a.reason ? `Permiso: ${a.reason}` : 'Permiso')));
        tarjeta.appendChild(el('div', 'proposito', a.purpose || herramientas.get(p.tool_call_id)?.querySelector('.titulo')?.textContent || p.name));
        if (a.justification) tarjeta.appendChild(el('div', 'justificacion', a.justification));
        if (a.diff) {
            tarjeta.appendChild(parche(a.diff));
        } else if (Array.isArray(a.argv) && a.argv.length) {
            tarjeta.appendChild(el('pre', 'orden', a.argv.join(' ')));
        } else {
            tarjeta.appendChild(el('pre', 'orden', JSON.stringify(p.arguments, null, 2)));
        }
        const botones = el('div', 'botones');
        const responder = (decision, alcance) => {
            vscode.postMessage(decision === 'approved'
                ? { tipo: 'aprobar', id: m.id, alcance }
                : { tipo: 'denegar', id: m.id });
            cerrarAprobacion(tarjeta, decision === 'approved' ? (alcance === 'session' ? 'Permitido en esta sesión' : 'Permitido') : 'Denegado');
        };
        botones.appendChild(boton('Permitir', 'primario', () => responder('approved', 'once')));
        if ((p.scopes || []).includes('session')) {
            botones.appendChild(boton(a.prefix ? `Permitir «${a.prefix}…» en la sesión` : 'Permitir en la sesión', 'secundario', () => responder('approved', 'session')));
        }
        botones.appendChild(boton('Denegar', 'secundario', () => responder('denied')));
        if (a.diff) botones.appendChild(boton('Abrir parche', 'enlace', () => vscode.postMessage({ tipo: 'verParche', parche: a.diff, titulo: a.purpose })));
        tarjeta.appendChild(botones);
        aprobaciones.set(m.id, tarjeta);
        tarjeta.querySelector('button.primario').focus({ preventScroll: true });
    }

    function cerrarAprobacion(tarjeta, resultado) {
        if (tarjeta.dataset.cerrada) return;
        tarjeta.dataset.cerrada = '1';
        tarjeta.querySelector('.botones').replaceWith(el('div', 'resultado', resultado));
    }

    function parche(diff) {
        const pre = el('pre', 'parche');
        for (const linea of String(diff).split('\n')) {
            const clase = linea.startsWith('+++') || linea.startsWith('---') ? 'cab'
                : linea.startsWith('+') ? 'mas' : linea.startsWith('-') ? 'menos' : linea.startsWith('@@') ? 'trozo' : '';
            pre.appendChild(el('span', clase, linea + '\n'));
        }
        return pre;
    }

    function cambios(p) {
        const files = p.files || [];
        if (!files.length) return;
        const caja = añadir(el('div', 'cambios'));
        const suma = files.reduce((t, f) => [t[0] + (f.added || 0), t[1] + (f.removed || 0)], [0, 0]);
        const cab = el('div', 'cab');
        cab.appendChild(el('span', null, `${files.length} archivo${files.length === 1 ? '' : 's'} cambiado${files.length === 1 ? '' : 's'}`));
        cab.appendChild(el('span', 'mas', ` +${suma[0]}`));
        cab.appendChild(el('span', 'menos', ` −${suma[1]}`));
        if (p.patch) cab.appendChild(boton('Ver parche', 'enlace', () => vscode.postMessage({ tipo: 'verParche', parche: p.patch, titulo: 'Cambios del turno' })));
        caja.appendChild(cab);
        for (const f of files) {
            const fila = el('div', 'archivo');
            const enlace = el('a', null, f.path);
            enlace.href = '#';
            enlace.addEventListener('click', e => { e.preventDefault(); vscode.postMessage({ tipo: 'abrirArchivo', ruta: f.path }); });
            fila.appendChild(enlace);
            fila.appendChild(el('span', 'mas', ` +${f.added || 0}`));
            fila.appendChild(el('span', 'menos', ` −${f.removed || 0}`));
            caja.appendChild(fila);
        }
    }

    function pintarPlan(pasos, explicacion) {
        plan.replaceChildren();
        if (!pasos.length) { plan.hidden = true; return; }
        plan.hidden = false;
        const hechos = pasos.filter(s => s.status === 'completed').length;
        plan.appendChild(el('div', 'cab', `Plan · ${hechos}/${pasos.length}`));
        if (explicacion) plan.appendChild(el('div', 'explicacion', explicacion));
        for (const s of pasos) {
            const fila = el('div', 'paso ' + s.status);
            fila.appendChild(el('span', 'marca', { completed: '✓', in_progress: '▸', pending: '○' }[s.status] || '○'));
            fila.appendChild(el('span', null, s.text));
            plan.appendChild(fila);
        }
    }

    function usuario({ id, texto: t, encauzado, archivo: adjunto, hora }) {
        if (id) propios.add(id);
        if (!titulado) {
            titulado = true;
            $('titulo').textContent = t.split('\n')[0].slice(0, 80);
        }
        marcaDeTiempo(hora);
        horaPendiente = true;
        const nodo = añadir(el('div', 'mensaje usuario' + (encauzado ? ' encauzado' : '')));
        if (adjunto?.ruta) {
            const chip = el('span', 'chip', adjunto.ruta.split('/').pop() + (adjunto.lineas ? ':' + adjunto.lineas : ''));
            chip.title = adjunto.ruta;
            nodo.appendChild(chip);
        }
        nodo.appendChild(el('div', 'cuerpo', t));
    }

    function aviso(nivel, t) {
        añadir(el('div', 'aviso ' + nivel, t));
    }

    // -- extensión → panel ---------------------------------------------------------
    window.addEventListener('message', ({ data }) => {
        switch (data.tipo) {
            case 'reinicio':
                conversacion.replaceChildren();
                mensajes.clear(); herramientas.clear(); aprobaciones.clear(); ejecuciones.clear(); propios.clear();
                avisosSistema = null;
                gasto = { sesion: null, contexto: null, limite: null };
                ocupado = false; vivo = false;
                ultimaFecha = null; horaPendiente = false; titulado = false;
                pintarPlan([]); pintarUso(); pintarEstado();
                $('titulo').textContent = 'Nueva sesión';
                break;
            case 'config':
                if (Array.isArray(data.modos) && data.modos.length) modos = data.modos;
                modoSel = data.modo || modoSel;
                pintarModo();
                if (!data.vivo) elegirModelo(data.modelo, data.esfuerzo);
                $('historial').hidden = !data.hayAnterior;
                vivo = data.vivo; ocupado = data.ocupado;
                pintarEstado();
                vacio(data.hayAnterior);
                break;
            case 'pacai': pacai(data.m); break;
            case 'historial': pintarHistorial(data); break;
            case 'usuario': usuario(data); break;
            case 'aviso': aviso(data.nivel, data.texto); break;
            case 'archivoActivo':
                archivo = data.ruta ? { ruta: data.ruta, lineas: data.lineas } : null;
                pintarContexto();
                break;
            case 'modos':
                if (Array.isArray(data.modos) && data.modos.length) { modos = data.modos; pintarModo(); }
                break;
            case 'modelos':
                modelos = data.modelos || [];
                pintarModelos(data.error);
                break;
            case 'resuelta': {
                const tarjeta = aprobaciones.get(data.id);
                if (tarjeta) cerrarAprobacion(tarjeta, data.texto);
                break;
            }
            case 'estado':
                ocupado = data.ocupado;
                if (data.vivo === false) vivo = false;
                else if (data.ocupado) vivo = true;
                pintarEstado();
                break;
            case 'insertar':
                texto.value = (texto.value ? texto.value.replace(/\s*$/, '\n') : '') + data.texto;
                texto.focus();
                texto.setSelectionRange(texto.value.length, texto.value.length);
                guardarBorrador();
                break;
        }
    });

    // -- panel → extensión ----------------------------------------------------------
    function guardarBorrador() {
        vscode.setState({ borrador: texto.value });
    }

    function ajustarAltura() {
        texto.style.height = 'auto';
        texto.style.height = Math.min(texto.scrollHeight, window.innerHeight * 0.4) + 'px';
    }

    function enviar() {
        const t = texto.value.trim();
        if (!t) return;
        const contexto = archivo?.ruta && archivoQuitado !== archivo.ruta ? archivo : undefined;
        vscode.postMessage({ tipo: 'enviar', texto: t, encauzar: ocupado, archivo: contexto });
        texto.value = '';
        ajustarAltura();
        guardarBorrador();
        pintarEstado();
    }

    $('redactor').addEventListener('submit', e => { e.preventDefault(); enviar(); });
    texto.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); enviar(); }
        if (e.key === 'Escape' && ocupado) vscode.postMessage({ tipo: 'detener' });
    });
    texto.addEventListener('input', () => { guardarBorrador(); ajustarAltura(); pintarEstado(); });
    $('historial').addEventListener('click', () => vscode.postMessage({ tipo: 'reanudar' }));
    $('quitar-contexto').addEventListener('click', () => {
        archivoQuitado = archivoQuitado === archivo?.ruta ? null : archivo?.ruta;
        pintarContexto();
    });
    $('esfuerzo-reset').addEventListener('click', () => fijarEsfuerzo(ajustarEsfuerzo(ESFUERZO_BASE, esfuerzosDe(modeloSel))));
    $('deslizador').addEventListener('keydown', e => {
        const validos = esfuerzosDe(modeloSel);
        const i = validos.indexOf(esfuerzoSel);
        if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); fijarEsfuerzo(validos[Math.min(i + 1, validos.length - 1)]); }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); if (i > 0) fijarEsfuerzo(validos[i - 1]); else fijarEsfuerzo(validos[0]); }
        if (e.key === 'Escape') cerrarMenu(true);
    });
    $('pildora').addEventListener('click', () => ($('menu').hidden ? abrirMenu() : cerrarMenu(false)));
    $('buscar').addEventListener('input', pintarMenu);
    $('buscar').addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.stopPropagation(); cerrarMenu(true); }
        if (e.key === 'Enter') { e.preventDefault(); $('lista').querySelector('button')?.click(); }
        if (e.key === 'ArrowDown') { e.preventDefault(); $('lista').querySelector('button')?.focus(); }
    });
    $('lista').addEventListener('keydown', e => {
        const botones = [...$('lista').querySelectorAll('button')];
        const i = botones.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); botones[Math.min(i + 1, botones.length - 1)]?.focus(); }
        if (e.key === 'ArrowUp') { e.preventDefault(); (i <= 0 ? $('buscar') : botones[i - 1]).focus(); }
        if (e.key === 'Escape') cerrarMenu(true);
    });
    document.addEventListener('mousedown', e => { if (!$('selector').contains(e.target)) cerrarMenu(false); });
    $('detener').addEventListener('click', () => vscode.postMessage({ tipo: 'detener' }));
    $('nueva').addEventListener('click', () => vscode.postMessage({ tipo: 'nueva' }));
    $('modo').addEventListener('click', () => ($('menu-modos').hidden ? abrirModos() : cerrarModos(false)));
    $('menu-modos').addEventListener('keydown', e => {
        const items = [...$('menu-modos').querySelectorAll('[role="menuitemradio"]')];
        const i = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
        if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
        if (e.key === 'Escape') { e.preventDefault(); cerrarModos(true); }
        // 1…9: el modo de ese número, como en la app de Mac (los que se confirman no llevan número).
        if (/^[1-9]$/.test(e.key)) items.find(b => b.dataset.atajo === e.key)?.click();
    });
    document.addEventListener('mousedown', e => { if (!$('modo-caja').contains(e.target)) cerrarModos(false); });
    texto.value = vscode.getState()?.borrador || '';
    ajustarAltura();
    pintarEstado();
    pintarPildora();
    pintarModo();
    vscode.postMessage({ tipo: 'listo' });
})();
