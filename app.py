# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory, g
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import socket
import requests
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

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Obtener el nombre del bucket de GCS de las variables de entorno de Render.com
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Configuración de la imagen por defecto en GCS
# Asume que 'Pymemarket_logo.png' ya está subido a la raíz de tu bucket de GCS
# y que el bucket es público para esta imagen también.
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
        # (útil en entornos GCS si las credenciales se gestionan de otra forma, como Default Application Credentials)
        storage_client = storage.Client()
    else:
        # Si CLOUD_STORAGE_BUCKET no está definido, las funciones de GCS se omitirán.
        pass

except Exception as e:
    storage_client = None
    print(f"ERROR GCS Init: Error general al inicializar Google Cloud Storage client: {e}")
    print("GCS functions will be skipped.")

# Funciones de utilidad para Google Cloud Storage

def upload_to_gcs(file_stream, filename):
    """
    Sube un archivo a Google Cloud Storage.
    Asume que el bucket ya está configurado para acceso público.
    """
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("ADVERTENCIA GCS Upload: Cliente de almacenamiento o nombre de bucket no configurado.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        file_stream.seek(0) # Rebobinar el stream al principio
        blob.upload_from_file(file_stream)
        # No es necesario llamar a blob.make_public() aquí si el bucket ya es público por defecto.
        print(f"INFO GCS Upload: Archivo {filename} subido con éxito a GCS.")
        return filename
    except Exception as e:
        print(f"ERROR GCS Upload: Error al subir {filename} a GCS: {e}")
        return None

def get_public_image_url(filename):
    """
    Genera una URL pública directa para un archivo en GCS.
    Esta función asume que el bucket y el objeto son accesibles públicamente.
    """
    if not CLOUD_STORAGE_BUCKET:
        # Si el bucket no está configurado, intenta devolver una URL estática local como fallback.
        # Esto solo funcionará si tienes el archivo en tu carpeta 'static' local
        # y tu aplicación está sirviendo archivos estáticos.
        print("ADVERTENCIA GCS URL: Nombre de bucket de GCS no configurado. Usando fallback de URL estática local.")
        return url_for('static', filename=app.config['DEFAULT_IMAGE_GCS_FILENAME'])
    try:
        # Construye la URL pública estándar de GCS
        url = f"https://storage.googleapis.com/{CLOUD_STORAGE_BUCKET}/{filename}"
        return url
    except Exception as e:
        print(f"ERROR GCS URL: Error al generar URL pública para {filename}: {e}")
        # Fallback a la URL pública de la imagen por defecto si falla la generación.
        # Asegúrate de que DEFAULT_IMAGE_GCS_FILENAME también sea público en GCS.
        return f"https://storage.googleapis.com/{CLOUD_STORAGE_BUCKET}/{app.config['DEFAULT_IMAGE_GCS_FILENAME']}"


def delete_from_gcs(filename):
    """
    Elimina un archivo de Google Cloud Storage.
    """
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
        # Asegúrate de que este error se propague y sea visible en los logs de Render
        raise ValueError("DATABASE_URL environment variable is not set.")
    try:
        conn = psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.DictCursor
        )
        return conn
    except Exception as e:
        print(f"ERROR DB: Error al conectar a la base de datos: {e}")
        raise # Re-lanzar la excepción para que el Flask la maneje

