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
from datetime import timedelta, datetime # Necesario para generar URLs firmadas temporales y manejar fechas
from decimal import Decimal, InvalidOperation 

# IMPORTACIONES PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS
from google.oauth2 import service_account # Necesario para cargar credenciales de JSON

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

# Inicialización del cliente de Google Cloud Storage
storage_client = None # Inicializar para asegurar que siempre esté definida
if os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'):
    try:
        # Cargar las credenciales desde la variable de entorno
        credentials_info = json.loads(os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'))
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        storage_client = storage.Client(credentials=credentials)
        app.logger.info("Cliente de Google Cloud Storage inicializado con credenciales de servicio.")
    except Exception as e:
        app.logger.error(f"Error al inicializar cliente de Google Cloud Storage: {e}")
else:
    try:
        storage_client = storage.Client()
        app.logger.info("Cliente de Google Cloud Storage inicializado con credenciales por defecto.")
    except Exception as e:
        app.logger.error(f"Error al inicializar cliente de Google Cloud Storage: {e}")

GCS_BUCKET_NAME = os.environ.get('GCS_BUCKET_NAME')

if not GCS_BUCKET_NAME:
    app.logger.warning("Variable de entorno GCS_BUCKET_NAME no configurada. Las subidas de archivos a GCS no funcionarán.")

# Función para subir archivos a Google Cloud Storage
def upload_blob(source_file, destination_blob_name):
    """Sube un archivo a un blob en el bucket."""
    if not storage_client or not GCS_BUCKET_NAME:
        app.logger.error("Cliente de GCS o nombre de bucket no configurado. No se puede subir el archivo.")
        return None

    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_file(source_file)
        app.logger.info(f"Archivo {destination_blob_name} subido a {GCS_BUCKET_NAME}.")
        # Generar una URL firmada para el archivo, válida por 1 año
        # Nota: Las URLs firmadas son seguras y no requieren el bucket público
        # Pero para visualización directa en HTML, a veces se prefieren públicas o CDN
        # Para simplificar, si se necesita una URL pública:
        # url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{destination_blob_name}"
        
        # Generar URL de acceso público (si el bucket permite acceso público)
        # O si el bucket no es público, generar una URL firmada por tiempo limitado
        # Para este caso, generaremos una URL de acceso pública si se necesita
        # (Esto asume que el bucket está configurado para acceso público si se necesita la URL directa en el navegador)
        url = blob.public_url # Esto solo funcionará si el objeto es público
        app.logger.info(f"URL pública generada para {destination_blob_name}: {url}")
        return url
    except Exception as e:
        app.logger.error(f"Error al subir archivo a GCS: {e}")
        return None

# Función para generar una URL firmada temporal (si el bucket es privado)
def generate_signed_url(blob_name):
    """Genera una URL firmada para un blob, válida por tiempo limitado."""
    if not storage_client or not GCS_BUCKET_NAME:
        app.logger.error("Cliente de GCS o nombre de bucket no configurado. No se puede generar URL firmada.")
        return None
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        # La URL será válida por 1 hora
        url = blob.generate_signed_url(expiration=timedelta(hours=1))
        return url
    except Exception as e:
        app.logger.error(f"Error al generar URL firmada para {blob_name}: {e}")
        return None

# -------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# -------------------------------------------------------------

# Función para establecer conexión con la base de datos PostgreSQL
def get_db_connection():
    conn = None # Inicializa conn a None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"),
                                cursor_factory=psycopg2.extras.DictCursor)
        conn.autocommit = True # Habilita autocommit si cada operación es una transacción independiente
        return conn
    except Exception as e:
        app.logger.error(f"Error al conectar a la base de datos: {e}")
        if conn:
            conn.close()
        # En un entorno de producción, podrías querer manejar este error de forma más elegante,
        # quizás mostrando una página de error genérica.
        raise # Vuelve a lanzar la excepción para que el servidor la capture si no hay manejo específico

# Cargar actividades y sectores desde el archivo JSON
# Esto se hace una vez al inicio de la aplicación
try:
    with open('actividades_y_sectores.json', 'r', encoding='utf-8') as f:
        ACTIVIDADES_Y_SECTORES = json.load(f)
except FileNotFoundError:
    app.logger.error("El archivo 'actividades_y_sectores.json' no se encontró.")
    ACTIVIDADES_Y_SECTORES = {}
except json.JSONDecodeError:
    app.logger.error("Error al decodificar 'actividades_y_sectores.json'. Asegúrate de que sea un JSON válido.")
    ACTIVIDADES_Y_SECTORES = {}


