# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket
import json # Importa el módulo json para cargar las actividades y sectores
import locale # Importa el módulo locale para formato numérico

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# Variable para rastrear si la configuración regional se estableció con éxito
locale_set_successfully = False
try:
    # Intenta establecer la localización española para el formato numérico.
    # 'es_ES.UTF-8' es común en sistemas Linux. 'es_ES' puede funcionar en otros.
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
    locale_set_successfully = True
except locale.Error:
    print("Advertencia: No se pudo establecer la localización 'es_ES.UTF-8'. Asegúrate de que está instalada en tu sistema.")
    try:
        # Intenta una alternativa si la primera falla
        locale.setlocale(locale.LC_ALL, 'es_ES')
        locale_set_successfully = True
    except locale.Error:
        print("Advertencia: No se pudo establecer la localización 'es_ES'. Los números serán formateados manualmente.")
        # locale_set_successfully permanece False

# Carpeta donde se guardarán las imágenes subidas
app.config['UPLOAD_FOLDER'] = 'static/uploads'
# Extensiones de archivo permitidas para las imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Carga de variables de entorno para la conexión a la base de datos y el envío de emails
DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

# Función interna para formatear números manualmente si locale falla
def _format_manual_euro(value, decimals=0):
    if value is None:
        return ""
    try:
        # Convertir a float y luego a cadena con el formato deseado
        # Primero, formato inglés (coma para miles, punto para decimales)
        val_str = f"{float(value):,.{decimals}f}"
        # Luego, reemplazar para obtener formato europeo
        # Reemplazar la coma de miles (inglés) por un marcador temporal
        val_str = val_str.replace(",", "TEMP_COMMA_PLACEHOLDER")
        # Reemplazar el punto decimal (inglés) por una coma
        val_str = val_str.replace(".", ",")
        # Reemplazar el marcador temporal por un punto de miles (europeo)
        val_str = val_str.replace("TEMP_COMMA_PLACEHOLDER", ".")
        return val_str
    except (ValueError, TypeError):
        return str(value) # Devuelve el valor original si no se puede formatear

# Filtro de Jinja2 para formato de números europeos (utiliza locale o manual)
def format_euro_number(value, decimals=0):
    if value is None:
        return ""
    # Si la localización se estableció con éxito, intentar usar locale.format_string
    if locale_set_successfully:
        try:
            return locale.format_string(f"%.{decimals}f", float(value), grouping=True)
        except (ValueError, TypeError):
            # Fallback a manual si locale.format_string falla por algún motivo
            # con un valor numérico válido (ej. valor fuera de rango para locale)
            return _format_manual_euro(value, decimals)
    else:
        # Si la localización no se pudo establecer, usar siempre el formato manual
        return _format_manual_euro(value, decimals)

# Registra el filtro personalizado 'euro_format' en el entorno de Jinja2.
# Ahora puedes usar {{ variable | euro_format(2) }} en tus plantillas HTML.
app.jinja_env.filters['euro_format'] = format_euro_number


