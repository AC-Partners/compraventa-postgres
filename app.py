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
# Obtener la clave de la cuenta de servicio de GCP de las variables de entorno
# Se asume que GCP_SERVICE_ACCOUNT_KEY_JSON es una cadena JSON
gcp_service_account_key_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
storage_client = None # Inicializar a None por defecto

if gcp_service_account_key_json:
    try:
        # Cargar las credenciales directamente del JSON proporcionado
        credentials_info = json.loads(gcp_service_account_key_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        storage_client = storage.Client(credentials=credentials)
        app.logger.info("Cliente de Google Cloud Storage inicializado con credenciales de JSON.")
    except Exception as e:
        app.logger.error(f"Error al inicializar cliente de Google Cloud Storage con GCP_SERVICE_ACCOUNT_KEY_JSON: {e}")
else:
    app.logger.warning("GCP_SERVICE_ACCOUNT_KEY_JSON no está configurada. Intentando inicializar cliente de GCS con credenciales por defecto.")
    try:
        # Intentar inicializar con credenciales por defecto si la variable no está (ej. en desarrollo local)
        storage_client = storage.Client()
        app.logger.info("Cliente de Google Cloud Storage inicializado con credenciales por defecto.")
    except Exception as e:
        app.logger.error(f"Error al inicializar cliente de Google Cloud Storage con credenciales por defecto: {e}")
        storage_client = None # Asegúrate de manejar esto si el cliente no se inicializa

CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')
if not CLOUD_STORAGE_BUCKET:
    app.logger.error("CLOUD_STORAGE_BUCKET no está configurado.")
# -------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# -------------------------------------------------------------

# Funciones de utilidad para GCS
def upload_to_gcs(file):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        app.logger.error("Cliente de GCS o nombre de bucket no configurado.")
        return None, None
    
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        # Generar un nombre de archivo único
        filename = secure_filename(file.filename)
        unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
        blob = bucket.blob(unique_filename)
        
        blob.upload_from_file(file, content_type=file.content_type)
        
        # Generar una URL firmada que expire después de un tiempo
        # La URL firmada permite acceso público temporal sin hacer el objeto público
        signed_url = blob.generate_signed_url(expiration=timedelta(minutes=30), version='v4')
        
        return unique_filename, signed_url
    except Exception as e:
        app.logger.error(f"Error al subir archivo a GCS: {e}")
        return None, None

def delete_from_gcs(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        app.logger.error("Cliente de GCS o nombre de bucket no configurado para eliminación.")
        return False
    
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        if blob.exists(): # Verifica si el blob existe antes de intentar eliminar
            blob.delete()
            app.logger.info(f"Archivo {filename} eliminado de GCS.")
            return True
        else:
            app.logger.warning(f"Archivo {filename} no encontrado en GCS para eliminar.")
            return False
    except Exception as e:
        app.logger.error(f"Error al eliminar archivo {filename} de GCS: {e}")
        return False

def generate_signed_url(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        app.logger.error("Cliente de GCS o nombre de bucket no configurado para generar URL firmada.")
        return None
    
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        if blob.exists():
            return blob.generate_signed_url(expiration=timedelta(minutes=30), version='v4')
        else:
            app.logger.warning(f"Archivo {filename} no encontrado en GCS para generar URL firmada.")
            return None
    except Exception as e:
        app.logger.error(f"Error al generar URL firmada para {filename}: {e}")
        return None


# Configuración de la base de datos PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    app.logger.error("DATABASE_URL no está configurada.")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        app.logger.error(f"Error al conectar con la base de datos: {e}")
        flash("Error al conectar con la base de datos. Inténtelo de nuevo más tarde.", "danger")
        return None

# Configuración de correo electrónico para notificaciones
SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
SMTP_SERVER = os.environ.get('SMTP_SERVER')
SMTP_PORT = os.environ.get('SMTP_PORT', 587) # Puerto por defecto para TLS/SSL

EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO', 'tu_email_admin@example.com') # Email del administrador

if not all([SMTP_USERNAME, SMTP_PASSWORD, SMTP_SERVER, SMTP_PORT]):
    app.logger.warning("Variables de entorno SMTP no completamente configuradas. El envío de correos podría fallar.")

def send_email(subject, body, to_email):
    if not all([SMTP_USERNAME, SMTP_PASSWORD, SMTP_SERVER, SMTP_PORT]):
        app.logger.error("No se pueden enviar correos: configuración SMTP incompleta.")
        return False
    
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email

    try:
        # Usar SSL/TLS explícito para 465, o STARTTLS para 587
        if int(SMTP_PORT) == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, int(SMTP_PORT))
        else: # Asume STARTTLS para otros puertos, como 587
            server = smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT))
            server.starttls()
        
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        app.logger.info(f"Correo enviado a {to_email}: '{subject}'")
        return True
    except smtplib.SMTPAuthenticationError:
        app.logger.error("Error de autenticación SMTP. Revisa usuario y contraseña.")
        return False
    except (smtplib.SMTPException, socket.error) as e:
        app.logger.error(f"Error al enviar correo: {e}")
        return False
    except Exception as e:
        app.logger.error(f"Error inesperado en send_email: {e}")
        return False

