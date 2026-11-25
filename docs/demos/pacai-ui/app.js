// La página de chat de la demo: recibe los mensajes PACAI del servidor y los pinta.
// Las pantallas (`pacai.ui/1`) las dibuja `Vista` (ui.js); sus acciones vuelven como `input` con `pacai.ui.action/1`.
import { Vista } from './ui.js';

const chat = document.getElementById('chat');
const estado = document.getElementById('estado');
const redactor = document.getElementById('redactor');
const texto = document.getElementById('texto');
const parar = document.getElementById('parar');
const traza = document.getElementById('traza');
const listaTraza = document.getElementById('lista-traza');

let vistas = new Map();          // nombre → {vista, sitio}: sitio es el mensaje donde se ve ahora
let mensajes = new Map();        // message_id → elemento del mensaje
let raiz = null;                 // run_id de la ejecución raíz en curso
let sesion = null;

async function enviar(type, payload, replyTo) {
    const res = await fetch('/enviar', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ type, payload, ...(replyTo && { reply_to: replyTo }) }),
    });
    if (!res.ok) aviso('No se pudo enviar: ' + (await res.json()).error, 'error');
}

// -- pintar ---------------------------------------------------------------------------------------------
function crear(etiqueta, clase, contenido) {
    const el = document.createElement(etiqueta);
    if (clase) el.className = clase;
    if (contenido !== undefined) el.textContent = contenido;
    return el;
}

function alFinal() {
    chat.scrollTop = chat.scrollHeight;
}

function burbuja(rol) {
    const el = crear('article', 'mensaje mensaje-' + rol);
    chat.append(el);
    alFinal();
    return el;
}

function aviso(contenido, tipo = 'aviso') {
    chat.append(crear('p', 'nota nota-' + tipo, contenido));
    alFinal();
}

function anadirTexto(el, contenido) {
    const limpio = contenido.replace(/^\n+|\n+$/g, '');
    if (!limpio) return;
    const ultimo = el.lastElementChild;
    if (ultimo?.classList.contains('texto')) ultimo.textContent += contenido;
    else el.append(crear('div', 'texto', limpio));
}

/** Un bloque `pacai.ui/1` dentro del mensaje `el`. */
function pantalla(el, data) {
    let entrada = vistas.get(data.view);
    if (!entrada) {
        entrada = { vista: new Vista(data.view, { alEnviar: accion }), sitio: null };
        vistas.set(data.view, entrada);
    }
    if (data.rev <= entrada.vista.rev) return;                  // revisión vieja
    const placement = data.placement ?? entrada.vista.placement;
    const mover = entrada.sitio === null || (placement === 'latest' && entrada.sitio !== el);
    // Donde estaba queda una foto de cómo se veía antes de este cambio.
    if (mover && entrada.sitio) entrada.vista.elemento.replaceWith(entrada.vista.foto());
    entrada.vista.aplicar(data);
    if (mover) {
        el.append(entrada.vista.elemento);
        entrada.sitio = el;
    }
    alFinal();
}

function aprobacion(m) {
    const p = m.payload;
    const tarjeta = crear('div', 'aprobacion');
    tarjeta.append(crear('strong', null, 'El agente pide permiso: ' + p.name),
                   crear('pre', null, JSON.stringify(p.arguments, null, 2)));
    const botones = crear('div', 'botones');
    for (const [etiqueta, decision] of [['Aprobar una vez', 'approved'], ['Denegar', 'denied']]) {
        const b = crear('button', decision === 'approved' ? 'ui-boton ui-boton-primary' : 'ui-boton', etiqueta);
        b.type = 'button';
        b.addEventListener('click', () => {
            enviar('approval_result', decision === 'approved' ? { decision, scope: 'once' } : { decision }, m.id);
            botones.replaceChildren(crear('span', 'nota', decision === 'approved' ? 'Aprobado.' : 'Denegado.'));
        });
        botones.append(b);
    }
    tarjeta.append(botones);
    chat.append(tarjeta);
    alFinal();
}

/** Lo que una `Vista` manda cuando el usuario pulsa o envía algo, con su texto legible. */
function accion(data, resumen) {
    const blocks = [{ type: 'text', text: resumen }, { type: 'structured', schema: 'pacai.ui.action/1', data }];
    // Con una ejecución en marcha entra en ella (steer); si no, abre una nueva.
    enviar('input', raiz ? { blocks, mode: 'steer', run_id: raiz } : { blocks });
}

