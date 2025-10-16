# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory, g
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
# ELIMINADAS: from email.message import EmailMessage, import smtplib, import socket
import requests # AÑADIDA: Necesaria para hacer llamadas a la API de Mailgun
import json # Importa el módulo json para cargar las actividades y sectores
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos en GCS y tokens
from datetime import timedelta, datetime # Necesario para generar URLs firmadas temporales y manejar fechas
from decimal import Decimal, InvalidOperation
from functools import wraps # Necesario para el decorador admin_required
from slugify import slugify # Necesario para generar slugs amigables

# IMPORTACIONES PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# --- PROCESADOR DE CONTEXTO GLOBAL DE JINJA2 ---
# Esta función inyectará 'current_year' en todas las plantillas automáticamente.
@app.context_processor
def inject_global_variables():
    """Inyecta variables globales como el año actual en todas las plantillas."""
    return dict(current_year=datetime.now().year)

# ==============================================================================
# --- CONFIGURACIÓN Y FUNCIÓN DE ENVÍO DE CORREO CON LA API DE MAILGUN ---
# ==============================================================================

# Las credenciales se leen de las Variables de Entorno de Render
MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
# Lee la región de Mailgun (opcional, usa 'us' como fallback)
MAILGUN_REGION = os.environ.get("MAILGUN_REGION", "us") 

# Definición de la URL base para la API REST (diferente si es región EU o US)
if MAILGUN_REGION.lower() == "eu":
    API_BASE_URL = "https://api.eu.mailgun.net/v3/"
else:
    API_BASE_URL = "https://api.mailgun.net/v3/"
    
MAILGUN_URL = API_BASE_URL + f"{MAILGUN_DOMAIN}/messages"

def send_email(to_email, subject, body, from_name="Contacto Pyme Market"):
    """
    [REEMPLAZO TOTAL DE SMTP] Envía un correo electrónico usando la API de Mailgun.
    """
    
    # Si las variables críticas no están, avisa y falla limpiamente
    if not MAILGUN_DOMAIN or not MAILGUN_API_KEY:
        print("WARNING Email: Las variables de entorno de Mailgun no están configuradas. No se puede enviar el correo.")
        return False

    # Define la dirección de origen usando el dominio verificado
    from_address = f"{from_name} <info@{MAILGUN_DOMAIN}>" 
    
    # Solicitud HTTP POST a la API de Mailgun
    try:
        response = requests.post(
            MAILGUN_URL,
            # Autenticación Básica
            auth=("api", MAILGUN_API_KEY), 
            data={
                "from": from_address,
                "to": to_email,
                "subject": subject,
                "text": body
            }
        )

        # Si el código de estado no es 2xx, lanza un error (e.g., 401, 400, 500)
        response.raise_for_status() 
        
        print(f"INFO Mailgun: Correo enviado a {to_email}. Status: {response.status_code}")
        return True

    except requests.exceptions.RequestException as e:
        # Esto captura errores de conexión, errores de Mailgun (4xx) o de servidor (5xx)
        print(f"ERROR Mailgun: Fallo al enviar el correo a través de la API. Error: {e}") 
        if e.response is not None:
             print(f"ERROR Mailgun Details: Status Code: {e.response.status_code}, Response: {e.response.text}")
        return False

# ==============================================================================
# --- FIN DE LA FUNCIÓN DE MAILGUN ---
# ==============================================================================


# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Obtener el nombre del bucket de GCS de las variables de entorno de Render.com
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Configuración de la imagen por defecto en GCS
app.config['DEFAULT_IMAGE_GCS_FILENAME'] = 'Pymemarket_logo.png'

# Inicializar el cliente de Cloud Storage
storage_client = None # Inicializar a None por defecto
try:
    if CLOUD_STORAGE_BUCKET and os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'):
        credentials_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
        try:
            credentials_dict = json.loads(credentials_json)
            storage_client = storage.Client.from_service_account_info(credentials_dict)
        except json.JSONDecodeError as jde:
            print(f"ERROR GCS Init: No se pudo parsear GCP_SERVICE_ACCOUNT_KEY_JSON. Error: {jde}")
            storage_client = None
        except Exception as e:
            print(f"ERROR GCS Init: Error inesperado al inicializar con from_service_account_info: {e}")
            storage_client = None
    elif CLOUD_STORAGE_BUCKET:
        # Si no hay credenciales de cuenta de servicio, intenta inicializar de forma predeterminada
        storage_client = storage.Client()
    else:
        pass

except Exception as e:
    storage_client = None
    print(f"ERROR GCS Init: Error general al inicializar Google Cloud Storage client: {e}")
    print("GCS functions will be skipped.")

# Funciones de utilidad para Google Cloud Storage

