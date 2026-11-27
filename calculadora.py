"""Calculadora orientativa de valoración por múltiplos para pymes.

Datos: Observatorio de múltiplos de AC Partners (https://acpartners.es/multiplos-valoracion-sector/),
bloque JSON estable «ac-multiplos-datos», versión 1. Se usa la copia incluida en data/ y, si la página
pública ya publica una edición más reciente, esa (se consulta como mucho una vez cada pocas horas).

Método (solo referencia de mercado para la calculadora web; no es el método de las valoraciones profesionales):
  1. Múltiplo del sector: mediana y cuartiles (P25-P75) de las cotizadas europeas del Observatorio, sin ajustes.
     La página avisa de que a una pyme hay que aplicarle ajustes (tamaño, iliquidez, personas clave...) y remite
     al formulario de contacto para ajustar la valoración a cada caso.
  2. Valor de empresa = múltiplo × EBITDA (o ventas). Valor de las participaciones = valor de empresa
     - deuda financiera + caja. El PER da directamente el valor de las participaciones.

Actualizar la copia local cada trimestre (o cuando se publique el Observatorio): data/observatorio_multiplos.json es
la salida de datos_maquina() de observatorio/actualizar_observatorio.py con el historico/observatorio_FECHA.json nuevo.
"""
import json
import os
import re
import threading
import time

import requests

RUTA_DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'observatorio_multiplos.json')
URL_OBSERVATORIO = os.environ.get('OBSERVATORIO_URL', 'https://acpartners.es/multiplos-valoracion-sector/')
REFRESCO_SEGUNDOS = 6 * 3600

METODOS = {
    'ev_ebitda': 'EV/EBITDA',
    'ev_ventas': 'EV/Ventas',
    'per': 'PER',
}

# Ejemplos de actividad por sector, para ayudar a elegir (sectores del Observatorio)
EJEMPLOS_SECTOR = {
    'Alimentación y bebidas': 'Fabricación de alimentos y bebidas, agroalimentaria, explotaciones agrícolas, mayoristas de alimentación.',
    'Construcción e ingeniería': 'Constructoras, ingeniería, reformas, materiales de construcción, promoción de viviendas.',
    'Distribución y comercio': 'Tiendas y comercio minorista, supermercados, distribución mayorista, moda, calzado, mobiliario.',
    'Energía y utilities': 'Energías renovables, electricidad, gas, agua, petróleo y servicios energéticos.',
    'Hostelería, turismo y ocio': 'Restaurantes, bares, hoteles, alojamientos, ocio, entretenimiento y actividades turísticas.',
    'Industria y fabricación': 'Maquinaria, metalurgia ligera, equipos eléctricos y electrónicos, componentes de automoción.',
    'Inmobiliario': 'Gestión y explotación de inmuebles, servicios inmobiliarios. Ojo: los múltiplos encajan mal con negocios patrimoniales.',
    'Logística y transporte': 'Transporte de mercancías, mensajería, logística y almacenamiento, transporte de viajeros.',
    'Química y materiales': 'Química, plásticos, papel, embalaje, metales y siderurgia.',
    'Salud y farmacia': 'Clínicas, centros sanitarios, productos sanitarios, farmacia, servicios de salud.',
    'Servicios a empresas': 'Consultoría, asesoría, marketing y publicidad, limpieza, seguridad, formación, servicios a particulares.',
    'Tecnología y software': 'Desarrollo de software, SaaS, consultoría IT, servicios informáticos, comercio electrónico.',
    'Telecomunicaciones y medios': 'Telecomunicaciones, operadores, medios de comunicación, edición y radiodifusión.',
}

_cache = {'datos': None, 'origen': None, 'comprobado': 0.0}
_lock = threading.Lock()


def _valido(d):
    return (isinstance(d, dict) and d.get('version') == 1 and isinstance(d.get('sectores'), list)
            and d['sectores'])


def _datos_locales():
    with open(RUTA_DATOS, encoding='utf-8') as f:
        return json.load(f)