function ocupado(si) {
    parar.hidden = !si;
    // Un tema puede poner sus propios textos en <body>: `data-trabajando` mientras el agente trabaja y `data-listo`
    // cuando espera (sin él, la sesión, que sirve para depurar la demo).
    const { trabajando = 'El agente está trabajando…', listo } = document.body.dataset;
    estado.textContent = si ? trabajando : listo ?? `Sesión ${sesion?.slice(0, 10) ?? ''}… · perfil ui activo`;
    if (!si) for (const { vista } of vistas.values()) vista.liberar();
}

// -- mensajes PACAI ---------------------------------------------------------------------------------------
function anotar(m) {
    const li = crear('li', 'traza-' + (m.sender ?? 'demo'));
    li.append(crear('span', 'traza-tipo', `${m.sender === 'client' ? '→' : '←'} ${m.type}`));
    const cuerpo = crear('pre', null, JSON.stringify(m.payload ?? {}, null, 1));
    li.append(cuerpo);
    listaTraza.append(li);
    if (!traza.hidden) li.scrollIntoView({ block: 'end' });
}

function recibir(m) {
    if (m.type === 'demo.reset') {
        vistas = new Map();
        mensajes = new Map();
        raiz = null;
        chat.replaceChildren(...chat.querySelectorAll(':scope > .bienvenida'));   // la bienvenida del tema se queda
        listaTraza.replaceChildren();
        return;
    }
    anotar(m);
    const p = m.payload ?? {};
    if (m.sender === 'client') {
        if (m.type === 'input') {
            const t = (p.blocks ?? []).filter(b => b.type === 'text').map(b => b.text).join('\n');
            const el = burbuja('usuario');
            el.append(crear('div', 'texto', t));
            if ((p.blocks ?? []).some(b => b.schema === 'pacai.ui.action/1')) el.classList.add('mensaje-accion');
        }
        return;
    }
    switch (m.type) {
        case 'session_started':
            sesion = m.session;
            if (!p.profiles.includes('ui')) aviso('El agente no aceptó el perfil ui: verás sólo texto.', 'error');
            ocupado(false);
            break;
        case 'run_started':
            if (!p.parent_run_id) {
                raiz = m.id;
                ocupado(true);
            }
            break;
        case 'run_finished':
            if (p.run_id === raiz) {
                raiz = null;
                ocupado(false);
            }
            if (p.outcome !== 'success') aviso('La ejecución terminó: ' + p.outcome + (p.detail ? ' · ' + p.detail : ''));
            break;
        case 'message_start': {
            if (p.role !== 'assistant') break;
            const el = burbuja('agente');
            mensajes.set(m.id, el);
            for (const b of p.blocks ?? []) bloque(el, b);
            break;
        }
        case 'message_delta':
        case 'message_end': {
            const el = mensajes.get(p.message_id);
            if (el) for (const b of p.blocks ?? []) bloque(el, b);
            break;
        }
        case 'tool_start':
            aviso('Herramienta: ' + (p.title ?? p.name));
            break;
        case 'approval_request':
            aprobacion(m);
            break;
        case 'alfalfa.ui_rejected':
            aviso(`El agente intentó dibujar algo que no vale en «${p.view}»: ${p.errors.join(' · ')}`, 'error');
            break;
        case 'error':
            aviso('Error del agente: ' + p.message, 'error');
            break;
        case 'close':
        case 'demo.agent_exit':
            estado.textContent = 'El agente se cerró.';
            redactor.querySelectorAll('textarea, button').forEach(c => { c.disabled = true; });
            break;
    }
}

function bloque(el, b) {
    if (b.type === 'text') anadirTexto(el, b.text);
    else if (b.type === 'structured' && b.schema === 'pacai.ui/1') pantalla(el, b.data);
}

// -- arranque ---------------------------------------------------------------------------------------------
const fuente = new EventSource('/eventos');
fuente.onmessage = ev => recibir(JSON.parse(ev.data));
fuente.onerror = () => { estado.textContent = 'Sin conexión con el servidor de la demo.'; };

redactor.addEventListener('submit', ev => {
    ev.preventDefault();
    const t = texto.value.trim();
    if (!t) return;
    enviar('input', { blocks: [{ type: 'text', text: t }] });
    texto.value = '';
});
texto.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        redactor.requestSubmit();
    }
});
parar.addEventListener('click', () => { if (raiz) enviar('interrupt', { run_id: raiz }); });
document.getElementById('ver-mensajes').addEventListener('change', ev => { traza.hidden = !ev.target.checked; });
