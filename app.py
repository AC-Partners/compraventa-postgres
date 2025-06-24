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

# IMPORTACIÓN PARA LOGGING
import logging

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# --- Configuración del logger global ---
logger = logging.getLogger(__name__)
# Para producción, establece el nivel a INFO. Los mensajes DEBUG no se mostrarán.
# Para depuración local, podrías cambiarlo a logging.DEBUG.
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
# ---------------------------------------

# --- PROCESADOR DE CONTEXTO GLOBAL DE JINJA2 ---
# Esta función inyectará 'current_year' en todas las plantillas automáticamente.
@app.context_processor
def inject_global_variables():
    """Inyecta variables globales como el año actual en todas las plantillas."""
    return dict(current_year=datetime.now().year)

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Asegúrate de que estas variables de entorno están configuradas en Render
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Inicializar el cliente de Cloud Storage
storage_client = None # Inicializar a None por defecto
try:
    if CLOUD_STORAGE_BUCKET and os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'):
        credentials_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"GCS Init: Longitud de GCP_SERVICE_ACCOUNT_KEY_JSON: {len(credentials_json) if credentials_json else 0}")
        try:
            credentials_dict = json.loads(credentials_json)
            storage_client = storage.Client.from_service_account_info(credentials_dict)
            logger.info("Google Cloud Storage client initialized successfully from environment variable.")
        except json.JSONDecodeError as jde:
            logger.error(f"GCS Init: No se pudo parsear GCP_SERVICE_ACCOUNT_KEY_JSON. Error: {jde}")
            storage_client = None
        except Exception as e:
            logger.error(f"GCS Init: Error inesperado al inicializar con from_service_account_info: {e}")
            storage_client = None
    elif CLOUD_STORAGE_BUCKET:
        logger.warning("CLOUD_STORAGE_BUCKET está configurado, pero GCP_SERVICE_ACCOUNT_KEY_JSON no. Intentando credenciales por defecto.")
        storage_client = storage.Client()
    else:
        logger.warning("El nombre del bucket de Google Cloud Storage no está configurado. Las funciones de GCS serán omitidas.")

    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"GCS Init: storage_client está inicializado: {storage_client is not None}")
    if not CLOUD_STORAGE_BUCKET:
        logger.debug("GCS Init: CLOUD_STORAGE_BUCKET no está definido.")

except Exception as e:
    storage_client = None
    logger.error(f"GCS Init: Error general al inicializar Google Cloud Storage client: {e}")
    logger.error("Las funciones de GCS serán omitidas debido al error general de inicialización.")


# Funciones de utilidad para Google Cloud Storage
def upload_to_gcs(file_stream, filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS Upload: Cliente GCS no inicializado o nombre de bucket no configurado. Omitiendo la subida a GCS.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        file_stream.seek(0) # Rebobinar el stream
        blob.upload_from_file(file_stream)
        logger.info(f"GCS Upload: Archivo {filename} subido a GCS.")
        return filename
    except Exception as e:
        logger.error(f"GCS Upload: Error al subir {filename} a GCS: {e}")
        return None

def generate_signed_url(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS URL: Cliente GCS no inicializado o nombre de bucket no configurado. No se puede generar URL firmada.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        # La URL firmada será válida por 7 días
        url = blob.generate_signed_url(expiration=timedelta(days=7), version='v4')
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"GCS URL: URL firmada generada para {filename}.")
        return url
    except Exception as e:
        logger.error(f"GCS URL: Error al generar URL firmada para {filename}: {e}")
        return None

def delete_from_gcs(filename):
    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"GCS Delete: Intentando eliminar de GCS el archivo: {filename}")
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS Delete: Cliente GCS no inicializado o nombre de bucket no configurado. Omitiendo la eliminación de GCS.")
        return
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        if blob.exists():
            blob.delete()
            logger.info(f"GCS Delete: Archivo {filename} eliminado de GCS.")
        else:
            logger.warning(f"GCS Delete: Archivo {filename} no encontrado en GCS. No se necesita eliminación.")
    except Exception as e:
        logger.error(f"GCS Delete: Error al eliminar {filename} de GCS: {e}")

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE LA BASE DE DATOS (PostgreSQL)
# ---------------------------------------------------------------

# Accede a la URL de conexión de la base de datos desde las variables de entorno de Render
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    # Convertido a debug. No se mostrará en INFO.
    logger.debug("DB: Conexión a la base de datos establecida.")
    return conn

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE LA BASE DE DATOS
# ---------------------------------------------------------------

