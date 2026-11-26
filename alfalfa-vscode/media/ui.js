// Renderizador web del perfil `ui` de PACAI (catálogo base). Sin dependencias.
//
// Una `Vista` guarda los nodos, el estado y la revisión de una pantalla, aplica los bloques `pacai.ui/1` que manda
// el agente y la dibuja dentro de su contenedor. Todo se construye con createElement y textContent: ningún texto
// del agente se interpreta como HTML.
//
// Este archivo es el original: la extensión de VS Code lleva una copia idéntica en `alfalfa-vscode/media/ui.js`
// (una prueba de la extensión comprueba que no se separen).

const SVG = 'http://www.w3.org/2000/svg';
const MAX_PROFUNDIDAD = 12;

export class Vista {
    /**
     * @param {string} nombre
     * @param {{alEnviar: (accion: object, resumen: string) => void, alAbrir?: (url: string) => void,
     *          imagenesRemotas?: boolean, piezas?: boolean, consultar?: (query: object) => Promise<object>,
     *          consultas?: object}} opciones
     *   `alEnviar` recibe el `data` de `pacai.ui.action/1` y su texto legible. `alAbrir` abre un enlace `https:` (por
     *   defecto, con confirm y window.open). Sin `imagenesRemotas`, una imagen `https:` enseña su `alt`.
     *   `piezas` (prototipo, apagado por defecto): Calc, Data, `si`, `rojo`, `value` y la validación de campos.
     *   `consultar` resuelve la `query` de un Data y `consultas` son las que declaró la app (`queries`).
     */
    constructor(nombre, { alEnviar, alAbrir = abrirConConfirmacion, imagenesRemotas = true, piezas = false,
                          consultar = null, consultas = {} }) {
        this.nombre = nombre;
        this.alEnviar = alEnviar;
        this.alAbrir = alAbrir;
        this.imagenesRemotas = imagenesRemotas;
        this.rev = 0;
        this.nodos = new Map();
        this.estado = {};
        this.placement = null;
        this.alt = '';
        this.enviando = null;          // id del nodo cuyo `send` espera respuesta
        this.piezas = piezas;
        this.consultar = consultar;
        this.consultas = consultas;
        this.derivados = {};           // salidas de los Calc: nombre → {valores, tipos}
        this.datos = new Map();        // por nodo Data: {clave, seq, valores}
        this.tocados = new Set();      // campos que el usuario ya tocó (sólo entonces se ve su error)
        this.modo = 'pantalla';        // cómo se ve: 'pantalla', 'texto' (markdown) o 'json' (pacai.ui/1)
        this.elemento = document.createElement('div');
        this.elemento.className = 'ui-vista';
        this.elemento.setAttribute('role', 'group');
    }

    /** Un bloque `pacai.ui/1`. Devuelve false si llegó tarde (revisión vieja) y se descartó. */
    aplicar(data) {
        if (data.rev <= this.rev) return false;
        this.rev = data.rev;
        if (data.op === 'replace') {
            this.nodos.clear();
            this.estado = {};
        }
        for (const nodo of data.nodes ?? []) this.nodos.set(nodo.id, nodo);
        for (const id of data.remove ?? []) this.nodos.delete(id);
        Object.assign(this.estado, data.state ?? {});
        if (this.piezas) this.estado = estadoInicial(this.nodos, this.estado);
        if (data.placement) this.placement = data.placement;
        if (data.alt) this.alt = data.alt;
        this.enviando = null;
        this.dibujar();
        return true;
    }

    /** La ejecución terminó: los controles que esperaban respuesta vuelven a estar activos. */
    liberar() {
        if (this.enviando === null) return;
        this.enviando = null;
        this.dibujar();
    }

    /** Copia estática de lo que se ve ahora (para el sitio que deja una vista que se mueve). */
    foto() {
        const copia = this.elemento.cloneNode(true);
        copia.classList.add('ui-foto');
        copia.querySelector(':scope > .ui-barra')?.remove();
        for (const control of copia.querySelectorAll('button, input, select, textarea')) control.disabled = true;
        return copia;
    }

    dibujar() {
        const foco = document.activeElement;
        const conFoco = foco && this.elemento.contains(foco) ? foco.dataset.nodo : null;
        const cursor = conFoco && typeof foco.selectionStart === 'number' ? foco.selectionStart : null;
        this.elemento.replaceChildren();
        if (this.alt) this.elemento.setAttribute('aria-label', this.alt);
        if (this.piezas) this._calcular();
        this.elemento.append(this._barra());
        if (this.modo !== 'pantalla') {
            this.elemento.append(crear('pre', 'ui-texto', this._comoTexto(this.modo) || '(vacía)'));
            return;
        }
        const raiz = this.nodos.get('root');
        if (raiz) this.elemento.append(this._nodo(raiz, 0, new Set(), {}));
        if (conFoco) {
            const el = this.elemento.querySelector(`[data-nodo="${CSS.escape(conFoco)}"]`);
            const control = el?.matches('input, select, textarea, button') ? el : el?.querySelector('input, select, textarea');
            control?.focus();
            if (cursor !== null && typeof control?.setSelectionRange === 'function') {
                try { control.setSelectionRange(cursor, cursor); } catch { /* un control sin cursor */ }
            }
        }
    }

    /** La pantalla como markdown legible ('texto') o como el bloque `pacai.ui/1` que la rehace ('json'). */
    _comoTexto(modo) {
        return modo === 'json' ? jsonDePantalla(this.nombre, this.rev, this.nodos, this.estado, this.alt, this.placement)
            : textoDePantalla(this.nodos, this.estado, this.alt);
    }

    /** Arriba a la derecha: «Pantalla / Texto / JSON» y copiar (el texto, o el JSON si es lo que se ve). */
    _barra() {
        const barra = crear('div', 'ui-barra');
        const modos = crear('div', 'ui-modos');
        modos.setAttribute('role', 'group');
        modos.setAttribute('aria-label', 'Ver como');
        for (const [modo, nombre] of [['pantalla', 'Pantalla'], ['texto', 'Texto'], ['json', 'JSON']]) {
            const boton = crear('button', 'ui-modo', nombre);
            boton.type = 'button';
            boton.setAttribute('aria-pressed', String(this.modo === modo));
            boton.addEventListener('click', () => { this.modo = modo; this.dibujar(); });
            modos.append(boton);
        }
        const copiar = crear('button', 'ui-copiar', 'Copiar');
        copiar.type = 'button';
        copiar.title = this.modo === 'json' ? 'Copiar el JSON (pacai.ui/1)' : 'Copiar como texto';
        copiar.addEventListener('click', () => {
            const texto = this._comoTexto(this.modo === 'json' ? 'json' : 'texto');
            const aviso = ok => {
                copiar.textContent = ok ? 'Copiado' : 'No se pudo copiar';
                setTimeout(() => { copiar.textContent = 'Copiar'; }, 1500);
            };
            Promise.resolve(navigator.clipboard?.writeText(texto) ?? Promise.reject())
                .then(() => aviso(true), () => aviso(copiarConSeleccion(texto)));
        });
        barra.append(modos, copiar);
        return barra;
    }