def upload_to_gcs(file_stream, filename):
    """Sube un archivo a Google Cloud Storage."""
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("ADVERTENCIA GCS Upload: Cliente de almacenamiento o nombre de bucket no configurado.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        file_stream.seek(0)
        blob.upload_from_file(file_stream)
        print(f"INFO GCS Upload: Archivo {filename} subido con éxito a GCS.")
        return filename
    except Exception as e:
        print(f"ERROR GCS Upload: Error al subir {filename} a GCS: {e}")
        return None

def get_public_image_url(filename):
    """
    Genera una URL pública directa para un archivo en GCS o un fallback local si CLOUD_STORAGE_BUCKET no está definido.
    """
    if not CLOUD_STORAGE_BUCKET:
        print("ADVERTENCIA GCS URL: Nombre de bucket de GCS no configurado. Usando fallback de URL estática local.")
        return url_for('static', filename=app.config['DEFAULT_IMAGE_GCS_FILENAME'])
    try:
        url = f"https://storage.googleapis.com/{CLOUD_STORAGE_BUCKET}/{filename}"
        return url
    except Exception as e:
        print(f"ERROR GCS URL: Error al generar URL pública para {filename}: {e}")
        return f"https://storage.googleapis.com/{CLOUD_STORAGE_BUCKET}/{app.config['DEFAULT_IMAGE_GCS_FILENAME']}"


def delete_from_gcs(filename):
    """Elimina un archivo de Google Cloud Storage."""
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("ADVERTENCIA GCS Delete: Cliente de almacenamiento o nombre de bucket no configurado.")
        return
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        if blob.exists():
            blob.delete()
            print(f"INFO GCS Delete: Archivo {filename} eliminado con éxito de GCS.")
        else:
            print(f"INFO GCS Delete: Archivo {filename} no encontrado en GCS. No se necesita eliminar.")
    except Exception as e:
        print(f"ERROR GCS Delete: Error al eliminar {filename} de GCS: {e}")

# -------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# -------------------------------------------------------------

@app.route('/robots.txt')
def robots_txt():
    # Asegúrate de que tu robots.txt esté en la carpeta 'static'
    return send_from_directory(app.static_folder, 'robots.txt')

# Configuración de la base de datos PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set.")
    try:
        conn = psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.DictCursor
        )
        return conn
    except Exception as e:
        print(f"ERROR DB: Error al conectar a la base de datos: {e}")
        raise

# Constantes para la aplicación (ACTUALIZADO A LA VERSIÓN CONOCIDA)
PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila",
    "Badajoz", "Barcelona", "Burgos", "Cáceres", "Cádiz", "Cantabria", "Castellón",
    "Ciudad Real", "Córdoba", "Cuenca", "Girona", "Granada", "Guadalajara",
    "Gipuzkoa", "Huelva", "Huesca", "Illes Balears", "Jaén", "León", "Lleida",
    "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense", "Palencia",
    "Las Palmas", "Pontevedra", "La Rioja", "Salamanca", "Santa Cruz de Tenerife",
    "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia",
    "Valladolid", "Bizkaia", "Zamora", "Zaragoza", "Ceuta", "Melilla"
]

ACTIVIDADES_Y_SECTORES = {
    "Tecnología y Software": ["Desarrollo de Software", "Consultoría IT", "E-commerce", "Ciberseguridad", "SaaS"],
    "Servicios Profesionales": ["Asesoría y Consultoría", "Marketing Digital", "Diseño Gráfico", "Recursos Humanos", "Servicios Legales"],
    "Hostelería y Restauración": ["Restaurantes", "Bares y Cafeterías", "Hoteles y Alojamientos", "Catering"],
    "Comercio al por Menor": ["Tiendas de Ropa", "Supermercados", "Electrónica", "Librerías", "Joyerías"],
    "Salud y Bienestar": ["Clínicas", "Fisioterapia y Masaje", "Gimnasios", "Centros de Estética", "Farmacias y Parafarmacias"],
    "Educación y Formación": ["Academias", "Formación Online", "Guarderías", "Centros de Idiomas"],
    "Industria y Fabricación": ["Metalurgia", "Textil", "Alimentaria", "Maquinaria", "Química"],
    "Construcción e Inmobiliaria": ["Promotoras", "Constructoras", "Agencias Inmobiliarias", "Reformas"],
    "Automoción": ["Talleres Mecánicos", "Concesionarios", "Venta de Recambios", "Autoescuelas"],
    "Transporte y Logística": ["Transporte de Mercancías", "Mensajería", "Logística de Almacenamiento"],
    "Agricultura y Ganadería": ["Explotaciones Agrícolas", "Explotaciones Ganaderas", "Agroindustria"],
    "Energía y Medio Ambiente": ["Energías Renovables", "Gestión de Residuos", "Eficiencia Energética"],
    "Turismo y Ocio": ["Agencias de Viajes", "Parques Temáticos", "Actividades de Aventura", "Ocio Nocturno"],
    "Belleza y Cuidado Personal": ["Peluquerías", "Salones de Belleza", "Barberías", "Spas"],
    "Deportes": ["Tiendas de Deportes", "Clubes Deportivos", "Instalaciones Deportivas"],
    "Alimentación y Bebidas": ["Panaderías y Pastelerías", "Fruterías", "Carnicerías", "Pescaderías", "Bodegas"],
    "Franquicias": ["Cualquier sector operado bajo modelo de franquicia"],
    "Otros": ["Otros sectores no especificados arriba"]
}