# Lista de provincias de España
PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila", "Badajoz", "Barcelona", "Burgos",
    "Cáceres", "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba", "Cuenca", "Girona", "Granada", "Guadalajara",
    "Gipuzkoa", "Huelva", "Huesca", "Illes Balears", "Jaén", "León", "Lleida", "Lugo", "Madrid", "Málaga", "Murcia",
    "Navarra", "Ourense", "Palencia", "Las Palmas", "Pontevedra", "La Rioja", "Salamanca", "Santa Cruz de Tenerife",
    "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid", "Bizkaia", "Zamora",
    "Zaragoza", "Ceuta", "Melilla"
]

# Configuración de correo electrónico
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com') # Usar Gmail por defecto
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587)) # Puerto estándar para TLS

if not EMAIL_USER or not EMAIL_PASS:
    app.logger.warning("Variables de entorno EMAIL_USER o EMAIL_PASS no configuradas. El envío de correos no funcionará.")

# Función para enviar correos electrónicos
def send_email(to_email, subject, body):
    if not EMAIL_USER or not EMAIL_PASS:
        app.logger.error("No se pueden enviar correos: credenciales no configuradas.")
        return False

    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_USER
    msg['To'] = to_email

    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()  # Habilitar seguridad TLS
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        app.logger.info(f"Correo enviado a {to_email}")
        return True
    except smtplib.SMTPAuthenticationError:
        app.logger.error("Error de autenticación SMTP. Revisa tus credenciales y configuración de la app.")
    except smtplib.SMTPConnectError:
        app.logger.error("Error de conexión SMTP. Revisa el host y puerto.")
    except socket.gaierror:
        app.logger.error("Error de dirección/nombre de host SMTP. El host no se pudo resolver.")
    except Exception as e:
        app.logger.error(f"Error al enviar correo: {e}")
    return False

# Ruta principal (index)
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()

    # Obtener parámetros de filtro
    filter_actividad = request.args.get('actividad')
    filter_sector = request.args.get('sector')
    filter_provincia = request.args.get('provincia')
    filter_min_facturacion = request.args.get('min_facturacion')
    filter_max_precio = request.args.get('max_precio')

    # Construir la consulta SQL dinámicamente
    query = "SELECT * FROM empresas WHERE 1=1"
    params = []

    if filter_actividad:
        query += " AND actividad = %s"
        params.append(filter_actividad)

    if filter_sector:
        query += " AND sector = %s"
        params.append(filter_sector)

    if filter_provincia:
        query += " AND provincia = %s"
        params.append(filter_provincia)

    if filter_min_facturacion:
        try:
            min_val = float(filter_min_facturacion.replace('.', '').replace(',', '.'))
            query += " AND facturacion >= %s"
            params.append(min_val)
        except (ValueError, InvalidOperation):
            flash("Formato de facturación mínima inválido.", "warning")

    if filter_max_precio:
        try:
            max_val = float(filter_max_precio.replace('.', '').replace(',', '.'))
            query += " AND precio <= %s"
            params.append(max_val)
        except (ValueError, InvalidOperation):
            flash("Formato de precio máximo inválido.", "warning")

    query += " ORDER BY id DESC" # Ordenar siempre por ID para consistencia

    try:
        cur.execute(query, params)
        empresas = cur.fetchall()
    except Exception as e:
        app.logger.error(f"Error al ejecutar consulta SQL: {e}")
        flash("Error al cargar empresas. Inténtalo de nuevo más tarde.", "danger")
        empresas = []
    finally:
        cur.close()
        conn.close()

    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    
    # Pasa los parámetros de filtro actuales para que los desplegables mantengan la selección
    return render_template('index.html', empresas=empresas,
                           actividades=actividades_list,
                           provincias=PROVINCIAS_ESPANA,
                           current_filter_actividad=filter_actividad,
                           current_filter_sector=filter_sector, # Necesitas esto si quieres preservar el sector
                           current_filter_provincia=filter_provincia,
                           current_filter_min_facturacion=filter_min_facturacion,
                           current_filter_max_precio=filter_max_precio,
                           actividades_dict=ACTIVIDADES_Y_SECTORES
                           )