    // -- nodos --------------------------------------------------------------------------------------
    _nodo(nodo, profundidad, vistos, ambito) {
        if (profundidad >= MAX_PROFUNDIDAD || vistos.has(nodo.id)) return document.createTextNode('');
        if (this.piezas && !esVisible(nodo, this.estado)) return document.createTextNode('');
        vistos.add(nodo.id);
        const p = this._props(nodo.props ?? {}, ambito);
        const dibujar = (this.piezas && PIEZAS[nodo.component]) || COMPONENTES[nodo.component]
            || PROPIOS.get(nodo.component)?.dibujar;
        if (!dibujar) return document.createTextNode('');          // fuera del catálogo: no se dibuja
        if (this.piezas && nodo.component === 'Text' && nodo.props?.value) {
            const valor = formatear(p.value, this._slot(nodo.props.value.$, ambito)[1]);
            p.text = typeof p.text === 'string' && p.text ? p.text + ' ' + valor : valor;   // «Margen: 20,0 %»
        }
        if (this.piezas && nodo.component === 'Data') ambito = { ...ambito, [p.name]: this._datos(nodo, ambito) };
        const el = dibujar.call(this, nodo, p, ambito);
        el.dataset.nodo = nodo.id;
        if (this.piezas && p.rojo === true) el.classList.add('ui-rojo');
        if (DE_CONTENEDOR.has(nodo.component) || PROPIOS.get(nodo.component)?.hijos
            || (this.piezas && nodo.component === 'Data')) {
            const destino = el.querySelector(':scope > .ui-hijos') ?? el;
            for (const id of nodo.children ?? []) {
                const hijo = this.nodos.get(id);
                destino.append(hijo ? this._nodo(hijo, profundidad + 1, vistos, ambito) : hueco());
            }
        }
        return el;
    }

    /** Las props con sus `{"$": "clave"}` ya leídos del estado (o de un slot `x.y` de Calc o de un Data). */
    _props(props, ambito = {}) {
        const salida = {};
        for (const [clave, valor] of Object.entries(props)) {
            salida[clave] = clave !== 'si' && valor && typeof valor === 'object' && !Array.isArray(valor) && '$' in valor
                ? this._slot(valor.$, ambito)[0] : valor;
            // Con las piezas, los enlaces dentro de una `query` también llegan leídos (null mientras falte uno).
            if (this.piezas && clave === 'query' && valor && typeof valor === 'object') {
                salida[clave] = argumentos(valor, k => this._slot(k, ambito)[0]);
            }
        }
        return salida;
    }

    /** [valor, tipo] de una clave: `x.y` es la salida `y` del Data `x` que la rodea o del Calc `x`. */
    _slot(clave, ambito = {}) {
        if (!this.piezas || !clave.includes('.')) return [this.estado[clave], null];
        const [prefijo, slot] = clave.split('.', 2);
        const fuente = ambito[prefijo] ?? this.derivados[prefijo];
        return fuente ? [fuente.valores[slot] ?? null, fuente.tipos[slot] ?? null] : [undefined, null];
    }

    /** Las salidas de cada Calc con el estado de ahora. */
    _calcular() {
        this.derivados = {};
        for (const nodo of this.nodos.values()) {
            if (nodo.component !== 'Calc' || !SALIDAS_CALC[nodo.props?.preset]) continue;
            const p = this._props(nodo.props, {});
            this.derivados[p.name] = { valores: calc(p.preset, p.compra, p.venta, p.cantidad),
                                       tipos: SALIDAS_CALC[p.preset] };
        }
    }

    /** Las salidas de un Data: pide cuando están todos sus argumentos y sólo vale la última respuesta. */
    _datos(nodo, ambito) {
        const p = nodo.props ?? {};
        const tipos = this.consultas[p.query?.tipo]?.out ?? {};
        let entrada = this.datos.get(nodo.id);
        const args = argumentos(p.query ?? {}, clave => this._slot(clave, ambito)[0]);
        if (args === null) return { valores: {}, tipos };       // espera a tener sus argumentos
        const clave = JSON.stringify(args);
        if (!entrada || entrada.clave !== clave) {
            const seq = (entrada?.seq ?? 0) + 1;
            entrada = { clave, seq, valores: entrada?.valores ?? {} };
            this.datos.set(nodo.id, entrada);
            if (this.consultar) {
                Promise.resolve(this.consultar(args)).then(valores => {
                    const actual = this.datos.get(nodo.id);
                    if (actual?.seq !== seq) return;              // llegó otra después: gana la última
                    actual.valores = valores ?? {};
                    this.dibujar();
                }, () => {});
            }
        }
        return { valores: entrada.valores, tipos };
    }

    /** Si todos los campos visibles del subárbol valen (lo que bloquea el envío de un Form). */
    _valido(nodo, ambito, vistos = new Set()) {
        if (!nodo || vistos.has(nodo.id) || !esVisible(nodo, this.estado)) return true;
        vistos.add(nodo.id);
        const p = this._props(nodo.props ?? {}, ambito);
        if ((nodo.component === 'Input' || nodo.component === 'Select')
            && validarCampo(nodo.component, p, this.estado[p.name]) !== null) return false;
        if (nodo.component === 'Data') ambito = { ...ambito, [p.name]: this._datos(nodo, ambito) };
        return (nodo.children ?? []).every(id => this._valido(this.nodos.get(id), ambito, vistos));
    }

