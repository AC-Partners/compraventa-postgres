# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
# from email.message import EmailMessage # <--- NO SE USA DIRECTAMENTE ESTA CLASE PARA EL NUEVO MÉTODO SMTP
import smtplib
import socket
import json # Importa el módulo json para cargar las actividades y sectores
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos en GCS
from datetime import timedelta # Necesario para generar URLs firmadas temporales

# IMPORTACIONES AÑADIDAS PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

# IMPORTACIONES AÑADIDAS/MODIFICADAS PARA EL NUEVO SISTEMA DE CORREO SMTP
from email.mime.text import MIMEText # Para crear mensajes HTML/texto plano
from email.header import Header # Para manejar encabezados con caracteres especiales (UTF-8)
import logging # Para un mejor manejo de logs en el envío de correos

# Configura el logger global para ver mensajes en los logs de Render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Obtener el nombre del bucket de GCS de las variables de entorno de Render.com
# Asegúrate de configurar la variable de entorno CLOUD_STORAGE_BUCKET en Render con el nombre de tu bucket.
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Inicializar el cliente de Cloud Storage
# Intentará cargar las credenciales desde la variable de entorno GCP_SERVICE_ACCOUNT_KEY_JSON.
# Esta variable debe contener el JSON completo de tu clave de cuenta de servicio en una sola línea.
gcs_key_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
if gcs_key_json:
    try:
        credentials_info = json.loads(gcs_key_json)
        storage_client = storage.Client.from_service_account_info(credentials_info)
        print("Cliente de Google Cloud Storage inicializado desde la variable de entorno JSON.")
    except json.JSONDecodeError as e:
        print(f"Error al decodificar JSON de credenciales de GCP: {e}")
        print("Asegúrate de que GCP_SERVICE_ACCOUNT_KEY_JSON contiene JSON válido y sin saltos de línea inesperados.")
        # En un entorno de producción real, aquí deberías considerar levantar una excepción o salir.
        storage_client = None # O asigna None para indicar que no se pudo inicializar
else:
    # Si la variable GCP_SERVICE_ACCOUNT_KEY_JSON no está configurada,
    # el cliente intentará buscar credenciales por defecto (ej. GOOGLE_APPLICATION_CREDENTIALS, gcloud CLI, etc.).
    # Esto es útil para desarrollo local, pero en Render deberías usar GCP_SERVICE_ACCOUNT_KEY_JSON.
    storage_client = storage.Client()
    print("Advertencia: GCP_SERVICE_ACCOUNT_KEY_JSON no encontrada. El cliente de GCS intentará credenciales por defecto.")
    print("Para Render, asegúrate de configurar GCP_SERVICE_ACCOUNT_KEY_JSON y CLOUD_STORAGE_BUCKET.")

# Función para subir un archivo a Google Cloud Storage
def upload_to_gcs(file_obj, filename, content_type):
    """
    Sube un objeto de archivo (FileStorage) a Google Cloud Storage.
    Genera un nombre de archivo único utilizando UUID para evitar colisiones.
    Retorna la URL firmada del archivo subido.
    """
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logging.error("Error: Cliente de GCS o nombre de bucket no configurado para la subida.")
        return None, None # Retorna None para URL y nombre si hay un error de configuración

    # Genera un nombre de archivo único para el blob en GCS
    # Esto evita colisiones si dos usuarios suben un archivo con el mismo nombre
    unique_filename = str(uuid.uuid4()) + '_' + secure_filename(filename)

    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(unique_filename)

        # Sube el archivo. file_obj.stream es un objeto tipo archivo que blob.upload_from_file puede leer.
        blob.upload_from_file(file_obj.stream, content_type=content_type)

        # Genera una URL firmada temporal para acceder al objeto
        # La duración de la URL es de 7 días. Ajusta según tus necesidades.
        # Esto es seguro porque el bucket no tiene acceso público directo.
        signed_url = blob.generate_signed_url(expiration=timedelta(days=7))
        return signed_url, unique_filename # Retorna la URL y el nombre único usado en GCS
    except Exception as e:
        logging.error(f"Error al subir el archivo {filename} a GCS: {e}", exc_info=True)
        return None, None # Retorna None si la subida falla

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
        # Verifica si el blob existe antes de intentar eliminarlo
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