# Configuración para subida de imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Filtro personalizado para formato de moneda (euros)
@app.template_filter('euro_format')
def euro_format(value):
    if value is None:
        return "N/A"

    try:
        if not isinstance(value, Decimal):
            value = Decimal(str(value))

        is_integer_value = (value == value.to_integral_value())

        if is_integer_value:
            integer_part_str = str(int(value.to_integral_value()))
            decimal_part_str = ""
        else:
            value = value.quantize(Decimal('0.01'))
            s = str(value)
            if '.' in s:
                parts = s.split('.')
                integer_part_str = parts[0]
                decimal_part_str = parts[1]
            else: 
                integer_part_str = s
                decimal_part_str = "00"

        formatted_integer_part = []
        n_digits = len(integer_part_str)
        for i, digit in enumerate(integer_part_str):
            formatted_integer_part.append(digit)
            if (n_digits - (i + 1)) % 3 == 0 and (n_digits - (i + 1)) != 0:
                formatted_integer_part.append('.')
        
        formatted_integer_part_str = "".join(formatted_integer_part)

        if is_integer_value:
            return f"{formatted_integer_part_str} €"
        else:
            return f"{formatted_integer_part_str},{decimal_part_str} €"

    except (ValueError, TypeError, AttributeError, InvalidOperation) as e:
        print(f"ERROR EuroFormat: Error en euro_format para valor '{value}' (Tipo: {type(value)}): {e}")
        return "N/A"
    except Exception as e:
        print(f"ERROR EuroFormat: Error inesperado en euro_format para valor '{value}' (Tipo: {type(value)}): {e}")
        return "N/A"


# TOKEN DE ADMINISTRADOR
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

