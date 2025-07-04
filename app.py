# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory
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
from datetime import timedelta, datetime # Necesario para generar URLs firmadas temporales y manejar fechas
from decimal import Decimal, InvalidOperation

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

# Función de utilidad para enviar correos (ADAPTADA PARA USAR VARIABLES DE ENTORNO SMTP)
def send_email(to_email, subject, body):
    # Obtener credenciales y configuración SMTP de las variables de entorno
    smtp_username = os.environ.get('SMTP_USERNAME')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port_str = os.environ.get('SMTP_PORT') # Leer como string

    if not smtp_username or not smtp_password or not smtp_server or not smtp_port_str:
        print("WARNING Email: Las variables de entorno 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_SERVER' o 'SMTP_PORT' no están configuradas. No se puede enviar el correo.")
        return False

    try:
        smtp_port = int(smtp_port_str) # Convertir el puerto a entero
    except ValueError:
        print(f"ERROR Email: La variable de entorno 'SMTP_PORT' no es un número válido: {smtp_port_str}. No se puede enviar el correo.")
        return False

    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = smtp_username # Usar SMTP_USERNAME como remitente
    msg['To'] = to_email

    try:
        # Usar SMTP_SSL para SSL/TLS directo en el puerto 465 (Jimdo)
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        print("ERROR Email: Error de autenticación SMTP: Verifica que 'SMTP_USERNAME' y 'SMTP_PASSWORD' sean correctos para tu servidor Jimdo.")
        return False
    except smtplib.SMTPException as e:
        print(f"ERROR Email: Error SMTP general: {e}")
        return False
    except socket.gaierror:
        print(f"ERROR Email: Error de red: No se pudo resolver el host SMTP ({smtp_server}). Verifica la dirección del servidor.")
        return False
    except Exception as e:
        print(f"ERROR Email: Ocurrió un error inesperado al enviar el correo: {e}")
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
    "Salud y Bienestar": ["Clínicas", "Farmacias", "Gimnasios", "Centros de Estética", "Parafarmacias"],
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


# Rutas de la aplicación
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    actividad_filter = request.args.get('actividad')
    sector_filter = request.args.get('sector')
    provincia_filter = request.args.get('provincia')
    min_facturacion_filter = request.args.get('min_facturacion')
    max_facturacion_filter = request.args.get('max_facturacion')
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

    # FILTROS NUMÉRICOS
    if min_facturacion_filter:
        try:
            min_facturacion_filter = float(min_facturacion_filter)
            query += " AND facturacion >= %s"
            params.append(min_facturacion_filter)
        except ValueError:
            pass # Ignora si no es un número válido
    if max_facturacion_filter:
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
                    f"Teléfono de Contacto del Anunciante: {telefono}\n" ##### MODIFICACIÓN: Añadir teléfono al email del admin
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
            else:
                pass # The environment variable 'EMAIL_DESTINO' is not configured. No new ad notification will be sent to the administrator.
            # --- FIN DE LA NUEVA LÓGICA ---


            return redirect(url_for('publicar'))

        except Exception as e:
            if conn: # Asegúrate de que conn no sea None antes de intentar rollback
                conn.rollback()
            flash(f'Error al publicar el negocio: {e}', 'danger')
            print(f"ERROR Publicar: Error al publicar el negocio: {e}") # Para depuración en los logs
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)
        finally:
            if conn:
                cur.close()
                conn.close()

    return render_template('vender_empresa.html', actividades=actividades_list, provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)


