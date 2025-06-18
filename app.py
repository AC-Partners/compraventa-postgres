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
# Intentará cargar las credenciales desde la variable de entorno GCP_SERVICE_ACCOUNT_KEY
# en un entorno de Render, o desde el archivo de credenciales local en desarrollo.
try:
    # Intenta cargar las credenciales desde la variable de entorno para Render
    # Si CLOUD_STORAGE_BUCKET no está configurado, asume que no estamos en Render y no inicializa el cliente de GCS
    if CLOUD_STORAGE_BUCKET and os.environ.get('GCP_SERVICE_ACCOUNT_KEY'):
        # Decodificar el JSON de la variable de entorno
        credentials_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY')
        credentials_dict = json.loads(credentials_json)
        storage_client = storage.Client.from_service_account_info(credentials_dict)
        print("Google Cloud Storage client initialized successfully from environment variable.")
    elif CLOUD_STORAGE_BUCKET:
        # Esto podría ocurrir si CLOUD_STORAGE_BUCKET está, pero GCP_SERVICE_ACCOUNT_KEY no.
        # En un entorno local, GCS client podría intentar usar ADC.
        print("CLOUD_STORAGE_BUCKET is set, but GCP_SERVICE_ACCOUNT_KEY is not. Attempting default credentials.")
        storage_client = storage.Client()
    else:
        storage_client = None
        print("Google Cloud Storage bucket name not set. GCS functions will be skipped.")
except Exception as e:
    storage_client = None
    print(f"Error initializing Google Cloud Storage client: {e}")
    print("GCS functions will be skipped.")