# Carpeta donde se guardarán las imágenes subidas (NO NECESARIA PARA GCS, pero la dejo si la usas para otra cosa)
# app.config['UPLOAD_FOLDER'] = 'static/uploads'
# Extensiones de archivo permitidas para las imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Carga de variables de entorno para la conexión a la base de datos y el envío de emails
DATABASE_URL = os.environ.get('DATABASE_URL')
# Las siguientes variables (EMAIL_ORIGEN, EMAIL_PASSWORD) se van a sustituir por las SMTP_
# Por eso, las comentamos/eliminamos para evitar confusiones y usar las nuevas:
# EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO') # ESTA SÍ SE MANTIENE para el destinatario de notificaciones
# EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

# Variables de entorno para la configuración SMTP del servicio de correo (Jimdo/srvr.com)
SMTP_SERVER = os.environ.get('SMTP_SERVER')
# Asegurarse de que SMTP_PORT es un entero o None si no está configurado
SMTP_PORT = int(os.environ.get('SMTP_PORT')) if os.environ.get('SMTP_PORT') else None
SMTP_USERNAME = os.environ.get('SMTP_USERNAME') # La cuenta pymemarket@acpartners.es
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')


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

# =====================================================================================
# FUNCIONES DE ENVÍO DE CORREO ACTUALIZADAS PARA USAR JIMDO/SRVR.COM VÍA SMTP
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

        # Crear el mensaje de correo
        msg = MIMEText(cuerpo_html, 'html', 'utf-8')
        if cuerpo_texto:
            # Añadir la versión de texto plano como alternativa para clientes de correo que no soportan HTML
            msg.add_alternative(cuerpo_texto, 'plain', 'utf-8')

        # Configurar los encabezados del correo (importante para que se muestre correctamente)
        msg['From'] = Header(f"{remitente_nombre} <{remitente_autenticacion}>", 'utf-8')
        msg['To'] = Header(destinatario, 'utf-8')
        msg['Subject'] = Header(asunto, 'utf-8')

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
# ANTES: usar 'enviar_email_notificacion_admin'
# AHORA: usa 'enviar_correo_smtp_externo'
def enviar_email_notificacion_admin(empresa_nombre, email_usuario, empresa_id):
    # Obtener el correo de destino para el admin desde la variable de entorno EMAIL_DESTINO
    destino_admin = os.environ.get('EMAIL_DESTINO')
    if not destino_admin:
        logging.error("EMAIL_DESTINO no está configurado. No se puede enviar la notificación al administrador.")
        return False

    asunto = f"📩 Nueva empresa publicada: {empresa_nombre}"
    # Se genera un cuerpo HTML y uno de texto plano para mayor compatibilidad
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

    # Llama a la nueva función de envío SMTP centralizada
    return enviar_correo_smtp_externo(destino_admin, asunto, cuerpo_html, cuerpo_texto=cuerpo_texto)

# Función para enviar un correo electrónico de interés al anunciante
# ANTES: usar 'enviar_email_interes_anunciante'
# AHORA: usa 'enviar_correo_smtp_externo'
def enviar_email_interes_anunciante(empresa_id, email_anunciante, nombre_interesado, email_interesado, telefono_interesado, mensaje_interes):
    asunto = f"✉️ Interés en tu anuncio con referencia: {empresa_id} en Pyme Market"
    
    # Construcción del cuerpo HTML del email
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
    
    # Construcción del cuerpo de texto plano del email (para clientes que no soportan HTML)
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

    # Llama a la nueva función de envío SMTP centralizada
    return enviar_correo_smtp_externo(email_anunciante, asunto, cuerpo_html, cuerpo_texto=cuerpo_texto)