# Cargar actividades y sectores desde el archivo JSON
# Esto debería ser un archivo que subas con tu código.
try:
    with open('actividades_sectores.json', 'r', encoding='utf-8') as f:
        ACTIVIDADES_Y_SECTORES = json.load(f)
except FileNotFoundError:
    logger.error("Error: actividades_sectores.json no encontrado.")
    ACTIVIDADES_Y_SECTORES = {} # Asegura que esté definido incluso si falla

# Provincias de España (pueden cargarse desde un archivo o definirse aquí)
PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila", "Badajoz", "Barcelona",
    "Bizkaia", "Burgos", "Cáceres", "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba", "Cuenca",
    "Gipuzkoa", "Girona", "Granada", "Guadalajara", "Huelva", "Huesca", "Illes Balears", "Jaén", "La Rioja",
    "Las Palmas", "León", "Lleida", "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense", "Palencia",
    "Pontevedra", "Salamanca", "Santa Cruz de Tenerife", "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel",
    "Toledo", "Valencia", "Valladolid", "Zamora", "Zaragoza", "Ceuta", "Melilla" # Añadí Ceuta y Melilla por si acaso
]

# Configurar el locale para el formato de moneda si es necesario
# Esto debe hacerse antes de cualquier operación de formato numérico que use locale.
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8') # Para sistemas Linux/Unix
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Spanish_Spain.1252') # Para sistemas Windows
    except locale.Error:
        logger.warning("No se pudo configurar el locale 'es_ES.UTF-8' o 'Spanish_Spain.1252'. El formato de moneda podría no ser el esperado.")


# Configuración del token de administrador
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'default-admin-token') # Usar una variable de entorno para esto es más seguro

# Define una lista de extensiones de archivo permitidas para las imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Ruta principal de la aplicación: muestra una lista de empresas
@app.route('/')
def index():
    conn = None # Inicializa conn a None
    cur = None # Inicializa cur a None
    empresas_con_urls = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) # Para acceder a las columnas por nombre
        cur.execute("SELECT * FROM empresas WHERE active = TRUE ORDER BY fecha_publicacion DESC")
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"Index Query: SELECT * FROM empresas WHERE active = TRUE ORDER BY fecha_publicacion DESC con params: []")
        empresas = cur.fetchall()

        for empresa in empresas:
            empresa_dict = dict(empresa) # Convierte el Row object a un diccionario
            # Formatear el valor del negocio a moneda española
            try:
                # Asegúrate de que valor_negocio sea Decimal antes de formatear
                valor_decimal = Decimal(empresa_dict['valor_negocio'])
                empresa_dict['valor_negocio_formato'] = locale.currency(valor_decimal, grouping=True, symbol=True)
            except (InvalidOperation, TypeError):
                empresa_dict['valor_negocio_formato'] = "Valor no disponible"
                logger.warning(f"Error al formatear el valor_negocio para la empresa ID {empresa_dict.get('id', 'N/A')}")

            # Generar URL firmada para la imagen
            if empresa_dict['imagen_filename']:
                image_url = generate_signed_url(empresa_dict['imagen_filename'])
                empresa_dict['imagen_url'] = image_url
            else:
                empresa_dict['imagen_url'] = url_for('static', filename='default_logo.png') # O una imagen por defecto
            empresas_con_urls.append(empresa_dict)

    except psycopg2.Error as e:
        logger.error(f"Error de base de datos en /: {e}")
        flash('Error al cargar los datos. Por favor, inténtelo de nuevo más tarde.', 'error')
    except Exception as e:
        logger.error(f"Error inesperado en /: {e}")
        flash('Se ha producido un error inesperado. Por favor, inténtelo de nuevo más tarde.', 'error')
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return render_template('index.html', empresas=empresas_con_urls)