# Funciones de utilidad para Google Cloud Storage
def upload_to_gcs(file_stream, filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("GCS client not initialized or bucket name not set. Skipping GCS upload.")
        return None
    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    # Reset stream position to the beginning before uploading
    file_stream.seek(0)
    blob.upload_from_file(file_stream)
    print(f"File {filename} uploaded to GCS.")
    # No es necesario devolver la URL pública ya que usaremos URLs firmadas
    return filename # Retorna el nombre del archivo en GCS

def generate_signed_url(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("GCS client not initialized or bucket name not set. Cannot generate signed URL.")
        return None
    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    # Genera una URL firmada que expira en 7 días
    # Requiere que el cliente de almacenamiento se inicialice con credenciales que tengan permisos para firmar URLs
    # (por ejemplo, una cuenta de servicio).
    url = blob.generate_signed_url(expiration=timedelta(days=7), version='v4')
    return url

def delete_from_gcs(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("GCS client not initialized or bucket name not set. Skipping GCS deletion.")
        return
    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    if blob.exists():
        blob.delete()
        print(f"File {filename} deleted from GCS.")
    else:
        print(f"File {filename} not found in GCS. No deletion needed.")

# -------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# -------------------------------------------------------------


# Configuración de la base de datos PostgreSQL
# Obtener las credenciales de la base de datos de las variables de entorno de Render.com
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_HOST = os.environ.get('DB_HOST')

def get_db_connection():
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        cursor_factory=psycopg2.extras.DictCursor # Esto permite acceder a las columnas por nombre
    )
    return conn

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
def euro_format_filter(value, decimal_places=2):
    try:
        # Configura la configuración regional a un valor que use la coma como separador decimal
        # 'es_ES' es común en España. Puede variar dependiendo del sistema operativo.
        # En algunos sistemas como Render (Linux), puede ser necesario 'es_ES.UTF-8' o similar
        locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
    except locale.Error:
        try:
            locale.setlocale(locale.LC_ALL, 'es_ES') # Intenta sin UTF-8 si falla
        except locale.Error:
            pass # Si falla, usa el formato por defecto o no aplica localización

    if value is None:
        return ""
    # Formatea el número a una cadena de moneda. Incluye el símbolo € explícitamente si el locale no lo añade.
    # El 'grouping=True' añade los separadores de miles.
    return locale.format_string(f"%.{decimal_places}f", value, grouping=True)


# TOKEN DE ADMINISTRADOR
# Este token DEBE establecerse como una variable de entorno en Render.com
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')


# Rutas de la aplicación
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Obtiene los parámetros de búsqueda del formulario
    provincia = request.args.get('provincia')
    pais = request.args.get('pais')
    actividad = request.args.get('actividad')
    sector = request.args.get('sector')

    # Construye la consulta SQL dinámicamente
    query = "SELECT * FROM empresas WHERE 1=1" # 'WHERE 1=1' es un truco para facilitar la adición de condiciones AND
    params = []

    if provincia and provincia != "Todas": # Asumiendo que "Todas" es el valor por defecto para no filtrar
        query += " AND ubicacion = %s" # Nombre de la columna en DB
        params.append(provincia)
    if pais and pais != "Todos":
        query += " AND pais = %s"
        params.append(pais)
    if actividad and actividad != "Todas":
        query += " AND actividad = %s"
        params.append(actividad)
    if sector and sector != "Todos":
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
    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES

    if request.method == 'POST':
        # Obtiene datos del formulario (campos de texto)
        nombre = request.form.get('nombre')
        email_contacto = request.form.get('email_contacto')
        actividad = request.form.get('actividad')
        sector = request.form.get('sector')
        pais = request.form.get('pais')
        ubicacion = request.form.get('ubicacion') # Ahora será una provincia de PROVINCIAS_ESPANA
        tipo_negocio = request.form.get('tipo_negocio') # Nuevo campo
        descripcion = request.form.get('descripcion')
        local_propiedad = request.form.get('local_propiedad')

        # Convertir a float/int, con manejo de errores si el valor es vacío o inválido
        try:
            # Usamos .get() con un valor por defecto None y luego convertimos
            facturacion = float(request.form.get('facturacion')) if request.form.get('facturacion') else None
            numero_empleados = int(request.form.get('numero_empleados')) if request.form.get('numero_empleados') else None
            resultado_antes_impuestos = float(request.form.get('resultado_antes_impuestos')) if request.form.get('resultado_antes_impuestos') else None
            deuda = float(request.form.get('deuda')) if request.form.get('deuda') else 0.0 # Valor por defecto 0
            precio_venta = float(request.form.get('precio_venta')) if request.form.get('precio_venta') else None
        except ValueError:
            flash('Por favor, introduce valores numéricos válidos para facturación, empleados, resultado, deuda y precio.', 'danger')
            # Pasa request.form para precargar datos si no se ha implementado en el front-end
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form) # Nota: esto precarga el formulario con los datos enviados


        acepto_condiciones = 'acepto_condiciones' in request.form
        imagen = request.files.get('imagen') # Obtener el objeto de archivo

        errores = []

        # Validaciones de los datos del formulario
        if not nombre:
            errores.append('El nombre de la empresa es obligatorio.')
        if not email_contacto or "@" not in email_contacto:
            errores.append('El email de contacto es obligatorio y debe ser válido.')
        if not actividad or actividad not in actividades_list:
            errores.append('Por favor, selecciona una actividad válida.')
        if not sector or (actividad and sector not in (actividades_dict.get(actividad, []))):
             # Solo valida el sector si la actividad no está vacía
            errores.append('Por favor, selecciona un sector válido para la actividad elegida.')
        if not pais:
            errores.append('El país es obligatorio.')
        if not ubicacion or ubicacion not in provincias_list:
            errores.append('Por favor, selecciona una provincia válida.')
        if not tipo_negocio:
            errores.append('El tipo de negocio es obligatorio.')
        if not descripcion:
            errores.append('La descripción del negocio es obligatoria.')
        if facturacion is None or facturacion < 0:
            errores.append('La facturación anual es obligatoria y debe ser un número no negativo.')
        if numero_empleados is None or numero_empleados < 0:
            errores.append('El número de empleados es obligatorio y debe ser un número no negativo.')
        if resultado_antes_impuestos is None: # Puede ser negativo, por eso no se valida < 0
            errores.append('El resultado antes de impuestos es obligatorio.')
        if deuda is None or deuda < 0:
            errores.append('La deuda actual es obligatoria y debe ser un número no negativo.')
        if precio_venta is None or precio_venta < 0:
            errores.append('El precio solicitado es obligatorio y debe ser un número no negativo.')
        if not acepto_condiciones:
            errores.append('Debes aceptar las condiciones de uso.')

        # Validación del archivo de imagen (tamaño, tipo)
        if imagen and imagen.filename:
            # Primero, rebobina el stream si ya ha sido leído (por ejemplo, para el tamaño)
            imagen.seek(0, os.SEEK_END)
            file_size = imagen.tell()
            imagen.seek(0) # Vuelve al principio para la subida

            if not allowed_file(imagen.filename):
                errores.append('Tipo de archivo de imagen no permitido. Solo se aceptan JPG, JPEG, PNG, GIF.')
            elif file_size > MAX_IMAGE_SIZE:
                errores.append(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024 * 1024):.1f} MB.')
        else:
            errores.append('La imagen es obligatoria.') # Se asume que la imagen es obligatoria para nuevos anuncios


        # Manejo de errores de validación
        if errores:
            for error in errores:
                flash(error, 'danger')
            # Si hay errores, renderiza la misma plantilla, pasando los datos del formulario actual
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)

        # Si no hay errores, procesar y guardar los datos
        try:
            imagen_url = None
            imagen_nombre_gcs = None
            if imagen and imagen.filename:
                filename = secure_filename(imagen.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1] # Genera un nombre único
                imagen_nombre_gcs = upload_to_gcs(imagen, unique_filename) # Sube la imagen a GCS
                if imagen_nombre_gcs: # Si la subida fue exitosa
                    imagen_url = generate_signed_url(imagen_nombre_gcs) # Obtiene la URL firmada

            # Generar un token de edición único para esta empresa
            edit_token = str(uuid.uuid4())

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO empresas (
                    nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio,
                    descripcion, facturacion, numero_empleados, local_propiedad,
                    resultado_antes_impuestos, deuda, precio_venta, imagen_nombre_gcs, imagen_url,
                    edit_token, fecha_publicacion, fecha_modificacion
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id;
            """, (
                nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio,
                descripcion, facturacion, numero_empleados, local_propiedad,
                resultado_antes_impuestos, deuda, precio_venta, imagen_nombre_gcs, imagen_url,
                edit_token
            ))
            empresa_id = cur.fetchone()[0] # Obtener el ID de la empresa recién insertada
            conn.commit()
            flash('¡Tu negocio ha sido publicado con éxito!', 'success')
            flash(f'Puedes editar tu anuncio en cualquier momento usando este enlace (guárdalo bien): {url_for("editar", edit_token=edit_token, _external=True)}', 'info')
            return redirect(url_for('publicar')) # Redirige para limpiar el formulario o a una página de confirmación

        except Exception as e:
            conn.rollback()
            flash(f'Error al publicar el negocio: {e}', 'danger')
            return render_template('vender_empresa.html',
                                   actividades=actividades_list,
                                   provincias=provincias_list,
                                   actividades_dict=actividades_dict,
                                   form_data=request.form)
        finally:
            if 'conn' in locals() and conn:
                cur.close()
                conn.close()

    return render_template('vender_empresa.html', actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


# Ruta para mostrar los detalles de una empresa
@app.route('/negocio/<int:empresa_id>')
def detalle(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
    # Usamos DictCursor para acceder a los resultados por nombre de columna
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Recuperar la empresa de la base de datos usando el token de edición
    cur.execute("SELECT * FROM empresas WHERE edit_token = %s", (edit_token,))
    empresa = cur.fetchone()

    if not empresa:
        flash('Anuncio no encontrado o token de edición inválido.', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('index'))

    # Preparar datos para los desplegables del formulario
    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    provincias_list = PROVINCIAS_ESPANA
    actividades_dict = ACTIVIDADES_Y_SECTORES

    if request.method == 'POST':
        # --- Lógica para ELIMINAR el anuncio ---
        # El formulario de editar.html envía un campo oculto 'eliminar'='true' cuando se confirma la eliminación.
        if request.form.get('eliminar') == 'true':
            try:
                # 1. Eliminar imagen de GCS si existe
                if empresa['imagen_nombre_gcs']:
                    delete_from_gcs(empresa['imagen_nombre_gcs'])
                    print(f"Imagen {empresa['imagen_nombre_gcs']} eliminada de GCS.")

                # 2. Eliminar registro de la base de datos
                cur.execute("DELETE FROM empresas WHERE edit_token = %s", (edit_token,))
                conn.commit()
                flash('Anuncio eliminado con éxito.', 'success')
                print(f"Anuncio con token {edit_token} eliminado de la base de datos.")
                # Redirigir a una página de confirmación o al inicio
                cur.close()
                conn.close()
                return redirect(url_for('publicar')) # O a url_for('index')
            except Exception as e:
                conn.rollback()
                flash(f'Error al eliminar el anuncio: {e}', 'danger')
                print(f"Error al eliminar anuncio con token {edit_token}: {e}")
                # En caso de error, volver a renderizar la página de edición
                cur.close()
                conn.close()
                return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


        # --- Lógica para ACTUALIZAR el anuncio (si no es una eliminación) ---
        nombre = request.form.get('nombre')
        email_contacto = request.form.get('email_contacto')
        actividad = request.form.get('actividad')
        sector = request.form.get('sector')
        pais = request.form.get('pais')
        ubicacion = request.form.get('ubicacion')
        tipo_negocio = request.form.get('tipo_negocio')
        descripcion = request.form.get('descripcion')
        local_propiedad = request.form.get('local_propiedad')

        # Manejo de valores numéricos
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
        imagen_nombre_gcs = empresa['imagen_nombre_gcs'] # Mantiene la imagen existente por defecto
        imagen_url = empresa['imagen_url'] # Mantiene la URL existente por defecto

        errores = []

        # Validaciones (puedes añadir más según tus necesidades)
        if not nombre: errores.append('El nombre de la empresa es obligatorio.')
        if not email_contacto or "@" not in email_contacto: errores.append('El email de contacto es obligatorio y debe ser válido.')
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
        
        # Validación de nueva imagen si se sube una
        if imagen_subida and imagen_subida.filename:
            # Rebobinar el stream para lectura de tamaño
            imagen_subida.seek(0, os.SEEK_END)
            file_size = imagen_subida.tell()
            imagen_subida.seek(0) # Volver al inicio para la subida

            if not allowed_file(imagen_subida.filename):
                errores.append('Tipo de archivo de imagen no permitido. Solo se aceptan JPG, JPEG, PNG, GIF.')
            elif file_size > MAX_IMAGE_SIZE:
                errores.append(f'La imagen excede el tamaño máximo permitido de {MAX_IMAGE_SIZE / (1024 * 1024):.1f} MB.')
        # Si no se sube nueva imagen y no hay imagen existente, es un error (asumiendo imagen obligatoria)
        elif not empresa['imagen_nombre_gcs']:
             errores.append('La imagen es obligatoria para el anuncio.')


        if errores:
            for error in errores:
                flash(error, 'danger')
            cur.close()
            conn.close()
            # Si hay errores, se renderiza la plantilla con los datos actuales de la empresa
            # Podrías pasar request.form aquí también si quieres que los datos que el usuario intentó enviar
            # se precarguen incluso con errores de validación, de manera similar a '/publicar'.
            # Por simplicidad, se usan los datos originales de 'empresa' si hay un error.
            return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

        try:
            # Si se subió una nueva imagen, procesarla
            if imagen_subida and imagen_subida.filename:
                # Eliminar la imagen antigua de GCS si existe
                if empresa['imagen_nombre_gcs']:
                    delete_from_gcs(empresa['imagen_nombre_gcs'])
                    print(f"Imagen antigua {empresa['imagen_nombre_gcs']} eliminada de GCS.")

                # Subir la nueva imagen
                filename_secure = secure_filename(imagen_subida.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename_secure)[1]
                imagen_nombre_gcs = upload_to_gcs(imagen_subida, unique_filename)
                if imagen_nombre_gcs: # Si la subida fue exitosa
                    imagen_url = generate_signed_url(imagen_nombre_gcs)
                else: # Si la subida a GCS falló por alguna razón, resetear a None
                    imagen_nombre_gcs = None
                    imagen_url = None
                    flash('No se pudo subir la nueva imagen.', 'warning')
            
            # Actualizar el registro de la base de datos
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, actividad = %s, sector = %s,
                    pais = %s, ubicacion = %s, tipo_negocio = %s, descripcion = %s,
                    facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    resultado_antes_impuestos = %s, deuda = %s, precio_venta = %s,
                    imagen_nombre_gcs = %s, imagen_url = %s,
                    fecha_modificacion = NOW()
                WHERE edit_token = %s
            """, (
                nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio,
                descripcion, facturacion, numero_empleados, local_propiedad,
                resultado_antes_impuestos, deuda, precio_venta,
                imagen_nombre_gcs, imagen_url, edit_token
            ))
            conn.commit()
            flash('Anuncio actualizado con éxito.', 'success')
            print(f"Anuncio con token {edit_token} actualizado en la base de datos.")

            # Después de la actualización exitosa, vuelve a obtener los datos actualizados de la empresa
            # para reflejar cualquier cambio (especialmente la URL de la imagen si se cambió)
            cur.execute("SELECT * FROM empresas WHERE edit_token = %s", (edit_token,))
            empresa_actualizada = cur.fetchone()
            cur.close()
            conn.close()
            return render_template('editar.html', empresa=empresa_actualizada, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

        except Exception as e:
            conn.rollback()
            flash(f'Error al actualizar el anuncio: {e}', 'danger')
            print(f"Error al actualizar anuncio con token {edit_token}: {e}")
            cur.close()
            conn.close()
            # En caso de error, volver a renderizar la página de edición
            return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)

    # Para la solicitud GET (cuando se carga la página por primera vez)
    cur.close()
    conn.close()
    return render_template('editar.html', empresa=empresa, actividades=actividades_list, provincias=provincias_list, actividades_dict=actividades_dict)


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
    # Obtiene el puerto del entorno o usa 5000 por defecto
    port = int(os.environ.get('PORT', 5000))
    # Para producción, NUNCA usar debug=True.
    # Flask desactiva el modo de depuración automáticamente en producción
    # si FLASK_ENV no está configurado como 'development'.
    # La configuración recomendada para producción es simplemente no incluir 'debug=True'.
    app.run(host='0.0.0.0', port=port)