    /** Ejecuta las acciones de un evento. `item`: la fila o el id del elemento, en row_press e item_press. */
    _evento(nodo, evento, item) {
        const spec = nodo.events?.[evento];
        if (!spec || this.enviando !== null) return;
        for (const accion of Array.isArray(spec) ? spec : [spec]) {
            if ('set' in accion) {
                Object.assign(this.estado, accion.set);
            } else if ('open_url' in accion) {
                if (/^https:\/\//.test(accion.open_url)) this.alAbrir(accion.open_url);
            } else if ('send' in accion) {
                const values = {};
                for (const clave of accion.with ?? Object.keys(this.estado)) values[clave] = this.estado[clave] ?? null;
                this.enviando = nodo.id;
                const data = { view: this.nombre, rev: this.rev, node: nodo.id, event: evento, name: accion.send,
                               values, ...(item !== undefined && { item }) };
                this.alEnviar(data, resumen(data, etiquetaDe(this._props(nodo.props ?? {}))));
            }
        }
        this.dibujar();
    }

    _desactivado(nodo) {
        return this.enviando !== null;
    }
}

const DE_CONTENEDOR = new Set(['Stack', 'Card', 'Form']);

/** El campo con su error debajo (el error sólo se dibuja; nunca vuelve en un `send`). */
function conError(envoltura, codigo, props) {
    if (codigo) envoltura.append(crear('span', 'ui-error', mensaje(codigo, props)));
    return envoltura;
}

// Con la opción `piezas`: cómo se dibujan Input, Select y Form con la semántica de PACAI, y Calc y Data.
const PIEZAS = {
    Input(nodo, p) {
        const multilinea = p.kind === 'multiline';
        const control = multilinea ? crear('textarea', 'ui-control') : crear('input', 'ui-control');
        if (!multilinea) control.type = { email: 'email', date: 'date' }[p.kind] ?? 'text';
        if (p.kind === 'number') control.inputMode = 'decimal';
        control.name = p.name;
        control.value = this.estado[p.name] ?? '';
        if (p.placeholder) control.placeholder = p.placeholder;
        control.dataset.nodo = nodo.id;
        control.addEventListener('input', () => {
            this.estado[p.name] = control.value;     // el texto tal cual: se lee con el parseo canónico
            this.tocados.add(nodo.id);
            if (nodo.events?.change) this._evento(nodo, 'change');
            else this.dibujar();
        });
        const codigo = this.tocados.has(nodo.id) ? validarCampo('Input', p, this.estado[p.name]) : null;
        control.setAttribute('aria-invalid', codigo ? 'true' : 'false');
        return conError(campo(p.label ?? p.name, control, p.required), codigo, p);
    },
    Select(nodo, p) {
        const control = crear('select', 'ui-control');
        control.name = p.name;
        control.multiple = Boolean(p.multiple);
        control.dataset.nodo = nodo.id;
        const actual = this.estado[p.name];
        if (p.required === true && !p.multiple) {
            const nada = crear('option', null, '—');
            nada.value = '';
            nada.selected = actual === '' || actual === undefined;
            control.append(nada);
        }
        for (const opcion of Array.isArray(p.options) ? p.options : []) {
            const el = crear('option', null, opcion.label ?? opcion.value);
            el.value = opcion.value;
            el.selected = Array.isArray(actual) ? actual.some(a => igual(a, opcion.value)) : igual(actual, opcion.value);
            control.append(el);
        }
        control.addEventListener('change', () => {
            this.estado[p.name] = control.multiple ? [...control.selectedOptions].map(o => o.value) : control.value;
            this.tocados.add(nodo.id);
            if (nodo.events?.change) this._evento(nodo, 'change');
            else this.dibujar();
        });
        const codigo = this.tocados.has(nodo.id) ? validarCampo('Select', p, actual) : null;
        return conError(campo(p.label ?? p.name, control, p.required), codigo, p);
    },
    Form(nodo, p, ambito) {
        const form = crear('form', 'ui-form');
        form.append(crear('div', 'ui-hijos'));
        const valido = this._valido(nodo, ambito);
        const boton = crear('button', 'ui-boton ui-boton-primary', p.submit_label ?? 'Enviar');
        boton.type = 'submit';
        boton.disabled = this._desactivado(nodo) || !valido;     // la norma: su propio envío espera a que todo valga
        if (this.enviando === nodo.id) boton.classList.add('ui-esperando');
        form.append(boton);
        form.noValidate = true;
        form.addEventListener('submit', ev => {
            ev.preventDefault();
            if (this._valido(nodo, ambito)) this._evento(nodo, 'submit');
        });
        return form;
    },
    Calc() {
        const el = crear('span', 'ui-calc');       // no se ve: publica sus salidas
        el.hidden = true;
        return el;
    },
    Data() {
        const el = crear('div', 'ui-data');
        el.append(crear('div', 'ui-hijos'));
        return el;
    },
};
// Componentes propios del cliente (los que declara en `render.ui.components`): nombre → {dibujar, hijos}.
const PROPIOS = new Map();

/**
 * Registra cómo dibuja este cliente uno de sus componentes propios. `dibujar(nodo, props, util)` devuelve un
 * elemento; `props` ya trae los enlaces `{"$": clave}` resueltos y `util` da `crear(etiqueta, clase, texto)` y
 * `evento(nombre, item)` para disparar los eventos del nodo. Con `hijos`, sus hijos van dentro de `.ui-hijos`
 * (o al final del elemento). Como en los base, nada del agente se interpreta como HTML: usa `crear` o textContent.
 */
export function registrarComponente(nombre, dibujar, { hijos = false } = {}) {
    if (COMPONENTES[nombre]) throw new Error('No se puede sustituir un componente base: ' + nombre);
    PROPIOS.set(nombre, {
        hijos,
        dibujar(nodo, props) {
            return dibujar(nodo, props, { crear, evento: (nombreEvento, item) => this._evento(nodo, nombreEvento, item) });
        },
    });
}

/**
 * Lo que el usuario «dijo» con una acción, para la conversación y para un cliente que no dibuje pantallas. Es el
 * mismo texto que pone la app de Mac (`ScreenActionRequest.summary`).
 */
export function resumen(data, etiqueta) {
    const que = `«${etiqueta || data.name}»`;
    if (data.event === 'submit') return 'Envié el formulario ' + que;
    if (data.event === 'row_press' || data.event === 'item_press') {
        return 'Elegí ' + (data.item === undefined ? que : describir(data.item));
    }
    return 'Pulsé ' + que;
}

function etiquetaDe(props) {
    return ['label', 'submit_label', 'title'].map(k => props[k]).find(v => typeof v === 'string' && v) ?? null;
}

/** Una fila como «T-12 · Alta · Login falla» (sus tres primeras claves en orden alfabético); un id, tal cual. */
function describir(item) {
    if (!item || typeof item !== 'object') return `«${item}»`;
    const partes = Object.keys(item).sort().slice(0, 3).map(k => item[k]).filter(v => v !== null && v !== '');
    return '«' + partes.join(' · ') + '»';
}

// -- la pantalla como texto ----------------------------------------------------------------------------
// Lo mismo que `Screen.markdown` y `Screen.rawJSON` en la app de Mac (alfalfa-mac/…/ScreenText.swift).

/** Un valor como se ve en una celda: los booleanos «Sí»/«No», las listas separadas por comas, null vacío. */
function comoCelda(valor) {
    if (valor === null || valor === undefined) return '';
    if (typeof valor === 'boolean') return valor ? 'Sí' : 'No';
    if (Array.isArray(valor)) return valor.map(comoCelda).join(', ');
    if (typeof valor === 'object') return '';
    return String(valor);
}

const enLinea = texto => String(texto).split(/\r?\n/).filter(Boolean).join(' ');

function tablaMarkdown(cabecera, filas) {
    const fila = celdas => '| ' + celdas.map(c => enLinea(c).replaceAll('|', '\\|')).join(' | ') + ' |';
    return [fila(cabecera), '|' + ' --- |'.repeat(cabecera.length), ...filas.map(fila)].join('\n');
}

/**
 * Una pantalla como markdown para copiarla: títulos y textos tal cual, una tabla como tabla, las tarjetas como lista,
 * los campos con su etiqueta y su valor actual y los gráficos como sus datos. Los botones no salen.
 */
export function textoDePantalla(nodos, estado = {}, alt = '') {
    const mapa = nodos instanceof Map ? nodos : new Map(Object.entries(nodos ?? {}));
    const raiz = mapa.get('root');
    if (!raiz) return alt ?? '';
    const leer = (nodo, clave) => {
        const valor = nodo.props?.[clave];
        return valor && typeof valor === 'object' && !Array.isArray(valor) && '$' in valor ? estado[valor.$] : valor;
    };
    const texto = (nodo, clave) => comoCelda(leer(nodo, clave));
    const hijosDe = nodo => [...new Set(nodo.children ?? [])].map(id => mapa.get(id)).filter(Boolean);
    const etiquetas = nodo => {
        const tags = (Array.isArray(leer(nodo, 'tags')) ? leer(nodo, 'tags') : []).map(comoCelda).filter(Boolean);
        return tags.map(t => '`' + t + '`').join(' ');
    };
    const hijos = (nodo, prof) => {
        const lista = hijosDe(nodo);
        if (lista.length > 1 && lista.every(h => h.component === 'Card')) {
            return [lista.map(h => tarjeta(h, prof + 1)).join('\n')];
        }
        return lista.flatMap(h => bloques(h, prof + 1));
    };
    const tarjeta = (nodo, prof) => {
        const titulo = [texto(nodo, 'title'), texto(nodo, 'subtitle')].filter(Boolean);
        let primera = titulo.length ? '- **' + enLinea(titulo[0]) + '**' : '-';
        if (titulo.length > 1) primera += ' · ' + enLinea(titulo[1]);
        const dentro = [texto(nodo, 'meta'), etiquetas(nodo), texto(nodo, 'detail')].filter(Boolean)
            .concat(hijos(nodo, prof));
        return [primera, ...dentro.map(b => b.split('\n').map(l => '  ' + l).join('\n'))].join('\n');
    };
    const bloques = (nodo, prof) => {
        if (prof >= MAX_PROFUNDIDAD) return [];
        switch (nodo.component) {
        case 'Text': {
            const t = texto(nodo, 'text');
            if (!t) return [];
            const estilo = texto(nodo, 'style');
            if (estilo === 'title') return ['## ' + enLinea(t)];
            if (estilo === 'subtitle') return ['### ' + enLinea(t)];
            if (estilo === 'caption' || estilo === 'muted') return ['*' + enLinea(t) + '*'];
            return [t];
        }
        case 'Card': {
            if (prof > 0) return [tarjeta(nodo, prof)];
            const cabeza = [];
            if (texto(nodo, 'title')) cabeza.push('## ' + enLinea(texto(nodo, 'title')));
            cabeza.push(...[texto(nodo, 'subtitle'), texto(nodo, 'meta'), etiquetas(nodo)].filter(Boolean));
            return [cabeza.join('\n'), texto(nodo, 'detail')].filter(Boolean).concat(hijos(nodo, prof));
        }
        case 'Table': {
            const columnas = (Array.isArray(leer(nodo, 'columns')) ? leer(nodo, 'columns') : [])
                .filter(c => c && typeof c === 'object');
            if (!columnas.length) return [];
            const filas = Array.isArray(leer(nodo, 'rows')) ? leer(nodo, 'rows') : [];
            return [tablaMarkdown(columnas.map(c => comoCelda(c.label ?? c.key)),
                filas.map(f => columnas.map(c => comoCelda(f?.[comoCelda(c.key)]))))];
        }
        case 'List': {
            const items = (Array.isArray(leer(nodo, 'items')) ? leer(nodo, 'items') : [])
                .filter(i => i && typeof i === 'object');
            if (!items.length) return [];
            return [items.map(i => '- ' + enLinea(comoCelda(i.title))
                + (comoCelda(i.subtitle) ? ' — ' + enLinea(comoCelda(i.subtitle)) : '')).join('\n')];
        }
        case 'Input': case 'Select': {
            const nombre = typeof nodo.props?.name === 'string' ? nodo.props.name : nodo.id;
            const etiqueta = texto(nodo, 'label') || nombre;
            const actual = estado[nombre];
            let vistos = Array.isArray(actual) ? actual : actual === null || actual === undefined || actual === '' ? [] : [actual];
            vistos = vistos.map(comoCelda);
            if (nodo.component === 'Select') {
                const opciones = Array.isArray(leer(nodo, 'options')) ? leer(nodo, 'options') : [];
                vistos = vistos.map(v => comoCelda(opciones.find(o => comoCelda(o?.value) === v)?.label) || v);
            }
            const valor = vistos.join(', ');
            const cabeza = '**' + enLinea(etiqueta) + ':**';
            if (nodo.props?.kind === 'multiline' && valor.includes('\n')) return [cabeza + '\n' + valor];
            return [cabeza + ' ' + (valor ? enLinea(valor) : '—')];
        }
        case 'Chart': {
            const series = (Array.isArray(leer(nodo, 'series')) ? leer(nodo, 'series') : [])
                .filter(s => s && Array.isArray(s.values));
            if (!series.length) return [];
            const etiquetasEje = (Array.isArray(leer(nodo, 'labels')) ? leer(nodo, 'labels') : []).map(comoCelda);
            const cuantas = Math.max(etiquetasEje.length, ...series.map(s => s.values.length));
            const cabecera = ['Etiqueta', ...series.map((s, i) => comoCelda(s.name) || 'Serie ' + (i + 1))];
            const filas = Array.from({ length: cuantas }, (_, i) =>
                [i < etiquetasEje.length ? etiquetasEje[i] : String(i + 1), ...series.map(s => comoCelda(s.values[i]))]);
            return [tablaMarkdown(cabecera, filas)];
        }
        case 'Image': {
            const src = texto(nodo, 'src');
            const altImagen = texto(nodo, 'alt');
            if (src.startsWith('https:')) return ['![' + enLinea(altImagen) + '](' + src + ')'];
            return altImagen ? ['*Imagen:* ' + enLinea(altImagen)] : [];
        }
        case 'Code': {
            const codigo = texto(nodo, 'code');
            const valla = codigo.includes('```') ? '~~~~' : '```';
            return [valla + texto(nodo, 'language') + '\n' + codigo + '\n' + valla];
        }
        case 'Button': return [];
        default: return hijos(nodo, prof);              // Stack, Form y los componentes propios
        }
    };
    return bloques(raiz, 0).join('\n\n');
}

/** Si el navegador no deja usar el portapapeles (un webview, una página sin permiso), copia seleccionando el texto. */
function copiarConSeleccion(texto) {
    const area = crear('textarea', null, texto);
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* sin portapapeles */ }
    area.remove();
    return ok;
}

