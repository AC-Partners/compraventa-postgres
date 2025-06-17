# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras # Para usar DictCursor en las consultas
from werkzeug.utils import secure_filename
import smtplib
import socket
import json # Importa el módulo json para cargar las actividades y sectores
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos (UUIDs)
from datetime import datetime, timedelta # Necesario para URLs firmadas temporales y expiración de tokens

# IMPORTACIONES AÑADIDAS PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

# IMPORTACIONES ADICIONALES PARA EMAIL (SI YA EXISTEN, SE MANTIENEN)
from email.mime.text import MIMEText # Para crear mensajes HTML/texto plano
from email.mime.multipart import MIMEMultipart # Para mensajes con múltiples partes (HTML y texto)
from email.header import Header # Para manejar encabezados con caracteres especiales (UTF-8)
import logging

# Configura el logger global para ver mensajes en los logs de Render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
# Se mantiene la variable de entorno FLASK_SECRET_KEY, con un valor por defecto para desarrollo.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key-super-segura-CAMBIAR-EN-PRODUCCION')

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Obtener el nombre del bucket de GCS de las variables de entorno de Render.com
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Inicializar el cliente de Cloud Storage
gcs_key_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
storage_client = None # Inicializar a None por defecto
if gcs_key_json:
    try:
        credentials_info = json.loads(gcs_key_json)
        storage_client = storage.Client.from_service_account_info(credentials_info)
        logging.info("Cliente de Google Cloud Storage inicializado desde la variable de entorno JSON.")
    except json.JSONDecodeError as e:
        logging.error(f"Error al decodificar JSON de credenciales de GCP: {e}. Asegúrate de que GCP_SERVICE_ACCOUNT_KEY_JSON contiene JSON válido y sin saltos de línea inesperados.")
    except Exception as e:
        logging.error(f"Error inesperado al inicializar cliente GCS: {e}", exc_info=True)
else:
    # Si la variable GCP_SERVICE_ACCOUNT_KEY_JSON no está configurada,
    # el cliente intentará buscar credenciales por defecto (útil para desarrollo local).
    storage_client = storage.Client()
    logging.warning("GCP_SERVICE_ACCOUNT_KEY_JSON no encontrada. El cliente de GCS intentará credenciales por defecto. Para Render, asegúrate de configurar GCP_SERVICE_ACCOUNT_KEY_JSON y CLOUD_STORAGE_BUCKET.")


# Función para subir un archivo a Google Cloud Storage
def upload_to_gcs(file_obj, filename, content_type):
    """
    Sube un objeto de archivo (FileStorage) a Google Cloud Storage.
    Genera un nombre de archivo único utilizando UUID para evitar colisiones.
    Retorna la URL firmada del archivo subido y el nombre único en GCS.
    """
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logging.error("Error: Cliente de GCS o nombre de bucket no configurado para la subida.")
        return None, None

    unique_filename = str(uuid.uuid4()) + '_' + secure_filename(filename)

    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(unique_filename)

        blob.upload_from_file(file_obj.stream, content_type=content_type)

        signed_url = blob.generate_signed_url(expiration=timedelta(days=7))
        return signed_url, unique_filename
    except Exception as e:
        logging.error(f"Error al subir el archivo {filename} a GCS: {e}", exc_info=True)
        return None, None

# Función para eliminar un archivo de Google Cloud Storage
def delete_from_gcs(filename_in_gcs):
    """
    Elimina un archivo del bucket de Google Cloud Storage.
    Recibe el nombre único del archivo tal como está en GCS.
    """
    if not storage_client or not CLOUD_STORAGE_BUCKET or not filename_in_gcs:
        logging.warning("Advertencia: No se pudo eliminar el archivo de GCS. Cliente/Bucket no configurado o nombre de archivo vacío.")
        return False

    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename_in_gcs)

    try:
        if blob.exists():
            blob.delete()
            logging.info(f"Archivo '{filename_in_gcs}' eliminado de GCS correctamente.")
            return True
        else:
            logging.warning(f"Advertencia: El archivo '{filename_in_gcs}' no existe en GCS. No se realizó la eliminación.")
            return False
    except Exception as e:
        logging.error(f"Error al eliminar el archivo '{filename_in_gcs}' de GCS: {e}", exc_info=True)
        return False

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------


# Variable para rastrear si la configuración regional se estableció con éxito
locale_set_successfully = False
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
    locale_set_successfully = True
except locale.Error:
    logging.warning("Advertencia: No se pudo establecer la localización 'es_ES.UTF-8'. Asegúrate de que está instalada en tu sistema.")
    try:
        locale.setlocale(locale.LC_ALL, 'es_ES')
        locale_set_successfully = True
    except locale.Error:
        logging.warning("Advertencia: No se pudo establecer la localización 'es_ES'. Los números serán formateados manualmente.")