# Definición de actividades y sectores en formato JSON (como una cadena de texto)
# Luego se parsea a un diccionario de Python
ACTIVIDADES_Y_SECTORES = '''
{
  "AGRICULTURA, GANADERÍA, SILVICULTURA Y PESCA": [
    "Agricultura, ganadería, caza y servicios relacionados con las mismas",
    "Silvicultura y explotación forestal",
    "Pesca y acuicultura"
  ],
  "INDUSTRIAS EXTRACTIVAS": [
    "Extracción de antracita, hulla, y lignito",
    "Extracción de crudo de petróleo y gas natural",
    "Extracción de minerales metálicos",
    "Otras industrias extractivas",
    "Actividades de apoyo a las industrias extractivas"
  ],
  "INDUSTRIA MANUFACTURERA": [
    "Industria alimentaria",
    "Fabricación de bebidas",
    "Industria del tabaco",
    "Industria textil",
    "Confección de prendas de vestir",
    "Industria del cuero y productos relacionados de otros materiales",
    "Industria de la madera y del corcho, excepto muebles; cestería y espartería",
    "Industria del papel",
    "Artes gráficas y reproducción de soportes grabados",
    "Coquerías y refino de petróleo",
    "Industria química",
    "Fabricación de productos farmacéuticos",
    "Fabricación de productos de caucho y plásticos",
    "Fabricación de otros productos minerales no metálicos",
    "Metalurgia",
    "Fabricación de productos metálicos, excepto maquinaria y equipo",
    "Fabricación de productos informáticos, electrónicos y ópticos",
    "Fabricación de material y equipo eléctrico",
    "Fabricación de maquinaria y equipo n.c.o.p.",
    "Fabricación de vehículos de motor, remolques y semirremolques",
    "Fabricación de otro material de transporte",
    "Fabricación de muebles",
    "Otras industrias manufactureras",
    "Reparación, mantenimiento e instalación de maquinaria y equipos"
  ],
  "SUMINISTRO DE ENERGIA ELECTRICA, GAS, VAPOR Y AIRE ACONDICIONADO": [
    "Suministro de energía eléctrica, gas, vapor y aire acondicionado"
  ],
  "SUMINISTRO DE AGUA, ACTIVIDADES DE SANEAMIENTO, GESTIÓN DE RESIDUOS Y DESCONTAMINACIÓN": [
    "Captación, depuración y distribución de agua",
    "Recogida y tratamiento de aguas residuales",
    "Actividades de recogida, tratamiento y eliminación de residuos",
    "Actividades de descontaminación y otros servicios de gestión de residuos"
  ],
  "CONSTRUCCIÓN": [
    "Construcción de edificios",
    "Ingeniería civil",
    "Actividades de construcción especializada"
  ],
  "COMERCIO AL POR MAYOR Y AL POR MENOR": [
    "Comercio al por mayor",
    "Comercio al por menor"
  ],
  "TRANSPORTE Y ALMACENAMIENTO": [
    "Transporte terrestre y por tubería",
    "Transporte marítimo y por vías navegables interiores",
    "Transporte aéreo",
    "Depósito, almacenamiento y actividades auxiliares del transporte",
    "Actividades postales y de mensajería"
  ],
  "HOSTELERÍA": [
    "Servicios de alojamiento",
    "Servicios de comidas y bebidas"
  ],
  "ACTIVIDADES DE EDICIÓN, RADIODIFUSIÓN Y PRODUCCIÓN Y DISTRIBUCIÓN DE CONTENIDOS": [
    "Edición",
    "Producción cinematográfica, de vídeo y de programas de televisión, grabación de sonido y edición musical",
    "Actividades de programación, radiodifusión, agencias de noticias y otras actividades de distribución de contenidos"
  ],
  "TELECOMUNICACIONES, PROGRAMACIÓN INFORMÁTICA, CONSULTORÍA, INFRAESTRUCTURA INFORMÁTICA Y OTROS SERVICIOS DE INFORMACIÓN": [
    "Telecomunicaciones",
    "Programación, consultoría y otras actividades relacionadas con la informática",
    "Infraestructura informática, tratamiento de datos, hosting y otras actividades de servicios de información"
  ],
  "ACTIVIDADES FINANCIERAS Y DE SEGUROS": [
    "Servicios financieros, excepto seguros y fondos de pensiones",
    "Seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria",
    "Actividades auxiliares a los servicios financieros y a los seguros"
  ],
  "ACTIVIDADES INMOBILIARIAS": [
    "Actividades inmobiliarias"
  ],
  "ACTIVIDADES PROFESIONALES, CIENTÍFICAS Y TÉCNICAS": [
    "Actividades jurídicas y de contabilidad",
    "Actividades de las sedes centrales y consultoría de gestión empresarial",
    "Servicios técnicos de arquitectura e ingeniería; ensayos y análisis técnicos",
    "Investigación y desarrollo",
    "Actividades de publicidad, estudios de mercado, relaciones públicas y comunicación",
    "Otras actividades profesionales, científicas y técnicas",
    "Actividades veterinarias"
  ],
  "ACTIVIDADES ADMINISTRATIVAS Y SERVICIOS AUXILIARES": [
    "Actividades de alquiler",
    "Actividades relacionadas con el empleo",
    "Actividades de agencias de viajes, operadores turísticos, servicios de reservas y actividades relacionadas",
    "Servicios de investigación y seguridad",
    "Servicios a edificios y actividades de jardinería",
    "Actividades administrativas de oficina y otras actividades auxiliares a las empresas"
  ],
  "ADMINISTRACIÓN PÚBLICA Y DEFENSA; SEGURIDAD SOCIAL OBLIGATORIA": [
    "Administración pública y defensa; seguridad social obligatoria"
  ],
  "EDUCACIÓN": [
    "Educación"
  ],
  "ACTIVIDADES SANITARIAS Y DE SERVICIOS SOCIALES": [
    "Actividades sanitarias",
    "Asistencia en establecimientos residenciales",
    "Actividades de servicios sociales sin alojamiento"
  ],
  "ACTIVIDADES ARTÍSTICAS, DEPORTIVAS Y DE ENTRETENIMIENTO": [
    "Actividades de creación artística y artes escénicas",
    "Actividades de bibliotecas, archivos, museos y otras actividades culturales",
    "Actividades de juegos de azar y apuestas",
    "Actividades deportivas, recreativas y de entretenimiento"
  ],
  "OTROS SERVICIOS": [
    "Actividades asociativas",
    "Reparación y mantenimiento de ordenadores, artículos personales y enseres domésticos y vehículos de motor y motocicletas",
    "Servicios personales"
  ],
  "ACTIVIDADES DE LOS HOGARES COMO EMPLEADORES DE PERSONAL DOMÉSTICO Y COMO PRODUCTORES DE BIENES Y SERVICIOS PARA USO PROPIO": [
    "Actividades de los hogares como empleadores de personal doméstico",
    "Actividades de los hogares como productores de bienes y servicios para uso propio"
  ]
}
'''
ACTIVIDADES_Y_SECTORES = json.loads(ACTIVIDADES_Y_SECTORES)

