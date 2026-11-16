//la interfaz del agente de precios de ragnar.
//habla con dos cosas del mismo servidor (servidor.py):
//  - la api de adk:  /apps/.../sessions (las conversaciones) y /run_sse (un turno del agente)
//  - la api propia:  /api/... (config, estado, informes, competidores, auditoria)
//ojo: la interfaz NO calcula ni decide nada de precios, solo muestra lo que devuelven
//el agente y sus tools

import { renderizarMarkdown } from './markdown.js';
import { iniciarVidrio } from './vidrio.js';

//----- funciones de ayuda -----
const $ = (selector, raiz = document) => raiz.querySelector(selector);

//crea un elemento. los hijos que son texto se meten como texto (nunca como html)
function crear(etiqueta, atributos = {}, ...hijos) {
  const el = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === 'class') el.className = valor;
    else if (clave === 'html') el.innerHTML = valor;          //solo html que ya paso por markdown.js
    else if (clave.startsWith('on')) el.addEventListener(clave.slice(2), valor);
    else el.setAttribute(clave, valor === true ? '' : valor);
  }
  el.append(...hijos.flat().filter((h) => h !== null && h !== undefined && h !== false));
  return el;
}

function icono(nombre) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icono');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${nombre}`);
  svg.append(use);
  return svg;
}

async function pedir(ruta, opciones = {}) {
  const { cuerpo, ...resto } = opciones;
  if (cuerpo !== undefined) {
    resto.method ??= 'POST';
    resto.headers = { 'Content-Type': 'application/json', ...resto.headers };
    resto.body = JSON.stringify(cuerpo);
  }
  const respuesta = await fetch(ruta, resto);
  if (!respuesta.ok) throw new Error(await detalleDeError(respuesta));
  return respuesta.json();
}

async function detalleDeError(respuesta) {
  const texto = await respuesta.text();
  try {
    const detalle = JSON.parse(texto).detail;
    if (detalle) return typeof detalle === 'string' ? detalle : JSON.stringify(detalle);
  } catch { /* no era json */ }
  return texto || `Error ${respuesta.status}`;
}

//lee una respuesta "text/event-stream" y va entregando cada evento ya pasado de json
async function* leerEventos(respuesta) {
  const lector = respuesta.body.getReader();
  const decodificador = new TextDecoder();
  let pendiente = '';
  while (true) {
    const { value, done } = await lector.read();
    if (done) break;
    pendiente += decodificador.decode(value, { stream: true });
    let corte;
    while ((corte = pendiente.indexOf('\n\n')) >= 0) {
      const bloque = pendiente.slice(0, corte);
      pendiente = pendiente.slice(corte + 2);
      const datos = bloque.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim()).join('\n');
      if (datos) yield JSON.parse(datos);
    }
  }
}

const recortar = (texto, largo) => (texto.length > largo ? `${texto.slice(0, largo - 1).trimEnd()}…` : texto);
const plural = (n, uno, muchos) => `${n} ${n === 1 ? uno : muchos}`;
const fechaHora = new Intl.DateTimeFormat('es-AR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const formatear = (formato, valor) => {
  const fecha = new Date(valor);
  return Number.isNaN(fecha.getTime()) ? String(valor ?? '') : formato.format(fecha);
};

//----- estado (lo que la pagina tiene en memoria) -----
const estado = {
  config: { app: 'ragnar_agent', usuario: 'area_comercial', modelo: '', herramientas: [], max_largo_mensaje: 500 },
  sesiones: [],
  sesionId: null,       //la conversacion abierta (null = todavia no se creo)
  turnos: [],           //[{ pedido, pasos, textos, error, estado }]
  enCurso: null,        //el AbortController del turno que esta corriendo
  filtroAuditoria: 'todos',
};

const rutaSesiones = () => `/apps/${estado.config.app}/users/${estado.config.usuario}/sessions`;

//consultas de ejemplo: las mismas del readme (criterios de aceptacion)
const ATAJOS = [
  { icono: 'balanza', texto: 'Comparar precios', pedido: 'Compará los precios de los desodorantes Dove con la competencia' },
  { icono: 'lupa', texto: 'Buscar en un competidor', pedido: 'Buscá el producto RAG-00003 en UruMarket' },
  { icono: 'etiqueta', texto: 'Promociones', pedido: 'Compará la lavandina Ayudin de 1000 ml en todos los supermercados' },
  { icono: 'regla', texto: 'Otra presentación', pedido: 'Compará el aceite de girasol Cañuelas de 1500 g' },
  { icono: 'informe', texto: 'Generar informe', pedido: 'Hacé un informe de los desodorantes Dove' },
  { icono: 'globo', texto: 'Competidores', pedido: '¿Qué competidores y monedas hay disponibles?' },
];

//que cartelito mostrar mientras corre cada tool
const TRABAJANDO = {
  consultar_catalogo: 'Consultando el catálogo de Ragnar',
  listar_competidores: 'Revisando los competidores',
  buscar_precios_competencia: 'Buscando precios en la competencia',
  comparar_precios: 'Calculando las diferencias',
  generar_informe: 'Armando el informe',
  web_search: 'Buscando en la web',
};