function ordenado(valor) {
    if (Array.isArray(valor)) return valor.map(ordenado);
    if (valor && typeof valor === 'object') {
        return Object.fromEntries(Object.keys(valor).sort().map(k => [k, ordenado(valor[k])]));
    }
    return valor;
}

/**
 * El bloque `pacai.ui/1` que deja la pantalla como se ve: un `replace` con sus nodos en el orden del árbol (los
 * sueltos al final) y el estado de ahora. Lo que tiene el cliente es siempre JSON: con PACAI UI Lang, el motor ya lo
 * tradujo antes de mandarlo.
 */
export function jsonDePantalla(nombre, rev, nodos, estado = {}, alt = '', placement = null) {
    const mapa = nodos instanceof Map ? nodos : new Map(Object.entries(nodos ?? {}));
    const vistos = new Set();
    const lista = [];
    const visitar = id => {
        const nodo = mapa.get(id);
        if (!nodo || vistos.has(id)) return;
        vistos.add(id);
        lista.push(nodo);
        (nodo.children ?? []).forEach(visitar);
    };
    visitar('root');
    for (const id of [...mapa.keys()].sort()) if (!vistos.has(id)) lista.push(mapa.get(id));
    const data = { view: nombre, op: 'replace', rev, nodes: lista };
    if (Object.keys(estado ?? {}).length) data.state = estado;
    if (alt) data.alt = alt;
    if (placement && placement !== 'inline') data.placement = placement;
    return JSON.stringify(ordenado(data), null, 2);
}

