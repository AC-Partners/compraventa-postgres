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
import uuid # Para generar nombres de archivo únicos en GCS y tokens
from datetime import timedelta # Necesario para generar URLs firmadas temporales

# IMPORTACIONES AÑADIDAS PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

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
        print("Error: Cliente de GCS o nombre de bucket no configurado para la subida.")
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
        print(f"Error al subir el archivo {filename} a GCS: {e}")
        return None, None # Retorna None si la subida falla

# Función para eliminar un archivo de Google Cloud Storage
def delete_from_gcs(filename_in_gcs):
    """
    Elimina un archivo del bucket de Google Cloud Storage.
    Recibe el nombre único del archivo tal como está en GCS.
    """
    if not storage_client or not CLOUD_STORAGE_BUCKET or not filename_in_gcs:
        print("Advertencia: No se pudo eliminar el archivo de GCS. Cliente/Bucket no configurado o nombre de archivo vacío.")
        return False

    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename_in_gcs)

    try:
        # Verifica si el blob existe antes de intentar eliminarlo
        if blob.exists():
            blob.delete()
            print(f"Archivo '{filename_in_gcs}' eliminado de GCS correctamente.")
            return True
        else:
            print(f"Advertencia: El archivo '{filename_in_gcs}' no existe en GCS. No se realizó la eliminación.")
            return False
    except Exception as e:
        print(f"Error al eliminar el archivo '{filename_in_gcs}' de GCS: {e}")
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

# Función para enviar un correo electrónico de notificación de nueva empresa (al admin)
def enviar_email_notificacion_admin(empresa_nombre, email_usuario):
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
        print(f"Correo de notificación de admin enviado para {empresa_nombre}")
    except smtplib.SMTPException as e:
        print(f"Error al enviar email de notificación de admin: {e}")
    except Exception as e:
        print(f"Error inesperado al enviar email de notificación de admin: {e}")

# Función para enviar un correo electrónico de interés al anunciante (MODIFICADA)
def enviar_email_interes_anunciante(empresa_id, email_anunciante, nombre_interesado, email_interesado, telefono_interesado, mensaje_interes): # Recibe nuevos campos
    msg = EmailMessage()
    # Asunto ahora usa el ID de referencia del anuncio
    msg['Subject'] = f"✉️ Interés en tu anuncio con referencia: {empresa_id} desde AC Partners"
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = email_anunciante
    
    email_body = f"""
Hola,

Un posible comprador está interesado en tu anuncio con referencia "{empresa_id}" en AC Partners.

Estos son los datos del interesado:
Nombre: {nombre_interesado}
Email: {email_interesado}
Teléfono: {telefono_interesado if telefono_interesado else 'No proporcionado'}

Este es el mensaje que te ha enviado:
---
{mensaje_interes}
---

Te recomendamos responder a esta persona directamente utilizando los datos de contacto proporcionados.

Gracias por confiar en AC Partners.
"""
    msg.set_content(email_body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"Correo de interés enviado al anunciante {email_anunciante} para anuncio ID: {empresa_id}")
    except smtplib.SMTPException as e:
        print(f"Error al enviar email de interés al anunciante: {e}")
    except Exception as e:
        print(f"Error inesperado al enviar email de interés al anunciante: {e}")

# NUEVA FUNCIÓN: Para enviar correo de confirmación al anunciante con enlaces de gestión
def enviar_email_confirmacion_anunciante(empresa_id, email_anunciante, anunciante_token):
    edit_url = url_for('editar_anuncio_anunciante', empresa_id=empresa_id, token=anunciante_token, _external=True)
    delete_url = url_for('eliminar_anuncio_anunciante', empresa_id=empresa_id, token=anunciante_token, _external=True)

    msg = EmailMessage()
    msg['Subject'] = f"✅ Anuncio Publicado y Enlaces de Gestión - Ref: {empresa_id}"
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = email_anunciante
    msg.set_content(f"""
¡Hola!

Tu anuncio con referencia **{empresa_id}** ha sido publicado correctamente en AC Partners.

Puedes gestionar tu anuncio a través de los siguientes enlaces (guárdalos bien, son privados para tu anuncio):

* **Modificar Anuncio:** {edit_url}
* **Anular Anuncio:** {delete_url}

Te recomendamos no compartir estos enlaces, ya que permiten la gestión directa de tu anuncio.

Gracias por usar AC Partners.
""")
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"Correo de confirmación con enlaces de gestión enviado a {email_anunciante} para anuncio ID: {empresa_id}")
    except smtplib.SMTPException as e:
        print(f"Error al enviar email de confirmación al anunciante: {e}")
    except Exception as e:
        print(f"Error inesperado al enviar email de confirmación al anunciante: {e}")


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
        # Se asume que estos campos son obligatorios