const refs = {
  app: $('#app'), vistaConsulta: $('#vista-consulta'), conversacion: $('#conversacion'), mensajes: $('#mensajes'),
  muelle: $('#muelle'), compositor: $('#compositor'), entrada: $('#entrada'), enviar: $('#enviar'),
  enviarIcono: $('#enviar-icono'), contador: $('#contador'), titulo: $('#barra-titulo'), velo: $('#velo'),
  listaConsultas: $('#lista-consultas'), avisoModelo: $('#aviso-modelo'),
};

//----- turnos: de los eventos de adk a lo que dibujamos -----
const nuevoTurno = (pedido, estadoTurno) => ({ pedido, pasos: [], textos: [], error: null, estado: estadoTurno });

//le suma al turno lo que trae un evento de adk (llamada a tool, resultado o texto)
function aplicarEvento(turno, evento) {
  if (evento.error) { turno.error = String(evento.error); return; }
  if (evento.errorMessage || evento.errorCode) turno.error = evento.errorMessage || evento.errorCode;
  for (const parte of evento.content?.parts ?? []) {
    if (parte.functionCall) {
      turno.pasos.push({ id: parte.functionCall.id, nombre: parte.functionCall.name,
        args: parte.functionCall.args ?? {}, respuesta: null, abierto: false });
    } else if (parte.functionResponse) {
      const fr = parte.functionResponse;
      const paso = turno.pasos.find((p) => p.id === fr.id && !p.respuesta)
        ?? turno.pasos.findLast((p) => p.nombre === fr.name && !p.respuesta);
      if (paso) paso.respuesta = fr.response ?? {};
    } else if (parte.text && !parte.thought && !evento.partial) {
      turno.textos.push(parte.text);
    }
  }
}

//rearma los turnos de una conversacion guardada
function turnosDeEventos(eventos) {
  const turnos = [];
  for (const evento of eventos ?? []) {
    if (evento.author === 'user') {
      const texto = (evento.content?.parts ?? []).map((p) => p.text).filter(Boolean).join('\n');
      if (texto) turnos.push(nuevoTurno(texto, 'listo'));
      continue;
    }
    if (!turnos.length) turnos.push(nuevoTurno('', 'listo'));
    aplicarEvento(turnos.at(-1), evento);
  }
  return turnos;
}

function estadoDePaso(paso) {
  if (!paso.respuesta) return 'ejecutando';
  if (paso.respuesta.bloqueado_por === 'seguridad') return 'bloqueado';
  return ['ok', 'sin_resultados', 'error'].includes(paso.respuesta.status) ? paso.respuesta.status : 'ok';
}

//una linea que resume lo que devolvio la tool
function resumenDePaso(paso, estadoPaso) {
  const r = paso.respuesta;
  if (estadoPaso === 'ejecutando') return 'en curso…';
  if (estadoPaso === 'bloqueado') return 'bloqueado por seguridad';
  if (estadoPaso === 'error') return 'error';
  if (estadoPaso === 'sin_resultados') return 'sin resultados';
  switch (paso.nombre) {
    case 'consultar_catalogo': return `${r.mostrando ?? 0} de ${plural(r.total_encontrados ?? 0, 'producto', 'productos')}`;
    case 'listar_competidores': return plural(r.competidores?.length ?? 0, 'competidor', 'competidores');
    case 'buscar_precios_competencia': return plural(r.ofertas?.length ?? 0, 'oferta', 'ofertas');
    case 'comparar_precios': return plural(r.productos?.length ?? 0, 'producto comparado', 'productos comparados');
    case 'generar_informe': return 'informe guardado';
    case 'web_search': return plural(r.resultados?.length ?? 0, 'resultado', 'resultados');
    default: return 'listo';
  }
}

const argumentosEnLinea = (args) => Object.entries(args)
  .map(([clave, valor]) => `${clave}: ${Array.isArray(valor) ? valor.join(', ') : valor}`).join(' · ');

function jsonRecortado(dato, maximo = 2400) {
  const texto = JSON.stringify(dato, null, 2) ?? '';
  return texto.length > maximo ? `${texto.slice(0, maximo)}\n… (recortado)` : texto;
}

//----- dibujo de la conversacion -----
function dibujarPaso(paso) {
  const estadoPaso = estadoDePaso(paso);
  const detalle = crear('div', { class: 'paso-detalle' },
    crear('div', {}, crear('h4', {}, 'Argumentos'), crear('pre', {}, jsonRecortado(paso.args))),
    paso.respuesta && crear('div', {}, crear('h4', {}, 'Resultado'),
      crear('pre', {}, paso.respuesta.mensaje && estadoPaso !== 'ok' ? String(paso.respuesta.mensaje) : jsonRecortado(paso.respuesta))));
  return crear('details', {
    class: 'paso', 'data-estado': estadoPaso, open: paso.abierto,
    ontoggle: (e) => { paso.abierto = e.currentTarget.open; },
  },
  crear('summary', {},
    crear('i', { class: 'punto' }),
    crear('span', { class: 'paso-nombre' }, paso.nombre),
    crear('span', { class: 'paso-args' }, argumentosEnLinea(paso.args)),
    crear('span', { class: 'paso-resultado' }, resumenDePaso(paso, estadoPaso)),
    icono('chevron')),
  detalle);
}

