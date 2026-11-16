//pasa markdown a html, sin librerias.
//SEGURIDAD: lo que mostramos es texto del modelo y de las tools, o sea contenido
//NO confiable. por eso primero se escapa TODO el texto y recien despues se agregan
//las etiquetas nuestras. nunca le llega html crudo al navegador, y los links
//solo pueden ser http(s)

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const MARCA = '\u0000';   //marca los pedacitos "protegidos" (codigo y links)

export function escapar(texto) {
  return String(texto).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

function enlace(url, texto) {
  return `<a href="${escapar(url)}" target="_blank" rel="noopener noreferrer" title="${escapar(url)}">${texto}</a>`;
}

//el formato adentro de una linea: codigo, links, negrita, cursiva y tachado
function enLinea(crudo) {
  const protegidos = [];
  const proteger = (html) => `${MARCA}${protegidos.push(html) - 1}${MARCA}`;

  let t = crudo.replace(/`([^`\n]+)`/g, (_, codigo) => proteger(`<code>${escapar(codigo)}</code>`));
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, texto, url) => proteger(enlace(url, escapar(texto))));
  t = t.replace(/https?:\/\/[^\s<>"'`|]+[^\s<>"'`|.,;:!?)\]]/g,
    (url) => proteger(enlace(url, escapar(url.replace(/^https?:\/\/(www\.)?/, '')))));

  t = escapar(t);
  t = t.replace(/\*\*(?=\S)([\s\S]+?)(?<=\S)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(?<![\w_])__(?=\S)([\s\S]+?)(?<=\S)__(?![\w_])/g, '<strong>$1</strong>');
  t = t.replace(/(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])/g, '<em>$1</em>');
  t = t.replace(/(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])/g, '<em>$1</em>');
  t = t.replace(/~~(?=\S)([^~\n]+?)(?<=\S)~~/g, '<del>$1</del>');

  return t.replace(new RegExp(`${MARCA}(\\d+)${MARCA}`, 'g'), (_, i) => protegidos[Number(i)]);
}

//----- tablas -----
const SEPARADOR_TABLA = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