# =====================================================================================
# FIN DE LAS FUNCIONES DE ENVÍO DE CORREO ACTUALIZADAS
# =====================================================================================


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
            # CAMBIO: Redirige a vender_empresa.html
            return redirect(url_for('publicar'))

        # Manejo de la subida de imagen a Google Cloud Storage
        imagen_file = request.files.get('imagen') # Usar .get() para evitar KeyError si el campo no está presente
        imagen_url = '' # Para almacenar la URL firmada de GCS
        imagen_filename_gcs = '' # Para almacenar el nombre único del archivo en GCS

        if imagen_file and allowed_file(imagen_file.filename):
            if storage_client and CLOUD_STORAGE_BUCKET: # Verificar que GCS está configurado
                # Llama a la función de subida a GCS
                imagen_url, imagen_filename_gcs = upload_to_gcs(imagen_file, imagen_file.filename, imagen_file.content_type)
                if not imagen_url:
                    flash('Error al subir la imagen a la nube. Inténtalo de nuevo.', 'error')
                    # CAMBIO: Redirige a vender_empresa.html
                    return redirect(url_for('publicar'))
            else:
                flash('La configuración del almacenamiento en la nube no es correcta. Contacta al administrador.', 'error')
                # CAMBIO: Redirige a vender_empresa.html
                return redirect(url_for('publicar'))

        conn = None # Inicializar conn a None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Inserta la nueva empresa en la base de datos
            # Asegúrate de que la tabla 'empresas' tiene las columnas adecuadas
            cur.execute(
                """
                INSERT INTO empresas (
                    nombre, email_contacto, actividad, sector, pais, ubicacion,
                    tipo_negocio, descripcion, local_propiedad, facturacion,
                    numero_empleados, resultado_antes_impuestos, deuda, precio_venta,
                    imagen_url, imagen_filename  -- <<<<< ¡¡CAMBIO AQUÍ: imagen_filename_gcs A imagen_filename !!
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    nombre, email_contacto, actividad, sector, pais, ubicacion,
                    tipo_negocio, descripcion, local_propiedad, facturacion,
                    numero_empleados, resultado_antes_impuestos, deuda, precio_venta,
                    imagen_url, imagen_filename_gcs # <<<< NOTA: La variable Python sigue siendo 'imagen_filename_gcs', que es correcta para el valor
                )
            )
            empresa_id = cur.fetchone()['id'] # Obtiene el ID de la empresa recién insertada
            conn.commit() # Confirma la transacción

            # Envía la notificación por correo al administrador
            # Se usa la función 'enviar_email_notificacion_admin' que ahora llama a 'enviar_correo_smtp_externo'
            if enviar_email_notificacion_admin(nombre, email_contacto, empresa_id):
                flash('Empresa publicada con éxito y notificación enviada al administrador.', 'success')
            else:
                flash('Empresa publicada, pero hubo un error al enviar la notificación por correo al administrador. Revisa logs.', 'warning')
            
            # Opcional: Enviar un correo de confirmación al propio anunciante
            asunto_anunciante = f"¡Tu empresa '{nombre}' ha sido publicada en Pyme Market!"
            cuerpo_html_anunciante = f"""
            <html>
            <body>
                <p>Estimado/a {email_contacto},</p>
                <p>Nos complace informarte que tu empresa '<strong>{nombre}</strong>' ha sido publicada en Pyme Market.</p>
                <p>Puedes ver tu anuncio aquí: <a href="{request.url_root}detalle/{empresa_id}">Ver tu Anuncio</a></p>
                <p>Gracias por confiar en nosotros.</p>
                <p>El equipo de Pyme Market</p>
            </body>
            </html>
            """
            cuerpo_texto_anunciante = f"""
            Estimado/a {email_contacto},

            Nos complace informarte que tu empresa '{nombre}' ha sido publicada en Pyme Market.
            Puedes ver tu anuncio aquí: {request.url_root}detalle/{empresa_id}

            Gracias por confiar en nosotros.
            El equipo de Pyme Market
            """
            # Se usa la función 'enviar_correo_smtp_externo' directamente para el anunciante
            if not enviar_correo_smtp_externo(email_contacto, asunto_anunciante, cuerpo_html_anunciante, cuerpo_texto=cuerpo_texto_anunciante):
                logging.warning(f"No se pudo enviar el correo de confirmación al anunciante {email_contacto}.")


            return redirect(url_for('index')) # O a una página de confirmación de éxito
        except psycopg2.Error as e:
            if conn: # Asegurarse de que conn no es None antes de rollback
                conn.rollback() # Revierte la transacción en caso de error en la DB
            logging.error(f"Error al insertar la empresa en la base de datos: {e}", exc_info=True)
            flash('Error al guardar la empresa en la base de datos. Por favor, inténtalo de nuevo.', 'error')
            # Si hubo una imagen subida, intenta eliminarla para limpiar
            if imagen_filename_gcs:
                delete_from_gcs(imagen_filename_gcs)
            # CAMBIO: Redirige a vender_empresa.html
            return redirect(url_for('publicar'))
        finally:
            if conn:
                cur.close()
                conn.close()

    # Para solicitudes GET, simplemente renderiza el formulario
    # CAMBIO: Vuelve a usar vender_empresa.html
    return render_template('vender_empresa.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()), actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


# Ruta para la página de detalle de una empresa
@app.route('/detalle/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if not empresa:
        flash('Empresa no encontrada.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Procesa el formulario de interés
        nombre_interesado = request.form['nombre']
        email_interesado = request.form['email']
        telefono_interesado = request.form.get('telefono') # 'get' por si no es obligatorio
        mensaje_interes = request.form['mensaje']

        # Envía el correo al anunciante
        # Se usa la función 'enviar_email_interes_anunciante' que ahora llama a 'enviar_correo_smtp_externo'
        if enviar_email_interes_anunciante(
            empresa['id'], # Usamos empresa['id'] directamente, ya que asumo es la referencia única
            empresa['email_contacto'],
            nombre_interesado,
            email_interesado,
            telefono_interesado,
            mensaje_interes
        ):
            flash('Tu mensaje ha sido enviado al anunciante con éxito.', 'success')
        else:
            flash('Hubo un error al enviar tu mensaje al anunciante. Por favor, inténtalo de nuevo más tarde.', 'danger')
        
        return redirect(url_for('detalle', empresa_id=empresa_id)) # Vuelve a la página de detalle

    return render_template('detalle.html', empresa=empresa)


# Ruta para la eliminación de una empresa (solo accesible con token de admin)
@app.route('/eliminar/<int:empresa_id>', methods=['POST'])
def eliminar(empresa_id):
    admin_token_param = request.args.get('token')
    if admin_token_param != ADMIN_TOKEN:
        flash('Acceso no autorizado para eliminar empresas.', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Primero, recupera el nombre del archivo en GCS si existe
    cur.execute("SELECT imagen_filename_gcs FROM empresas WHERE id = %s", (empresa_id,))
    result = cur.fetchone()
    imagen_filename_gcs_to_delete = result['imagen_filename_gcs'] if result else None

    try:
        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
        conn.commit()
        flash('Empresa eliminada con éxito.', 'success')
        
        # Si había una imagen en GCS, intenta eliminarla también
        if imagen_filename_gcs_to_delete:
            delete_from_gcs(imagen_filename_gcs_to_delete)

    except psycopg2.Error as e:
        conn.rollback()
        logging.error(f"Error al eliminar la empresa de la base de datos: {e}", exc_info=True)
        flash('Error al eliminar la empresa de la base de datos.', 'danger')
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('index'))

# Rutas estáticas para páginas como valoración, estudio de ahorros y contacto
@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

@app.route('/contacto', methods=['GET', 'POST'])
def contacto():
    if request.method == 'POST':
        nombre = request.form['nombre']
        email = request.form['email']
        mensaje = request.form['mensaje']

        # Dirección de correo donde quieres recibir los mensajes del formulario de contacto
        # Usamos EMAIL_DESTINO ya que es el correo del administrador
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
            <p>Teléfono: {request.form.get('telefono', 'No proporcionado')}</p>
        </body>
        </html>
        """
        cuerpo_texto_cliente = f"""
        De: {nombre} <{email}>
        Mensaje:
        {mensaje}
        Teléfono: {request.form.get('telefono', 'No proporcionado')}
        """
        
        # Llama a la función de envío SMTP centralizada
        if enviar_correo_smtp_externo(correo_recepcion, asunto_cliente, cuerpo_html_cliente, cuerpo_texto=cuerpo_texto_cliente):
            flash("Tu mensaje ha sido enviado con éxito.", "success")
        else:
            flash("Hubo un error al enviar tu mensaje. Por favor, inténtalo de nuevo más tarde.", "danger")
        
        return redirect(url_for('contacto'))
    return render_template('contacto.html')


# Ruta para las políticas de cookies y nota legal (ejemplo)
@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')

@app.route('/nota-legal')
def nota_legal():
    return render_template('nota_legal.html')


# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    # Obtiene el puerto del entorno o usa 5000 por defecto para desarrollo local
    # Render.com proporciona el puerto a través de la variable de entorno 'PORT'.
    # Si no está definida (ej. en desarrollo local), usa el 5000.
    port = int(os.environ.get('PORT', 5000))
    # Ejecuta la aplicación en todas las interfaces de red disponibles (0.0.0.0)
    # En un entorno de producción como Render, Gunicorn gestionará esto.
    # Este bloque es principalmente para pruebas y desarrollo local.
    # Establece debug=False para producción.
    app.run(debug=False, host='0.0.0.0', port=port)