# Constantes para la validación de archivos
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024 # 5 MB

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Definir las actividades y sectores (ahora cargadas desde un JSON)
# Cargar actividades y sectores desde un archivo JSON (o definir directamente)
# Es más robusto cargar desde un archivo o una variable de entorno grande si es complejo
# Para este ejemplo, lo defino como un diccionario Python
ACTIVIDADES_Y_SECTORES = {
    "Comercio": ["Minorista", "Mayorista", "E-commerce"],
    "Servicios": ["Consultoría", "Hostelería", "Transporte", "Limpieza", "Salud", "Educación", "Tecnología"],
    "Industria": ["Manufactura", "Alimentación", "Textil"],
    "Hostelería y Restauración": ["Restaurante", "Bar", "Hotel", "Cafetería"],
    "Agricultura y Pesca": ["Cultivo", "Ganadería", "Pesca"],
    "Construcción": ["Obra Civil", "Edificación"],
    "Inmobiliaria": ["Agencia", "Gestión de propiedades"],
    "Financiero": ["Asesoría", "Correduría"],
    "Marketing y Publicidad": ["Agencia Digital", "Diseño Gráfico"],
    "Automoción": ["Taller", "Concesionario"]
}

# Lista de actividades para el desplegable principal
actividades_list = sorted(list(ACTIVIDADES_Y_SECTORES.keys()))

# Lista de provincias de España
PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila", "Badajoz", "Barcelona",
    "Bizkaia", "Burgos", "Cáceres", "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba",
    "Cuenca", "Girona", "Granada", "Guadalajara", "Gipuzkoa", "Huelva", "Huesca", "Illes Balears",
    "Jaén", "León", "Lleida", "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense", "Palencia",
    "Las Palmas", "Pontevedra", "La Rioja", "Salamanca", "Santa Cruz de Tenerife", "Segovia", "Sevilla",
    "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid", "Zamora", "Zaragoza", "Ceuta", "Melilla"
]

# Configuración del locale para formato de moneda (ej. para euro_format)
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8') # Para sistemas Linux
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Spanish_Spain.1252') # Para sistemas Windows
    except locale.Error:
        app.logger.warning("No se pudo establecer el locale para el formato de moneda. Usando el formato por defecto.")


# Filtro personalizado de Jinja2 para formato de Euro
@app.template_filter('euro_format')
def euro_format_filter(value):
    try:
        # Asegurarse de que el valor sea numérico antes de formatear
        num_value = Decimal(value)
        # Formato de número con separador de miles de punto y separador decimal de coma
        # y dos decimales, seguido del símbolo de euro
        return locale.format_string("%.2f €", num_value, grouping=True).replace('.', 'X').replace(',', '.').replace('X', ',')
    except (InvalidOperation, TypeError):
        return f"{value} €" # Retorna el valor original con euro si no es numérico