# Extensiones de archivo permitidas para las imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Carga de variables de entorno para la conexión a la base de datos y el envío de emails
DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO') # Para el correo del administrador
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN') # Token para acceso al panel de administración

# Variables de entorno para la configuración SMTP del servicio de correo (Jimdo/srvr.com)
SMTP_SERVER = os.environ.get('SMTP_SERVER')
# Asegurarse de que SMTP_PORT es un entero o None si no está configurado
SMTP_PORT = int(os.environ.get('SMTP_PORT')) if os.environ.get('SMTP_PORT') else None
SMTP_USERNAME = os.environ.get('SMTP_USERNAME') # La cuenta pymemarket@acpartners.es
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')

# --- Datos Estáticos o de Soporte (si no vienen de DB) ---
# Se mantienen las definiciones originales del usuario
ACTIVIDADES_Y_SECTORES = json.loads('''
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
''')

PROVINCIAS_ESPANA = [
    'Álava', 'Albacete', 'Alicante', 'Almería', 'Asturias', 'Ávila',
    'Badajoz', 'Barcelona', 'Burgos', 'Cáceres', 'Cádiz', 'Cantabria',
    'Castellón', 'Ciudad Real', 'Córdoba', 'Cuenca', 'Gerona', 'Granada',
    'Guadalajara', 'Guipúzcoa', 'Huelva', 'Huesca', 'Islas Baleares',
    'Jaén', 'La Coruña', 'La Rioja', 'Las Palmas', 'León', 'Lérida',
    'Lugo', 'Madrid', 'Málaga', 'Murcia', 'Navarra', 'Orense',
    'Palencia', 'Pontevedra', 'Salamanca', 'Santa Cruz de Tenerife',
    'Segovia', 'Sevilla', 'Soria', 'Tarragona', 'Teruel', 'Toledo',
    'Valencia', 'Valladolid', 'Vizcaya', 'Zamora', 'Zaragoza', 'Ceuta', 'Melilla'
]

# Funciones auxiliares para obtener datos estáticos (para las plantillas)
def get_all_actividades():
    return list(ACTIVIDADES_Y_SECTORES.keys())

def get_all_provincias():
    return PROVINCIAS_ESPANA

def get_actividades_sectores_dict():
    return ACTIVIDADES_Y_SECTORES

# Función para establecer la conexión a la base de datos PostgreSQL (Neon)
def get_db_connection():
    # Parche para psycopg2 con Render.com (fuerza IPv4 para la conexión a la DB)
    orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *args, **kwargs: [
        info for info in orig_getaddrinfo(*args, **kwargs) if info[0] == socket.AF_INET
    ]
    # Conecta a la base de datos usando la URL de entorno
    # Se añade el 'sslmode=require' para conexiones seguras, común en servicios cloud como Neon
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    # Configura el cursor para devolver diccionarios (acceso por nombre de columna)
    conn.cursor_factory = psycopg2.extras.DictCursor
    return conn

# Función para verificar si un archivo tiene una extensión permitida
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# =====================================================================================
# FUNCIONES DE ENVÍO DE CORREO USANDO SMTP DIRECTO (Jimdo/srvr.com)
# =====================================================================================