# Lista de provincias de España (para usar en los desplegables de ubicación)
PROVINCIAS_ESPANA = [
    'Álava', 'Albacete', 'Alicante', 'Almería', 'Asturias', 'Ávila',
    'Badajoz', 'Barcelona', 'Burgos', 'Cáceres', 'Cádiz', 'Cantabria',
    'Castellón', 'Ciudad Real', 'Córdoba', 'Cuenca', 'Gerona', 'Granada',
    'Guadalajara', 'Guipúzcoa', 'Huelva', 'Huesca', 'Islas Baleares',
    'Jaén', 'La Coruña', 'La Rioja', 'Las Palmas', 'León', 'Lérida',
    'Lugo', 'Madrid', 'Málaga', 'Murcia', 'Navarra', 'Orense',
    'Palencia', 'Pontevedra', 'Salamanca', 'Santa Cruz de Tenerife',
    'Segovia', 'Sevilla', 'Soria', 'Tarragona', 'Teruel', 'Toledo',
    'Valencia', 'Valladolid', 'Vizcaya', 'Zamora', 'Zaragoza'
]


# Función para establecer la conexión a la base de datos PostgreSQL
def get_db_connection():
    # Parche para psycopg2 con Render.com (fuerza IPv4 para la conexión a la DB)
    orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *args, **kwargs: [
        info for info in orig_getaddrinfo(*args, **kwargs) if info[0] == socket.AF_INET
    ]
    # Conecta a la base de datos usando la URL de entorno
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    # Configura el cursor para devolver diccionarios (acceso por nombre de columna)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

# Función para verificar si un archivo tiene una extensión permitida
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Función para enviar un correo electrónico de notificación de nueva empresa
def enviar_email_interes(empresa_nombre, email_usuario):
    msg = EmailMessage()
    msg['Subject'] = f"📩 Nueva empresa publicada: {empresa_nombre}"
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = EMAIL_DESTINO
    msg.set_content(f"""
¡Se ha publicado una nueva empresa en el portal!

Nombre: {empresa_nombre}
Contacto: {email_usuario}
""")
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
            smtp.send_message(msg)
    except smtplib.SMTPException as e:
        print(f"Error al enviar email: {e}")
        # En un entorno de producción, podrías registrar esto o reintentarlo
    except Exception as e:
        print(f"Error inesperado al enviar email: {e}")