function dibujarFallo(turno) {
  const mensaje = String(turno.error);
  //si el error viene de ollama le sugerimos que revisar
  const esDeModelo = /ollama|connect|refused|not found|litellm|timeout/i.test(mensaje);
  return crear('div', { class: 'fallo' },
    crear('strong', {}, 'No se pudo completar la consulta'),
    esDeModelo && crear('p', {}, 'Revisá que Ollama esté activo y que el modelo esté descargado: ',
      crear('code', {}, `ollama pull ${estado.config.modelo}`)),
    crear('code', {}, recortar(mensaje, 420)));
}

function dibujarTurno(turno, esNuevo) {
  const hijos = [];
  if (turno.pedido) hijos.push(crear('div', { class: 'pedido cristal' }, turno.pedido));

  const respuesta = crear('div', { class: 'respuesta' },
    crear('div', { class: 'respuesta-autor' }, crear('span', { class: 'sello' }, 'R'), 'Agente de precios'));
  if (turno.pasos.length) respuesta.append(crear('div', { class: 'pasos' }, turno.pasos.map(dibujarPaso)));

  for (const paso of turno.pasos) {
    const ruta = paso.nombre === 'generar_informe' && paso.respuesta?.ruta_archivo;
    if (!ruta) continue;
    const nombre = String(ruta).split(/[\\/]/).pop();
    respuesta.append(crear('a', { class: 'tarjeta-informe cristal', href: `#/informes/${encodeURIComponent(nombre)}` },
      icono('informe'), crear('span', {}, crear('strong', {}, 'Abrir el informe completo'), crear('small', {}, nombre)),
      icono('chevron')));
  }

  if (turno.textos.length) {
    respuesta.append(crear('div', { class: 'prosa', html: renderizarMarkdown(turno.textos.join('\n\n')) }));
  }
  if (turno.estado === 'en_curso') {
    const actual = turno.pasos.findLast((p) => !p.respuesta);
    respuesta.append(crear('div', { class: 'pensando' }, crear('i', { class: 'gota' }),
      `${(actual && TRABAJANDO[actual.nombre]) || 'Pensando'}…`));
  }
  if (turno.error) respuesta.append(dibujarFallo(turno));
  if (turno.estado === 'detenido') respuesta.append(crear('p', { class: 'detenido' }, 'Consulta detenida.'));
  if (turno.estado === 'listo' && !turno.textos.length && !turno.error) {
    respuesta.append(crear('p', { class: 'detenido' }, 'El agente no devolvió texto.'));
  }
  hijos.push(respuesta);
  return crear('article', { class: 'turno', 'data-nuevo': esNuevo }, hijos);
}

const cercaDelFinal = () => {
  const c = refs.conversacion;
  return c.scrollHeight - c.scrollTop - c.clientHeight < 160;
};
const irAlFinal = (suave = true) => refs.conversacion.scrollTo({ top: refs.conversacion.scrollHeight, behavior: suave ? 'smooth' : 'instant' });

//redibuja toda la conversacion (al abrir una consulta o al empezar otra)
function dibujarConversacion({ aviso = null } = {}) {
  const modo = estado.turnos.length || aviso ? 'charla' : 'portada';
  const cambiar = () => {
    refs.vistaConsulta.dataset.modo = modo;
    refs.mensajes.replaceChildren(...estado.turnos.map((t) => dibujarTurno(t, false)));
    if (aviso) refs.mensajes.append(aviso);
  };
  //el cuadro de texto "viaja" de la portada al borde de abajo, si el navegador lo soporta
  const animar = refs.vistaConsulta.dataset.modo !== modo && document.startViewTransition
    && !matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (animar) {
    //si otra transicion la interrumpe, `ready` falla, pero el cambio se aplica igual
    const alTerminar = () => irAlFinal(false);
    const transicion = document.startViewTransition(cambiar);
    transicion.ready.catch(() => {});
    transicion.finished.then(alTerminar, alTerminar);
  } else { cambiar(); irAlFinal(false); }
  actualizarTitulo();
}

//redibuja un solo turno (mientras van llegando los eventos)
function actualizarTurno(turno) {
  const indice = estado.turnos.indexOf(turno);
  if (indice < 0) return;                       //el usuario ya se fue a otra conversacion
  const seguir = cercaDelFinal();
  const anterior = refs.mensajes.children[indice];
  const nuevo = dibujarTurno(turno, false);
  if (anterior) anterior.replaceWith(nuevo); else refs.mensajes.append(nuevo);
  if (seguir) irAlFinal();
}