def enviar_correo_smtp_externo(destinatario, asunto, cuerpo_html, remitente_nombre="Pyme Market", cuerpo_texto=None):
    """
    Envía un correo electrónico usando la configuración SMTP externa (Jimdo/srvr.com).
    Args:
        destinatario (str): La dirección de correo del destinatario.
        asunto (str): El asunto del correo.
        cuerpo_html (str): El contenido del correo en formato HTML.
        remitente_nombre (str): El nombre que aparecerá como remitente.
        cuerpo_texto (str, optional): Contenido del correo en texto plano (fallback).
    Returns:
        bool: True si el correo se envió con éxito, False en caso contrario.
    """
    try:
        # Asegúrate de que las variables de entorno para SMTP están cargadas y son válidas
        if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD]):
            logging.error("Variables de entorno SMTP no configuradas correctamente. No se puede enviar el correo.")
            return False

        # El remitente técnico (FROM real en la autenticación) debe ser la misma cuenta que usas para autenticar
        remitente_autenticacion = SMTP_USERNAME

        # Crear el mensaje de tipo MIMEMultipart para permitir HTML y texto plano
        msg = MIMEMultipart('alternative') 

        # Configurar los encabezados del correo
        msg['From'] = Header(remitente_autenticacion, 'utf-8')
        msg['To'] = Header(destinatario, 'utf-8')
        msg['Subject'] = Header(asunto, 'utf-8')

        # Adjuntar la parte de texto plano (si existe)
        if cuerpo_texto:
            part1 = MIMEText(cuerpo_texto, 'plain', 'utf-8')
            msg.attach(part1)

        # Adjuntar la parte HTML
        part2 = MIMEText(cuerpo_html, 'html', 'utf-8')
        msg.attach(part2)

        server = None
        # Intenta conectar con SSL/TLS dependiendo del puerto configurado
        if SMTP_PORT == 465: # Puerto estándar para SMTPS (SSL/TLS directo)
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        elif SMTP_PORT == 587: # Puerto estándar para SMTP con STARTTLS
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls() # Inicia el cifrado TLS
        elif SMTP_PORT == 8025: # Si se usa este puerto, asumimos SSL/TLS también como 465 (Jimdo lo lista)
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            logging.error(f"Puerto SMTP no soportado o desconocido: {SMTP_PORT}. No se puede conectar.")
            return False

        # Autenticación y envío del correo
        server.login(remitente_autenticacion, SMTP_PASSWORD)
        server.sendmail(remitente_autenticacion, destinatario, msg.as_string())
        server.quit()

        logging.info(f"Correo enviado exitosamente a {destinatario} desde Jimdo/srvr.com (Asunto: {asunto}).")
        return True

    except smtplib.SMTPAuthenticationError:
        logging.error("Error de autenticación SMTP. Revisa el usuario y contraseña (SMTP_USERNAME/SMTP_PASSWORD) para el servidor SMTP.")
        return False
    except smtplib.SMTPConnectError:
        logging.error(f"Error de conexión SMTP a {SMTP_SERVER}:{SMTP_PORT}. Revisa la configuración del servidor y el puerto, o la conectividad de red.")
        return False
    except socket.gaierror:
        logging.error(f"Error de resolución DNS para el servidor SMTP: {SMTP_SERVER}. Asegúrate de que el nombre del servidor es correcto y accesible.")
        return False
    except Exception as e:
        logging.error(f"Error general al enviar correo con Jimdo/srvr.com: {e}", exc_info=True)
        return False

# Función para enviar un correo electrónico de notificación de nueva empresa al admin
def enviar_email_notificacion_admin(empresa_nombre, email_usuario, empresa_id):
    destino_admin = os.environ.get('EMAIL_DESTINO')
    if not destino_admin:
        logging.error("EMAIL_DESTINO no está configurado. No se puede enviar la notificación al administrador.")
        return False

    asunto = f"📩 Nueva empresa publicada: {empresa_nombre}"
    cuerpo_html = f"""
    <html>
    <body>
        <p>¡Se ha publicado una nueva empresa en el portal Pyme Market!</p>
        <p><strong>Nombre de la Empresa:</strong> {empresa_nombre}</p>
        <p><strong>Email de Contacto del Anunciante:</strong> {email_usuario}</p>
        <p>Puedes ver los detalles de la empresa en el siguiente enlace:</p>
        <p><a href="{request.url_root}detalle/{empresa_id}">Ver Empresa</a></p>
        <p>Saludos,</p>
        <p>El equipo de Pyme Market</p>
    </body>
    </html>
    """
    cuerpo_texto = f"""
    ¡Se ha publicado una nueva empresa en el portal Pyme Market!

    Nombre de la Empresa: {empresa_nombre}
    Email de Contacto del Anunciante: {email_usuario}
    Puedes ver los detalles de la empresa en el siguiente enlace:
    {request.url_root}detalle/{empresa_id}

    Saludos,
    El equipo de Pyme Market
    """
    return enviar_correo_smtp_externo(destino_admin, asunto, cuerpo_html, cuerpo_texto=cuerpo_texto)

# Función para enviar un correo electrónico de interés al anunciante
def enviar_email_interes_anunciante(empresa_id, email_anunciante, nombre_interesado, email_interesado, telefono_interesado, mensaje_interes):
    asunto = f"✉️ Interés en tu anuncio con referencia: {empresa_id} en Pyme Market"
    cuerpo_html = f"""
    <html>
    <body>
        <p>Hola,</p>
        <p>Un posible comprador está interesado en tu anuncio con referencia "<strong>{empresa_id}</strong>" en Pyme Market.</p>
        
        <p>Estos son los datos del interesado:</p>
        <ul>
            <li><strong>Nombre:</strong> {nombre_interesado}</li>
            <li><strong>Email:</strong> {email_interesado}</li>
            <li><strong>Teléfono:</strong> {telefono_interesado if telefono_interesado else 'No proporcionado'}</li>
        </ul>

        <p>Este es el mensaje que te ha enviado:</p>
        <div style="border: 1px solid #eee; padding: 10px; margin: 15px 0; background-color: #f9f9f9;">
            <em>{mensaje_interes}</em>
        </div>

        <p>Te recomendamos responder a esta persona directamente utilizando los datos de contacto proporcionados.</p>

        <p>Gracias por confiar en Pyme Market.</p>
    </body>
    </html>
    """
    cuerpo_texto = f"""
Hola,

Un posible comprador está interesado en tu anuncio con referencia "{empresa_id}" en Pyme Market.

Estos son los datos del interesado:
Nombre: {nombre_interesado}
Email: {email_interesado}
Teléfono: {telefono_interesado if telefono_interesado else 'No proporcionado'}

Este es el mensaje que te ha enviado:
---
{mensaje_interes}
---

Te recomendamos responder a esta persona directamente utilizando los datos de contacto proporcionados.

Gracias por confiar en Pyme Market.
"""
    return enviar_correo_smtp_externo(email_anunciante, asunto, cuerpo_html, cuerpo_texto=cuerpo_texto)

