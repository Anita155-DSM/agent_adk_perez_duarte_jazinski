//el efecto de "vidrio liquido", son dos cosas:
// 1. el reflejo: una luz suavecita que sigue al mouse arriba de cada panel .vidrio
// 2. la refraccion: en los elementos .lente el borde del vidrio "dobla" lo que pasa
//    por atras, como una lupa. se hace con un filtro svg de desplazamiento adentro de
//    backdrop-filter, y eso hoy solo lo soportan los navegadores chromium (chrome, edge).
//    en los demas queda el vidrio esmerilado comun de estilos.css

const SVG = 'http://www.w3.org/2000/svg';
const BISEL = 26;      //ancho en px de la franja del borde que refracta
const FUERZA = 70;     //cuanto se corre como maximo en px (el atributo scale del filtro)

const esChromium = /Chrom(e|ium)\//.test(navigator.userAgent);
const sinTransparencia = matchMedia('(prefers-reduced-transparency: reduce)').matches;

/*dibuja el mapa de desplazamiento de un rectangulo redondeado.
  en cada pixel: rojo = cuanto correr en X, verde = cuanto correr en Y (128 = nada).
  en el centro no se corre nada; cerca del borde se muestra lo que esta mas
  adentro, y eso es lo que da el efecto de lupa*/
function mapaDeDesplazamiento(ancho, alto, radio) {
  const lienzo = document.createElement('canvas');
  lienzo.width = ancho;
  lienzo.height = alto;
  const ctx = lienzo.getContext('2d');
  const imagen = ctx.createImageData(ancho, alto);
  const medioX = ancho / 2;
  const medioY = alto / 2;
  const r = Math.min(radio, medioX, medioY);

  for (let y = 0; y < alto; y++) {
    for (let x = 0; x < ancho; x++) {
      const px = x + 0.5 - medioX;
      const py = y + 0.5 - medioY;
      const qx = Math.abs(px) - (medioX - r);
      const qy = Math.abs(py) - (medioY - r);

      //distancia al borde (hacia adentro) y la direccion que apunta para afuera
      let distancia; let nx; let ny;
      if (qx > 0 && qy > 0) {                       //zona de una esquina redondeada
        const largo = Math.hypot(qx, qy);
        distancia = r - largo;
        nx = (qx / largo) * Math.sign(px);
        ny = (qy / largo) * Math.sign(py);
      } else if (qx > qy) {                         //lados izquierdo / derecho
        distancia = r - qx; nx = Math.sign(px); ny = 0;
      } else {                                      //lados de arriba / abajo
        distancia = r - qy; nx = 0; ny = Math.sign(py);
      }

      const cercania = Math.max(0, Math.min(1, 1 - distancia / BISEL));
      const fuerza = cercania * cercania;           //crece suave hacia el borde
      const i = (y * ancho + x) * 4;
      imagen.data[i] = 128 - nx * fuerza * 127;
      imagen.data[i + 1] = 128 - ny * fuerza * 127;
      imagen.data[i + 2] = 128;
      imagen.data[i + 3] = 255;
    }
  }
  ctx.putImageData(imagen, 0, 0);
  return lienzo.toDataURL();
}

function instalarLente(elemento, indice, defs) {
  const id = `lente-${indice}`;
  const filtro = document.createElementNS(SVG, 'filter');
  filtro.setAttribute('id', id);
  filtro.setAttribute('x', '0');
  filtro.setAttribute('y', '0');
  filtro.setAttribute('width', '100%');
  filtro.setAttribute('height', '100%');
  //sRGB: asi el gris 128 del mapa vale justo "sin desplazamiento"
  filtro.setAttribute('color-interpolation-filters', 'sRGB');

  const mapa = document.createElementNS(SVG, 'feImage');
  mapa.setAttribute('x', '0');
  mapa.setAttribute('y', '0');
  mapa.setAttribute('preserveAspectRatio', 'none');
  mapa.setAttribute('result', 'mapa');

  const desplazar = document.createElementNS(SVG, 'feDisplacementMap');
  desplazar.setAttribute('in', 'SourceGraphic');
  desplazar.setAttribute('in2', 'mapa');
  desplazar.setAttribute('scale', String(FUERZA));
  desplazar.setAttribute('xChannelSelector', 'R');
  desplazar.setAttribute('yChannelSelector', 'G');

  filtro.append(mapa, desplazar);
  defs.append(filtro);

  let pendiente = 0;
  const actualizar = () => {
    pendiente = 0;
    const ancho = Math.round(elemento.offsetWidth);
    const alto = Math.round(elemento.offsetHeight);
    if (!ancho || !alto) return;
    const radio = parseFloat(getComputedStyle(elemento).borderTopLeftRadius) || 0;
    mapa.setAttribute('width', String(ancho));
    mapa.setAttribute('height', String(alto));
    mapa.setAttribute('href', mapaDeDesplazamiento(ancho, alto, radio));
  };
  //el cuadro de texto cambia de alto cuando escribimos, asi que hay que volver a dibujar el mapa
  new ResizeObserver(() => { pendiente ||= requestAnimationFrame(actualizar); }).observe(elemento);
  actualizar();

  elemento.style.setProperty('--refraccion', `url(#${id})`);
  elemento.classList.add('lente-activa');
}

function seguirPuntero() {
  let ultimo = null;
  document.addEventListener('pointermove', (evento) => {
    const panel = evento.target.closest?.('.vidrio');
    if (ultimo && ultimo !== panel) ultimo.style.setProperty('--brillo', '0');
    ultimo = panel ?? null;
    if (!panel) return;
    const caja = panel.getBoundingClientRect();
    panel.style.setProperty('--px', `${evento.clientX - caja.left}px`);
    panel.style.setProperty('--py', `${evento.clientY - caja.top}px`);
    panel.style.setProperty('--brillo', '1');
  }, { passive: true });
  document.addEventListener('pointerleave', () => ultimo?.style.setProperty('--brillo', '0'));
}

export function iniciarVidrio() {
  seguirPuntero();
  if (!esChromium || sinTransparencia) return;
  const defs = document.getElementById('filtros-lente');
  document.querySelectorAll('.lente').forEach((elemento, i) => instalarLente(elemento, i, defs));
}