# Ruta para publicar una nueva empresa
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES

    if request.method == 'POST':
        nombre_empresa = request.form['nombre_empresa']
        actividad = request.form['actividad']
        sector = request.form['sector']
        provincia = request.form['provincia']
        facturacion = request.form['facturacion'].replace('.', '').replace(',', '.') # Limpiar formato de números
        precio = request.form['precio'].replace('.', '').replace(',', '.') # Limpiar formato de números
        contacto_nombre = request.form['contacto_nombre']
        contacto_email = request.form['contacto_email']
        contacto_telefono = request.form['contacto_telefono']
        descripcion = request.form['descripcion']
        imagen = request.files.get('imagen') # Usar .get() para evitar KeyError si no hay imagen

        errores = []

        if not nombre_empresa:
            errores.append("El nombre de la empresa es obligatorio.")
        if not actividad or actividad not in actividades_list:
            errores.append("La actividad seleccionada no es válida.")
        if not sector or (actividad and sector not in ACTIVIDADES_Y_SECTORES.get(actividad, [])):
            errores.append("El sector seleccionado no es válido para la actividad elegida.")
        if not provincia or provincia not in PROVINCIAS_ESPANA:
            errores.append("La provincia seleccionada no es válida.")
        
        try:
            facturacion_val = Decimal(facturacion) if facturacion else None
            if facturacion_val is not None and facturacion_val < 0:
                errores.append("La facturación no puede ser negativa.")
        except InvalidOperation:
            errores.append("Formato de facturación anual inválido. Usa solo números, comas o puntos.")

        try:
            precio_val = Decimal(precio) if precio else None
            if precio_val is not None and precio_val < 0:
                errores.append("El precio no puede ser negativo.")
        except InvalidOperation:
            errores.append("Formato de precio inválido. Usa solo números, comas o puntos.")

        if not contacto_nombre:
            errores.append("El nombre de contacto es obligatorio.")
        if not contacto_email or "@" not in contacto_email:
            errores.append("El correo electrónico de contacto es obligatorio y debe ser válido.")
        if not descripcion:
            errores.append("La descripción del negocio es obligatoria.")
        
        # Validar si se subió una imagen y si tiene un formato permitido
        imagen_url = None
        if imagen and imagen.filename:
            # Puedes añadir más validaciones de tipo de archivo si lo deseas
            if not secure_filename(imagen.filename):
                errores.append("Nombre de archivo de imagen no seguro.")
        
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
            conn = get_db_connection()
            cur = conn.cursor()

            # Si hay imagen, subirla a GCS
            if imagen and imagen.filename:
                # Generar un nombre de archivo único para GCS
                unique_filename = f"{uuid.uuid4()}_{secure_filename(imagen.filename)}"
                imagen.seek(0) # Asegúrate de que el puntero esté al inicio del archivo
                imagen_url = upload_blob(imagen, unique_filename)
                if not imagen_url:
                    flash("Error al subir la imagen.", "danger")
                    # No es un error fatal, pero el usuario debe saberlo
                    imagen_url = None # Asegúrate de que no se intente guardar una URL None en la DB si falló la subida
            
            cur.execute(
                """INSERT INTO empresas (
                    nombre_empresa, actividad, sector, provincia, facturacion, precio,
                    contacto_nombre, contacto_email, contacto_telefono, descripcion, imagen_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (nombre_empresa, actividad, sector, provincia,
                 facturacion_val if facturacion_val is not None else None,
                 precio_val if precio_val is not None else None,
                 contacto_nombre, contacto_email, contacto_telefono, descripcion, imagen_url)
            )
            conn.commit() # Asegura que los cambios se guarden en la DB
            flash("¡Tu negocio ha sido publicado con éxito!", "success")

            # Enviar correo de confirmación al contacto
            subject = f"Confirmación de publicación de {nombre_empresa} en PYMEMARKET"
            body = f"""
            Hola {contacto_nombre},

            Hemos recibido tu publicación de negocio '{nombre_empresa}' en PYMEMARKET.
            Pronto nos pondremos en contacto contigo.

            Detalles de tu publicación:
            Actividad: {actividad}
            Sector: {sector}
            Provincia: {provincia}
            Facturación anual: {facturacion_val if facturacion_val is not None else 'No especificado'} €
            Precio de venta: {precio_val if precio_val is not None else 'No especificado'} €
            Descripción: {descripcion}

            Gracias por confiar en PYMEMARKET.

            Atentamente,
            El equipo de PYMEMARKET
            """
            send_email(contacto_email, subject, body)

            return redirect(url_for('publicar')) # Redirigir para evitar re-envío de formulario

        except Exception as e:
            if conn: # Asegúrate de que conn no sea None antes de intentar rollback
                conn.rollback()
            flash(f'Error al publicar el negocio: {e}', 'danger')
            app.logger.error(f"Error al publicar el negocio: {e}") # Para depuración en los logs
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)
        finally:
            if conn:
                cur.close()
                conn.close()

    # Para solicitudes GET, simplemente renderizar el formulario
    return render_template('vender_empresa.html', actividades=actividades_list, provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)


# Ruta para valorar una empresa
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

@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')


# Ruta de administración (necesita un token para ser accesible)
@app.route('/admin')
def admin():
    token = request.args.get('admin_token')
    # ADMIN_TOKEN se carga de os.environ.get('ADMIN_TOKEN')
    ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN') 
    if token != ADMIN_TOKEN:
        return "Acceso denegado. Se requiere token de administrador.", 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC") # Ordena por ID para ver los más recientes primero
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', empresas=empresas, admin_token=token)


# Punto de entrada para la aplicación Flask
if __name__ == '__main__':
    # Obtener el puerto de la variable de entorno PORT, o usar 5000 por defecto para desarrollo local
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