# Ruta principal (listado de anuncios)
@app.route('/')
def index():
    conn = get_db_connection()
    if conn is None:
        # Pasa ACTIVIDADES_Y_SECTORES incluso si no hay conexión a la base de datos
        return render_template('index.html', empresas=[], actividades=actividades_list, provincias=PROVINCIAS_ESPANA,
                               current_filter_actividad=None, current_filter_sector=None, current_filter_provincia=None,
                               current_filter_min_facturacion=None, current_filter_max_precio=None,
                               actividades_dict=ACTIVIDADES_Y_SECTORES) # **Añadido: Pasar actividades_dict**

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Obtener parámetros de filtro
    filter_actividad = request.args.get('actividad')
    filter_sector = request.args.get('sector')
    filter_provincia = request.args.get('provincia')
    filter_min_facturacion = request.args.get('min_facturacion')
    filter_max_precio = request.args.get('max_precio')

    query = "SELECT * FROM empresas WHERE 1=1"
    params = []

    if filter_actividad:
        query += " AND actividad = %s"
        params.append(filter_actividad)
    if filter_sector:
        query += " AND sector = %s"
        params.append(filter_sector)
    if filter_provincia:
        query += " AND ubicacion = %s"
        params.append(filter_provincia)
    if filter_min_facturacion:
        try:
            min_fact = Decimal(filter_min_facturacion)
            query += " AND facturacion >= %s"
            params.append(min_fact)
        except InvalidOperation:
            flash('El valor de facturación mínima no es válido.', 'warning')
    if filter_max_precio:
        try:
            max_p = Decimal(filter_max_precio)
            query += " AND precio_venta <= %s"
            params.append(max_p)
        except InvalidOperation:
            flash('El valor de precio máximo no es válido.', 'warning')

    query += " ORDER BY fecha_publicacion DESC"

    cur.execute(query, params)
    empresas = cur.fetchall()
    cur.close()
    conn.close()

    # Pasa los parámetros de filtro actuales para que los desplegables mantengan la selección
    return render_template('index.html', empresas=empresas,
                           actividades=actividades_list,
                           provincias=PROVINCIAS_ESPANA,
                           current_filter_actividad=filter_actividad,
                           current_filter_sector=filter_sector, # Necesitas esto si quieres preservar el sector
                           current_filter_provincia=filter_provincia,
                           current_filter_min_facturacion=filter_min_facturacion,
                           current_filter_max_precio=filter_max_precio,
                           actividades_dict=ACTIVIDADES_Y_SECTORES # **Añadido: Pasar actividades_dict**
                           )


