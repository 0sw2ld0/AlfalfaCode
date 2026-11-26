'use strict';
// El renderizador de pantallas del panel (media/ui.js) es una copia del de la demo web del perfil `ui`.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const COPIA = path.join(__dirname, '..', 'media', 'ui.js');
const ORIGINAL = path.join(__dirname, '..', '..', 'docs', 'demos', 'pacai-ui', 'ui.js');

test('media/ui.js es idéntico al de la demo (cópialo de allí si cambia)', { skip: !fs.existsSync(ORIGINAL) }, () => {
    assert.equal(fs.readFileSync(COPIA, 'utf8'), fs.readFileSync(ORIGINAL, 'utf8'));
});

test('el texto legible de una acción es el mismo que pone la app de Mac', async () => {
    const { resumen } = await import(pathToFileURL(COPIA).href);
    const base = { view: 'v', rev: 1, node: 'n', name: 'crear', values: {} };
    assert.equal(resumen({ ...base, event: 'press' }, 'Crear ticket'), 'Pulsé «Crear ticket»');
    assert.equal(resumen({ ...base, event: 'press' }, null), 'Pulsé «crear»');
    assert.equal(resumen({ ...base, event: 'submit' }, 'Crear'), 'Envié el formulario «Crear»');
    assert.equal(resumen({ ...base, event: 'row_press', item: { title: 'Login', id: 'T-12', priority: 'Alta' } }),
        'Elegí «T-12 · Alta · Login»');
    assert.equal(resumen({ ...base, event: 'item_press', item: 'a1' }), 'Elegí «a1»');
});

// La pantalla como texto para copiarla: los mismos casos que `ScreenTextTests` de la app de Mac.
const pantalla = lista => new Map(lista.map(n => [n.id, n]));

test('una tabla sale como tabla markdown y los botones no salen', async () => {
    const { textoDePantalla } = await import(pathToFileURL(COPIA).href);
    const nodos = pantalla([
        { id: 'root', component: 'Stack', children: ['titulo', 'tabla', 'crear'] },
        { id: 'titulo', component: 'Text', props: { text: '3 tickets abiertos', style: 'title' } },
        { id: 'tabla', component: 'Table', props: {
            columns: [{ key: 'id', label: 'Id' }, { key: 'title', label: 'Título' }, { key: 'n' }],
            rows: { $: 'tickets' } } },
        { id: 'crear', component: 'Button', props: { label: 'Crear ticket' } },
    ]);
    const estado = { tickets: [{ id: 'T-1', title: 'Falla | Safari', n: 2 }, { id: 'T-2', title: 'Lento\nya' }] };
    assert.equal(textoDePantalla(nodos, estado), [
        '## 3 tickets abiertos', '',
        '| Id | Título | n |', '| --- | --- | --- |', '| T-1 | Falla \\| Safari | 2 |', '| T-2 | Lento ya |  |',
    ].join('\n'));
});

test('las tarjetas salen como lista con título, meta y detalle', async () => {
    const { textoDePantalla } = await import(pathToFileURL(COPIA).href);
    const nodos = pantalla([
        { id: 'root', component: 'Stack', props: { direction: 'grid' }, children: ['a', 'b'] },
        { id: 'a', component: 'Card', props: { title: 'Sale Swift 7', subtitle: 'swift.org', meta: 'Hoy · Lenguajes',
            tags: ['Swift', 'Apple'], detail: 'Trae macros nuevas.\nY más.' }, children: ['abrir'] },
        { id: 'abrir', component: 'Button', props: { label: 'Abrir fuente' } },
        { id: 'b', component: 'Card', props: { title: 'Python 4' } },
    ]);
    assert.equal(textoDePantalla(nodos), [
        '- **Sale Swift 7** · swift.org', '  Hoy · Lenguajes', '  `Swift` `Apple`', '  Trae macros nuevas.', '  Y más.',
        '- **Python 4**',
    ].join('\n'));
});

test('un formulario da cada campo con su etiqueta y su valor actual', async () => {
    const { textoDePantalla } = await import(pathToFileURL(COPIA).href);
    const nodos = pantalla([
        { id: 'root', component: 'Form', children: ['titulo', 'prioridad', 'etiquetas'] },
        { id: 'titulo', component: 'Input', props: { name: 'title', label: 'Título' } },
        { id: 'prioridad', component: 'Select', props: { name: 'priority', label: 'Prioridad',
            options: [{ value: 'low', label: 'Baja' }, { value: 'high', label: 'Alta' }] } },
        { id: 'etiquetas', component: 'Select', props: { name: 'tags', label: 'Etiquetas', multiple: true,
            options: [{ value: 'ui', label: 'Interfaz' }, { value: 'api', label: 'API' }] } },
    ]);
    assert.equal(textoDePantalla(nodos, { priority: 'low' }), '**Título:** —\n\n**Prioridad:** Baja\n\n**Etiquetas:** —');
    assert.equal(textoDePantalla(nodos, { title: 'Login roto', priority: 'high', tags: ['ui', 'api'] }),
        '**Título:** Login roto\n\n**Prioridad:** Alta\n\n**Etiquetas:** Interfaz, API');
});

test('un gráfico sale como la tabla de sus datos', async () => {
    const { textoDePantalla } = await import(pathToFileURL(COPIA).href);
    const nodos = pantalla([{ id: 'root', component: 'Chart', props: { kind: 'bar', labels: ['Ene', 'Feb', 'Mar'],
        series: [{ name: 'Ventas', values: [10, 12.5, 9] }, { values: [3, 4] }] } }]);
    assert.equal(textoDePantalla(nodos), [
        '| Etiqueta | Ventas | Serie 2 |', '| --- | --- | --- |', '| Ene | 10 | 3 |', '| Feb | 12.5 | 4 |', '| Mar | 9 |  |',
    ].join('\n'));
    assert.equal(textoDePantalla(new Map(), {}, 'Cargando'), 'Cargando', 'sin raíz, su alt');
});

test('el JSON crudo es un replace que rehace la misma pantalla', async () => {
    const { jsonDePantalla } = await import(pathToFileURL(COPIA).href);
    const nodos = pantalla([
        { id: 'root', component: 'Stack', children: ['crear', 'titulo'] },
        { id: 'titulo', component: 'Text', props: { text: { $: 'count' } } },
        { id: 'crear', component: 'Button', props: { label: 'Crear' }, events: { press: { send: 'crear' } } },
        { id: 'suelto', component: 'Text' },
    ]);
    const data = JSON.parse(jsonDePantalla('tickets', 3, nodos, { count: 4 }, 'Tickets', 'inline'));
    assert.deepEqual(data.nodes.map(n => n.id), ['root', 'crear', 'titulo', 'suelto']);
    assert.equal(data.op, 'replace');
    assert.deepEqual(data.state, { count: 4 });
    assert.equal(data.alt, 'Tickets');
    assert.equal(data.placement, undefined);
});