# Decorador para proteger las rutas de administración
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.args.get('admin_token')
        if admin_token != ADMIN_TOKEN:
            flash('Acceso no autorizado.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Rutas de la aplicación
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    actividad_filter = request.args.get('actividad')
    sector_filter = request.args.get('sector')
    provincia_filter = request.args.get('provincia')
    
    min_facturacion_filter = request.args.get('min_facturacion_slider')
    max_facturacion_filter = request.args.get('max_facturacion_slider')

    max_precio_filter = request.args.get('max_precio')

    query = "SELECT * FROM empresas WHERE active = TRUE"
    params = []

    if actividad_filter and actividad_filter != 'Todas las actividades':
        query += " AND actividad = %s"
        params.append(actividad_filter)
    if sector_filter and sector_filter != 'Todos los sectores':
        query += " AND sector = %s"
        params.append(sector_filter)
    if provincia_filter and provincia_filter != 'Todas':
        query += " AND ubicacion = %s"
        params.append(provincia_filter)

    if min_facturacion_filter and min_facturacion_filter != '0':
        try:
            min_facturacion_filter = float(min_facturacion_filter)
            query += " AND facturacion >= %s"
            params.append(min_facturacion_filter)
        except ValueError:
            pass
            
    if max_facturacion_filter and max_facturacion_filter != '10000000':
        try:
            max_facturacion_filter = float(max_facturacion_filter)
            query += " AND facturacion <= %s"
            params.append(max_facturacion_filter)
        except ValueError:
            pass
            
    if max_precio_filter:
        try:
            max_precio_filter = float(max_precio_filter)
            query += " AND precio_venta <= %s"
            params.append(max_precio_filter)
        except ValueError:
            pass

    query += " ORDER BY fecha_publicacion DESC"
   
    cur.execute(query, params)
    empresas = cur.fetchall()
    cur.close()
    conn.close()

    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    return render_template('index.html', empresas=empresas, actividades=actividades_list, sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


# Ruta para publicar una nueva empresa
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email_contacto = request.form.get('email_contacto')
        telefono = request.form.get('telefono') 
        actividad = request.form.get('actividad')
        sector = request.form.get('sector')
        pais = request.form.get('pais')
        ubicacion = request.form.get('ubicacion')
        tipo_negocio = request.form.get('tipo_negocio')
        descripcion = request.form.get('descripcion')
        local_propiedad = request.form.get('local_propiedad')

        try:
            facturacion = float(request.form.get('facturacion')) if request.form.get('facturacion') else None
            numero_empleados = int(request.form.get('numero_empleados')) if request.form.get('numero_empleados') else None
            resultado_antes_impuestos = float(request.form.get('resultado_antes_impuestos')) if request.form.get('resultado_antes_impuestos') else None
            deuda = float(request.form.get('deuda')) if request.form.get('deuda') else 0.0
            precio_venta = float(request.form.get('precio_venta')) if request.form.get('precio_venta') else None
        except ValueError:
            flash('Por favor, introduce valores numéricos válidos para facturación, empleados, resultado, deuda y precio.', 'danger')
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)

        acepto_condiciones = 'acepto_condiciones' in request.form
        imagen = request.files.get('imagen') 

        errores = []

        if not nombre: errores.append('El nombre de la empresa es obligatorio.')
        if not email_contacto or "@" not in email_contacto: errores.append('El email de contacto es obligatorio y debe ser válido.')
        if not telefono or len(telefono) != 9 or not telefono.isdigit(): errores.append('El teléfono de contacto es obligatorio y debe tener 9 dígitos numéricos.')
        if not actividad or actividad not in actividades_list: errores.append('Por favor, selecciona una actividad válida.')
        if not sector or (actividad and sector not in (actividades_dict.get(actividad, []))): errores.append('Por favor, selecciona un sector válido para la actividad elegida.')
        if not pais: errores.append('El país es obligatorio.')
        if not ubicacion or ubicacion not in provincias_list: errores.append('Por favor, selecciona una provincia válida.')
        if not tipo_negocio: errores.append('El tipo de negocio es obligatorio.')
        if not descripcion: errores.append('La descripción del negocio es obligatoria.')
        if facturacion is None or facturacion < 0: errores.append('La facturación anual es obligatoria y debe ser un número no negativo.')
        if numero_empleados is None or numero_empleados < 0: errores.append('El número de empleados es obligatorio y debe ser un número no negativo.')
        if resultado_antes_impuestos is None: errores.append('El resultado antes de impuestos es obligatorio.')
        if deuda is None or deuda < 0: errores.append('La deuda actual es obligatoria y debe ser un número no negativo.')
        if precio_venta is None or precio_venta < 0: errores.append('El precio solicitado es obligatorio y debe ser un número no negativo.')
        if not acepto_condiciones: errores.append('Debes aceptar las condiciones de uso.')

        if imagen and imagen.filename:
            imagen.seek(0, os.SEEK_END)
            file_size = imagen.tell()
            imagen.seek(0)

            if not allowed_file(imagen.filename): errores.append('Tipo de archivo de imagen no permitido. Solo se aceptan JPG, JPEG, PNG, GIF.')
            elif file_size > MAX_IMAGE_SIZE: errores.append(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024 * 1024):.1f} MB.')

        if errores:
            for error in errores:
                flash(error, 'danger')
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)

        conn = None
        try:
            imagen_url = None
            imagen_filename_gcs = None

            if imagen and imagen.filename and allowed_file(imagen.filename) and imagen.tell() <= MAX_IMAGE_SIZE:
                filename = secure_filename(imagen.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                imagen_filename_gcs = upload_to_gcs(imagen, unique_filename)
                if imagen_filename_gcs:
                    imagen_url = get_public_image_url(imagen_filename_gcs)
                else:
                    imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                    imagen_url = get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])
                    flash('Hubo un problema al subir tu imagen. Se usará una imagen de defecto.', 'warning')
            else:
                imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                imagen_url = get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])

            token_edicion = str(uuid.uuid4())

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO empresas (
                    nombre, email_contacto, telefono, actividad, sector, pais, ubicacion, tipo_negocio,
                    descripcion, facturacion, numero_empleados, local_propiedad,
                    resultado_antes_impuestos, deuda, precio_venta, imagen_filename_gcs, imagen_url,
                    token_edicion, fecha_publicacion, fecha_modificacion
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id;
            """, (
                nombre, email_contacto, telefono, actividad, sector, pais, ubicacion, tipo_negocio,
                descripcion, facturacion, numero_empleados, local_propiedad,
                resultado_antes_impuestos, deuda, precio_venta, imagen_filename_gcs, imagen_url,
                token_edicion
            ))
            empresa_id = cur.fetchone()[0]
            conn.commit()

            edit_link = url_for("editar", edit_token=token_edicion, _external=True)
            email_subject_advertiser = f"Confirmación de publicación de tu anuncio en Pyme Market: {nombre}"
            email_body_advertiser = (
                f"Hola,\n\n"
                f"Tu anuncio para el negocio '{nombre}' ha sido publicado con éxito en Pyme Market.\n\n"
                f"Puedes editar o eliminar tu anuncio en cualquier momento usando el siguiente enlace:\n"
                f"{edit_link}\n\n"
                f"Por favor, guarda este enlace en un lugar seguro, ya que es la única forma de acceder a la edición de tu anuncio.\n\n"
                f"Gracias por usar Pyme Market."
            )

            if send_email(email_contacto, email_subject_advertiser, email_body_advertiser):
                flash('¡Tu negocio ha sido publicado con éxito y te hemos enviado el enlace de edición a tu correo!', 'success')
            else:
                flash('¡Tu negocio ha sido publicado con éxito! Sin embargo, no pudimos enviarte el enlace de edición por correo. Por favor, copia este enlace y guárdalo: ' + edit_link, 'warning')

            admin_email_for_notifications = os.environ.get('EMAIL_DESTINO')
            if admin_email_for_notifications:
                admin_subject = f"🔔 Nuevo Anuncio Publicado en Pyme Market: '{nombre}' (ID: {empresa_id})"
                precio_venta_formateado = f"{precio_venta:.2f} €" if precio_venta is not None else "N/A"

                admin_body = (
                    f"Se ha publicado un nuevo anuncio en Pyme Market.\n\n"
                    f"Detalles del Anuncio:\n"
                    f"----------------------------------------------------\n"
                    f"Nombre del Negocio: {nombre}\n"
                    f"Email de Contacto del Anunciante: {email_contacto}\n"
                    f"Teléfono de Contacto del Anunciante: {telefono}\n"
                    f"Actividad: {actividad}\n"
                    f"Sector: {sector}\n"
                    f"Ubicación: {ubicacion}, {pais}\n"
                    f"Precio de Venta: {precio_venta_formateado}\n"
                    f"Link Directo al Anuncio: {url_for('detalle', empresa_id=empresa_id, _external=True)}\n"
                    f"Link de Edición (para el anunciante): {edit_link}\n"
                    f"----------------------------------------------------\n\n"
                    f"Puedes revisar y gestionar todos los anuncios en el panel de administración:\n"
                    f"{url_for('admin', admin_token=ADMIN_TOKEN, _external=True) if ADMIN_TOKEN else 'Panel de administración'}\n"
                )
                
                if not send_email(admin_email_for_notifications, admin_subject, admin_body):
                    print(f"WARNING Publicar: No se pudo enviar el correo de notificación al administrador ({admin_email_for_notifications}) para el anuncio '{nombre}'.")

            return redirect(url_for('publicar'))

        except Exception as e:
            if conn:
                conn.rollback()
            flash(f'Error al publicar el negocio: {e}', 'danger')
            print(f"ERROR Publicar: Error al publicar el negocio: {e}")
            return render_template('vender_empresa.html', actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict, form_data=request.form)
        finally:
            if conn:
                cur.close()
                conn.close()

    return render_template('vender_empresa.html', actividades=actividades_list, provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)


# Ruta para mostrar los detalles de una empresa Y procesar el formulario de contacto
@app.route('/negocio/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = None
    cur = None
    
    try:
        if request.method == 'POST':
            nombre_interesado = request.form.get('nombre')
            email_interesado = request.form.get('email')
            telefono_interesado = request.form.get('telefono')
            mensaje_interes = request.form.get('mensaje')
            
            if not nombre_interesado or not email_interesado or not mensaje_interes:
                flash('Por favor, completa todos los campos obligatorios del formulario de contacto.', 'danger')
                return redirect(url_for('detalle', empresa_id=empresa_id))
            if "@" not in email_interesado:
                flash('Por favor, introduce una dirección de correo electrónico válida.', 'danger')
                return redirect(url_for('detalle', empresa_id=empresa_id))

            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT email_contacto, nombre, telefono FROM empresas WHERE id = %s", (empresa_id,))
            empresa_info = cur.fetchone()

            if not empresa_info:
                flash('Error: Anuncio no encontrado para enviar el mensaje.', 'danger')
                return redirect(url_for('detalle', empresa_id=empresa_id))

            email_destinatario = os.environ.get('EMAIL_DESTINO')
            nombre_anuncio = empresa_info['nombre']
            email_anunciante = empresa_info['email_contacto']
            telefono_anunciante = empresa_info['telefono']

            subject = f"Mensaje de interés para el anuncio: '{nombre_anuncio}' (Ref: #{empresa_id})"
            body = (
                f"Has recibido un nuevo mensaje de interés para el anuncio '{nombre_anuncio}' (Ref: #{empresa_id}) "
                f"en Pyme Market.\n\n"
                f"**Datos del interesado:**\n"
                f"Nombre: {nombre_interesado}\n"
                f"Email: {email_interesado}\n"
                f"Teléfono: {telefono_interesado if telefono_interesado else 'No proporcionado'}\n\n"
                f"**Mensaje:**\n"
                f"----------------------------------------------------\n"
                f"{mensaje_interes}\n"
                f"----------------------------------------------------\n\n"
                f"**Datos del Anunciante:**\n"
                f"Nombre de la empresa: {nombre_anuncio}\n"
                f"Email: {email_anunciante}\n"
                f"Teléfono: {telefono_anunciante if telefono_anunciante else 'No proporcionado'}\n\n"
                f"Por favor, contacta directamente con el interesado."
            )

            if send_email(email_destinatario, subject, body, from_name=nombre_interesado):
                flash('¡Mensaje enviado con éxito al anunciante!', 'success')
            else:
                flash('Error al enviar el mensaje. Inténtelo de nuevo más tarde.', 'danger')
            
            return redirect(url_for('detalle', empresa_id=empresa_id))

        # Lógica para manejar solicitudes GET
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s AND active = TRUE", (empresa_id,))
        empresa = cur.fetchone()

        if empresa is None:
            return render_template('404.html'), 404

        imagen_url = get_public_image_url(empresa['imagen_filename_gcs']) if empresa['imagen_filename_gcs'] else get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])

        return render_template('detalle.html', empresa=empresa, imagen_url=imagen_url)

    except Exception as e:
        print(f"ERROR Detalle: Error en la ruta detalle: {e}")
        if conn and cur:
            conn.rollback()
        elif conn:
            pass
        
        flash('Se ha producido un error al cargar la página.', 'danger')
        try:
             return render_template('404.html'), 404
        except:
             return "Error 404: Página no encontrada.", 404

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Ruta de edición de empresas (protegida por token)
@app.route('/editar', methods=['GET', 'POST'])
def editar():
    edit_token = request.args.get('edit_token')
    if not edit_token:
        flash('Token de edición no proporcionado.', 'danger')
        return redirect(url_for('index'))

    conn = None
    cur = None

    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
        empresa = cur.fetchone()

        if empresa is None:
            flash('Token de edición inválido o caducado.', 'danger')
            return redirect(url_for('index'))

        if request.method == 'POST':
            nombre = request.form.get('nombre')
            email_contacto = request.form.get('email_contacto')
            telefono = request.form.get('telefono') 
            actividad = request.form.get('actividad')
            sector = request.form.get('sector')
            pais = request.form.get('pais')
            ubicacion = request.form.get('ubicacion')
            tipo_negocio = request.form.get('tipo_negocio')
            descripcion = request.form.get('descripcion')
            local_propiedad = request.form.get('local_propiedad')
            active = 'active' in request.form
            
            imagen_actual_gcs_filename = empresa['imagen_filename_gcs']
            
            try:
                facturacion = float(request.form.get('facturacion')) if request.form.get('facturacion') else None
                numero_empleados = int(request.form.get('numero_empleados')) if request.form.get('numero_empleados') else None
                resultado_antes_impuestos = float(request.form.get('resultado_antes_impuestos')) if request.form.get('resultado_antes_impuestos') else None
                deuda = float(request.form.get('deuda')) if request.form.get('deuda') else 0.0
                precio_venta = float(request.form.get('precio_venta')) if request.form.get('precio_venta') else None
            except ValueError:
                flash('Por favor, introduce valores numéricos válidos para facturación, empleados, resultado, deuda y precio.', 'danger')
                return render_template('editar.html',
                                       empresa=empresa,
                                       actividades=actividades_list,
                                       provincias=provincias_list,
                                       actividades_dict=actividades_dict)


            imagen_nueva = request.files.get('imagen_nueva') 
            eliminar_imagen = 'eliminar_imagen' in request.form
            
            errores = []
            if not nombre: errores.append('El nombre de la empresa es obligatorio.')
            if not email_contacto or "@" not in email_contacto: errores.append('El email de contacto es obligatorio y debe ser válido.')
            if not telefono or len(telefono) != 9 or not telefono.isdigit(): errores.append('El teléfono de contacto es obligatorio y debe tener 9 dígitos numéricos.')
            if not actividad or actividad not in actividades_list: errores.append('Por favor, selecciona una actividad válida.')
            if not sector or (actividad and sector not in (actividades_dict.get(actividad, []))): errores.append('Por favor, selecciona un sector válido para la actividad elegida.')
            if not pais: errores.append('El país es obligatorio.')
            if not ubicacion or ubicacion not in provincias_list: errores.append('Por favor, selecciona una provincia válida.')
            if not tipo_negocio: errores.append('El tipo de negocio es obligatorio.')
            if not descripcion: errores.append('La descripción del negocio es obligatoria.')
            if facturacion is None or facturacion < 0: errores.append('La facturación anual es obligatoria y debe ser un número no negativo.')
            if numero_empleados is None or numero_empleados < 0: errores.append('El número de empleados es obligatorio y debe ser un número no negativo.')
            if resultado_antes_impuestos is None: errores.append('El resultado antes de impuestos es obligatorio.')
            if deuda is None or deuda < 0: errores.append('La deuda actual es obligatoria y debe ser un número no negativo.')
            if precio_venta is None or precio_venta < 0: errores.append('El precio solicitado es obligatorio y debe ser un número no negativo.')

            if errores:
                for error in errores:
                    flash(error, 'danger')
                return redirect(url_for('editar', edit_token=edit_token))

            
            imagen_filename_gcs = imagen_actual_gcs_filename
            imagen_url = empresa['imagen_url']

            if eliminar_imagen and imagen_actual_gcs_filename != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
                delete_from_gcs(imagen_actual_gcs_filename)
                imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                imagen_url = get_public_image_url(imagen_filename_gcs)
                flash('Imagen eliminada y restaurada al logo por defecto.', 'info')
            
            elif imagen_nueva and imagen_nueva.filename:
                imagen_nueva.seek(0, os.SEEK_END)
                file_size = imagen_nueva.tell()
                imagen_nueva.seek(0)
                
                if allowed_file(imagen_nueva.filename) and file_size <= MAX_IMAGE_SIZE:
                    if imagen_actual_gcs_filename and imagen_actual_gcs_filename != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
                        delete_from_gcs(imagen_actual_gcs_filename)
                        
                    filename = secure_filename(imagen_nueva.filename)
                    unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                    imagen_filename_gcs = upload_to_gcs(imagen_nueva, unique_filename)
                    
                    if imagen_filename_gcs:
                        imagen_url = get_public_image_url(imagen_filename_gcs)
                        flash('Imagen actualizada con éxito.', 'success')
                    else:
                        imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                        imagen_url = get_public_image_url(imagen_filename_gcs)
                        flash('Error al subir la nueva imagen. Se usará la imagen por defecto.', 'warning')
                        
                else:
                    flash('La imagen nueva no es válida (formato o tamaño). No se ha realizado la actualización de la imagen.', 'warning')
                    
            
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, telefono = %s, actividad = %s, sector = %s, 
                    pais = %s, ubicacion = %s, tipo_negocio = %s, descripcion = %s, facturacion = %s, 
                    numero_empleados = %s, local_propiedad = %s, resultado_antes_impuestos = %s, 
                    deuda = %s, precio_venta = %s, active = %s, imagen_filename_gcs = %s, 
                    imagen_url = %s, fecha_modificacion = NOW()
                WHERE token_edicion = %s
            """, (
                nombre, email_contacto, telefono, actividad, sector, pais, ubicacion, tipo_negocio,
                descripcion, facturacion, numero_empleados, local_propiedad,
                resultado_antes_impuestos, deuda, precio_venta, active, imagen_filename_gcs,
                imagen_url, edit_token
            ))
            conn.commit()

            flash('¡Anuncio actualizado con éxito!', 'success')
            return redirect(url_for('detalle', empresa_id=empresa['id']))


        imagen_url = get_public_image_url(empresa['imagen_filename_gcs']) if empresa['imagen_filename_gcs'] else get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])
        empresa['imagen_url'] = imagen_url

        return render_template('editar.html', 
                               empresa=empresa, 
                               actividades=actividades_list, 
                               provincias=provincias_list,
                               actividades_dict=actividades_dict)


    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Error de edición: {e}', 'danger')
        print(f"ERROR Edición: Error en la ruta editar: {e}") 
        return redirect(url_for('index'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Ruta para eliminar una empresa (protegida por token)
@app.route('/eliminar', methods=['POST'])
def eliminar():
    edit_token = request.form.get('edit_token')
    
    if not edit_token:
        flash('Token de edición no proporcionado.', 'danger')
        return redirect(url_for('index'))

    conn = None
    cur = None
    imagen_filename = None
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id, imagen_filename_gcs FROM empresas WHERE token_edicion = %s", (edit_token,))
        empresa_info = cur.fetchone()
        
        if not empresa_info:
            flash('Token de edición inválido o caducado. No se pudo eliminar.', 'danger')
            return redirect(url_for('index'))

        empresa_id = empresa_info[0]
        imagen_filename = empresa_info[1]

        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
        conn.commit()
        flash('Anuncio eliminado con éxito.', 'success')
        
    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Error al eliminar el anuncio: {e}', 'danger')
        print(f"ERROR Eliminar: Error al eliminar el anuncio: {e}")
        return redirect(url_for('index'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    if imagen_filename and imagen_filename != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
        try:
            delete_from_gcs(imagen_filename)
            flash('Imagen asociada eliminada de Google Cloud Storage.', 'info')
        except Exception as e:
            flash(f'Advertencia: No se pudo eliminar la imagen de GCS: {e}', 'warning')
            
    return redirect(url_for('index'))


# Ruta de administración (necesita un token para ser accesible)
@app.route('/admin')
@admin_required
def admin():
    token = request.args.get('admin_token')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', empresas=empresas)


# Ruta para cambiar el estado de la empresa
@app.route('/admin/toggle_active/<int:empresa_id>', methods=['POST'])
@admin_required
def toggle_active(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT active, nombre FROM empresas WHERE id = %s", (empresa_id,))
        result = cur.fetchone()
        
        if result is None:
            flash('Empresa no encontrada.', 'danger')
            return redirect(url_for('admin', admin_token=ADMIN_TOKEN))

        current_active = result[0]
        nombre_empresa = result[1]
        
        new_active = not current_active
        cur.execute("UPDATE empresas SET active = %s WHERE id = %s", (new_active, empresa_id))
        conn.commit()
        
        estado = "ACTIVA" if new_active else "INACTIVA"
        flash(f'El estado de "{nombre_empresa}" ha sido cambiado a {estado} con éxito.', 'success')
        
    except Exception as e:
        conn.rollback()
        flash(f'Error al cambiar el estado de la empresa: {e}', 'danger')
        print(f"ERROR Toggle: Error al cambiar estado: {e}")
        
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('admin', admin_token=ADMIN_TOKEN))

# ==============================================================================
# --- FIX: NUEVA RUTA ESPECÍFICA para el enlace de la plantilla ---
# ==============================================================================
# La plantilla llama url_for('estudio_ahorros'), por lo que debe existir este endpoint.
@app.route('/estudio-ahorros')
def estudio_ahorros():
    """Ruta específica para la página 'Estudio de ahorros'.
    Utiliza la ruta genérica para renderizar la plantilla 'estudio-ahorros.html'.
    """
    # Llama a la ruta genérica (o renderiza directamente 'estudio-ahorros.html')
    # Usar ruta_generica es más seguro si ya tienes la lógica centralizada allí.
    return ruta_generica('estudio-ahorros')

# ==============================================================================
# --- RUTA GENÉRICA AL FINAL DEL ARCHIVO (IMPORTANTE MANTENERLA) ---
# ==============================================================================
@app.route('/<nombre_ruta>')
def ruta_generica(nombre_ruta):
    # Intenta renderizar la plantilla con el mismo nombre que la ruta.
    try:
        # Esto permite que urls como /aviso-legal carguen aviso-legal.html
        return render_template(f'{nombre_ruta}.html')
    except Exception:
        # MANEJO DE ERROR ROBUSTO: Si falla, intenta renderizar 404.html, si también falla, devuelve texto plano 
        try:
            return render_template('404.html'), 404
        except Exception:
            # Fallback final de texto plano si la plantilla 404.html no se encuentra
            return "Error 404: Página no encontrada.", 404
        
# Ruta del sitemap.xml
@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    urls = []
    # Añadir las rutas estáticas
    urls.append({'loc': url_for('index', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'daily', 'priority': '1.0'})
    urls.append({'loc': url_for('publicar', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'monthly', 'priority': '0.7'})
    
    # AÑADIDA: La nueva ruta de estudio de ahorros
    urls.append({'loc': url_for('estudio_ahorros', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'monthly', 'priority': '0.7'})
    
    urls.append({'loc': url_for('ruta_generica', nombre_ruta='aviso-legal', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'monthly', 'priority': '0.3'})
    urls.append({'loc': url_for('ruta_generica', nombre_ruta='politica-privacidad', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'monthly', 'priority': '0.3'})
    
    # Añadir rutas dinámicas de empresas
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, fecha_modificacion FROM empresas WHERE active = TRUE ORDER BY fecha_modificacion DESC") 
        empresas = cur.fetchall()

        for empresa in empresas:
            lastmod_str = empresa['fecha_modificacion'].strftime('%Y-%m-%d') if empresa['fecha_modificacion'] else datetime.now().strftime('%Y-%m-%d')
            urls.append({
                'loc': url_for('detalle', empresa_id=empresa[0], _external=True), 
                'lastmod': lastmod_str, 
                'changefreq': 'weekly', 
                'priority': '0.9'
            })
    except Exception as e:
        print(f"Error generando sitemap: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    # Construcción del XML del sitemap
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url_data in urls:
        xml_content += '    <url>\n'
        xml_content += f'        <loc>{url_data["loc"]}</loc>\n'
        xml_content += f'        <lastmod>{url_data["lastmod"]}</lastmod>\\n'
        xml_content += f'        <changefreq>{url_data["changefreq"]}</changefreq>\\n'
        xml_content += f'        <priority>{url_data["priority"]}</priority>\\n'
        xml_content += '    </url>\n'
    xml_content += '</urlset>'

    return Response(xml_content, mimetype='application/xml')

# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