function celdas(linea) {
  const limpia = linea.trim().replace(/^\|/, '').replace(/\|$/, '');
  return limpia.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function claseDeCelda(texto, alineacion) {
  const clases = [];
  const plano = texto.replace(/\*\*|`/g, '').trim();
  if (alineacion === 'right' || /^[+\-−]?\s?\$?\s?\d[\d.,]*\s?%?$/.test(plano)) clases.push('num');
  //la convencion del proyecto: diferencia = competidor − ragnar.
  //positiva = ragnar mas barato; negativa = ragnar mas caro
  const signo = /^([+\-−])\s?\d[\d.,]*\s?%/.exec(plano);
  if (signo) clases.push(signo[1] === '+' ? 'pos' : 'neg');
  return clases.length ? ` class="${clases.join(' ')}"` : '';
}

function tabla(lineas) {
  const encabezado = celdas(lineas[0]);
  const alineaciones = celdas(lineas[1]).map((c) =>
    (c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : 'left'));
  const filas = lineas.slice(2).map(celdas);
  const th = encabezado.map((c, i) =>
    `<th${alineaciones[i] === 'right' ? ' class="num"' : ''}>${enLinea(c)}</th>`).join('');
  const cuerpo = filas.map((fila) => '<tr>' + encabezado.map((_, i) => {
    const c = fila[i] ?? '';
    return `<td${claseDeCelda(c, alineaciones[i])}>${enLinea(c)}</td>`;
  }).join('') + '</tr>').join('');
  return `<div class="tabla-envoltura"><table><thead><tr>${th}</tr></thead><tbody>${cuerpo}</tbody></table></div>`;
}

//----- listas (se anidan segun la sangria) -----
const ITEM_LISTA = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

function lista(lineas) {
  const sangriaBase = ITEM_LISTA.exec(lineas[0])[1].length;
  const grupos = [];              //cada grupo es una lista <ul> u <ol>
  let item = null;
  for (const linea of lineas) {
    const m = ITEM_LISTA.exec(linea);
    if (m && m[1].length <= sangriaBase + 1) {
      const ordenada = /\d/.test(m[2]);
      if (!grupos.length || grupos.at(-1).ordenada !== ordenada) grupos.push({ ordenada, items: [] });
      item = { texto: m[3], resto: [] };
      grupos.at(-1).items.push(item);
    } else if (item) {
      item.resto.push(linea);     //sublista o continuacion del item
    }
  }
  return grupos.map((g) => {
    const etiqueta = g.ordenada ? 'ol' : 'ul';
    const items = g.items.map((it) => {
      const sangria = Math.min(...it.resto.filter((l) => l.trim()).map((l) => /^\s*/.exec(l)[0].length), 99);
      const interior = it.resto.map((l) => l.slice(Math.min(sangria, l.length)));
      return `<li>${enLinea(it.texto)}${interior.some((l) => l.trim()) ? bloques(interior) : ''}</li>`;
    }).join('');
    return `<${etiqueta}>${items}</${etiqueta}>`;
  }).join('');
}

//----- bloques -----
const TITULO = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const REGLA = /^\s*([-*_])(\s*\1){2,}\s*$/;
const CERCO = /^\s*(```|~~~)/;

function iniciaBloque(linea, siguiente) {
  return TITULO.test(linea) || REGLA.test(linea) || CERCO.test(linea) || /^\s*>/.test(linea) ||
    ITEM_LISTA.test(linea) || (linea.includes('|') && siguiente !== undefined && SEPARADOR_TABLA.test(siguiente));
}

function bloques(lineas) {
  const salida = [];
  let i = 0;
  while (i < lineas.length) {
    const linea = lineas[i];

    if (!linea.trim()) { i++; continue; }

    if (CERCO.test(linea)) {
      const cierre = CERCO.exec(linea)[1];
      const codigo = [];
      i++;
      while (i < lineas.length && !lineas[i].trim().startsWith(cierre)) codigo.push(lineas[i++]);
      i++;
      salida.push(`<pre><code>${escapar(codigo.join('\n'))}</code></pre>`);
      continue;
    }

    const titulo = TITULO.exec(linea);
    if (titulo) {
      salida.push(`<h${titulo[1].length}>${enLinea(titulo[2])}</h${titulo[1].length}>`);
      i++;
      continue;
    }

    if (REGLA.test(linea)) { salida.push('<hr>'); i++; continue; }

    if (linea.includes('|') && i + 1 < lineas.length && SEPARADOR_TABLA.test(lineas[i + 1])) {
      const filas = [linea, lineas[i + 1]];
      i += 2;
      while (i < lineas.length && lineas[i].trim() && lineas[i].includes('|')) filas.push(lineas[i++]);
      salida.push(tabla(filas));
      continue;
    }

    if (/^\s*>/.test(linea)) {
      const cita = [];
      while (i < lineas.length && /^\s*>/.test(lineas[i])) cita.push(lineas[i++].replace(/^\s*>\s?/, ''));
      salida.push(`<blockquote>${bloques(cita)}</blockquote>`);
      continue;
    }

    if (ITEM_LISTA.test(linea)) {
      const items = [];
      while (i < lineas.length) {
        const actual = lineas[i];
        if (ITEM_LISTA.test(actual) || /^\s{2,}\S/.test(actual)) { items.push(actual); i++; continue; }
        //una linea en blanco sigue siendo parte de la lista si despues viene otro item
        const sigue = lineas[i + 1];
        if (!actual.trim() && sigue !== undefined && (ITEM_LISTA.test(sigue) || /^\s{2,}\S/.test(sigue))) { i++; continue; }
        break;
      }
      salida.push(lista(items));
      continue;
    }

    const parrafo = [];
    while (i < lineas.length && lineas[i].trim() && !(parrafo.length && iniciaBloque(lineas[i], lineas[i + 1]))) {
      parrafo.push(lineas[i++].trim());
    }
    //las notas que agrega el guardrail de salida (seguridad/salida.py) las resaltamos
    const clase = parrafo[0].startsWith('⚠') ? ' class="nota nota-alerta"'
      : parrafo[0].startsWith('ℹ') ? ' class="nota nota-info"' : '';
    salida.push(`<p${clase}>${parrafo.map(enLinea).join('<br>')}</p>`);
  }
  return salida.join('');
}

export function renderizarMarkdown(texto) {
  const limpio = String(texto ?? '').replaceAll(MARCA, '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ');
  return bloques(limpio.split('\n'));
}