function actualizarTitulo() {
  const seccion = rutaActual().seccion;
  const titulos = { informes: 'Informes', competidores: 'Competidores', auditoria: 'Auditoría' };
  const sesion = estado.sesiones.find((s) => s.id === estado.sesionId);
  const titulo = titulos[seccion] ?? sesion?.state?.titulo ?? (estado.turnos[0]?.pedido ? recortar(estado.turnos[0].pedido, 80) : '');
  refs.titulo.textContent = titulos[seccion] ? '' : titulo;
  document.title = titulo ? `${titulo} · Ragnar` : 'Ragnar · Inteligencia de precios';
}

//----- el cuadro para escribir -----
function actualizarCompositor() {
  const entrada = refs.entrada;
  entrada.style.height = 'auto';
  entrada.style.height = `${entrada.scrollHeight}px`;

  const largo = entrada.value.length;
  const maximo = estado.config.max_largo_mensaje;
  //el limite es el mismo del guardrail de entrada (seguridad/config.py): avisamos
  //antes de enviar en vez de esperar que el servidor lo rechace
  refs.contador.textContent = largo ? `${largo}/${maximo}` : '';
  refs.contador.toggleAttribute('data-excedido', largo > maximo);

  const corriendo = Boolean(estado.enCurso);
  refs.enviar.dataset.modo = corriendo ? 'detener' : 'enviar';
  refs.enviar.setAttribute('aria-label', corriendo ? 'Detener la consulta' : 'Enviar consulta');
  refs.enviarIcono.setAttribute('href', corriendo ? '#i-alto' : '#i-flecha');
  refs.enviar.disabled = !corriendo && (!entrada.value.trim() || largo > maximo);
}

function detener() {
  estado.enCurso?.abort();      //cuando cortamos la conexion, adk cancela el turno en el servidor
}

async function enviar() {
  const texto = refs.entrada.value.trim();
  if (estado.enCurso || !texto || texto.length > estado.config.max_largo_mensaje) return;

  const turno = nuevoTurno(texto, 'en_curso');
  const control = new AbortController();
  estado.enCurso = control;
  estado.turnos.push(turno);
  refs.entrada.value = '';
  actualizarCompositor();

  if (estado.turnos.length === 1) dibujarConversacion();
  else { refs.mensajes.append(dibujarTurno(turno, true)); irAlFinal(); }

  try {
    if (!estado.sesionId) {
      //la conversacion se crea recien con el primer mensaje, y el titulo queda guardado en su estado
      const sesion = await pedir(rutaSesiones(), { cuerpo: { state: { titulo: recortar(texto, 80) } }, signal: control.signal });
      estado.sesionId = sesion.id;
      history.replaceState(null, '', `#/c/${sesion.id}`);
      cargarSesiones();
    }
    //streaming: false -> adk manda un evento por paso (llamada, resultado, respuesta),
    //no token por token. asi el guardrail de salida revisa SIEMPRE la respuesta
    //completa antes de mostrarla (los pedacitos parciales no pasan por ese control)
    const respuesta = await fetch('/run_sse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: control.signal,
      body: JSON.stringify({
        app_name: estado.config.app, user_id: estado.config.usuario, session_id: estado.sesionId,
        streaming: false, new_message: { role: 'user', parts: [{ text: texto }] },
      }),
    });
    if (!respuesta.ok) throw new Error(await detalleDeError(respuesta));
    for await (const evento of leerEventos(respuesta)) {
      aplicarEvento(turno, evento);
      actualizarTurno(turno);
    }
    turno.estado = turno.error ? 'error' : 'listo';
  } catch (error) {
    if (error.name === 'AbortError') turno.estado = 'detenido';
    else {
      turno.estado = 'error';
      //fetch tira TypeError cuando no llega al servidor (se cerro servidor.py)
      turno.error = error instanceof TypeError
        ? 'No hay conexión con el servidor de la interfaz. Revisá que "python servidor.py" siga corriendo.'
        : error.message || String(error);
    }
  } finally {
    if (estado.enCurso === control) estado.enCurso = null;
    actualizarTurno(turno);
    actualizarCompositor();
    cargarSesiones();
    if (turno.estado === 'error') comprobarEstado();
  }
}

//----- conversaciones (la barra del costado) -----
async function cargarSesiones() {
  try {
    const sesiones = await pedir(rutaSesiones());
    estado.sesiones = sesiones.sort((a, b) => (b.lastUpdateTime ?? 0) - (a.lastUpdateTime ?? 0));
  } catch { /* si falla dejamos la lista como estaba */ }
  dibujarSesiones();
  actualizarTitulo();
}