# Ruta para mostrar los detalles de una empresa Y procesar el formulario de contacto
@app.route('/negocio/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if request.method == 'POST':
        # Lógica para manejar el formulario de contacto
        nombre_interesado = request.form.get('nombre')
        email_interesado = request.form.get('email')
        telefono_interesado = request.form.get('telefono')
        mensaje_interes = request.form.get('mensaje')

        # Validaciones básicas del formulario de contacto
        if not nombre_interesado or not email_interesado or not mensaje_interes:
            flash('Por favor, completa todos los campos obligatorios del formulario de contacto.', 'danger')
            cur.close()
            conn.close()
            return redirect(url_for('detalle', empresa_id=empresa_id))
        if "@" not in email_interesado:
            flash('Por favor, introduce una dirección de correo electrónico válida.', 'danger')
            cur.close()
            conn.close()
            return redirect(url_for('detalle', empresa_id=empresa_id))

        try:
            cur.execute("SELECT email_contacto, nombre, telefono FROM empresas WHERE id = %s", (empresa_id,)) ##### MODIFICACIÓN: Incluir 'telefono' en el SELECT
            empresa_info = cur.fetchone()

            if not empresa_info:
                flash('Error: Anuncio no encontrado para enviar el mensaje.', 'danger')
                cur.close()
                conn.close()
                return redirect(url_for('detalle', empresa_id=empresa_id))

            email_anunciante = empresa_info['email_contacto']
            nombre_anuncio = empresa_info['nombre']
            telefono_anunciante = empresa_info['telefono'] ##### MODIFICACIÓN: Recuperar el teléfono del anunciante

            # Construir el cuerpo del correo electrónico
            subject = f"Mensaje de interés para tu anuncio: '{nombre_anuncio}' (Ref: #{empresa_id})"
            body = (
                f"Has recibido un nuevo mensaje de interés para tu anuncio '{nombre_anuncio}' (Ref: #{empresa_id}) "
                f"en Pyme Market.\n\n"
                f"Datos del interesado:\n"
                f"Nombre: {nombre_interesado}\n"
                f"Email: {email_interesado}\n"
                f"Teléfono: {telefono_interesado if telefono_interesado else 'No proporcionado'}\n\n"
                f"Mensaje:\n"
                f"----------------------------------------------------\n"
                f"{mensaje_interes}\n"
                f"----------------------------------------------------\n\n"
                f"Por favor, contacta directamente con el interesado para más detalles."
            )

            # Usar send_email con las variables de entorno configuradas
            if send_email(email_anunciante, subject, body):
                flash('¡Mensaje enviado con éxito al anunciante!', 'success')
            else:
                flash('Hubo un problema al enviar el mensaje. Por favor, inténtalo de nuevo más tarde.', 'danger')

        except Exception as e:
            flash(f'Error al procesar el envío del mensaje: {e}', 'danger')
            print(f"ERROR Detalle POST: {e}")
        finally:
            cur.close()
            conn.close()
            return redirect(url_for('detalle', empresa_id=empresa_id))

    # Lógica para manejar solicitudes GET (mostrar los detalles del negocio)
    else: # request.method == 'GET'
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()
        cur.close()
        conn.close()

        if empresa is None:
            flash('Negocio no encontrado.', 'danger')
            return redirect(url_for('index'))

        return render_template('detalle.html', empresa=empresa)

# Ruta para editar una empresa (accesible con un token de edición)
@app.route('/editar/<string:edit_token>', methods=['GET', 'POST'])
def editar(edit_token):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
    empresa = cur.fetchone()

    if not empresa:
        flash('Anuncio no encontrado o token de edición inválido.', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('index'))

    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES

    if request.method == 'POST':
        # --- Lógica para ELIMINAR el anuncio ---
        if request.form.get('eliminar') == 'true':
            try:
                # Comprobar si hay un nombre de archivo de GCS y no es la imagen por defecto antes de intentar eliminar
                if empresa['imagen_filename_gcs'] and empresa['imagen_filename_gcs'] != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
                    delete_from_gcs(empresa['imagen_filename_gcs'])
                else:
                    pass # No hay imagen_filename_gcs en DB para eliminar de GCS o es la imagen por defecto.

                cur.execute("DELETE FROM empresas WHERE token_edicion = %s", (edit_token,))
                conn.commit()
                flash('Anuncio eliminado con éxito.', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('publicar'))
            except Exception as e:
                conn.rollback()
                flash(f'Error al eliminar el anuncio: {e}', 'danger')
                print(f"ERROR Eliminar: Error al eliminar anuncio con token {edit_token}: {e}") # Depuración
                cur.close()
                conn.close()
                return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


        # --- Lógica para ACTUALIZAR el anuncio (si no es una eliminación) ---
        nombre = request.form.get('nombre')
        email_contacto = request.form.get('email_contacto')
        
        ##### MODIFICACIÓN: Recuperar el campo de teléfono del formulario de edición #####
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
            cur.close()
            conn.close()
            return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


        imagen_subida = request.files.get('imagen')
        # **MODIFICADO: Inicializar con los valores de la empresa existentes**
        imagen_filename_gcs = empresa['imagen_filename_gcs']
        imagen_url = empresa['imagen_url']

        errores = []

        if not nombre: errores.append('El nombre de la empresa es obligatorio.')
        if not email_contacto or "@" not in email_contacto: errores.append('El email de contacto es obligatorio y debe ser válido.')
        
        ##### MODIFICACIÓN: Validación del campo de teléfono para la edición #####
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
        
        # **MODIFICADO: La validación de imagen es opcional para la edición**
        if imagen_subida and imagen_subida.filename: # Solo valida si se subió una nueva imagen
            imagen_subida.seek(0, os.SEEK_END)
            file_size = imagen_subida.tell()
            imagen_subida.seek(0)

            if not allowed_file(imagen_subida.filename): errores.append('Tipo de archivo de imagen no permitido. Solo se aceptan JPG, JPEG, PNG, GIF.')
            elif file_size > MAX_IMAGE_SIZE: errores.append(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024 * 1024):.1f} MB.')
        # REMOVIDO: Ya no se exige que haya una imagen si no se sube una nueva. La lógica de abajo decide qué imagen usar.

        if errores:
            for error in errores:
                flash(error, 'danger')
            cur.close()
            conn.close()
            return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

        try:
            # **MODIFICADO: Lógica para la imagen en la actualización**
            if imagen_subida and imagen_subida.filename and allowed_file(imagen_subida.filename) and imagen_subida.tell() <= MAX_IMAGE_SIZE:
                # Si la imagen existente no es la por defecto, la eliminamos de GCS
                if empresa['imagen_filename_gcs'] and empresa['imagen_filename_gcs'] != app.config['DEFAULT_IMAGE_GCS_FILENAME']:
                    delete_from_gcs(empresa['imagen_filename_gcs'])
                    
                filename_secure = secure_filename(imagen_subida.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename_secure)[1]
                imagen_filename_gcs = upload_to_gcs(imagen_subida, unique_filename)
                if imagen_filename_gcs:
                    # AHORA USA get_public_image_url
                    imagen_url = get_public_image_url(imagen_filename_gcs)
                else:
                    # Fallback a la imagen por defecto si falla la subida de la nueva imagen
                    imagen_filename_gcs = app.config['DEFAULT_IMAGE_GCS_FILENAME']
                    # AHORA USA get_public_image_url
                    imagen_url = get_public_image_url(app.config['DEFAULT_IMAGE_GCS_FILENAME'])
                    flash('No se pudo subir la nueva imagen. Se mantendrá la imagen por defecto o la anterior si no se había cambiado.', 'warning')
            # Si no se subió una nueva imagen, se conservan los valores `imagen_filename_gcs` e `imagen_url`
            # que se inicializaron con los valores de la base de datos al inicio de la función.
            # No hay necesidad de un 'else' explícito aquí para la imagen por defecto, porque si no se
            # sube nada nuevo, los valores preexistentes (incluida la por defecto si ya estaba) se mantienen.

            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, telefono = %s, actividad = %s, sector = %s,
                    pais = %s, ubicacion = %s, tipo_negocio = %s, descripcion = %s,
                    facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    resultado_antes_impuestos = %s, deuda = %s, precio_venta = %s,
                    imagen_filename_gcs = %s, imagen_url = %s,
                    fecha_modificacion = NOW()
                WHERE token_edicion = %s
            """, (
                nombre, email_contacto, telefono, actividad, sector, pais, ubicacion, tipo_negocio,
                descripcion, facturacion, numero_empleados, local_propiedad,
                resultado_antes_impuestos, deuda, precio_venta,
                imagen_filename_gcs, imagen_url, edit_token
            ))
            conn.commit()
            flash('Anuncio actualizado con éxito.', 'success')

            # Refrescar los datos de la empresa para la plantilla después de la actualización
            cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
            empresa_actualizada = cur.fetchone()
            cur.close()
            conn.close()
            return render_template('editar.html', empresa=empresa_actualizada, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

        except Exception as e:
            conn.rollback()
            flash(f'Error al actualizar el anuncio: {e}', 'danger')
            print(f"ERROR Actualizar: Error al actualizar anuncio con token {edit_token}: {e}") # Depuración
            cur.close()
            conn.close()
            # Si hay error, pasar los datos originales de 'empresa' y un formulario vacío si se desea, o los datos del request.form
            return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

    # Si es una solicitud GET, simplemente muestra el formulario con los datos actuales
    cur.close()
    conn.close()
    return render_template('editar.html', empresa=empresa, actividades=actividades_list, sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

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

@app.route('/politica-privacidad')
def politica_privacidad():
    return render_template('politica_privacidad.html')

@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')

# NUEVA RUTA PARA EL SITEMAP DINÁMICO
@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    """
    Genera el archivo sitemap.xml dinámicamente.
    Incluye URLs estáticas y URLs de empresas activas.
    """
    urls = []

    # 1. URLs estáticas
    static_routes = [
        'index', # Representa '/'
        'publicar',
        'valorar_empresa',
        'estudio_ahorros',
        'contacto',
        'nota_legal',
        'politica_privacidad',
        'politica_cookies',
    ]

    for route_name in static_routes:
        # url_for con _external=True generará la URL completa con el dominio.
        # Asegúrate de que FLASK_SERVER_NAME o SERVER_NAME esté configurado en producción
        # para que el dominio sea correcto (e.g., https://www.pymemarket.es).
        # En desarrollo, puede que sea 'http://127.0.0.1:5000/'.
        loc = url_for(route_name, _external=True)
        urls.append({
            'loc': loc,
            'lastmod': datetime.now().strftime('%Y-%m-%d'), # Fecha actual para estáticas
            'changefreq': 'weekly', # Frecuencia estimada de cambio
            'priority': '0.8' # Prioridad, 1.0 para la home, 0.8 para otras importantes
        })
    
    # Ajustar prioridad de la página de inicio
    for url_data in urls:
        if url_data['loc'] == url_for('index', _external=True):
            url_data['priority'] = '1.0'
            break

    # 2. URLs dinámicas (detalle de empresas activas)
    conn = None # Inicializar conn a None
    cur = None # Inicializar cur a None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Selecciona id y fecha_modificacion de empresas activas
        cur.execute("SELECT id, fecha_modificacion FROM empresas WHERE active = TRUE ORDER BY fecha_modificacion DESC")
        empresas = cur.fetchall()

        for empresa in empresas:
            loc = url_for('detalle', empresa_id=empresa['id'], _external=True)
            lastmod = empresa['fecha_modificacion'].strftime('%Y-%m-%d') if empresa['fecha_modificacion'] else datetime.now().strftime('%Y-%m-%d')
            urls.append({
                'loc': loc,
                'lastmod': lastmod,
                'changefreq': 'daily', # Las páginas de anuncios pueden cambiar más a menudo
                'priority': '0.9' # Alta prioridad para anuncios individuales
            })
    except Exception as e:
        print(f"ERROR Sitemap: Error al generar URLs dinámicas desde la DB: {e}")
        # En caso de error de DB, el sitemap se generará solo con las rutas estáticas.
        # Podrías considerar loggear el error más profundamente o notificar al admin.
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

# Ruta de administración (necesita un token para ser accesible)
@app.route('/admin')
def admin():
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso denegado. Se requiere token de administrador.", 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC") # Ordena por ID para ver los más recientes primero
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', empresas=empresas, admin_token=token)


# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