# =====================================================================================
# FIN DE LAS FUNCIONES DE ENVÍO DE CORREO
# =====================================================================================


# Función interna para formatear números manualmente si locale falla
def _format_manual_euro(value, decimals=0):
    if value is None:
        return ""
    try:
        val_str = f"{float(value):,.{decimals}f}"
        val_str = val_str.replace(",", "TEMP_COMMA_PLACEHOLDER")
        val_str = val_str.replace(".", ",")
        val_str = val_str.replace("TEMP_COMMA_PLACEHOLDER", ".")
        return val_str
    except (ValueError, TypeError):
        return str(value)

# Filtro de Jinja2 para formato de números europeos (utiliza locale o manual)
@app.template_filter('euro_format') # Registrar como filtro de Jinja
def format_euro_number(value, decimals=0):
    if value is None:
        return ""
    if locale_set_successfully:
        try:
            return locale.format_string(f"%.{decimals}f", float(value), grouping=True)
        except (ValueError, TypeError):
            return _format_manual_euro(value, decimals)
    else:
        return _format_manual_euro(value, decimals)


# --- RUTAS DE LA APLICACIÓN ---

# Ruta principal de la aplicación: muestra el listado de empresas
@app.route('/', methods=['GET'])
def index():
    # Obtiene parámetros de filtro de la URL
    provincia = request.args.get('provincia')
    pais = request.args.get('pais', 'España')
    actividad = request.args.get('actividad')
    sector = request.args.get('sector')
    min_fact = request.args.get('min_facturacion', 0, type=float)
    max_fact = request.args.get('max_facturacion', 1e12, type=float)
    max_precio = request.args.get('max_precio', 1e12, type=float)

    conn = None
    empresas = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Construcción dinámica de la consulta SQL para filtrar empresas
        query = "SELECT * FROM empresas WHERE facturacion BETWEEN %s AND %s AND precio_venta <= %s"
        params = [min_fact, max_fact, max_precio]

        if provincia:
            query += " AND ubicacion = %s"
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
        
        # Ordenar por ID descendente para ver las últimas publicaciones primero
        query += " ORDER BY id DESC"

        cur.execute(query, tuple(params))
        empresas = cur.fetchall()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al cargar empresas para el index: {e}", exc_info=True)
        flash("Hubo un problema al cargar los anuncios. Por favor, inténtalo de nuevo.", "danger")
    finally:
        if conn:
            conn.close()
    
    return render_template('index.html', empresas=empresas,
                           actividades=get_all_actividades(), # Devuelve las claves de ACTIVIDADES_Y_SECTORES
                           sectores=[], # Se llenarán dinámicamente con JS
                           actividades_dict=get_actividades_sectores_dict(), # Para JS
                           provincias=get_all_provincias(), # Para el desplegable de provincias
                           request_args=request.args # Pasa los argumentos de la solicitud para mantener los filtros seleccionados
                           )