# Ruta para publicar un anuncio
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        nombre = request.form['nombre']
        email_contacto = request.form['email_contacto']
        actividad = request.form['actividad']
        sector = request.form['sector']
        pais = request.form['pais']
        ubicacion = request.form['ubicacion']
        tipo_negocio = request.form['tipo_negocio']
        descripcion = request.form['descripcion']
        
        # Validación de campos numéricos
        try:
            facturacion = Decimal(request.form['facturacion'])
            numero_empleados = int(request.form['numero_empleados'])
            local_propiedad = request.form['local_propiedad']
            resultado_antes_impuestos = Decimal(request.form['resultado_antes_impuestos'])
            deuda = Decimal(request.form['deuda'])
            precio_venta = Decimal(request.form['precio_venta'])
            
            if facturacion < 0 or numero_empleados < 0 or deuda < 0 or precio_venta < 0:
                flash('Los valores numéricos (facturación, empleados, deuda, precio) no pueden ser negativos.', 'danger')
                return redirect(request.url)
        except InvalidOperation:
            flash('Error: Formato numérico inválido en facturación, resultado, deuda o precio. Usa solo números.', 'danger')
            return redirect(request.url)
        except ValueError:
            flash('Error: El número de empleados debe ser un número entero válido.', 'danger')
            return redirect(request.url)

        # Manejo de la imagen
        imagen_file = request.files.get('imagen')
        imagen_gcs_filename = None
        imagen_url = None

        if imagen_file and imagen_file.filename != '':
            if not allowed_file(imagen_file.filename):
                flash('Tipo de archivo de imagen no permitido.', 'danger')
                return redirect(request.url)
            
            # Verificar tamaño del archivo (opcional, pero buena práctica)
            imagen_file.seek(0, os.SEEK_END)
            file_size = imagen_file.tell()
            imagen_file.seek(0) # Vuelve al inicio del archivo
            
            if file_size > MAX_IMAGE_SIZE:
                flash(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024*1024):.0f}MB.', 'danger')
                return redirect(request.url)
            
            imagen_gcs_filename, imagen_url = upload_to_gcs(imagen_file)
            if not imagen_gcs_filename:
                flash('Error al subir la imagen a Google Cloud Storage. Inténtelo de nuevo.', 'danger')
                return redirect(request.url)
        else:
            flash('Es necesario subir una imagen para el anuncio.', 'danger')
            return redirect(request.url) # La imagen es obligatoria en la publicación

        conn = get_db_connection()
        if conn is None:
            return redirect(url_for('publicar')) # Redirigir para mostrar el flash message

        cur = conn.cursor()
        edit_token = str(uuid.uuid4()) # Generar un token único para edición

        try:
            cur.execute("""
                INSERT INTO empresas (
                    nombre, email_contacto, actividad, sector, ubicacion, pais,
                    tipo_negocio, descripcion, facturacion, numero_empleados,
                    local_propiedad, resultado_antes_impuestos, deuda, precio_venta,
                    imagen_gcs_filename, imagen_url, token_edicion
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                nombre, email_contacto, actividad, sector, ubicacion, pais,
                tipo_negocio, descripcion, facturacion, numero_empleados,
                local_propiedad, resultado_antes_impuestos, deuda, precio_venta,
                imagen_gcs_filename, imagen_url, edit_token
            ))
            conn.commit()

            # Enviar email al anunciante con el enlace de edición
            edit_link = url_for('editar', edit_token=edit_token, _external=True)
            send_email(
                f"Tu anuncio de '{nombre}' en Pyme Market ha sido publicado.",
                f"Hola,\n\nTu anuncio para '{nombre}' ha sido publicado con éxito en Pyme Market.\n\nPuedes editar o eliminar tu anuncio en cualquier momento visitando este enlace:\n{edit_link}\n\nGracias por usar Pyme Market.",
                email_contacto
            )

            # Enviar email al administrador
            send_email(
                f"Nuevo anuncio publicado en Pyme Market: {nombre}",
                f"Se ha publicado un nuevo anuncio:\n\nNombre: {nombre}\nEmail de Contacto: {email_contacto}\nEnlace de Edición (Admin): {edit_link}",
                EMAIL_DESTINO
            )

            flash('Anuncio publicado correctamente. Revisa tu email para el enlace de edición.', 'success')
            return redirect(url_for('publicar')) # O redirigir a la página de éxito
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Error al publicar anuncio: {e}")
            flash('Ocurrió un error al publicar el anuncio. Inténtelo de nuevo.', 'danger')
            # Si la publicación falla, intenta eliminar la imagen de GCS para evitar orfandad
            if imagen_gcs_filename:
                delete_from_gcs(imagen_gcs_filename)
                app.logger.info(f"Imagen {imagen_gcs_filename} revertida de GCS debido a error de publicación.")
            return redirect(request.url)
        finally:
            cur.close()
            conn.close()

    return render_template('publicar.html', actividades=actividades_list, provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)

# Ruta para ver detalles de un anuncio
@app.route('/negocio/<int:empresa_id>', methods=['GET', 'POST'])
def detalle(empresa_id):
    conn = get_db_connection()
    if conn is None:
        return redirect(url_for('index'))

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa is None:
        flash('Anuncio no encontrado.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        nombre_interesado = request.form['nombre_interesado']
        email_interesado = request.form['email_interesado']
        mensaje_interesado = request.form['mensaje_interesado']

        subject = f"Interesado en el negocio: {empresa['nombre']} (ID: {empresa['id']})"
        body = f"""
        Hola {empresa['nombre']},

        Un posible comprador está interesado en tu negocio '{empresa['nombre']}'.

        Detalles del Interesado:
        Nombre: {nombre_interesado}
        Email: {email_interesado}
        Mensaje:
        {mensaje_interesado}

        Por favor, contacta con el interesado directamente.

        Saludos,
        Equipo de Pyme Market
        """
        if send_email(subject, body, empresa['email_contacto']):
            flash('Tu mensaje ha sido enviado al anunciante.', 'success')
        else:
            flash('Error al enviar tu mensaje. Inténtalo de nuevo más tarde.', 'danger')
        return redirect(url_for('detalle', empresa_id=empresa_id))

    return render_template('detalle.html', empresa=empresa)

# Ruta para editar o eliminar un anuncio
@app.route('/editar/<string:edit_token>', methods=['GET', 'POST'])
def editar(edit_token):
    conn = get_db_connection()
    if conn is None:
        return redirect(url_for('index'))

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
    empresa = cur.fetchone()

    if empresa is None:
        cur.close()
        conn.close()
        flash('Anuncio no encontrado o token de edición inválido.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # --- Lógica de Eliminación ---
        if request.form.get('eliminar') == 'true':
            try:
                # 1. Obtener el nombre del archivo de la imagen actual antes de eliminar el registro
                #    Esto es CRÍTICO. Necesitamos el nombre del archivo para GCS.
                #    Ya tenemos 'empresa' cargada, así que podemos usarla directamente.
                filename_to_delete = empresa['imagen_gcs_filename']
                
                # 2. Eliminar el registro de la base de datos PRIMERO (o dentro de una transacción)
                #    Luego, si la DB tiene éxito, eliminamos la imagen.
                cur.execute("DELETE FROM empresas WHERE token_edicion = %s", (edit_token,))
                conn.commit()

                # 3. Eliminar la imagen de GCS SOLO SI LA DB TUVO ÉXITO
                if filename_to_delete:
                    delete_from_gcs(filename_to_delete)
                
                flash('Anuncio eliminado correctamente.', 'success')
                return redirect(url_for('index')) # Redirigir a la página principal

            except Exception as e:
                conn.rollback() # Revertir la DB si algo falla
                app.logger.error(f"Error al eliminar anuncio o imagen de GCS: {e}")
                flash('Ocurrió un error al intentar eliminar el anuncio.', 'danger')
                # No redirigir a index, volver a la página de edición para ver el error
                return redirect(request.url)
            finally:
                cur.close()
                conn.close()
            # Si se intentó eliminar, no se debe continuar con la lógica de edición
            # Esta línea es alcanzada si la eliminación fue exitosa o falló
            # y ya se manejó la redirección o el mensaje flash.
            # Por seguridad, si llega aquí sin redirección previa, puede ser un error.
            # La línea 'return redirect(request.url)' o 'return redirect(url_for('index'))'
            # dentro del try/except ya maneja esto.

        # --- Lógica de Edición (si no es una solicitud de eliminación) ---
        else:
            nombre = request.form['nombre']
            email_contacto = request.form['email_contacto']
            actividad = request.form['actividad']
            sector = request.form['sector']
            pais = request.form['pais']
            ubicacion = request.form['ubicacion']
            tipo_negocio = request.form['tipo_negocio']
            descripcion = request.form['descripcion']
            
            try:
                facturacion = Decimal(request.form['facturacion'])
                numero_empleados = int(request.form['numero_empleados'])
                local_propiedad = request.form['local_propiedad']
                resultado_antes_impuestos = Decimal(request.form['resultado_antes_impuestos'])
                deuda = Decimal(request.form['deuda'])
                precio_venta = Decimal(request.form['precio_venta'])

                if facturacion < 0 or numero_empleados < 0 or deuda < 0 or precio_venta < 0:
                    flash('Los valores numéricos (facturación, empleados, deuda, precio) no pueden ser negativos.', 'danger')
                    return redirect(request.url)
            except InvalidOperation:
                conn.rollback() # Asegurar rollback en caso de error en la validación numérica
                flash('Error: Datos numéricos inválidos. Por favor, asegúrate de que los valores monetarios y de números sean válidos.', 'danger')
                return redirect(request.url) # Añadido return para detener la ejecución
            except ValueError:
                conn.rollback() # Asegurar rollback
                flash('Error: El número de empleados debe ser un número entero válido.', 'danger')
                return redirect(request.url) # Añadido return para detener la ejecución


            # Manejo de la nueva imagen
            new_image_file = request.files.get('imagen')
            current_imagen_gcs_filename = empresa['imagen_gcs_filename'] # Obtener el nombre actual
            current_imagen_url = empresa['imagen_url'] # Obtener la URL actual

            # Si se ha subido una nueva imagen
            if new_image_file and new_image_file.filename != '':
                if not allowed_file(new_image_file.filename):
                    flash('Tipo de archivo de imagen no permitido.', 'danger')
                    return redirect(request.url)
                
                new_image_file.seek(0, os.SEEK_END)
                file_size = new_image_file.tell()
                new_image_file.seek(0)
                
                if file_size > MAX_IMAGE_SIZE:
                    flash(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024*1024):.0f}MB.', 'danger')
                    return redirect(request.url)

                new_filename_gcs, new_signed_url = upload_to_gcs(new_image_file)
                if not new_filename_gcs:
                    flash('Error al subir la nueva imagen a Google Cloud Storage.', 'danger')
                    return redirect(request.url)
                
                # Si la subida de la nueva imagen fue exitosa, la asignamos
                imagen_gcs_filename_to_update = new_filename_gcs
                imagen_url_to_update = new_signed_url

                # Eliminar la imagen antigua de GCS si existía y se subió una nueva
                if current_imagen_gcs_filename:
                    delete_from_gcs(current_imagen_gcs_filename)
                    app.logger.info(f"Antigua imagen {current_imagen_gcs_filename} eliminada de GCS al subir una nueva.")
            else:
                # No se subió una nueva imagen, mantener la existente
                imagen_gcs_filename_to_update = current_imagen_gcs_filename
                imagen_url_to_update = current_imagen_url
                # Nota: Si el usuario borra la imagen desde el navegador pero no sube una nueva,
                # la lógica actual mantendrá la imagen antigua. Si se desea permitir "borrar la imagen sin reemplazar",
                # se necesitaría un checkbox o lógica adicional para eso.

            try:
                cur.execute("""
                    UPDATE empresas SET
                        nombre = %s, email_contacto = %s, actividad = %s, sector = %s,
                        ubicacion = %s, pais = %s, tipo_negocio = %s, descripcion = %s,
                        facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                        resultado_antes_impuestos = %s, deuda = %s, precio_venta = %s,
                        imagen_gcs_filename = %s, imagen_url = %s
                    WHERE token_edicion = %s
                """, (
                    nombre, email_contacto, actividad, sector, ubicacion, pais,
                    tipo_negocio, descripcion, facturacion, numero_empleados,
                    local_propiedad, resultado_antes_impuestos, deuda, precio_venta,
                    imagen_gcs_filename_to_update, imagen_url_to_update, edit_token
                ))
                conn.commit()
                flash('Anuncio actualizado correctamente.', 'success')
                # Recargar la empresa para que la plantilla muestre los cambios
                cur.execute("SELECT * FROM empresas WHERE token_edicion = %s", (edit_token,))
                empresa = cur.fetchone()
                # Redirigir para evitar problemas de reenvío de formulario y actualizar la URL firmada de la imagen si cambió
                return redirect(url_for('editar', edit_token=edit_token))

            except InvalidOperation:
                conn.rollback()
                flash('Error: Datos numéricos inválidos. Por favor, asegúrate de que los valores monetarios y de números sean válidos.', 'danger')
            except Exception as e:
                conn.rollback()
                app.logger.error(f"Error al actualizar anuncio: {e}")
                flash('Ocurrió un error al intentar actualizar el anuncio.', 'danger')
            finally:
                pass # El cursor y la conexión se cierran al final de la ruta GET si no hay POST

    # Lógica GET para mostrar el formulario (o si el POST falló y no hubo redirección)
    # Se debe recargar la empresa si hubo un POST fallido para que la vista muestre los datos actuales
    # Si la empresa fue eliminada, el redirect de arriba ya se encarga.
    # Si fue una edición que falló, empresa ya contiene los datos originales que cargamos al inicio.
    if conn and not conn.closed: # Asegurarse de que la conexión esté abierta antes de intentar cerrar
        cur.close()
        conn.close() # Se cierran aquí si no hay POST o si el POST ya manejó el cierre

    # Si se llegó aquí después de un POST fallido, 'empresa' ya está cargada.
    # Si es un GET inicial, se carga al principio de la función.
    # Asegúrate de que la URL de la imagen sea válida/actualizada para la vista.
    if empresa and empresa['imagen_gcs_filename']:
        # Regenerar la URL firmada si es necesario para asegurar que no caduque.
        # Esto es importante para el GET si la página se mantiene abierta mucho tiempo.
        # En el POST exitoso, ya se actualiza si se subió una nueva imagen.
        # Si no se subió una nueva, la URL_actual es la que se usará.
        # Es mejor regenerar la URL aquí para cada carga GET de la página de edición,
        # para que la imagen siempre se vea si la URL firmada anterior ha caducado.
        empresa['imagen_url'] = generate_signed_url(empresa['imagen_gcs_filename'])

    return render_template('editar.html', empresa=empresa,
                           actividades=actividades_list,
                           provincias=PROVINCIAS_ESPANA,
                           actividades_dict=ACTIVIDADES_Y_SECTORES)


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
    # **CAMBIO:** Deshabilitar debug=True para producción.
    # Render (o cualquier servidor WSGI de producción) controlará el host y el puerto.
    app.run()
