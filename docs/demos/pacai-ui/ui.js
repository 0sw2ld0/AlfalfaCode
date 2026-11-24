// Renderizador web del perfil `ui` de PACAI (catálogo base). Sin dependencias.
//
// Una `Vista` guarda los nodos, el estado y la revisión de una pantalla, aplica los bloques `pacai.ui/1` que manda
// el agente y la dibuja dentro de su contenedor. Todo se construye con createElement y textContent: ningún texto
// del agente se interpreta como HTML.

const SVG = 'http://www.w3.org/2000/svg';
const MAX_PROFUNDIDAD = 12;

export class Vista {
    /**
     * @param {string} nombre
     * @param {{alEnviar: (accion: object) => void}} opciones
     */
    constructor(nombre, { alEnviar }) {
        this.nombre = nombre;
        this.alEnviar = alEnviar;
        this.rev = 0;
        this.nodos = new Map();
        this.estado = {};
        this.placement = null;
        this.alt = '';
        this.enviando = null;          // id del nodo cuyo `send` espera respuesta
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
        for (const control of copia.querySelectorAll('button, input, select, textarea')) control.disabled = true;
        return copia;
    }

    dibujar() {
        const foco = document.activeElement;
        const conFoco = foco && this.elemento.contains(foco) ? foco.dataset.nodo : null;
        this.elemento.replaceChildren();
        if (this.alt) this.elemento.setAttribute('aria-label', this.alt);
        const raiz = this.nodos.get('root');
        if (raiz) this.elemento.append(this._nodo(raiz, 0, new Set()));
        if (conFoco) {
            const el = this.elemento.querySelector(`[data-nodo="${CSS.escape(conFoco)}"]`);
            (el?.matches('input, select, textarea, button') ? el : el?.querySelector('input, select, textarea'))?.focus();
        }
    }

    // -- nodos --------------------------------------------------------------------------------------
    _nodo(nodo, profundidad, vistos) {
        if (profundidad >= MAX_PROFUNDIDAD || vistos.has(nodo.id)) return document.createTextNode('');
        vistos.add(nodo.id);
        const p = this._props(nodo.props ?? {});
        const dibujar = COMPONENTES[nodo.component];
        if (!dibujar) return document.createTextNode('');          // fuera del catálogo: no se dibuja
        const el = dibujar.call(this, nodo, p);
        el.dataset.nodo = nodo.id;
        if (DE_CONTENEDOR.has(nodo.component)) {
            const destino = el.querySelector(':scope > .ui-hijos') ?? el;
            for (const id of nodo.children ?? []) {
                const hijo = this.nodos.get(id);
                destino.append(hijo ? this._nodo(hijo, profundidad + 1, vistos) : hueco());
            }
        }
        return el;
    }

    /** Las props con sus `{"$": "clave"}` ya leídos del estado. */
    _props(props) {
        const salida = {};
        for (const [clave, valor] of Object.entries(props)) {
            salida[clave] = valor && typeof valor === 'object' && !Array.isArray(valor) && '$' in valor
                ? this.estado[valor.$] : valor;
        }
        return salida;
    }

    /** Ejecuta las acciones de un evento. `item`: la fila o el id del elemento, en row_press e item_press. */
    _evento(nodo, evento, item) {
        const spec = nodo.events?.[evento];
        if (!spec || this.enviando !== null) return;
        for (const accion of Array.isArray(spec) ? spec : [spec]) {
            if ('set' in accion) {
                Object.assign(this.estado, accion.set);
            } else if ('open_url' in accion) {
                if (/^https:\/\//.test(accion.open_url) && confirm('¿Abrir ' + accion.open_url + '?')) {
                    window.open(accion.open_url, '_blank', 'noopener,noreferrer');
                }
            } else if ('send' in accion) {
                const values = {};
                for (const clave of accion.with ?? Object.keys(this.estado)) values[clave] = this.estado[clave];
                this.enviando = nodo.id;
                this.alEnviar({ view: this.nombre, rev: this.rev, node: nodo.id, event: evento, name: accion.send,
                                values, ...(item !== undefined && { item }) });
            }
        }
        this.dibujar();
    }

    _desactivado(nodo) {
        return this.enviando !== null;
    }
}

const DE_CONTENEDOR = new Set(['Stack', 'Card', 'Form']);

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
        return crear('div', `ui-stack ui-${p.direction === 'horizontal' ? 'fila' : 'columna'} ui-gap-${p.gap ?? 'medium'}`);
    },
    Card(nodo, p) {
        const el = crear('section', 'ui-card');
        if (p.title) el.append(crear('h3', 'ui-card-titulo', p.title));
        if (p.subtitle) el.append(crear('p', 'ui-card-subtitulo', p.subtitle));
        el.append(crear('div', 'ui-hijos'));
        if (nodo.events?.press) {
            el.classList.add('ui-pulsable');
            el.tabIndex = 0;
            el.addEventListener('click', ev => {
                // Un control dentro de la tarjeta tiene su propio evento.
                if (ev.target.closest('.ui-card') === el && !ev.target.closest('button, input, select, textarea, tr, li')) {
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
        return campo(p.label ?? p.name, control, false);
    },
    Chart(nodo, p) {
        return grafico(p);
    },
    Image(nodo, p) {
        if (typeof p.src !== 'string' || !/^(https:\/\/|data:image\/(png|jpeg|gif|webp);base64,)/.test(p.src)) {
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