# Ruta para la página de detalle de una empresa
@app.route('/detalle/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = None
    empresa = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al obtener detalle de empresa {empresa_id}: {e}", exc_info=True)
        flash('Hubo un problema al cargar los detalles del anuncio.', 'danger')
        return redirect(url_for('index'))
    finally:
        if conn:
            conn.close()

    if not empresa:
        flash('Empresa no encontrada.', 'danger') # Cambiado a danger para consistencia
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Procesa el formulario de interés (desde detalle.html)
        nombre_interesado = request.form.get('nombre') # Usar .get() para evitar KeyError
        email_interesado = request.form.get('email')
        telefono_interesado = request.form.get('telefono')
        mensaje_interes = request.form.get('mensaje')

        # Validación básica para el formulario de interés
        if not nombre_interesado or not email_interesado or not mensaje_interes:
            flash('Por favor, completa todos los campos obligatorios del formulario de contacto.', 'danger')
            return render_template('detalle.html', empresa=empresa) # Vuelve a renderizar la página con el error

        # Envía el correo al anunciante
        if enviar_email_interes_anunciante(
            empresa['id'],
            empresa['email_contacto'],
            nombre_interesado,
            email_interesado,
            telefono_interesado,
            mensaje_interes
        ):
            flash('Tu mensaje ha sido enviado al anunciante con éxito.', 'success')
        else:
            flash('Hubo un error al enviar tu mensaje al anunciante. Por favor, inténtalo de nuevo más tarde.', 'danger')
        
        return redirect(url_for('detalle', empresa_id=empresa_id))

    return render_template('detalle.html', empresa=empresa)


# Ruta para publicar una nueva empresa
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        # Obtiene datos del formulario
        datos_formulario = {
            'nombre': request.form.get('nombre'),
            'email_contacto': request.form.get('email_contacto'),
            'actividad': request.form.get('actividad'),
            'sector': request.form.get('sector'),
            'pais': request.form.get('pais'),
            'ubicacion': request.form.get('ubicacion'),
            'tipo_negocio': request.form.get('tipo_negocio'),
            'descripcion': request.form.get('descripcion'),
            'local_propiedad': request.form.get('local_propiedad'),
            'facturacion': request.form.get('facturacion'), # Se validará y convertirá a float
            'numero_empleados': request.form.get('numero_empleados'), # Se validará y convertirá a int
            'resultado_antes_impuestos': request.form.get('resultado_antes_impuestos'), # Se validará y convertirá a float
            'deuda': request.form.get('deuda'), # Se validará y convertirá a float
            'precio_venta': request.form.get('precio_venta') # Se validará y convertirá a float
        }

        # --- Validación y conversión de campos numéricos ---
        errores = []
        for field in ['nombre', 'email_contacto', 'actividad', 'sector', 'pais', 'ubicacion', 'tipo_negocio', 'descripcion', 'precio_venta']:
            if not datos_formulario.get(field):
                errores.append(f"El campo '{field.replace('_', ' ').capitalize()}' es obligatorio.")

        try:
            datos_formulario['facturacion'] = float(datos_formulario['facturacion']) if datos_formulario['facturacion'] else None
        except (ValueError, TypeError):
            errores.append("Facturación debe ser un número válido.")
        
        try:
            datos_formulario['numero_empleados'] = int(datos_formulario['numero_empleados']) if datos_formulario['numero_empleados'] else None
        except (ValueError, TypeError):
            errores.append("Número de empleados debe ser un número entero válido.")
            
        try:
            datos_formulario['resultado_antes_impuestos'] = float(datos_formulario['resultado_antes_impuestos']) if datos_formulario['resultado_antes_impuestos'] else None
        except (ValueError, TypeError):
            errores.append("Resultado antes de impuestos debe ser un número válido.")
            
        try:
            datos_formulario['deuda'] = float(datos_formulario['deuda']) if datos_formulario['deuda'] else None
        except (ValueError, TypeError):
            errores.append("Deuda debe ser un número válido.")
            
        try:
            datos_formulario['precio_venta'] = float(datos_formulario['precio_venta']) if datos_formulario['precio_venta'] else None
        except (ValueError, TypeError):
            errores.append("Precio de venta debe ser un número válido.")
        
        if not '@' in datos_formulario.get('email_contacto', ''):
            errores.append("El email de contacto no es válido.")

        # Manejo de la subida de imagen a Google Cloud Storage
        imagen_file = request.files.get('imagen')
        imagen_url = None
        imagen_filename_gcs = None

        if imagen_file and imagen_file.filename and allowed_file(imagen_file.filename):
            if storage_client and CLOUD_STORAGE_BUCKET:
                imagen_url, imagen_filename_gcs = upload_to_gcs(imagen_file, imagen_file.filename, imagen_file.content_type)
                if not imagen_url:
                    errores.append('Error al subir la imagen a la nube.')
            else:
                errores.append('La configuración del almacenamiento en la nube no es correcta. Contacta al administrador.')
        
        datos_formulario['imagen_url'] = imagen_url # Añadir la URL al diccionario de datos a guardar

        if not errores:
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()

                # CRUCIAL: Modificar el INSERT para incluir las nuevas columnas token_edicion y token_expiracion
                # Y el campo imagen_filename_gcs
                cur.execute(
                    """
                    INSERT INTO empresas (
                        nombre, email_contacto, actividad, sector, pais, ubicacion,
                        tipo_negocio, descripcion, local_propiedad, facturacion,
                        numero_empleados, resultado_antes_impuestos, deuda, precio_venta,
                        imagen_url, imagen_filename_gcs, token_edicion, token_expiracion
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        datos_formulario['nombre'], datos_formulario['email_contacto'], datos_formulario['actividad'],
                        datos_formulario['sector'], datos_formulario['pais'], datos_formulario['ubicacion'],
                        datos_formulario['tipo_negocio'], datos_formulario['descripcion'], datos_formulario['local_propiedad'],
                        datos_formulario['facturacion'], datos_formulario['numero_empleados'],
                        datos_formulario['resultado_antes_impuestos'], datos_formulario['deuda'],
                        datos_formulario['precio_venta'], datos_formulario['imagen_url'], imagen_filename_gcs, # Aquí se usa la variable real
                        str(uuid.uuid4()), # Genera el token_edicion
                        datetime.utcnow() + timedelta(days=7) # Genera la fecha de expiración (7 días desde ahora)
                    )
                )
                empresa_id = cur.fetchone()[0] # Obtiene el ID de la empresa recién insertada
                conn.commit()

                # Envía la notificación por correo al administrador
                if enviar_email_notificacion_admin(datos_formulario['nombre'], datos_formulario['email_contacto'], empresa_id):
                    flash('Empresa publicada con éxito.', 'success')
                else:
                    flash('Empresa publicada, pero hubo un error al enviar la notificación por correo al administrador. Revisa logs.', 'warning')
                
                # Opcional: Enviar un correo de confirmación al propio anunciante CON ENLACES DE EDICIÓN/BORRADO
                token_para_email = get_empresa_by_id(empresa_id)['token_edicion'] # Recupera el token para el email
                asunto_anunciante = f"¡Tu empresa '{datos_formulario['nombre']}' ha sido publicada en Pyme Market!"
                
                enlace_detalle = url_for('detalle', empresa_id=empresa_id, _external=True)
                # Utiliza el token generado para los enlaces de edición/borrado
                enlace_editar_anunciante = url_for('editar_anuncio_anunciante', empresa_id=empresa_id, token_edicion=token_para_email, _external=True)
                enlace_borrar_anunciante = url_for('borrar_anuncio_anunciante', empresa_id=empresa_id, token_edicion=token_para_email, _external=True)

                cuerpo_html_anunciante = f"""
                <html>
                <body>
                    <p>Estimado/a anunciante,</p>
                    <p>Nos complace informarte que tu empresa '<strong>{datos_formulario['nombre']}</strong>' ha sido publicada en Pyme Market.</p>
                    <p>Puedes ver tu anuncio aquí: <a href="{enlace_detalle}">Ver tu Anuncio</a></p>
                    <p>Para <strong>editar</strong> tu anuncio: <a href="{enlace_editar_anunciante}">Haz clic aquí para editar</a></p>
                    <p>Si deseas <strong>eliminar</strong> tu anuncio: <a href="{enlace_borrar_anunciante}">Haz clic aquí para eliminar</a></p>
                    <p>Este enlace de edición/eliminación es válido por 7 días. Por favor, guárdalo de forma segura.</p>
                    <p>Por ser miembro de nuestra comunidad, te ofrecemos de manera gratuita revisar la póliza de seguros de tu negocio y local para conseguir ahorros. Responde a este e-mail adjuntando tu póliza y la revisaremos encantados y ¡Gratuitamente!</p>
                    <p>Gracias por confiar en nosotros.</p>
                    <p>El equipo de Pyme Market</p>
                </body>
                </html>
                """
                cuerpo_texto_anunciante = f"""
                Estimado/a anunciante,

                Nos complace informarte que tu empresa '{datos_formulario['nombre']}' ha sido publicada en Pyme Market.
                Puedes ver tu anuncio aquí: {enlace_detalle}

                Para editar tu anuncio: {enlace_editar_anunciante}
                Si deseas eliminar tu anuncio: {enlace_borrar_anunciante}

                Este enlace de edición/eliminación es válido por 7 días. Por favor, guárdalo de forma segura.

                Gracias por confiar en nosotros.
                El equipo de Pyme Market
                """
                if not enviar_correo_smtp_externo(datos_formulario['email_contacto'], asunto_anunciante, cuerpo_html_anunciante, cuerpo_texto=cuerpo_texto_anunciante):
                    logging.warning(f"No se pudo enviar el correo de confirmación al anunciante {datos_formulario['email_contacto']}.")

                return redirect(url_for('index'))
            except psycopg2.Error as e:
                if conn:
                    conn.rollback()
                logging.error(f"Error al insertar la empresa en la base de datos: {e}", exc_info=True)
                flash('Error al guardar la empresa en la base de datos. Por favor, inténtalo de nuevo.', 'danger')
                if imagen_filename_gcs:
                    delete_from_gcs(imagen_filename_gcs) # Intenta eliminar la imagen si la subida a DB falló
                return render_template('vender_empresa.html', actividades=get_all_actividades(), actividades_dict=get_actividades_sectores_dict(), provincias=get_all_provincias(), errores=errores, **datos_formulario)
            finally:
                if conn:
                    cur.close()
                    conn.close()
        else: # Si hay errores de validación (errores no vacíos)
            return render_template('vender_empresa.html', actividades=get_all_actividades(), actividades_dict=get_actividades_sectores_dict(), provincias=get_all_provincias(), errores=errores, **datos_formulario)
            
    return render_template('vender_empresa.html', actividades=get_all_actividades(), actividades_dict=get_actividades_sectores_dict(), provincias=get_all_provincias())


# --- NUEVAS RUTAS PARA EL ANUNCIANTE (EDITAR/BORRAR CON TOKEN) ---
@app.route('/editar-anuncio/<int:empresa_id>/<string:token_edicion>', methods=['GET', 'POST'])
def editar_anuncio_anunciante(empresa_id, token_edicion):
    empresa_data = get_empresa_by_id(empresa_id)

    if not empresa_data:
        flash('Anuncio no encontrado o ya no está disponible.', 'danger')
        return redirect(url_for('index'))

    empresa = dict(empresa_data) # Convierte el DictRow a un diccionario estándar

    # 1. Verificar el token
    if empresa.get('token_edicion') != token_edicion:
        flash('El enlace de edición no es válido o es incorrecto.', 'danger')
        return redirect(url_for('index'))

    # 2. Verificar la expiración del token
    if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow():
        flash('El enlace de edición ha expirado. Por favor, contacta con nosotros si necesitas actualizar tu anuncio.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Captura los datos actualizados del formulario
        datos_actualizados = {
            'nombre': request.form.get('nombre'),
            'descripcion': request.form.get('descripcion'),
            'tipo_negocio': request.form.get('tipo_negocio'),
            'actividad': request.form.get('actividad'),
            'sector': request.form.get('sector'),
            'ubicacion': request.form.get('ubicacion'),
            'pais': request.form.get('pais'),
            'facturacion': float(request.form.get('facturacion')) if request.form.get('facturacion') else None,
            'numero_empleados': int(request.form.get('numero_empleados')) if request.form.get('numero_empleados') else None,
            'resultado_antes_impuestos': float(request.form.get('resultado_antes_impuestos')) if request.form.get('resultado_antes_impuestos') else None,
            'deuda': float(request.form.get('deuda')) if request.form.get('deuda') else None,
            'precio_venta': float(request.form.get('precio_venta')) if request.form.get('precio_venta') else None,
            # NOTA: No permitir cambiar email_contacto o token_edicion/expiracion directamente desde aquí
            # Si se permiten cambios de imagen, la lógica de GCS iría aquí antes de actualizar 'imagen_url' y 'imagen_filename_gcs'
        }

        try:
            update_empresa_details_in_db(empresa_id, datos_actualizados)

            # Opcional: Regenerar el token después de una edición exitosa (invalida el enlace viejo)
            # nuevo_token = str(uuid.uuid4())
            # nueva_expiracion = datetime.utcnow() + timedelta(days=7)
            # update_empresa_token_in_db(empresa_id, nuevo_token, nueva_expiracion)

            flash('Tu anuncio ha sido actualizado con éxito.', 'success')
            return redirect(url_for('detalle', empresa_id=empresa_id))
        except Exception as e:
            flash(f'Error al actualizar tu anuncio: {e}. Por favor, inténtalo de nuevo.', 'danger')
            app.logger.error(f"Error en ruta /editar-anuncio para {empresa_id}: {e}", exc_info=True)
            return render_template('editar_anuncio_anunciante.html',
                                   empresa=empresa, # Conserva los datos originales en caso de fallo
                                   actividades=get_all_actividades(),
                                   provincias=get_all_provincias(),
                                   actividades_dict=get_actividades_sectores_dict())

    # Si es GET, mostrar el formulario de edición precargado
    return render_template('editar_anuncio_anunciante.html',
                           empresa=empresa,
                           actividades=get_all_actividades(),
                           provincias=get_all_provincias(),
                           actividades_dict=get_actividades_sectores_dict())


@app.route('/borrar-anuncio/<int:empresa_id>/<string:token_edicion>', methods=['GET', 'POST'])
def borrar_anuncio_anunciante(empresa_id, token_edicion):
    empresa_data = get_empresa_by_id(empresa_id)

    if not empresa_data:
        flash('Anuncio no encontrado o ya ha sido eliminado.', 'danger')
        return redirect(url_for('index'))

    empresa = dict(empresa_data) # Convierte el DictRow a un diccionario estándar

    # 1. Verificar el token
    if empresa.get('token_edicion') != token_edicion:
        flash('El enlace de eliminación no es válido o es incorrecto.', 'danger')
        return redirect(url_for('index'))

    # 2. Verificar la expiración del token
    if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow():
        flash('El enlace de eliminación ha expirado. Por favor, contacta con nosotros si necesitas eliminar tu anuncio.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        try:
            # Recupera el nombre del archivo en GCS para eliminarlo
            imagen_filename_gcs_to_delete = empresa.get('imagen_filename_gcs')
            
            delete_empresa_from_db(empresa_id)
            
            # Si la eliminación de la DB fue exitosa y hay imagen, elimínala de GCS
            if imagen_filename_gcs_to_delete:
                delete_from_gcs(imagen_filename_gcs_to_delete)

            flash('Tu anuncio ha sido eliminado con éxito.', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            flash(f'Error al eliminar tu anuncio: {e}. Por favor, inténtalo de nuevo.', 'danger')
            app.logger.error(f"Error en ruta /borrar-anuncio para {empresa_id}: {e}", exc_info=True)
            return render_template('confirmar_borrado_anunciante.html', empresa=empresa)
    else:
        # Si es GET, mostrar una página de confirmación antes de borrar
        return render_template('confirmar_borrado_anunciante.html', empresa=empresa)


# --- RUTA PARA LA ELIMINACIÓN DE UNA EMPRESA (ADMIN ONLY) ---
# Esta ruta se mantiene como la original del usuario, para uso de administrador
@app.route('/eliminar/<int:empresa_id>', methods=['POST'])
def eliminar(empresa_id):
    admin_token_param = request.args.get('token')
    if admin_token_param != ADMIN_TOKEN:
        flash('Acceso no autorizado para eliminar empresas.', 'danger')
        return redirect(url_for('index'))

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) # Usar DictCursor para obtener el filename

        # Primero, recupera el nombre del archivo en GCS si existe
        cur.execute("SELECT imagen_filename_gcs FROM empresas WHERE id = %s", (empresa_id,))
        result = cur.fetchone()
        imagen_filename_gcs_to_delete = result['imagen_filename_gcs'] if result else None

        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
        conn.commit()
        flash('Empresa eliminada con éxito (Panel de Admin).', 'success') # Mensaje para distinguir

        # Si había una imagen en GCS, intenta eliminarla también
        if imagen_filename_gcs_to_delete:
            delete_from_gcs(imagen_filename_gcs_to_delete)

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al eliminar la empresa de la base de datos (Panel Admin): {e}", exc_info=True)
        flash('Error al eliminar la empresa de la base de datos (Panel de Admin).', 'danger')
    finally:
        if conn:
            cur.close()
            conn.close()

    return redirect(url_for('admin_panel')) # Redirige al panel de admin


# --- Rutas estáticas para páginas como valoración, estudio de ahorros y contacto ---
@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

@app.route('/contacto', methods=['GET', 'POST'])
def contacto():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        mensaje = request.form.get('mensaje')
        telefono = request.form.get('telefono', 'No proporcionado')

        correo_recepcion = os.environ.get('EMAIL_DESTINO')
        if not correo_recepcion:
            logging.error("EMAIL_DESTINO no está configurado para el formulario de contacto. No se puede enviar el mensaje.")
            flash("Error en la configuración del correo de contacto. Por favor, inténtelo de nuevo más tarde.", "danger")
            return redirect(url_for('contacto'))

        asunto_cliente = f"Mensaje de {nombre} desde el formulario de contacto de Pyme Market"
        cuerpo_html_cliente = f"""
        <html>
        <body>
            <p><strong>De:</strong> {nombre} &lt;{email}&gt;</p>
            <p><strong>Mensaje:</strong></p>
            <p>{mensaje}</p>
            <p>Teléfono: {telefono}</p>
        </body>
        </html>
        """
        cuerpo_texto_cliente = f"""
        De: {nombre} <{email}>
        Mensaje:
        {mensaje}
        Teléfono: {telefono}
        """
        
        if enviar_correo_smtp_externo(correo_recepcion, asunto_cliente, cuerpo_html_cliente, cuerpo_texto=cuerpo_texto_cliente):
            flash("Tu mensaje ha sido enviado con éxito.", "success")
        else:
            flash("Hubo un error al enviar tu mensaje. Por favor, inténtalo de nuevo más tarde.", "danger")
        
        return redirect(url_for('contacto'))
    return render_template('contacto.html')


# Ruta para las políticas de cookies y nota legal
@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')

@app.route('/nota-legal')
def nota_legal():
    return render_template('nota_legal.html')

# Ruta para el Panel de Administración (ejemplo)
@app.route('/admin-panel')
def admin_panel():
    # Aquí puedes añadir tu propia lógica de autenticación para el administrador
    # Por ejemplo: if request.args.get('token') != ADMIN_TOKEN: ...
    conn = None
    empresas = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas ORDER BY id DESC") # Obtiene todas las empresas
        empresas = cur.fetchall()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al cargar empresas para el panel de administración: {e}", exc_info=True)
        flash("Hubo un problema al cargar los anuncios en el panel de administración.", "danger")
    finally:
        if conn:
            conn.close()
    return render_template('admin_panel.html', empresas=empresas)


# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Establece debug=False para producción por seguridad.
    # Si usas Gunicorn u otro servidor WSGI, esta línea no se ejecuta en producción.
    app.run(debug=False, host='0.0.0.0', port=port)