function abrirConConfirmacion(url) {
    if (confirm('¿Abrir ' + url + '?')) window.open(url, '_blank', 'noopener,noreferrer');
}

function crear(etiqueta, clase, texto) {
    const el = document.createElement(etiqueta);
    if (clase) el.className = clase;
    if (texto !== undefined && texto !== null) el.textContent = String(texto);
    return el;
}

function hueco() {
    return crear('div', 'ui-hueco');                 // un hijo que aún no llegó
}

function campo(etiqueta, control, requerido) {
    const envoltura = crear('label', 'ui-campo');
    envoltura.append(crear('span', 'ui-etiqueta', (etiqueta ?? '') + (requerido ? ' *' : '')), control);
    return envoltura;
}

const COMPONENTES = {
    Stack(nodo, p) {
        const sentido = { horizontal: 'fila', grid: 'rejilla' }[p.direction] ?? 'columna';
        return crear('div', `ui-stack ui-${sentido} ui-gap-${p.gap ?? 'medium'}`);
    },
    Card(nodo, p) {
        const el = crear('section', 'ui-card');
        const imagen = typeof p.image === 'string' && /^(https:\/\/|data:image\/(png|jpeg|gif|webp);base64,)/.test(p.image)
            && (this.imagenesRemotas || !p.image.startsWith('https:'));
        if (imagen) {
            const img = crear('img', 'ui-card-imagen');
            img.src = p.image;
            img.alt = '';                              // decorativa: el título ya dice qué es
            img.referrerPolicy = 'no-referrer';
            img.loading = 'lazy';
            el.append(img);
        }
        if (p.meta) el.append(crear('p', 'ui-card-meta', p.meta));
        if (p.title) el.append(crear('h3', 'ui-card-titulo', p.title));
        if (p.subtitle) el.append(crear('p', 'ui-card-subtitulo', p.subtitle));
        if (Array.isArray(p.tags) && p.tags.length) {
            const chips = crear('div', 'ui-card-tags');
            for (const tag of p.tags.slice(0, 8)) chips.append(crear('span', 'ui-chip', tag));
            el.append(chips);
        }
        if (p.detail) {
            // El detalle se despliega aquí, sin volver a llamar al agente.
            const detalle = crear('details', 'ui-card-detalle');
            detalle.append(crear('summary', null, 'Ver más'), crear('p', null, p.detail));
            el.append(detalle);
        }
        el.append(crear('div', 'ui-hijos'));
        if (nodo.events?.press) {
            el.classList.add('ui-pulsable');
            el.tabIndex = 0;
            el.addEventListener('click', ev => {
                // Un control dentro de la tarjeta tiene su propio evento.
                if (ev.target.closest('.ui-card') === el && !ev.target.closest('button, input, select, textarea, tr, li, details')) {
                    this._evento(nodo, 'press');
                }
            });
        }
        return el;
    },
    Text(nodo, p) {
        return crear(p.style === 'title' ? 'h2' : p.style === 'subtitle' ? 'h3' : 'p', `ui-text ui-text-${p.style ?? 'body'}`, p.text);
    },
    Table(nodo, p) {
        const envoltura = crear('div', 'ui-tabla-envoltura');
        const tabla = crear('table', 'ui-tabla');
        const cabecera = tabla.createTHead().insertRow();
        for (const col of p.columns ?? []) cabecera.append(crear('th', null, col.label ?? col.key));
        const cuerpo = tabla.createTBody();
        for (const fila of Array.isArray(p.rows) ? p.rows : []) {
            const tr = cuerpo.insertRow();
            for (const col of p.columns ?? []) tr.append(crear('td', null, fila?.[col.key] ?? ''));
            if (nodo.events?.row_press) {
                tr.classList.add('ui-pulsable');
                tr.tabIndex = 0;
                tr.addEventListener('click', () => this._evento(nodo, 'row_press', fila));
                tr.addEventListener('keydown', ev => { if (ev.key === 'Enter') this._evento(nodo, 'row_press', fila); });
            }
        }
        envoltura.append(tabla);
        return envoltura;
    },
    List(nodo, p) {
        const lista = crear('ul', 'ui-lista');
        for (const item of Array.isArray(p.items) ? p.items : []) {
            const li = crear('li', null);
            li.append(crear('span', 'ui-lista-titulo', item.title));
            if (item.subtitle) li.append(crear('span', 'ui-lista-subtitulo', item.subtitle));
            if (nodo.events?.item_press) {
                li.classList.add('ui-pulsable');
                li.tabIndex = 0;
                li.addEventListener('click', () => this._evento(nodo, 'item_press', item.id));
            }
            lista.append(li);
        }
        return lista;
    },
    Button(nodo, p) {
        const boton = crear('button', `ui-boton ui-boton-${p.variant ?? 'secondary'}`, p.label);
        boton.type = 'button';
        boton.disabled = Boolean(p.disabled) || this._desactivado(nodo);
        if (this.enviando === nodo.id) boton.classList.add('ui-esperando');
        boton.addEventListener('click', () => this._evento(nodo, 'press'));
        return boton;
    },
    Form(nodo, p) {
        const form = crear('form', 'ui-form');
        form.append(crear('div', 'ui-hijos'));
        const boton = crear('button', 'ui-boton ui-boton-primary', p.submit_label ?? 'Enviar');
        boton.type = 'submit';
        boton.disabled = this._desactivado(nodo);
        if (this.enviando === nodo.id) boton.classList.add('ui-esperando');
        form.append(boton);
        form.addEventListener('submit', ev => {
            ev.preventDefault();
            if (form.reportValidity()) this._evento(nodo, 'submit');
        });
        return form;
    },
    Input(nodo, p) {
        const control = p.kind === 'multiline' ? crear('textarea', 'ui-control') : crear('input', 'ui-control');
        if (p.kind !== 'multiline') control.type = { number: 'number', email: 'email', date: 'date' }[p.kind] ?? 'text';
        control.name = p.name;
        control.value = this.estado[p.name] ?? '';
        if (p.placeholder) control.placeholder = p.placeholder;
        control.required = Boolean(p.required);
        control.dataset.nodo = nodo.id;
        control.addEventListener('input', () => {
            this.estado[p.name] = p.kind === 'number' && control.value !== '' ? Number(control.value) : control.value;
            if (nodo.events?.change) this._evento(nodo, 'change');
        });
        return campo(p.label ?? p.name, control, p.required);
    },
    Select(nodo, p) {
        const control = crear('select', 'ui-control');
        control.name = p.name;
        control.multiple = Boolean(p.multiple);
        control.dataset.nodo = nodo.id;
        const actual = this.estado[p.name];
        // Con `required` empieza sin opción elegida, y el Form no se envía hasta elegir una (validación del navegador).
        control.required = Boolean(p.required);
        if (control.required && !control.multiple) {
            const nada = crear('option', null, '—');
            nada.value = '';
            nada.selected = actual === undefined || actual === null || actual === '';
            control.append(nada);
        }
        for (const opcion of Array.isArray(p.options) ? p.options : []) {
            const el = crear('option', null, opcion.label ?? opcion.value);
            el.value = opcion.value;
            el.selected = Array.isArray(actual) ? actual.includes(opcion.value) : actual === opcion.value;
            control.append(el);
        }
        control.addEventListener('change', () => {
            this.estado[p.name] = control.multiple ? [...control.selectedOptions].map(o => o.value) : control.value;
            if (nodo.events?.change) this._evento(nodo, 'change');
        });
        return campo(p.label ?? p.name, control, control.required);
    },
    Chart(nodo, p) {
        return grafico(p);
    },
    Image(nodo, p) {
        const valida = typeof p.src === 'string' && /^(https:\/\/|data:image\/(png|jpeg|gif|webp);base64,)/.test(p.src);
        if (!valida || (!this.imagenesRemotas && p.src.startsWith('https:'))) {
            return crear('p', 'ui-text ui-text-muted', p.alt ?? '');
        }
        const img = crear('img', 'ui-imagen');
        img.src = p.src;
        img.alt = p.alt ?? '';
        img.referrerPolicy = 'no-referrer';
        return img;
    },
    Code(nodo, p) {
        const pre = crear('pre', 'ui-codigo');
        pre.append(crear('code', null, p.code));
        if (p.language) pre.dataset.lenguaje = p.language;
        return pre;
    },
};