function dibujarSesiones() {
  if (!estado.sesiones.length) {
    refs.listaConsultas.replaceChildren(crear('li', { class: 'vacio' }, 'Todavía no hay consultas.'));
    return;
  }
  const enConsulta = ['consulta', 'c'].includes(rutaActual().seccion);
  refs.listaConsultas.replaceChildren(...estado.sesiones.map((sesion) => {
    const titulo = sesion.state?.titulo || 'Consulta sin título';
    const borrar = crear('button', { class: 'borrar', type: 'button', 'aria-label': `Borrar la consulta: ${titulo}`, title: 'Borrar' }, icono('cerrar'));
    borrar.addEventListener('click', () => confirmarBorrado(borrar, sesion.id));
    return crear('li', { 'data-actual': enConsulta && sesion.id === estado.sesionId },
      crear('a', { href: `#/c/${sesion.id}`, title: titulo }, titulo), borrar);
  }));
}

//borrar pide un segundo clic: el primero solo cambia el boton a "¿Borrar?"
function confirmarBorrado(boton, id) {
  if (!boton.hasAttribute('data-confirmar')) {
    boton.setAttribute('data-confirmar', '');
    boton.replaceChildren('¿Borrar?');
    setTimeout(() => { if (boton.isConnected) { boton.removeAttribute('data-confirmar'); boton.replaceChildren(icono('cerrar')); } }, 3500);
    return;
  }
  pedir(`${rutaSesiones()}/${id}`, { method: 'DELETE' })
    .catch(() => {})
    .finally(() => {
      if (id === estado.sesionId) { reiniciarConsulta(); location.hash = '#/'; }
      cargarSesiones();
    });
}

function reiniciarConsulta() {
  detener();
  estado.enCurso = null;
  estado.sesionId = null;
  estado.turnos = [];
}

async function abrirConsulta(id) {
  if (estado.sesionId === id) { dibujarConversacion(); return; }   //ya la tenemos en memoria
  reiniciarConsulta();
  estado.sesionId = id;
  dibujarConversacion({ aviso: crear('div', { class: 'pensando' }, crear('i', { class: 'gota' }), 'Abriendo la consulta…') });
  try {
    const sesion = await pedir(`${rutaSesiones()}/${id}`);
    if (estado.sesionId !== id) return;
    estado.turnos = turnosDeEventos(sesion.events);
    dibujarConversacion(estado.turnos.length ? {} : {
      aviso: crear('p', { class: 'detenido' }, 'Esta consulta todavía no tiene mensajes.') });
  } catch {
    if (estado.sesionId !== id) return;
    estado.sesionId = null;
    dibujarConversacion({ aviso: crear('div', { class: 'fallo' },
      crear('strong', {}, 'No existe esta consulta'),
      crear('p', {}, 'Puede que se haya borrado. Escribí abajo para empezar una nueva.')) });
  }
  dibujarSesiones();
}

//----- paginas: informes, competidores y auditoria -----
function cabecera(titulo, bajada, accion) {
  return crear('header', { class: 'pagina-cabecera' },
    crear('div', {}, crear('h1', { class: 'pagina-titulo' }, titulo), crear('p', { class: 'pagina-bajada' }, bajada)),
    accion);
}

const cargando = (texto) => crear('div', { class: 'pensando' }, crear('i', { class: 'gota' }), texto);
const falloDePagina = (error) => crear('div', { class: 'fallo' },
  crear('strong', {}, 'No se pudieron cargar los datos'), crear('code', {}, error.message));

function usarAtajo(pedido) {
  refs.entrada.value = pedido;
  location.hash = '#/';
  actualizarCompositor();
  refs.entrada.focus();
}

async function mostrarInformes(nombre) {
  const vista = $('#vista-informes');
  const pagina = crear('div', { class: 'pagina' },
    cabecera('Informes', 'Los informes comparativos que generó el agente, con sus fuentes y fechas de consulta. Se guardan en la carpeta informes/.'));
  vista.replaceChildren(pagina);
  const zona = crear('div', {}, cargando('Buscando informes…'));
  pagina.append(zona);

  let lista;
  try { lista = await pedir('/api/informes'); } catch (error) { zona.replaceChildren(falloDePagina(error)); return; }
  if (rutaActual().seccion !== 'informes') return;

  if (!lista.length) {
    const pedido = ATAJOS.find((a) => a.icono === 'informe').pedido;
    zona.replaceChildren(crear('div', { class: 'estado-vacio vidrio' },
      crear('strong', {}, 'Todavía no hay informes'),
      crear('p', {}, `Pedile uno al agente, por ejemplo: «${pedido}».`),
      crear('button', { class: 'boton cristal', type: 'button', onclick: () => usarAtajo(pedido) }, icono('informe'), 'Usar ese pedido')));
    return;
  }

  //en pantallas anchas se abre el mas nuevo, en el celu primero se ve la lista
  const elegido = nombre || (matchMedia('(min-width: 881px)').matches ? lista[0].nombre : '');
  const lector = crear('article', { class: 'lector vidrio' });
  zona.replaceChildren(crear('div', { class: 'informes', 'data-abierto': Boolean(elegido) },
    crear('nav', { class: 'informes-lista', 'aria-label': 'Informes guardados' }, lista.map((inf) =>
      crear('a', { class: 'informe-item cristal', href: `#/informes/${encodeURIComponent(inf.nombre)}`,
        'aria-current': inf.nombre === elegido ? 'true' : null },
      crear('strong', {}, inf.titulo), crear('small', {}, `${formatear(fechaHora, inf.modificado)} · ${Math.max(1, Math.round(inf.bytes / 1024))} KB`)))),
    lector));

  if (!elegido) return;
  lector.replaceChildren(cargando('Abriendo el informe…'));
  try {
    const informe = await pedir(`/api/informes/${encodeURIComponent(elegido)}`);
    lector.replaceChildren(
      crear('a', { class: 'boton cristal volver', href: '#/informes' }, icono('volver'), 'Informes'),
      crear('div', { class: 'prosa', html: renderizarMarkdown(informe.markdown) }));
  } catch (error) {
    lector.replaceChildren(falloDePagina(error));
  }
}