# Ruta principal de la aplicación: muestra el listado de empresas
@app.route('/', methods=['GET'])
def index():
    # Obtiene parámetros de filtro de la URL
    provincia = request.args.get('provincia')
    pais = request.args.get('pais', 'España') # Valor por defecto 'España'
    actividad = request.args.get('actividad')
    sector = request.args.get('sector')
    # Conversión a float para rangos de facturación y precio de venta
    min_fact = request.args.get('min_facturacion', 0, type=float)
    max_fact = request.args.get('max_facturacion', 1e12, type=float) # 1e12 es un número muy grande para el máximo
    max_precio = request.args.get('max_precio', 1e12, type=float)

    conn = get_db_connection()
    cur = conn.cursor()

    # Construcción dinámica de la consulta SQL para filtrar empresas
    query = "SELECT * FROM empresas WHERE facturacion BETWEEN %s AND %s AND precio_venta <= %s"
    params = [min_fact, max_fact, max_precio]

    if provincia:
        query += " AND ubicacion = %s" # Cambiado a 'ubicacion' para coincidir con la columna en DB
        params.append(provincia)
    if pais:
        query += " AND pais = %s"
        params.append(pais)
    if actividad:
        query += " AND actividad = %s"
        params.append(actividad)
    if sector:
        query += " AND sector = %s"
        params.append(sector)

    cur.execute(query, tuple(params))
    empresas = cur.fetchall() # Obtiene todos los resultados

    cur.close()
    conn.close()

    # Renderiza la plantilla index.html con las empresas y los datos para los desplegables
    return render_template('index.html', empresas=empresas, actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

# Ruta para publicar una nueva empresa
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        # Obtiene datos del formulario (campos de texto)
        nombre = request.form['nombre']
        email_contacto = request.form['email_contacto']
        actividad = request.form['actividad']
        sector = request.form['sector']
        pais = request.form['pais']
        ubicacion = request.form['ubicacion'] # Ahora será una provincia de PROVINCIAS_ESPANA
        tipo_negocio = request.form['tipo_negocio'] # Nuevo campo
        descripcion = request.form['descripcion']
        local_propiedad = request.form['local_propiedad']


        # --- Manejo y validación de campos numéricos ---
        # Se asume que estos campos son obligatorios en el front-end (HTML con 'required').
        # Se usa un bloque try-except para capturar posibles errores de conversión
        # si la validación del front-end falla o es omitida.
        try:
            facturacion = float(request.form['facturacion'])
            numero_empleados = int(request.form['numero_empleados'])
            # Nuevo nombre: resultado_antes_impuestos (anteriormente beneficio_impuestos)
            resultado_antes_impuestos = float(request.form['resultado_antes_impuestos'])
            deuda = float(request.form['deuda'])
            precio_venta = float(request.form['precio_venta'])
        except ValueError:
            # Si hay un error de conversión (ej. texto en campo numérico), muestra un mensaje y redirige
            flash('Por favor, asegúrate de que todos los campos numéricos contengan solo números válidos.', 'error')
            return redirect(url_for('publicar'))

        # Manejo de la subida de imagen
        imagen = request.files.get('imagen') # Usar .get() para evitar KeyError si el campo no está presente
        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))

        conn = get_db_connection()
        cur = conn.cursor()
        # Inserta los datos en la tabla 'empresas'
        cur.execute("""
            INSERT INTO empresas (nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, descripcion, facturacion,
                                  numero_empleados, local_propiedad, resultado_antes_impuestos, deuda, precio_venta, imagen_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, descripcion, facturacion, numero_empleados,
              local_propiedad, resultado_antes_impuestos, deuda, precio_venta, imagen_filename))
        conn.commit() # Confirma los cambios en la base de datos
        cur.close()
        conn.close()

        # Envía un correo electrónico de notificación
        enviar_email_interes(nombre, email_contacto)

        flash('Empresa publicada correctamente', 'success')
        return redirect(url_for('index')) # Redirige a la página principal

    # Si es una solicitud GET, renderiza el formulario de publicación
    return render_template('vender_empresa.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

# --- INICIO DE LA RUTA 'DETALLE' AÑADIDA ---
@app.route('/detalle/<int:empresa_id>', methods=['GET'])
def detalle(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa is None:
        flash('La empresa solicitada no existe.', 'error')
        return redirect(url_for('index')) # O puedes retornar un error 404 más explícito

    return render_template('detalle.html', empresa=empresa)
# --- FIN DE LA RUTA 'DETALLE' AÑADIDA ---


# Ruta para editar o eliminar un anuncio existente (requiere token de administrador)
@app.route('/editar/<int:empresa_id>', methods=['GET', 'POST'])
def editar_anuncio(empresa_id):
    # Verifica el token de administrador para permitir el acceso a la edición
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso denegado", 403

    conn = get_db_connection()
    cur = conn.cursor()
    # Obtiene los datos de la empresa a editar
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()

    if request.method == 'POST':
        # Si se solicita eliminar la empresa
        if 'eliminar' in request.form:
            cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
            conn.commit()
            cur.close()
            conn.close()
            flash('Anuncio eliminado correctamente', 'success')
            return redirect(url_for('admin', admin_token=token))

        # --- Manejo y validación de campos numéricos para la actualización ---
        # Recolecta los valores del formulario para actualizar
        try:
            nombre = request.form['nombre']
            email_contacto = request.form['email_contacto']
            actividad = request.form['actividad']
            sector = request.form['sector']
            pais = request.form['pais']
            ubicacion = request.form['ubicacion'] # Ahora será una provincia de PROVINCIAS_ESPANA
            tipo_negocio = request.form['tipo_negocio'] # Nuevo campo
            descripcion = request.form['descripcion']

            facturacion = float(request.form['facturacion'])
            numero_empleados = int(request.form['numero_empleados'])
            local_propiedad = request.form['local_propiedad']
            # Nuevo nombre: resultado_antes_impuestos
            resultado_antes_impuestos = float(request.form['resultado_antes_impuestos'])
            deuda = float(request.form['deuda'])
            precio_venta = float(request.form['precio_venta'])

            # Crea la lista de valores para la consulta UPDATE
            nuevos_valores = [
                nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, # Añadido tipo_negocio
                descripcion, facturacion, numero_empleados,
                local_propiedad, resultado_antes_impuestos, deuda, precio_venta
            ]

        except ValueError:
            # Si hay un error de conversión, muestra un mensaje y redirige al formulario de edición
            flash('Por favor, asegúrate de que todos los campos numéricos contengan solo números válidos.', 'error')
            return redirect(url_for('editar_anuncio', empresa_id=empresa_id, admin_token=token))

        # Manejo de la actualización de imagen
        imagen = request.files.get('imagen') # Usar .get()
        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))
            nuevos_valores.append(imagen_filename) # Añade el nombre de la imagen al final de la lista
            # Actualiza todos los campos, incluyendo la URL de la imagen
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, actividad = %s, sector = %s, pais = %s, ubicacion = %s, tipo_negocio = %s,
                    descripcion = %s, facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    resultado_antes_impuestos = %s, deuda = %s, precio_venta = %s, imagen_url = %s
                WHERE id = %s
            """, (*nuevos_valores, empresa_id))
        else:
            # Actualiza los campos sin cambiar la URL de la imagen si no se subió una nueva
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, actividad = %s, sector = %s, pais = %s, ubicacion = %s, tipo_negocio = %s,
                    descripcion = %s, facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    resultado_antes_impuestos = %s, deuda = %s, precio_venta = %s
                WHERE id = %s
            """, (*nuevos_valores, empresa_id))

        conn.commit()
        flash('Anuncio actualizado correctamente', 'success')
        return redirect(url_for('admin', admin_token=token)) # Redirige a la página de administración

    # Si es una solicitud GET, renderiza el formulario de edición con los datos actuales de la empresa
    cur.close()
    conn.close()
    # Pasa la lista de provincias a la plantilla de edición
    return render_template('editar.html', empresa=empresa, actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

# Rutas para otras páginas estáticas o informativas
@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

@app.route('/contacto')
def contacto():
    return render_template('contacto.html')

@app.route('/nota-legal')
def nota_legal():
    return render_template('nota_legal.html')

# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    # Obtiene el puerto del entorno o usa 5000 por defecto
    port = int(os.environ.get('PORT', 5000))
    # Ejecuta la aplicación en todas las interfaces de red disponibles
    app.run(host='0.0.0.0', port=port)