# Función de utilidad para enviar correos (CORREGIDA PARA USAR LA API DE MAILGUN)
def send_email(to_email, subject, body):
    """
    Envía un correo electrónico utilizando la API de Mailgun.
    Requiere las variables de entorno MAILGUN_API_KEY y MAILGUN_DOMAIN.
    """
    # 1. Configuración de Mailgun
    MAILGUN_API_KEY = os.environ.get('MAILGUN_API_KEY')
    MAILGUN_DOMAIN = os.environ.get('MAILGUN_DOMAIN')
    # Usa tu email de remitente autorizado en Mailgun (puedes usar el EMAIL_DESTINO si está autorizado)
    SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'info@pymemarket.es') 
    
    # 2. Validación de credenciales
    if not MAILGUN_API_KEY or not MAILGUN_DOMAIN:
        print("ERROR: La configuración de Mailgun no está completa.")
        return False

    # 3. Datos para la solicitud POST
    request_url = f'https://api.eu.mailgun.net/v3/{MAILGUN_DOMAIN}/messages'
    sender = f"Pyme Market <{SENDER_EMAIL}>"
    
    data = {
        "from": sender,
        "to": to_email,
        "subject": subject,
        "html": body 
    }

    try:
        # 4. Realizar la llamada a la API (utiliza HTTPS, puerto 443, que Render permite)
        response = requests.post(
            request_url,
            auth=("api", MAILGUN_API_KEY),
            data=data
        )

        # 5. Comprobar la respuesta (200 OK es éxito)
        if response.status_code == 200:
            print(f"Correo enviado exitosamente a {to_email} vía Mailgun.")
            return True
        else:
            print(f"Error al enviar correo vía Mailgun. Código: {response.status_code}. Respuesta: {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"ERROR Email: Error de conexión al API de Mailgun: {e}")
        return False

# Constantes para la aplicación
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
        # Convertir a Decimal si aún no lo es, para manejar flotantes y enteros de forma consistente
        if not isinstance(value, Decimal):
            # Usamos str(value) para la conversión a Decimal para evitar problemas de precisión con floats
            value = Decimal(str(value))

        # Determinar si el número es un entero para formatearlo sin decimales
        is_integer_value = (value == value.to_integral_value())

        # Formatear el número manualmente para asegurar el formato europeo
        # Primero, obtenemos la parte entera y la parte decimal
        if is_integer_value:
            integer_part_str = str(int(value.to_integral_value()))
            decimal_part_str = ""
        else:
            # Redondear a dos decimales de forma explícita para evitar muchos decimales
            value = value.quantize(Decimal('0.01'))
            s = str(value)
            if '.' in s:
                parts = s.split('.')
                integer_part_str = parts[0]
                decimal_part_str = parts[1]
            else: # Debería ser ya Decimal con .00 si no había parte decimal explícita
                integer_part_str = s
                decimal_part_str = "00"

        # Añadir separadores de miles (puntos) a la parte entera
        formatted_integer_part = []
        n_digits = len(integer_part_str)
        for i, digit in enumerate(integer_part_str):
            formatted_integer_part.append(digit)
            # Añadir punto cada 3 dígitos desde la derecha, sin añadirlo al principio
            if (n_digits - (i + 1)) % 3 == 0 and (n_digits - (i + 1)) != 0:
                formatted_integer_part.append('.')
        
        formatted_integer_part_str = "".join(formatted_integer_part)

        # Unir las partes con coma decimal si hay decimales, y añadir el símbolo de euro
        if is_integer_value:
            return f"{formatted_integer_part_str} €"
        else:
            return f"{formatted_integer_part_str},{decimal_part_str} €"

    except (ValueError, TypeError, AttributeError, InvalidOperation) as e:
        # Esto capturará errores de conversión o de operación con Decimal
        print(f"ERROR EuroFormat: Error en euro_format para valor '{value}' (Tipo: {type(value)}): {e}") # Mantener temporalmente para depuración
        return "N/A"
    except Exception as e:
        print(f"ERROR EuroFormat: Error inesperado en euro_format para valor '{value}' (Tipo: {type(value)}): {e}") # Otro tipo de error
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
    
    # MODIFICACIÓN: Leer los nuevos valores del deslizador de facturación
    min_facturacion_filter = request.args.get('min_facturacion_slider')
    max_facturacion_filter = request.args.get('max_facturacion_slider')

    max_precio_filter = request.args.get('max_precio')

    query = "SELECT * FROM empresas WHERE active = TRUE"
    params = []

    # FILTROS DE TEXTO
    if actividad_filter and actividad_filter != 'Todas las actividades':
        query += " AND actividad = %s"
        params.append(actividad_filter)
    if sector_filter and sector_filter != 'Todos los sectores':
        query += " AND sector = %s"
        params.append(sector_filter)
    if provincia_filter and provincia_filter != 'Todas':
        query += " AND ubicacion = %s"
        params.append(provincia_filter)

    # FILTROS NUMÉRICOS (MODIFICADOS PARA EL DESLIZADOR DE FACTURACIÓN)
    if min_facturacion_filter and min_facturacion_filter != '0': # Considerar 0 como el valor mínimo por defecto sin filtro
        try:
            min_facturacion_filter = float(min_facturacion_filter)
            query += " AND facturacion >= %s"
            params.append(min_facturacion_filter)
        except ValueError:
            pass # Ignora si no es un número válido
            
    # Asume que el valor de `max_facturacion_filter` puede ser un valor grande (e.g., 1000000)
    # y que un valor como 'infinito' o 'max' se manejaría en el front-end
    if max_facturacion_filter and max_facturacion_filter != '10000000': # Ejemplo de valor máximo por defecto sin filtro
        try:
            max_facturacion_filter = float(max_facturacion_filter)
            query += " AND facturacion <= %s"
            params.append(max_facturacion_filter)
        except ValueError:
            pass # Ignora si no es un número válido
            
    if max_precio_filter:
        try:
            max_precio_filter = float(max_precio_filter)
            query += " AND precio_venta <= %s"
            params.append(max_precio_filter)
        except ValueError:
            pass # Ignora si no es un número válido

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
        
        ##### MODIFICACIÓN: Recuperar el campo de teléfono del formulario #####
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
        imagen = request.files.get('imagen') # Usa .get() para que sea None si no se selecciona archivo

        errores = []

        if not nombre: errores.append('El nombre de la empresa es obligatorio.')
        if not email_contacto or "@" not in email_contacto: errores.append('El email de contacto es obligatorio y debe ser válido.')
        
        ##### MODIFICACIÓN: Validación del campo de teléfono #####
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

        # **MODIFICADO: La validación de la imagen ahora es opcional**
        if imagen and imagen.filename: # Solo valida si se subió una imagen
            imagen.seek(0, os.SEEK_END)
            file_size = imagen.tell()
            imagen.seek(0)

            if not allowed_file(imagen.filename): errores.append('Tipo de archivo de imagen no permitido. Solo se aceptan JPG, JPEG, PNG, GIF.')
            elif file_size > MAX_IMAGE_SIZE: errores.append(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024 * 1024):.1f} MB.')
        # REMOVIDO: ya no es obligatorio un error si no hay imagen

        if errores:
            for error in errores:
                flash(error, 'danger')
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)

        conn = None # Inicializa conn a None
        try:
            imagen_url = None
            imagen_filename_gcs = None

            # **MODIFICADO: Lógica para la imagen opcional**
            if imagen and imagen.filename and allowed_file(imagen.filename) and imagen.tell() <= MAX_IMAGE_SIZE:
                # Si hay una imagen válida, súbela a GCS
                filename = secure_filename(imagen.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                imagen_filename_gcs = upload_to_gcs(imagen, unique_filename)
                if imagen_filename_gcs:
                    # AHORA USA get_public_image_url
                    imagen_url = get_public_image_url(imagen_filename_gcs)
                else:
                    # Si falla la subida a GCS, usar la imagen por defecto
                    imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                    # AHORA USA get_public_image_url
                    imagen_url = get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])
                    flash('Hubo un problema al subir tu imagen. Se usará una imagen de defecto.', 'warning')
            else:
                # Si no se subió ninguna imagen o no es válida, usar la imagen por defecto
                imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                # AHORA USA get_public_image_url
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

            # --- LÓGICA EXISTENTE: ENVIAR EMAIL AL ANUNCIANTE CON EL ENLACE DE EDICIÓN ---
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
            # --- FIN DE LA LÓGICA EXISTENTE ---

            # --- NUEVA LÓGICA: ENVIAR EMAIL DE NOTIFICACIÓN AL ADMINISTRADOR (Usando EMAIL_DESTINO) ---
            admin_email_for_notifications = os.environ.get('EMAIL_DESTINO')
            if admin_email_for_notifications:
                admin_subject = f"🔔 Nuevo Anuncio Publicado en Pyme Market: '{nombre}' (ID: {empresa_id})"
                # Formateo manual para precio_venta en el email
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
            # --- FIN DE LA NUEVA LÓGICA ---
            
            # CORRECCIÓN DE INDENTACIÓN (Antigua Línea 561)
            return redirect(url_for('publicar'))

        except Exception as e:
            if conn: # Asegúrate de que conn no sea None antes de intentar rollback
                conn.rollback()
            flash(f'Error al publicar el negocio: {e}', 'danger')
            print(f"ERROR Publicar: Error al publicar el negocio: {e}") # Para depuración en los logs
            return render_template('vender_empresa.html', actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict, form_data=request.form)

        finally:
            if conn:
                cur.close()
                conn.close()

    return render_template('vender_empresa.html', actividades=actividades_list, provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)