async function mostrarCompetidores() {
  const vista = $('#vista-competidores');
  const pagina = crear('div', { class: 'pagina' },
    cabecera('Competidores', 'Los supermercados contra los que el agente compara los precios de Ragnar, con su país, su moneda y la última fecha de consulta.'));
  vista.replaceChildren(pagina);
  const zona = crear('div', { class: 'pagina-cuerpo' }, cargando('Consultando las fuentes…'));
  pagina.append(zona);

  let datos;
  try {
    datos = await pedir('/api/competidores');
    if (datos.status !== 'ok') throw new Error(datos.mensaje || 'La herramienta no devolvió datos.');
  } catch (error) { zona.replaceChildren(falloDePagina(error)); return; }

  const tarjetas = datos.competidores.map((c) => crear('article', { class: 'competidor vidrio' },
    crear('div', {}, crear('span', { class: 'rotulo' }, c.pais), crear('h2', { class: 'competidor-nombre' }, c.competidor)),
    crear('div', {}, crear('div', { class: 'competidor-cifra' }, String(c.productos_publicados)), crear('span', {}, 'productos publicados')),
    crear('dl', {},
      crear('div', {}, crear('dt', {}, 'Moneda'), crear('dd', {}, c.moneda)),
      crear('div', {}, crear('dt', {}, 'Última consulta'), crear('dd', {}, c.ultima_fecha_consulta))),
    //el tipo de fuente viene del dato: hoy son todas SIMULADAS y eso se tiene que ver
    crear('span', { class: 'insignia' }, `Fuente ${String(c.tipo_fuente).split(' ')[0].toLowerCase()}`)));

  const cambios = datos.tipos_de_cambio ?? [];
  const tablaCambios = cambios.length && crear('section', { class: 'panel vidrio' },
    crear('h2', { class: 'rotulo' }, 'Tipos de cambio a pesos argentinos'),
    crear('div', { class: 'prosa', html: renderizarMarkdown([
      '| Moneda | ARS por unidad | Fecha | Fuente |', '|---|---:|---|---|',
      ...cambios.map((t) => `| ${t.moneda} | ${t.ars_por_unidad} | ${t.fecha} | ${String(t.fuente).replaceAll('|', '/')} |`),
    ].join('\n')) }));

  zona.replaceChildren(crear('div', { class: 'competidores' }, tarjetas), tablaCambios || '');
}

const EVENTOS = {
  tool_ejecutada: { nombre: 'Herramienta ejecutada', tono: 'ok' },
  tool_bloqueada: { nombre: 'Herramienta bloqueada', tono: 'alerta' },
  mensaje_bloqueado: { nombre: 'Mensaje bloqueado', tono: 'alerta' },
  respuesta_bloqueada: { nombre: 'Respuesta bloqueada', tono: 'alerta' },
  respuesta_corregida: { nombre: 'Respuesta con nota', tono: 'nota' },
};

function resumenDeEvento(e) {
  if (e.evento === 'tool_ejecutada' || e.evento === 'tool_bloqueada') {
    const extra = e.evento === 'tool_bloqueada' || e.status === 'error' ? e.mensaje : argumentosEnLinea(e.argumentos ?? {});
    return `${e.tool} → ${e.status ?? ''}${extra ? ` · ${extra}` : ''}`;
  }
  if (e.evento === 'mensaje_bloqueado') {
    return e.motivo === 'largo' ? `largo: ${e.largo} caracteres` : `inyección (${e.detalle ?? ''}) · ${e.mensaje ?? ''}`;
  }
  if (e.evento === 'respuesta_corregida') return `números sin respaldo: ${(e.numeros ?? []).join(', ')}`;
  return String(e.motivo ?? '').replaceAll('_', ' ');
}