// -- gráficos en SVG ------------------------------------------------------------------------------------
function svg(etiqueta, atributos = {}) {
    const el = document.createElementNS(SVG, etiqueta);
    for (const [k, v] of Object.entries(atributos)) el.setAttribute(k, String(v));
    return el;
}

function grafico(p) {
    const figura = crear('figure', 'ui-grafico');
    const series = (Array.isArray(p.series) ? p.series : []).filter(s => Array.isArray(s.values));
    const etiquetas = Array.isArray(p.labels) ? p.labels : [];
    const ancho = 360;
    const alto = 180;
    const lienzo = svg('svg', { viewBox: `0 0 ${ancho} ${alto + 24}`, role: 'img' });
    lienzo.append(svg('title'));
    lienzo.firstChild.textContent = series.map(s => `${s.name ?? ''}: ${s.values.join(', ')}`).join('; ');
    const n = Math.max(etiquetas.length, ...series.map(s => s.values.length), 1);
    if (p.kind === 'pie') {
        const valores = series[0]?.values ?? [];
        const total = valores.reduce((a, b) => a + Math.max(0, b), 0) || 1;
        let angulo = -Math.PI / 2;
        valores.forEach((v, i) => {
            const fin = angulo + (Math.max(0, v) / total) * Math.PI * 2;
            const [cx, cy, r] = [alto / 2 + 10, alto / 2 + 4, alto / 2 - 4];
            const grande = fin - angulo > Math.PI ? 1 : 0;
            const d = `M${cx},${cy} L${cx + r * Math.cos(angulo)},${cy + r * Math.sin(angulo)} `
                + `A${r},${r} 0 ${grande} 1 ${cx + r * Math.cos(fin)},${cy + r * Math.sin(fin)} Z`;
            lienzo.append(svg('path', { d, class: `ui-serie-${i % 6}` }));
            const leyenda = svg('text', { x: alto + 30, y: 20 + i * 18, class: 'ui-eje' });
            leyenda.textContent = `${etiquetas[i] ?? i + 1}: ${v}`;
            lienzo.append(svg('rect', { x: alto + 16, y: 10 + i * 18, width: 10, height: 10, class: `ui-serie-${i % 6}` }), leyenda);
            angulo = fin;
        });
    } else {
        const maximo = Math.max(...series.flatMap(s => s.values), 1);
        const paso = ancho / n;
        const y = v => alto - (Math.max(0, v) / maximo) * (alto - 10);
        lienzo.append(svg('line', { x1: 0, y1: alto, x2: ancho, y2: alto, class: 'ui-eje-linea' }));
        series.forEach((s, j) => {
            if (p.kind === 'line') {
                const puntos = s.values.map((v, i) => `${paso * i + paso / 2},${y(v)}`).join(' ');
                lienzo.append(svg('polyline', { points: puntos, class: `ui-linea ui-trazo-${j % 6}` }));
            } else {
                const ancho1 = (paso * 0.7) / series.length;
                s.values.forEach((v, i) => lienzo.append(svg('rect', {
                    x: paso * i + paso * 0.15 + ancho1 * j, y: y(v), width: ancho1, height: alto - y(v),
                    class: `ui-serie-${j % 6}`, rx: 2,
                })));
            }
        });
        etiquetas.forEach((e, i) => {
            const t = svg('text', { x: paso * i + paso / 2, y: alto + 16, class: 'ui-eje', 'text-anchor': 'middle' });
            t.textContent = e;
            lienzo.append(t);
        });
    }
    figura.append(lienzo);
    if (series.length > 1 && p.kind !== 'pie') {
        const leyenda = crear('figcaption', 'ui-leyenda');
        series.forEach((s, j) => {
            const item = crear('span', null);
            item.append(crear('i', `ui-muestra ui-fondo-${j % 6}`), document.createTextNode(s.name ?? ''));
            leyenda.append(item);
        });
        figura.append(leyenda);
    }
    return figura;
}