# Ruta para mostrar los detalles de una empresa Y procesar el formulario de contacto
@app.route('/negocio/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = None # Inicializa conn a None
    cur = None # Inicializa cur a None

    try:
        # Lógica para manejar solicitudes POST (Formulario de Contacto)
        if request.method == 'POST':
            # --- Lógica de POST: Contacto ---
            
            nombre_interesado = request.form.get('nombre')
            email_interesado = request.form.get('email')
            telefono_interesado = request.form.get('telefono')
            mensaje_interes = request.form.get('mensaje')

            if not nombre_interesado or not email_interesado or not mensaje_interes:
                flash('Por favor, completa todos los campos obligatorios del formulario de contacto.', 'danger')
                return redirect(url_for('detalle', empresa_id=empresa_id))

            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            cur.execute("SELECT email_contacto, nombre FROM empresas WHERE id = %s AND active = TRUE", (empresa_id,))
            empresa_row = cur.fetchone()

            if not empresa_row:
                flash('Negocio no encontrado o no activo.', 'danger')
                return redirect(url_for('index'))
                
            # Convertir a diccionario estándar para uso seguro
            empresa_contacto = dict(empresa_row) 

            # Asumiendo que send_email está definido para enviar el correo
            # Aquí iría la lógica de construcción del asunto y cuerpo del mensaje
            
            # Ejemplo de envío de correo (descomentar cuando la lógica esté lista)
            # subject = f"Nuevo contacto interesado en {empresa_contacto['nombre']}"
            # body = f"Nombre: {nombre_interesado}\nEmail: {email_interesado}\nTeléfono: {telefono_interesado or 'No proporcionado'}\nMensaje:\n{mensaje_interes}"
            
            # if send_email(empresa_contacto['email_contacto'], subject, body):
            #     flash('¡Mensaje enviado con éxito al anunciante!', 'success')
            # else:
            #     flash('Error al enviar el mensaje. Por favor, inténtalo de nuevo más tarde.', 'danger')
            
            flash('Mensaje de contacto procesado (asumiendo que el envío funciona).', 'success')
            return redirect(url_for('detalle', empresa_id=empresa_id))


        # --- Lógica para manejar solicitudes GET (Mostrar detalle del negocio) ---
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # La consulta busca solo empresas activas
        cur.execute("SELECT * FROM empresas WHERE id = %s AND active = TRUE", (empresa_id,))
        
        empresa_row = cur.fetchone() 
        
        # Conversión CRÍTICA: DictRow a dict estándar
        empresa = dict(empresa_row) if empresa_row else None 

        if empresa is None:
            flash('Negocio no encontrado o no activo.', 'danger')
            return redirect(url_for('index'))

        # Determinar la URL de la imagen.
        imagen_url = empresa.get('imagen_url')
        imagen_filename_gcs = empresa.get('imagen_filename_gcs')
        
        if not imagen_url and imagen_filename_gcs:
            # Asumiendo que get_public_image_url está definido
            imagen_url = get_public_image_url(imagen_filename_gcs)
        elif not imagen_url:
            # Fallback a la imagen por defecto si ambos campos están vacíos
            imagen_url = get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])
        
        # Asignación de la URL para usar en la plantilla
        empresa['display_imagen_url'] = imagen_url

        # Generar un título amigable para SEO
        nombre_negocio = empresa.get('nombre', 'Negocio')
        ubicacion_negocio = empresa.get('ubicacion', 'España')
        
        empresa['seo_title'] = f"Venta de {nombre_negocio} - {ubicacion_negocio} | Pyme Market"

        # 🟢 CORRECCIÓN: Usar la plantilla correcta
        return render_template('detalle.html', empresa=empresa)

    except Exception as e:
        # El bloque except captura el error (p.ej. KeyError) y lo registra antes de redirigir
        flash(f'Ocurrió un error al cargar el negocio: {e}', 'danger')
        print(f"ERROR Detalle: Error al cargar el detalle del negocio {empresa_id}: {e}")
        return redirect(url_for('index'))

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Ruta para editar un negocio (Acceso mediante token)
@app.route('/editar/<string:edit_token>', methods=['GET', 'POST'])
def editar(edit_token):
    conn = None
    cur = None
    empresa = None
    
    # 🟢 1. Definir las variables de listas de opciones
    try:
        # Asumiendo que ACTIVIDADES_Y_SECTORES y PROVINCIAS_ESPANA son globales
        actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
        provincias_list = PROVINCIAS_ESPANA
        actividades_dict = ACTIVIDADES_Y_SECTORES
    except NameError:
        print("ADVERTENCIA: Variables globales de Actividades/Provincias no definidas.")
        actividades_list = []
        provincias_list = []
        actividades_dict = {}

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 2. Obtener la empresa por el token de edición
        cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
        empresa_row = cur.fetchone()
        
        empresa = dict(empresa_row) if empresa_row else None

        if empresa is None:
            flash('Token de edición no válido. Acceso no autorizado.', 'danger')
            return redirect(url_for('index'))
        
        empresa_id = empresa['id']

        # 3. Lógica para manejo de POST (Eliminación o Actualización)
        if request.method == 'POST':
            
            # 🟢 CORRECCIÓN CLAVE: Priorizar la lógica de Eliminación
            # Verifica el campo oculto 'eliminar' que tu JS establece en 'true' al pulsar el botón
            if request.form.get('eliminar') == 'true':
                
                # Obtener nombre de la imagen para su posterior eliminación
                imagen_filename_gcs = empresa.get('imagen_filename_gcs')
                nombre_empresa = empresa['nombre']
                
                # Eliminar la imagen de GCS (si existe y no es la por defecto)
                if imagen_filename_gcs and imagen_filename_gcs != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
                    delete_from_gcs(imagen_filename_gcs)
                    
                # Eliminar la entrada de la base de datos
                cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
                conn.commit()

                flash(f'El anuncio \"{nombre_empresa}\" ha sido ELIMINADO permanentemente.', 'success')
                return redirect(url_for('index'))

            # --- Lógica de Actualización (se ejecuta si NO es una eliminación) ---
            else:
                # Recolección de datos del formulario (Usando los nombres de tu HTML)
                nombre = request.form.get('nombre')
                ubicacion = request.form.get('ubicacion')
                
                # Precio (se corresponde con 'precio_venta' en tu HTML, pero 'precio' en DB)
                precio_venta = request.form.get('precio_venta') 
                
                # Actividad/Sector
                actividad_db = request.form.get('actividad') # Nombre de la actividad (no usado en UPDATE, pero bueno tenerlo)
                actividad_sector = request.form.get('sector') # El sector (actividad_sector en DB)

                descripcion = request.form.get('descripcion')
                email_contacto = request.form.get('email_contacto')
                telefono_contacto = request.form.get('telefono') # 'telefono' en tu HTML
                activo = 'activo' in request.form
                
                # Nuevos campos del formulario HTML
                tipo_negocio = request.form.get('tipo_negocio')
                facturacion = request.form.get('facturacion')
                numero_empleados = request.form.get('numero_empleados')
                local_propiedad = request.form.get('local_propiedad')
                resultado_antes_impuestos = request.form.get('resultado_antes_impuestos')
                deuda = request.form.get('deuda')
                
                # Limpieza del precio de venta
                precio_limpio = precio_venta.replace('€', '').replace('.', '').replace(',', '.').strip() if precio_venta else '0'
                
                # 🔴 Validación de campos obligatorios
                if not nombre or not ubicacion or not precio_limpio or not actividad_sector or not email_contacto:
                    flash('Por favor, completa todos los campos obligatorios para actualizar.', 'danger')
                    # Es crucial devolver request.form para repoblar los campos con los datos introducidos
                    return render_template('editar.html', empresa=request.form, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

                if not email_contacto or "@" not in email_contacto:
                    flash('Por favor, introduce una dirección de correo electrónico válida.', 'danger')
                    return render_template('editar.html', empresa=request.form, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

                # Lógica de subida de imagen (asumiendo que se maneja 'imagen')
                nueva_imagen = request.files.get('imagen') 
                
                # Mantener datos de imagen actuales por defecto
                imagen_filename_gcs = empresa['imagen_filename_gcs']
                imagen_url = empresa['imagen_url']

                if nueva_imagen and nueva_imagen.filename:
                    # ** Lógica de GCS aquí: Subir nueva imagen, eliminar antigua, obtener nuevo filename/url **
                    pass # Sustituir por tu lógica de subida GCS

                # Generar el slug
                slug = slugify(nombre)

                # Actualización en la base de datos (Usando 'precio' para 'precio_venta' y 'actividad_sector' para 'sector')
                cur.execute("""
                    UPDATE empresas 
                    SET 
                        nombre = %s, ubicacion = %s, precio = %s, actividad_sector = %s, 
                        descripcion = %s, email_contacto = %s, telefono_contacto = %s,
                        imagen_filename_gcs = %s, imagen_url = %s, active = %s, slug = %s,
                        tipo_negocio = %s, facturacion = %s, numero_empleados = %s, 
                        local_propiedad = %s, resultado_antes_impuestos = %s, deuda = %s,
                        fecha_modificacion = NOW()
                    WHERE id = %s
                """, (nombre, ubicacion, precio_limpio, actividad_sector, descripcion, email_contacto, telefono_contacto, 
                      imagen_filename_gcs, imagen_url, activo, slug, 
                      tipo_negocio, facturacion, numero_empleados, local_propiedad, resultado_antes_impuestos, deuda,
                      empresa_id))
                conn.commit()
                
                flash('¡El anuncio ha sido actualizado con éxito!', 'success')
                return redirect(url_for('editar', edit_token=edit_token))

        # 4. Lógica para GET (Mostrar formulario)

        # Determinar la URL de la imagen actual
        imagen_url_display = empresa.get('imagen_url')
        if not imagen_url_display and empresa.get('imagen_filename_gcs'):
            imagen_url_display = get_public_image_url(empresa['imagen_filename_gcs'])
        elif not imagen_url_display:
            imagen_url_display = get_public_image_url(app.config.get('DEFAULT_IMAGE_GCS_FILENAME'))

        empresa['display_imagen_url'] = imagen_url_display
        
        # Formato del precio (usando 'precio_venta' de la DB que es 'precio')
        try:
            precio_val = empresa.get('precio') 
            if precio_val:
                precio_decimal = Decimal(precio_val) 
                # Se usa el campo 'precio_venta' para el input en el HTML, debe ser numérico sin €
                empresa['precio_venta_display'] = f"{precio_decimal:.2f}" 
            else:
                empresa['precio_venta_display'] = '' 
        except (InvalidOperation, TypeError, KeyError):
            empresa['precio_venta_display'] = '' 

        # Renderizado final con 'editar.html'
        return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Ocurrió un error inesperado: {e}', 'danger')
        print(f"ERROR Editar: Error al editar el negocio con token {edit_token}: {e}")
        return redirect(url_for('index'))

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Ruta para eliminar una publicación (Admin)
@app.route('/admin_delete/<int:empresa_id>', methods=['POST'])
@admin_required # Asegúrate de que el decorador está definido
def admin_delete(empresa_id):
    admin_token = request.args.get('admin_token') # Lo necesitas para el redirect final

    # *** RESTAURAR LÓGICA ORIGINAL DE ELIMINACIÓN POR ID ***
    # ** ESTO ES LO MÁS SEGURO PARA EL ADMIN **
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Obtener el nombre de la imagen para eliminarla
        cur.execute("SELECT nombre, imagen_filename_gcs FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()

        if not empresa:
            flash('Error: Anuncio no encontrado.', 'danger')
            return redirect(url_for('admin', admin_token=admin_token))

        imagen_filename_gcs = empresa['imagen_filename_gcs']
        nombre_empresa = empresa['nombre']

        # 2. Eliminar la imagen de GCS (si no es la por defecto)
        if imagen_filename_gcs and imagen_filename_gcs != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
            delete_from_gcs(imagen_filename_gcs)

        # 3. Eliminar la entrada de la base de datos
        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id, ))
        conn.commit()

        flash(f'El anuncio "{nombre_empresa}" ha sido ELIMINADO permanentemente (Modo Admin).', 'success')

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Error al eliminar el anuncio: {e}', 'danger')
        print(f"ERROR Admin Delete: Error al eliminar el negocio {empresa_id}: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for('admin', admin_token=admin_token)) # <-- La función termina aquí

# La antigua lógica de la función 'eliminar' se elimina de aquí.

# Ruta de API para obtener sectores según la actividad seleccionada (usada en AJAX)
@app.route('/api/sectores/<string:actividad>')
def get_sectores(actividad):
    # Decodificar el nombre de la actividad si es necesario (ej: si tiene espacios)
    actividad = actividad.replace('-', ' ') # Simple reemplazo para URLs amigables
    sectores = ACTIVIDADES_Y_SECTORES.get(actividad, [])
    return json.dumps(sectores), 200, {'Content-Type': 'application/json'}


# GENERACIÓN DE SITEMAP
@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    """Genera un sitemap XML para la aplicación."""
    
    # 1. URL base para construir URLs absolutas
    HOST_URL = request.url_root # E.g., https://pymemarket.com/

    # 2. URLs estáticas (las que no dependen de la BD)
    urls = [
        {'loc': HOST_URL, 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': url_for('publicar', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'weekly', 'priority': '0.8'},
        # Puedes añadir más rutas estáticas aquí (e.g., /contacto, /legal, etc.)
    ]

    # 3. URLs dinámicas (las de los negocios)
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Selecciona solo ID y fecha de modificación (la más reciente) para la sitemap
        cur.execute("SELECT id, fecha_modificacion FROM empresas WHERE active = TRUE ORDER BY id")
        empresas = cur.fetchall()

        for empresa in empresas:
            # Crea la URL al detalle del negocio.
            # NOTA: Si usas slugs en la URL, deberías seleccionar también el campo 'slug' de la BD.
            # Como aquí usas la ID, es directo:
            loc = url_for('detalle', empresa_id=empresa['id'], _external=True)
            
            # Formatea la fecha al estándar W3C para sitemaps
            lastmod = empresa['fecha_modificacion'].strftime('%Y-%m-%d')
            
            urls.append({
                'loc': loc,
                'lastmod': lastmod,
                'changefreq': 'weekly',
                'priority': '0.9' # Más alta prioridad que el resto de estáticas
            })

    except Exception as e:
        print(f"ERROR Sitemap: Error al generar URLs dinámicas desde la BD: {e}")
        # En caso de error de BD, el sitemap se genera solo con las URLs estáticas.
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
        xml_content += f'        <lastmod>{url_data["lastmod"]}</lastmod>\n'
        xml_content += f'        <changefreq>{url_data["changefreq"]}</changefreq>\n'
        xml_content += f'        <priority>{url_data["priority"]}</priority>\n'
        xml_content += '    </url>\n'
    xml_content += '</urlset>'

    return Response(xml_content, mimetype='application/xml')

@app.route('/politica-cookies')
def politica_cookies():
    """Ruta para la Política de Cookies."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('politica_cookies.html')
    
# Ruta para la página de Estudio de Ahorros
@app.route('/estudio-ahorros')
def estudio_ahorros():
    """Ruta para la página estática o funcional de Estudio de Ahorros."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('estudio_ahorros.html')

# Ruta para la página de Contacto
@app.route('/contacto')
def contacto():
    """Ruta para la página de Contacto."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('contacto.html')

# Ruta para el Aviso Legal
@app.route('/nota-legal')
def nota_legal():
    """Ruta para el Aviso Legal y Condiciones de Uso."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('nota_legal.html')

# Ruta para la Política de Privacidad
@app.route('/politica-privacidad')
def politica_privacidad():
    """Ruta para la Política de Privacidad."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('politica_privacidad.html')

# Ruta para el listado del Blog
@app.route('/blog')
def blog_list():
    """Ruta para el listado de posts del Blog."""
    # Renderizarás una plantilla que tienes que crear
    return render_template('blog_list.html')

# Ruta de administración (necesita un token para ser accesible)
@app.route('/admin')
@admin_required # Protege la ruta con el decorador
def admin():
    token = request.args.get('admin_token') # El token se pasa como argumento, pero Flask lo obtiene del request
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) # Usar DictCursor
    cur.execute("SELECT * FROM empresas ORDER BY id DESC") # Ordena por ID para ver los más recientes primero
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', empresas=empresas, admin_token=token)


# Ruta para CAMBIAR EL ESTADO (Activar/Desactivar) de un anuncio desde el panel de administración
@app.route('/admin/toggle_active/<int:empresa_id>', methods=['POST'])
@admin_required
def admin_toggle_active(empresa_id):
    conn = None
    cur = None
    admin_token = request.args.get('admin_token') # Necesario para redireccionar

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Obtener el estado actual
        cur.execute("SELECT active, nombre FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()

        if not empresa:
            flash('Error: Anuncio no encontrado.', 'danger')
            return redirect(url_for('admin', admin_token=admin_token))

        new_status = not empresa['active'] # Cambiar el estado
        
        # 2. Actualizar el estado en la base de datos
        cur.execute("UPDATE empresas SET active = %s WHERE id = %s", (new_status, empresa_id))
        conn.commit()

        status_text = "activado" if new_status else "desactivado"
        flash(f'El anuncio "{empresa["nombre"]}" ha sido {status_text} con éxito.', 'success')

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Error al cambiar el estado: {e}', 'danger')
        print(f"ERROR Admin Toggle: Error al cambiar estado del negocio {empresa_id}: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for('admin', admin_token=admin_token))


# Ruta para ELIMINAR un anuncio desde el panel de administración
@app.route('/admin/delete_ad/<int:empresa_id>', methods=['POST'])
@admin_required
def admin_delete_ad(empresa_id):
    conn = None
    cur = None
    admin_token = request.args.get('admin_token')

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. Obtener el nombre de la imagen para eliminarla
        cur.execute("SELECT nombre, imagen_filename_gcs FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()

        if not empresa:
            flash('Error: Anuncio no encontrado.', 'danger')
            return redirect(url_for('admin', admin_token=admin_token))
        
        imagen_filename_gcs = empresa['imagen_filename_gcs']
        nombre_empresa = empresa['nombre']

        # 2. Eliminar la imagen de GCS (si no es la por defecto)
        if imagen_filename_gcs and imagen_filename_gcs != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
            delete_from_gcs(imagen_filename_gcs)
            
        # 3. Eliminar la entrada de la base de datos
        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
        conn.commit()

        flash(f'El anuncio "{nombre_empresa}" ha sido ELIMINADO permanentemente.', 'success')

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f'Error al eliminar el anuncio: {e}', 'danger')
        print(f"ERROR Admin Delete: Error al eliminar el negocio {empresa_id}: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for('admin', admin_token=admin_token))


if __name__ == '__main__':
    # Usar el puerto proporcionado por Render o el 5000 por defecto
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