async function mostrarAuditoria() {
  const vista = $('#vista-auditoria');
  const pagina = crear('div', { class: 'pagina' },
    cabecera('Auditoría', 'Lo que registraron los guardrails: herramientas ejecutadas o bloqueadas, mensajes rechazados y respuestas corregidas. Sale de logs/auditoria.jsonl.',
      crear('button', { class: 'boton cristal', type: 'button', onclick: mostrarAuditoria }, icono('refrescar'), 'Actualizar')));
  vista.replaceChildren(pagina);
  const zona = crear('div', { class: 'pagina-cuerpo' }, cargando('Leyendo el registro…'));
  pagina.append(zona);

  let eventos;
  try { eventos = await pedir('/api/auditoria?limite=300'); } catch (error) { zona.replaceChildren(falloDePagina(error)); return; }

  if (!eventos.length) {
    zona.replaceChildren(crear('div', { class: 'estado-vacio vidrio' },
      crear('strong', {}, 'El registro está vacío'),
      crear('p', {}, 'Cuando el agente use una herramienta o un guardrail actúe, va a aparecer acá.')));
    return;
  }

  const contar = (...tipos) => eventos.filter((e) => tipos.includes(e.evento)).length;
  const cifras = crear('div', { class: 'cifras' },
    [['Herramientas ejecutadas', contar('tool_ejecutada'), ''],
      ['Herramientas bloqueadas', contar('tool_bloqueada'), 'alerta'],
      ['Mensajes bloqueados', contar('mensaje_bloqueado'), 'alerta'],
      ['Respuestas intervenidas', contar('respuesta_corregida', 'respuesta_bloqueada'), 'nota'],
    ].map(([rotulo, valor, tono]) => crear('div', { class: 'cifra cristal', 'data-tono': valor ? tono : '' },
      crear('span', { class: 'rotulo' }, rotulo), crear('b', {}, String(valor)))));

  const lista = crear('div', { class: 'eventos' });
  const dibujarLista = () => {
    const visibles = eventos.filter((e) => estado.filtroAuditoria === 'todos' || e.evento === estado.filtroAuditoria);
    lista.replaceChildren(...(visibles.length ? visibles.map((e) => {
      const tipo = EVENTOS[e.evento] ?? { nombre: e.evento, tono: '' };
      return crear('details', { class: 'evento', 'data-tono': tipo.tono },
        crear('summary', {},
          crear('time', {}, formatear(fechaHora, e.fecha)),
          crear('span', { class: 'evento-tipo' }, crear('i', { class: 'punto' }), tipo.nombre),
          crear('span', { class: 'evento-resumen' }, resumenDeEvento(e)),
          icono('chevron')),
        crear('div', { class: 'evento-detalle' }, crear('pre', {}, jsonRecortado(e)),
          e.sesion && crear('a', { href: `#/c/${encodeURIComponent(e.sesion)}` }, 'Ver la consulta →')));
    }) : [crear('p', { class: 'detenido' }, 'No hay eventos de este tipo.')]));
  };

  const filtros = crear('div', { class: 'filtros', role: 'group', 'aria-label': 'Filtrar por tipo de evento' });
  const dibujarFiltros = () => filtros.replaceChildren(...[['todos', 'Todos'], ...Object.entries(EVENTOS).map(([k, v]) => [k, v.nombre])]
    .map(([clave, nombre]) => crear('button', {
      class: 'filtro cristal', type: 'button', 'aria-pressed': String(estado.filtroAuditoria === clave),
      onclick: () => { estado.filtroAuditoria = clave; dibujarFiltros(); dibujarLista(); },
    }, nombre)));

  dibujarFiltros();
  dibujarLista();
  zona.replaceChildren(cifras, filtros, lista,
    crear('p', { class: 'detenido' }, `Se muestran los últimos ${plural(eventos.length, 'evento', 'eventos')}.`));
}