// -- piezas declarativas (prototipo, sólo con la opción `piezas`) ---------------------------------------------
// La misma semántica que `alfalfa/pacai/piezas.py`; las dos pasan `tests/conformidad/pacai-ui-piezas.json`.
// Números en coma fija (BigInt ×10⁶, menos de 18 cifras, half-even por operación), sin float ni locale.

const ESCALA = 1000000n;
const TOPE = 10n ** 18n;
const RE_DECIMAL = /^(-?)(\d{1,12})(?:[.,](\d{1,6}))?$/;
const RE_FECHA = /^(\d{4})-(\d{2})-(\d{2})$/;
const RE_CORREO = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const SALIDAS_CALC = { margen: { pct: 'fraccion', u: 'dinero', total: 'dinero', perdida: 'bool' } };

const abs = n => (n < 0n ? -n : n);

/** Lo que teclea el usuario (o un número del documento) en millonésimas; null si no es un número canónico. */
export function decimal(valor) {
    if (valor === null || valor === undefined || typeof valor === 'boolean') return null;
    if (typeof valor === 'number') {
        if (!Number.isFinite(valor)) return null;
        if (Number.isInteger(valor)) {
            const n = BigInt(valor) * ESCALA;
            return abs(n) < TOPE ? n : null;
        }
        valor = String(valor);
    }
    if (typeof valor !== 'string') return null;
    const m = RE_DECIMAL.exec(valor.trim());
    if (!m) return null;
    const n = BigInt(m[2]) * ESCALA + BigInt((m[3] ?? '').padEnd(6, '0'));
    return m[1] && n ? -n : n;
}

function redondear(n, d) {
    let q = abs(n) / abs(d);
    const r = abs(n) % abs(d);
    if (2n * r > abs(d) || (2n * r === abs(d) && q % 2n === 1n)) q += 1n;
    return (n < 0n) !== (d < 0n) && q ? -q : q;
}

const acotar = n => (n === null || abs(n) >= TOPE ? null : n);

/** Millonésimas → texto canónico (`-12.5`, `3`). */
export function aTexto(n) {
    if (n === null || n === undefined) return null;
    const entera = abs(n) / ESCALA;
    const fraccion = abs(n) % ESCALA;
    const texto = String(entera) + (fraccion ? '.' + String(fraccion).padStart(6, '0').replace(/0+$/, '') : '');
    return (n < 0n ? '-' : '') + texto;
}

/** Las salidas de un Calc: números en texto canónico o null, y `perdida` bool o null. */
export function calc(preset, compra, venta, cantidad) {
    if (preset !== 'margen') throw new Error('Calc sólo conoce el preset margen.');
    const c = decimal(compra), v = decimal(venta), n = decimal(cantidad);
    const u = c !== null && v !== null ? acotar(v - c) : null;
    const pct = u !== null && v ? acotar(redondear(u * ESCALA, v)) : null;
    const total = u !== null && n !== null ? acotar(redondear(u * n, ESCALA)) : null;
    const perdida = c !== null && v !== null ? v < c : null;
    return { pct: aTexto(pct), u: aTexto(u), total: aTexto(total), perdida };
}