# Ruta para publicar un nuevo anuncio de empresa
@app.route('/publicar', methods=('GET', 'POST'))
def publicar():
    if request.method == 'POST':
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"Publicar POST: Recibida solicitud POST con datos de formulario: {request.form}")

        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        try:
            # Eliminar puntos de miles y reemplazar coma decimal por punto
            valor_negocio_str = request.form['valor_negocio'].replace('.', '').replace(',', '.')
            valor_negocio = Decimal(valor_negocio_str)
        except InvalidOperation:
            flash('El valor del negocio no es un número válido.', 'error')
            return redirect(url_for('publicar'))
        contacto_email = request.form['contacto_email']
        contacto_telefono = request.form.get('contacto_telefono')
        actividad_principal = request.form['actividad_principal']
        provincia = request.form['provincia']
        password = request.form['password'] # Contraseña para editar/eliminar
        token_privado = str(uuid.uuid4()) # Generar un UUID único para la edición/eliminación

        imagen_filename = None
        if 'imagen' in request.files:
            imagen = request.files['imagen']
            if imagen and allowed_file(imagen.filename):
                extension = imagen.filename.rsplit('.', 1)[1].lower()
                unique_filename = str(uuid.uuid4()) + '.' + extension
                imagen_filename = unique_filename
                upload_success = upload_to_gcs(imagen, imagen_filename)
                if not upload_success:
                    flash('Error al subir la imagen a Cloud Storage.', 'error')
                    logger.error(f"Error al subir imagen {imagen_filename} durante la publicación.")
                    return redirect(url_for('publicar'))
                # Convertido a debug. No se mostrará en INFO.
                logger.debug(f"Publicar POST: Archivo subido: {imagen.filename} como {imagen_filename}")
            else:
                flash('Tipo de archivo de imagen no permitido o archivo no seleccionado.', 'warning')
                # Convertido a debug. No se mostrará en INFO.
                logger.debug("Publicar POST: No se subió ninguna imagen o el tipo no es permitido.")
        else:
            # Convertido a debug. No se mostrará en INFO.
            logger.debug("Publicar POST: No se subió ninguna imagen.")


        conn = None
        cur = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO empresas (nombre, descripcion, valor_negocio, contacto_email, contacto_telefono, actividad_principal, provincia, password, token_privado, imagen_filename) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (nombre, descripcion, valor_negocio, contacto_email, contacto_telefono, actividad_principal, provincia, password, token_privado, imagen_filename)
            )
            empresa_id = cur.fetchone()[0]
            conn.commit()
            # Convertido a debug. No se mostrará en INFO.
            logger.debug(f"Publicar POST: Datos para insertar en DB: {request.form.to_dict()} (con imagen: {imagen_filename})")
            logger.info(f"Publicar POST: Insertando nueva empresa con ID: {empresa_id}") # Convertido a info

            flash('¡Anuncio publicado con éxito!', 'success')
            flash(f'Guarda este token para editar o eliminar tu anuncio: <strong>{token_privado}</strong>', 'info')
            return redirect(url_for('index'))
        except psycopg2.Error as e:
            conn.rollback() # Deshace la transacción en caso de error
            logger.error(f"Publicar POST: Error al insertar la empresa en la DB: {e}")
            flash('Error al publicar el anuncio. Por favor, inténtelo de nuevo.', 'error')
        except Exception as e:
            conn.rollback()
            logger.error(f"Publicar POST: Error inesperado durante la publicación: {e}")
            flash('Se ha producido un error inesperado al publicar. Por favor, inténtelo de nuevo.', 'error')
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    # Si es GET request o si hubo un error en POST, renderiza el formulario
    actividades_list = sorted(list(ACTIVIDADES_Y_SECTORES.keys()))
    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"Publicar GET: Actividades cargadas: {actividades_list}")
    return render_template('publicar.html', actividades=actividades_list, sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

# Ruta para ver los detalles de un negocio específico
@app.route('/negocio/<int:empresa_id>')
def negocio(empresa_id):
    conn = None
    cur = None
    empresa = None
    image_url = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"Negocio: Buscando negocio con ID: {empresa_id}")
        empresa = cur.fetchone()

        if empresa:
            # Convertido a debug. No se mostrará en INFO.
            logger.debug(f"Negocio: Negocio encontrado: {dict(empresa)}")
            # Formatear el valor del negocio a moneda española
            try:
                valor_decimal = Decimal(empresa['valor_negocio'])
                empresa['valor_negocio_formato'] = locale.currency(valor_decimal, grouping=True, symbol=True)
            except (InvalidOperation, TypeError):
                empresa['valor_negocio_formato'] = "Valor no disponible"
                logger.warning(f"Error al formatear el valor_negocio para la empresa ID {empresa_id}")

            # Generar URL firmada para la imagen si existe
            if empresa['imagen_filename']:
                image_url = generate_signed_url(empresa['imagen_filename'])
                if not image_url:
                    logger.warning(f"Negocio: No se pudo generar URL firmada para {empresa['imagen_filename']}. Usando imagen por defecto.")
                    image_url = url_for('static', filename='default_logo.png') # Fallback
                # Convertido a debug. No se mostrará en INFO.
                logger.debug(f"Negocio: URL de la imagen: {image_url}")
            else:
                image_url = url_for('static', filename='default_logo.png') # Imagen por defecto
                # Convertido a debug. No se mostrará en INFO.
                logger.debug("Negocio: No se encontró la imagen o no se pudo generar la URL.")
        else:
            flash('Anuncio no encontrado.', 'warning')
            # Convertido a warning.
            logger.warning(f"Negocio: Anuncio no encontrado con ID: {empresa_id}")
            return redirect(url_for('index'))

    except psycopg2.Error as e:
        logger.error(f"Error de base de datos en /negocio/{empresa_id}: {e}")
        flash('Error al cargar el anuncio. Por favor, inténtelo de nuevo más tarde.', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error inesperado en /negocio/{empresa_id}: {e}")
        flash('Se ha producido un error inesperado. Por favor, inténtelo de nuevo más tarde.', 'error')
        return redirect(url_for('index'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template('negocio.html', empresa=empresa, image_url=image_url)


# Ruta para editar un anuncio existente
@app.route('/editar/<uuid:token_privado>', methods=('GET', 'POST'))
def editar(token_privado):
    conn = None
    cur = None
    empresa = None
    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"Editar GET: Intentando editar empresa con token: {token_privado}")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE token_privado = %s", (str(token_privado),))
        empresa = cur.fetchone()

        if empresa is None:
            flash('Token de edición no válido o anuncio no encontrado.', 'error')
            # Convertido a warning.
            logger.warning(f"Editar GET: Empresa no encontrada para editar con token: {token_privado}")
            return redirect(url_for('index'))

        if request.method == 'POST':
            # Convertido a debug. No se mostrará en INFO.
            logger.debug(f"Editar POST: Recibida solicitud POST para editar empresa con token: {token_privado}")
            nombre = request.form['nombre']
            descripcion = request.form['descripcion']
            try:
                valor_negocio_str = request.form['valor_negocio'].replace('.', '').replace(',', '.')
                valor_negocio = Decimal(valor_negocio_str)
            except InvalidOperation:
                flash('El valor del negocio no es un número válido.', 'error')
                return redirect(url_for('editar', token_privado=token_privado))
            contacto_email = request.form['contacto_email']
            contacto_telefono = request.form.get('contacto_telefono')
            actividad_principal = request.form['actividad_principal']
            provincia = request.form['provincia']
            current_password = request.form['current_password'] # Contraseña actual para verificación
            new_password = request.form.get('new_password') # Nueva contraseña (opcional)

            # Verificar la contraseña actual
            if current_password != empresa['password']:
                flash('Contraseña actual incorrecta.', 'error')
                return redirect(url_for('editar', token_privado=token_privado))

            imagen_filename = empresa['imagen_filename'] # Mantener la imagen existente por defecto
            if 'imagen' in request.files:
                imagen = request.files['imagen']
                if imagen and allowed_file(imagen.filename):
                    # Convertido a debug. No se mostrará en INFO.
                    logger.debug(f"Editar POST: Archivo subido: {imagen.filename}")
                    # Eliminar imagen antigua si existe y es diferente a la nueva
                    if imagen_filename:
                        delete_from_gcs(imagen_filename)

                    extension = imagen.filename.rsplit('.', 1)[1].lower()
                    unique_filename = str(uuid.uuid4()) + '.' + extension
                    imagen_filename = unique_filename
                    upload_success = upload_to_gcs(imagen, imagen_filename)
                    if not upload_success:
                        flash('Error al subir la nueva imagen a Cloud Storage.', 'error')
                        logger.error(f"Error al subir nueva imagen {imagen_filename} durante la edición.")
                        return redirect(url_for('editar', token_privado=token_privado))
                elif imagen.filename == '':
                    # No se subió una nueva imagen (campo vacío), mantener la existente.
                    # Convertido a debug. No se mostrará en INFO.
                    logger.debug("Editar POST: No se subió nueva imagen (campo vacío), manteniendo la existente.")
                    pass
                else:
                    flash('Tipo de archivo de imagen no permitido.', 'warning')
                    return redirect(url_for('editar', token_privado=token_privado))
            else:
                # Convertido a debug. No se mostrará en INFO.
                logger.debug("Editar POST: No se subió nueva imagen.")

            # Preparar los datos para la actualización
            update_data = {
                'nombre': nombre,
                'descripcion': descripcion,
                'valor_negocio': valor_negocio,
                'contacto_email': contacto_email,
                'contacto_telefono': contacto_telefono,
                'actividad_principal': actividad_principal,
                'provincia': provincia,
                'imagen_filename': imagen_filename
            }
            if new_password: # Si se proporcionó una nueva contraseña, actualizarla
                update_data['password'] = new_password
            else: # Si no, mantener la actual
                update_data['password'] = current_password

            # Convertido a debug. No se mostrará en INFO.
            logger.debug(f"Editar POST: Datos de formulario para actualizar: {update_data}")

            # Construir la consulta UPDATE dinámicamente
            set_clauses = [f"{key} = %s" for key in update_data.keys()]
            query = f"UPDATE empresas SET {', '.join(set_clauses)} WHERE token_privado = %s"
            params = list(update_data.values()) + [str(token_privado)]

            cur.execute(query, params)
            conn.commit()
            # Convertido a debug. No se mostrará en INFO.
            logger.debug(f"Editar POST: Ejecutando UPDATE para empresa con token: {token_privado}")
            logger.info(f"Editar POST: Empresa con token {token_privado} actualizada correctamente.")

            flash('Anuncio actualizado con éxito.', 'success')
            return redirect(url_for('index'))

        # Si es GET request, o después de POST fallido, renderiza el formulario
        actividades_list = sorted(list(ACTIVIDADES_Y_SECTORES.keys()))
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"Editar GET: Actividades cargadas: {actividades_list}")
        return render_template('editar.html', empresa=empresa, actividades=actividades_list,
                               sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES,
                               provincias=PROVINCIAS_ESPANA)

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"ERROR Editar POST: Error al actualizar la empresa en la DB: {e}")
        flash('Error al actualizar el anuncio. Por favor, inténtelo de nuevo.', 'error')
        return redirect(url_for('editar', token_privado=token_privado))
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"ERROR Editar POST: Error inesperado en la ruta de edición con token {token_privado}: {e}")
        flash('Se ha producido un error inesperado al editar. Por favor, inténtelo de nuevo.', 'error')
        return redirect(url_for('editar', token_privado=token_privado))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# Ruta para eliminar un anuncio