//----- navegacion (por hash: #/, #/c/<id>, #/informes[/<archivo>], #/competidores, #/auditoria) -----
function rutaActual() {
  const [seccion, ...resto] = location.hash.replace(/^#\/?/, '').split('/');
  let argumento = resto.join('/');
  try { argumento = decodeURIComponent(argumento); } catch { /* lo dejamos como esta */ }
  return { seccion: seccion || 'consulta', argumento };
}

function navegar() {
  const { seccion, argumento } = rutaActual();
  const pagina = ['informes', 'competidores', 'auditoria'].includes(seccion) ? seccion : 'consulta';
  for (const vista of document.querySelectorAll('.vista')) vista.hidden = vista.id !== `vista-${pagina}`;
  for (const link of document.querySelectorAll('.navegacion a')) {
    if (link.dataset.seccion === pagina) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  refs.app.removeAttribute('data-menu');
  refs.velo.hidden = true;

  if (pagina === 'informes') mostrarInformes(argumento);
  else if (pagina === 'competidores') mostrarCompetidores();
  else if (pagina === 'auditoria') mostrarAuditoria();
  else if (seccion === 'c' && argumento) abrirConsulta(argumento);
  else {
    reiniciarConsulta();
    dibujarConversacion();
    actualizarCompositor();
    if (matchMedia('(pointer: fine)').matches) refs.entrada.focus();
  }
  dibujarSesiones();
  actualizarTitulo();
}

//----- estado del modelo (ollama) -----
async function comprobarEstado() {
  const pastilla = $('#estado-modelo');
  const texto = $('#estado-modelo-texto');
  let datos;
  try { datos = await pedir('/api/estado'); } catch { datos = null; }
  const modelo = datos?.modelo || estado.config.modelo;

  if (!datos) {
    pastilla.dataset.estado = 'sin_conexion';
    texto.textContent = 'Servidor sin conexión';
    refs.avisoModelo.hidden = true;
  } else if (datos.ollama !== 'ok') {
    pastilla.dataset.estado = 'sin_conexion';
    texto.textContent = 'Ollama sin conexión';
    refs.avisoModelo.replaceChildren('No se pudo conectar con Ollama. Abrilo (o ejecutá ', crear('code', {}, 'ollama serve'), ') para que el agente pueda responder.');
    refs.avisoModelo.hidden = false;
  } else if (!datos.modelo_disponible) {
    pastilla.dataset.estado = 'sin_modelo';
    texto.textContent = `${modelo} · sin descargar`;
    refs.avisoModelo.replaceChildren(`El modelo ${modelo} no está descargado en Ollama. Ejecutá `, crear('code', {}, `ollama pull ${modelo}`), ' y volvé a intentar.');
    refs.avisoModelo.hidden = false;
  } else {
    pastilla.dataset.estado = 'ok';
    texto.textContent = modelo;
    refs.avisoModelo.hidden = true;
  }
  pastilla.title = texto.textContent;   //en el celu se ve solo el puntito de color
}

//----- arranque -----
function conectarEventos() {
  refs.compositor.addEventListener('submit', (e) => { e.preventDefault(); if (estado.enCurso) detener(); else enviar(); });
  refs.entrada.addEventListener('input', actualizarCompositor);
  refs.entrada.addEventListener('keydown', (e) => {
    //enter envia, shift+enter hace un salto de linea (isComposing es para teclados con acentos/ime)
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!estado.enCurso) enviar(); }
  });

  $('#atajos').replaceChildren(...ATAJOS.map((a) =>
    crear('button', { class: 'atajo cristal', type: 'button', title: a.pedido, onclick: () => usarAtajo(a.pedido) }, icono(a.icono), a.texto)));

  //la barra del costado: en pantallas anchas se pliega, en el celu es un cajon con un velo atras
  const esMovil = () => matchMedia('(max-width: 880px)').matches;
  const cerrarMenu = () => { refs.app.removeAttribute('data-menu'); refs.velo.hidden = true; };
  $('#boton-plegar').addEventListener('click', () => {
    if (esMovil()) { cerrarMenu(); return; }
    refs.app.dataset.lateral = 'plegado';
    localStorage.setItem('ragnar.lateral', 'plegado');
  });
  $('#boton-menu').addEventListener('click', () => {
    if (esMovil()) { refs.app.dataset.menu = 'abierto'; refs.velo.hidden = false; return; }
    refs.app.removeAttribute('data-lateral');
    localStorage.removeItem('ragnar.lateral');
  });
  refs.velo.addEventListener('click', cerrarMenu);
  //en el celu, tocar cualquier link del cajon lo cierra (aunque no cambie la ruta)
  $('#lateral').addEventListener('click', (e) => { if (e.target.closest('a')) cerrarMenu(); });
  if (localStorage.getItem('ragnar.lateral') === 'plegado') refs.app.dataset.lateral = 'plegado';

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (location.hash === '#/' || location.hash === '') navegar(); else location.hash = '#/';
      refs.entrada.focus();
    }
    if (e.key === 'Escape') cerrarMenu();
  });

  //"nueva consulta" estando ya en #/ no dispara hashchange, asi que navegamos a mano
  $('#nueva-consulta').addEventListener('click', () => { if (location.hash === '#/') navegar(); });
  window.addEventListener('hashchange', navegar);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) comprobarEstado(); });

  //el alto del cuadro de texto define cuanto lugar libre queda al final de la conversacion
  new ResizeObserver(() => refs.conversacion.style.setProperty('--alto-muelle', `${refs.muelle.offsetHeight}px`)).observe(refs.muelle);
}

async function iniciar() {
  conectarEventos();
  iniciarVidrio();
  try {
    estado.config = { ...estado.config, ...(await pedir('/api/config')) };
  } catch { /* seguimos con los valores por defecto; el estado avisa si no hay servidor */ }

  $('#modelo').textContent = estado.config.modelo;
  const herramientas = estado.config.herramientas;
  const dato = $('#bandeja-herramientas');
  dato.textContent = herramientas.length ? plural(herramientas.length, 'herramienta', 'herramientas') : '';
  dato.title = herramientas.join(', ');

  pedir('/api/competidores')
    .then((d) => { if (d.competidores) $('#bandeja-competidores').textContent = plural(d.competidores.length, 'competidor', 'competidores'); })
    .catch(() => {});

  comprobarEstado();
  await cargarSesiones();
  navegar();
}

iniciar();