function miles(entera) {
    return String(entera).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

function numero(n, decimales) {
    const r = redondear(n, 10n ** BigInt(6 - decimales));
    const base = 10n ** BigInt(decimales);
    const texto = miles(abs(r) / base) + (decimales ? ',' + String(abs(r) % base).padStart(decimales, '0') : '');
    return (r < 0n ? '-' : '') + texto;
}

/** Cómo se escribe un slot tipado, igual en todos los clientes: miles con punto, decimales con coma, nulo «—». */
export function formatear(valor, tipo) {
    if (valor === null || valor === undefined) return '—';
    if (tipo === 'bool') return valor === true ? 'sí' : valor === false ? 'no' : '—';
    if (tipo === 'texto' || tipo === 'fecha') return typeof valor === 'string' ? valor : '—';
    const n = decimal(valor);
    if (n === null) return '—';
    if (tipo === 'dinero') return numero(n, 2);
    if (tipo === 'fraccion') return abs(n * 100n) < TOPE ? numero(n * 100n, 1) + ' %' : '—';
    if (tipo === 'entero') return numero(n, 0);
    if (tipo === 'numero') {
        const [entera, fraccion] = aTexto(abs(n)).split('.');
        return (n < 0n ? '-' : '') + miles(entera) + (fraccion ? ',' + fraccion : '');
    }
    return '—';
}

/** Igualdad de texto en NFC: `café` precompuesto y `cafe`+U+0301 son lo mismo. */
export function igual(a, b) {
    if (typeof a === 'string' && typeof b === 'string') return a.normalize('NFC') === b.normalize('NFC');
    return a === b;
}

const vacio = v => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)
    || (typeof v === 'string' && !v.trim());

function fechaValida(texto) {
    const m = typeof texto === 'string' ? RE_FECHA.exec(texto) : null;
    if (!m) return false;
    const [a, mes, dia] = [Number(m[1]), Number(m[2]), Number(m[3])];
    const bisiesto = (a % 4 === 0 && a % 100 !== 0) || a % 400 === 0;
    const dias = [31, bisiesto ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1];
    return a >= 1 && mes >= 1 && mes <= 12 && dia >= 1 && dia <= dias;
}

/** El código del primer error de un Input o Select (null si vale). `props` ya trae sus enlaces resueltos. */
export function validarCampo(componente, props, valor) {
    if (vacio(valor)) return props.required === true ? 'obligatorio' : null;
    if (componente === 'Select') {
        const opciones = (Array.isArray(props.options) ? props.options : []).map(o => o?.value);
        const elegidos = Array.isArray(valor) ? valor : [valor];
        return elegidos.every(e => opciones.some(o => igual(e, o))) ? null : 'opcion';
    }
    const kind = props.kind ?? 'text';
    if (kind === 'number') {
        const n = decimal(valor);
        if (n === null) return 'numero';
        const minimo = decimal(props.min), maximo = decimal(props.max), paso = decimal(props.step);
        if (minimo !== null && n < minimo) return 'minimo';
        if (maximo !== null && n > maximo) return 'maximo';
        if (paso !== null && paso > 0n && (n - (minimo ?? 0n)) % paso !== 0n) return 'paso';
        return null;
    }
    if (kind === 'date') {
        if (!fechaValida(valor)) return 'fecha';
        if (typeof props.min === 'string' && RE_FECHA.test(props.min) && valor < props.min) return 'minimo';
        if (typeof props.max === 'string' && RE_FECHA.test(props.max) && valor > props.max) return 'maximo';
        return null;
    }
    if (kind === 'email') return typeof valor === 'string' && RE_CORREO.test(valor.trim()) ? null : 'correo';
    return null;
}

/** El texto bajo el campo: sólo se dibuja, nunca vuelve en un `send`. */
export function mensaje(codigo, props) {
    const tipo = props.kind === 'date' ? 'fecha' : 'numero';
    return {
        obligatorio: 'Obligatorio.',
        numero: 'Escribe un número.',
        minimo: 'Mínimo ' + formatear(props.min, tipo) + '.',
        maximo: 'Máximo ' + formatear(props.max, tipo) + '.',
        paso: 'Usa pasos de ' + formatear(props.step, 'numero') + '.',
        fecha: 'Escribe una fecha (AAAA-MM-DD).',
        correo: 'Escribe un correo válido.',
        opcion: 'Elige una de las opciones.',
    }[codigo] ?? 'No es válido.';
}

const lista = nodos => (nodos instanceof Map ? [...nodos.values()] : Array.isArray(nodos) ? nodos : Object.values(nodos));

/** El estado con el valor de partida de cada Select: vacío con `required`; si no, su primera opción. */
export function estadoInicial(nodos, estado = {}) {
    const salida = { ...estado };
    for (const nodo of lista(nodos)) {
        const p = nodo.props ?? {};
        if (nodo.component !== 'Select' || typeof p.name !== 'string' || p.name in salida) continue;
        const opciones = Array.isArray(p.options) ? p.options : [];
        if (p.multiple) salida[p.name] = [];
        else if (p.required === true || !opciones.length) salida[p.name] = '';
        else salida[p.name] = opciones[0].value;
    }
    return salida;
}

export function esVisible(nodo, estado) {
    const si = nodo.props?.si;
    return !si || typeof si !== 'object' || igual(estado[si.$] ?? null, si.es);
}

/** Los ids que se dibujan, en DFS desde root: un nodo con `si` falso se salta con su subárbol. */
export function visibles(nodos, estado) {
    const porId = new Map(lista(nodos).map(n => [n.id, n]));
    const salida = [];
    const recorrer = (id, profundidad) => {
        const nodo = porId.get(id);
        if (!nodo || salida.includes(id) || profundidad > 12 || !esVisible(nodo, estado)) return;
        salida.push(id);
        for (const hijo of nodo.children ?? []) recorrer(hijo, profundidad + 1);
    };
    recorrer('root', 0);
    return salida;
}

/** Los argumentos de una `query` de Data con sus enlaces leídos; null mientras falte alguno (nulo o vacío). */
export function argumentos(valor, leer) {
    if (valor && typeof valor === 'object' && !Array.isArray(valor)) {
        const claves = Object.keys(valor);
        if (claves.length === 1 && claves[0] === '$' && typeof valor.$ === 'string') {
            const leido = leer(valor.$);
            return leido === null || leido === undefined || leido === '' ? null : leido;
        }
        const salida = {};
        for (const [k, v] of Object.entries(valor)) {
            salida[k] = argumentos(v, leer);
            if (salida[k] === null && v !== null) return null;
        }
        return salida;
    }
    if (Array.isArray(valor)) {
        const salida = valor.map(v => argumentos(v, leer));
        return salida.some((s, i) => s === null && valor[i] !== null) ? null : salida;
    }
    return valor;
}