@app.route('/eliminar/<uuid:token_privado>', methods=('POST',))
def eliminar(token_privado):
    conn = None
    cur = None
    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"Eliminar: Solicitud de eliminación para token: {token_privado}")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Primero, obtener el nombre del archivo de la imagen para eliminarlo de GCS
        cur.execute("SELECT id, imagen_filename FROM empresas WHERE token_privado = %s", (str(token_privado),))
        empresa_a_eliminar = cur.fetchone()

        if empresa_a_eliminar:
            imagen_filename = empresa_a_eliminar['imagen_filename']
            empresa_id = empresa_a_eliminar['id']

            # Eliminar el registro de la base de datos
            cur.execute("DELETE FROM empresas WHERE token_privado = %s", (str(token_privado),))
            conn.commit()
            logger.info(f"Eliminar: Empresa con ID {empresa_id} eliminada de la DB.")

            # Eliminar la imagen de Google Cloud Storage si existe
            if imagen_filename:
                # Convertido a debug. No se mostrará en INFO.
                logger.debug(f"Eliminar: Archivo de imagen asociado: {imagen_filename}")
                delete_from_gcs(imagen_filename) # Llama a la función de utilidad GCS

            flash('Anuncio eliminado con éxito.', 'success')
        else:
            flash('Token de eliminación no válido o anuncio no encontrado.', 'error')
            logger.warning(f"Eliminar: No se encontró la empresa para eliminar con token: {token_privado}")

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"ERROR Eliminar: Error al eliminar la empresa de la DB con token {token_privado}: {e}")
        flash('Error al eliminar el anuncio. Por favor, inténtelo de nuevo.', 'error')
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"ERROR Eliminar: Error inesperado al eliminar la empresa con token {token_privado}: {e}")
        flash('Se ha producido un error inesperado al eliminar. Por favor, inténtelo de nuevo.', 'error')
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return redirect(url_for('index'))


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
    if token != ADMIN_TOKEN:
        logger.warning(f"Admin: Intento de acceso no autorizado a /admin con token: {token}") # Convertido a warning
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
    # Este bloque solo se ejecuta cuando corres el script directamente (ej. python app.py)
    # y no cuando se usa un servidor WSGI como Gunicorn en producción.
    # Para despliegues en producción (Render), el servidor WSGI es quien inicia la aplicación.
    # Puedes usar este bloque para ejecutar un servidor de desarrollo local con debug=True si lo necesitas.
    port = int(os.environ.get('PORT', 5000))
    # logger.debug(f"DEBUG App: Iniciando la aplicación Flask en el puerto {port} (modo desarrollo).")
    # app.run(host='0.0.0.0', port=port, debug=True) # debug=True para desarrollo local
    logger.info(f"Aplicación Flask preparada para ser iniciada por un servidor WSGI en el puerto {port}.")
    # Si quieres ejecutar directamente con Flask en local, descomenta la línea de app.run()
    # app.run(host='0.0.0.0', port=port, debug=True)