def _datos_remotos():
    """Lee el bloque <script id="ac-multiplos-datos"> de la página pública del Observatorio."""
    r = requests.get(URL_OBSERVATORIO, timeout=3, headers={'User-Agent': 'Pymemarket-calculadora/1.0'})
    r.raise_for_status()
    m = re.search(r'<script[^>]*id=["\']ac-multiplos-datos["\'][^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return None
    d = json.loads(m.group(1))
    return d if _valido(d) else None


def obtener_datos():
    """Devuelve (datos, origen). Usa la edición publicada si es más reciente que la copia local."""
    ahora = time.time()
    with _lock:
        if _cache['datos'] is None:
            _cache['datos'], _cache['origen'] = _datos_locales(), 'local'
        if os.environ.get('OBSERVATORIO_REMOTO', '1') != '0' and ahora - _cache['comprobado'] > REFRESCO_SEGUNDOS:
            _cache['comprobado'] = ahora
            try:
                remoto = _datos_remotos()
                if remoto and remoto.get('fecha_datos', '') >= _cache['datos'].get('fecha_datos', ''):
                    _cache['datos'], _cache['origen'] = remoto, 'web'
            except Exception as e:  # la calculadora sigue funcionando con la copia local
                print(f"AVISO calculadora: no se pudo leer el Observatorio publicado: {e}")
        return _cache['datos'], _cache['origen']


def parametros(datos):
    """Todo lo que necesita el navegador para calcular: múltiplos por sector."""
    sectores = []
    for s in datos['sectores']:
        sectores.append(dict(
            sector=s['sector'], empresas=s.get('empresas'), ejemplos=EJEMPLOS_SECTOR.get(s['sector'], ''),
            ev_ebitda_espana=s.get('ev_ebitda_espana'),
            **{f'{m}_{k}': s.get(f'{m}_{k}') for m in METODOS for k in ('p25', 'mediana', 'p75')}))
    return dict(fecha_datos=datos.get('fecha_datos'), empresas=datos.get('empresas'), url=datos.get('url'), sectores=sectores)


def _num(v):
    if v in (None, ''):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('€', '').replace(' ', '').replace('\xa0', '')
    if ',' in s or re.fullmatch(r'-?\d{1,3}(\.\d{3})+', s):  # formato español: 1.234.567,89
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def valorar(datos, sector, ventas, ebitda, beneficio=None, deuda=0, caja=0):
    """Mismo cálculo que hace la página en el navegador. Devuelve None si faltan datos."""
    ventas, ebitda, beneficio = _num(ventas), _num(ebitda), _num(beneficio)
    deuda, caja = _num(deuda) or 0.0, _num(caja) or 0.0
    s = next((x for x in datos['sectores'] if x['sector'] == sector), None)
    if s is None or not ventas or ventas <= 0 or ebitda is None:
        return None
    res = {'sector': sector, 'metodos': {}}
    for m, base in (('ev_ebitda', ebitda), ('ev_ventas', ventas), ('per', beneficio)):
        if base is None or base <= 0 or s.get(f'{m}_mediana') is None:
            continue
        fila = {'multiplo_sector': s[f'{m}_mediana']}
        for k in ('p25', 'mediana', 'p75'):
            v = s[f'{m}_{k}'] * base
            fila[k] = v if m == 'per' else v - deuda + caja  # PER ya da el valor de las participaciones
            if m != 'per':
                fila[f'ev_{k}'] = v
        res['metodos'][m] = fila
    principal = 'ev_ebitda' if 'ev_ebitda' in res['metodos'] else 'ev_ventas'
    res['principal'] = principal
    res['valor'] = {k: max(0.0, res['metodos'][principal][k]) for k in ('p25', 'mediana', 'p75')}
    return res


def eur(v):
    return f"{v:,.0f} €".replace(',', '.')


def resumen_texto(res):
    """Texto para el email/solicitud cuando el usuario pide una valoración profesional."""
    if not res:
        return ''
    v = res['valor']
    lineas = [f"Resultado de referencia de la calculadora, sin ajustes ({METODOS[res['principal']]}, sector {res['sector']}): "
              f"{eur(v['p25'])} – {eur(v['p75'])} (central {eur(v['mediana'])})."]
    for m, f in res['metodos'].items():
        lineas.append(f"  {METODOS[m]}: múltiplo del sector {f['multiplo_sector']:.2f}x".replace('.', ',')
                      + f" → {eur(f['mediana'])}")
    return '\n'.join(lineas)
