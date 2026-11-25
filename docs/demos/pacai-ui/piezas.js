// Las cuatro pantallas de la 4.ª lluvia con las piezas declarativas, sin agente: `node servidor.mjs --ui-lang` y
// abre /piezas. Los nodos son los de tests/conformidad/muestras-pacai-ui-lang.json (lo que escribe el modelo en
// PACAI UI Lang, ya traducido a pacai.ui/1). Los componentes de CuyMercado se dibujan aquí como marcadores.
import { Vista, registrarComponente } from './ui.js';

const CASOS = {
 "margen": [
  {
   "id": "root",
   "component": "Stack",
   "props": {
    "direction": "vertical"
   },
   "children": [
    "compra",
    "venta",
    "cantidad",
    "m",
    "pct",
    "u",
    "total"
   ]
  },
  {
   "id": "compra",
   "component": "Input",
   "props": {
    "label": "Precio de compra",
    "name": "compra",
    "kind": "number"
   }
  },
  {
   "id": "venta",
   "component": "Input",
   "props": {
    "label": "Precio de venta",
    "name": "venta",
    "kind": "number"
   }
  },
  {
   "id": "cantidad",
   "component": "Input",
   "props": {
    "label": "Cantidad",
    "name": "cantidad",
    "kind": "number"
   }
  },
  {
   "id": "m",
   "component": "Calc",
   "props": {
    "name": "m",
    "preset": "margen",
    "compra": {
     "$": "compra"
    },
    "venta": {
     "$": "venta"
    },
    "cantidad": {
     "$": "cantidad"
    }
   }
  },
  {
   "id": "pct",
   "component": "Text",
   "props": {
    "text": "Margen:",
    "value": {
     "$": "m.pct"
    }
   }
  },
  {
   "id": "u",
   "component": "Text",
   "props": {
    "text": "Ganancia por unidad:",
    "value": {
     "$": "m.u"
    },
    "rojo": {
     "$": "m.perdida"
    }
   }
  },
  {
   "id": "total",
   "component": "Text",
   "props": {
    "text": "Ganancia total:",
    "value": {
     "$": "m.total"
    },
    "rojo": {
     "$": "m.perdida"
    }
   }
  }
 ],
 "vista": [
  {
   "id": "root",
   "component": "Stack",
   "props": {
    "direction": "vertical"
   },
   "children": [
    "ver",
    "resumen",
    "detalle"
   ]
  },
  {
   "id": "ver",
   "component": "Select",
   "props": {
    "label": "Ver",
    "name": "vista",
    "options": [
     {
      "value": "resumen",
      "label": "Resumen"
     },
     {
      "value": "detalle",
      "label": "Detalle"
     }
    ]
   }
  },
  {
   "id": "resumen",
   "component": "Grid",
   "props": {
    "columns": 3,
    "si": {
     "$": "vista",
     "es": "resumen"
    }
   },
   "children": [
    "s_sub",
    "s_baj",
    "s_igu"
   ]
  },
  {
   "id": "s_sub",
   "component": "Stat",
   "props": {
    "label": "Subieron",
    "icon": "subio",
    "query": {
     "tipo": "totales",
     "campo": "subieron"
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  },
  {
   "id": "s_baj",
   "component": "Stat",
   "props": {
    "label": "Bajaron",
    "icon": "bajo",
    "query": {
     "tipo": "totales",
     "campo": "bajaron"
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  },
  {
   "id": "s_igu",
   "component": "Stat",
   "props": {
    "label": "Iguales",
    "icon": "igual",
    "query": {
     "tipo": "totales",
     "campo": "iguales"
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  },
  {
   "id": "detalle",
   "component": "Stack",
   "props": {
    "si": {
     "$": "vista",
     "es": "detalle"
    }
   },
   "children": [
    "tabla",
    "grafico"
   ]
  },
  {
   "id": "tabla",
   "component": "PriceBars",
   "props": {
    "query": {
     "tipo": "productos",
     "orden": "baja",
     "limite": 6
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  },
  {
   "id": "grafico",
   "component": "PriceLines",
   "props": {
    "query": {
     "tipo": "historial",
     "productos": [
      "palta",
      "limon"
     ]
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  }
 ],
 "pedido": [
  {
   "id": "root",
   "component": "Data",
   "props": {
    "name": "q",
    "query": {
     "tipo": "formPedido"
    }
   },
   "children": [
    "pedido"
   ]
  },
  {
   "id": "pedido",
   "component": "Form",
   "props": {
    "submit_label": "Enviar"
   },
   "children": [
    "producto",
    "cantidad",
    "fecha",
    "correo"
   ],
   "events": {
    "submit": {
     "send": "crear_pedido",
     "with": [
      "producto",
      "cantidad",
      "fecha",
      "correo"
     ]
    }
   }
  },
  {
   "id": "producto",
   "component": "Select",
   "props": {
    "label": "Producto",
    "name": "producto",
    "required": true,
    "options": {
     "$": "q.lista"
    }
   }
  },
  {
   "id": "cantidad",
   "component": "Input",
   "props": {
    "label": "Cantidad",
    "name": "cantidad",
    "kind": "number",
    "required": true,
    "min": 1,
    "max": 500,
    "step": 1
   }
  },
  {
   "id": "fecha",
   "component": "Input",
   "props": {
    "label": "Fecha de entrega",
    "name": "fecha",
    "kind": "date",
    "required": true,
    "min": {
     "$": "q.hoy"
    }
   }
  },
  {
   "id": "correo",
   "component": "Input",
   "props": {
    "label": "Correo",
    "name": "correo",
    "kind": "email",
    "required": true
   }
  }
 ],
 "comparar": [
  {
   "id": "root",
   "component": "Stack",
   "props": {
    "direction": "vertical"
   },
   "children": [
    "a",
    "b",
    "cmp"
   ]
  },
  {
   "id": "a",
   "component": "Select",
   "props": {
    "label": "Producto A",
    "name": "a",
    "options": [
     {
      "value": "palta",
      "label": "Palta"
     },
     {
      "value": "limon",
      "label": "Limón"
     },
     {
      "value": "papa",
      "label": "Papa"
     }
    ]
   }
  },
  {
   "id": "b",
   "component": "Select",
   "props": {
    "label": "Producto B",
    "name": "b",
    "options": [
     {
      "value": "palta",
      "label": "Palta"
     },
     {
      "value": "limon",
      "label": "Limón"
     },
     {
      "value": "papa",
      "label": "Papa"
     }
    ]
   }
  },
  {
   "id": "cmp",
   "component": "Data",
   "props": {
    "name": "c",
    "query": {
     "tipo": "comparar",
     "a": {
      "$": "a"
     },
     "b": {
      "$": "b"
     }
    }
   },
   "children": [
    "frase",
    "lineas"
   ]
  },
  {
   "id": "frase",
   "component": "Text",
   "props": {
    "value": {
     "$": "c.frase"
    }
   }
  },
  {
   "id": "lineas",
   "component": "PriceLines",
   "props": {
    "query": {
     "tipo": "historial",
     "productos": [
      {
       "$": "a"
      },
      {
       "$": "b"
      }
     ]
    },
    "period": {
     "$": "periodo"
    },
    "family": {
     "$": "familia"
    }
   }
  }
 ]
};

const resumenQuery = q => Object.entries(q ?? {}).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(' · ');
registrarComponente('Grid', (nodo, p, { crear }) => crear('div', 'ui-stack ui-rejilla ui-gap-medium'), { hijos: true });
registrarComponente('Panel', (nodo, p, { crear }) => {
    const el = crear('section', 'ui-card');
    el.append(crear('h3', 'ui-card-titulo', p.title), crear('div', 'ui-hijos'));
    return el;
}, { hijos: true });
registrarComponente('Stat', (nodo, p, { crear }) => {
    const el = crear('div', 'ui-card');
    el.append(crear('p', 'ui-card-meta', p.label), crear('p', 'ui-text ui-text-muted', resumenQuery(p.query)));
    return el;
});
for (const nombre of ['PriceBars', 'PriceLines', 'Filters']) {
    registrarComponente(nombre, (nodo, p, { crear }) => crear('p', 'ui-text ui-text-muted', nombre + ' · ' + resumenQuery(p.query)));
}

const consultas = {
    formPedido: { out: { lista: 'opciones', hoy: 'fecha' } },
    comparar: { out: { frase: 'texto', series: 'series' } },
};
const consultar = query => fetch('/consulta', { method: 'POST', headers: { 'content-type': 'application/json' },
                                                body: JSON.stringify(query) }).then(r => r.json());
const registro = document.getElementById('registro');
for (const [nombre, nodos] of Object.entries(CASOS)) {
    const vista = new Vista(nombre, {
        piezas: true, consultas, consultar,
        alEnviar: (data, texto) => { registro.textContent = texto + ' ' + JSON.stringify(data.values); },
    });
    vista.aplicar({ view: nombre, op: 'replace', rev: 1, nodes: nodos, alt: nombre });
    const caja = document.createElement('section');
    caja.className = 'caso';
    const titulo = document.createElement('h2');
    titulo.textContent = nombre;
    caja.append(titulo, vista.elemento);
    document.getElementById('casos').append(caja);
}
