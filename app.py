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

# Variables de entorno para GCS
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')
GCP_SERVICE_ACCOUNT_KEY_JSON = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')

storage_client = None # Inicializar a None por defecto
try:
    if CLOUD_STORAGE_BUCKET and GCP_SERVICE_ACCOUNT_KEY_JSON:
        credentials_json = GCP_SERVICE_ACCOUNT_KEY_JSON
        #print(f"DEBUG GCS Init: Longitud de GCP_SERVICE_ACCOUNT_KEY_JSON: {len(credentials_json) if credentials_json else 0}") # Depuración
        try:
            credentials_dict = json.loads(credentials_json)
            storage_client = storage.Client.from_service_account_info(credentials_dict)
            #print("Google Cloud Storage client initialized successfully from environment variable.") # Comentado
        except json.JSONDecodeError as jde:
            print(f"ERROR GCS Init: No se pudo parsear GCP_SERVICE_ACCOUNT_KEY_JSON. Error: {jde}")
            storage_client = None
        except Exception as e:
            print(f"ERROR GCS Init: Error inesperado al inicializar con from_service_account_info: {e}")
            storage_client = None
    elif CLOUD_STORAGE_BUCKET:
        #print("CLOUD_STORAGE_BUCKET is set, but GCP_SERVICE_ACCOUNT_KEY_JSON is not. Attempting default credentials.") # Comentado
        storage_client = storage.Client()
    else:
        print("Google Cloud Storage bucket name not set. GCS functions will be skipped.")
        #print(f"DEBUG GCS Init: storage_client is initialized: {storage_client is not None}") # Depuración
        pass
    if not CLOUD_STORAGE_BUCKET:
        #print("DEBUG GCS Init: CLOUD_STORAGE_BUCKET no está definido.") # Depuración
        pass

except Exception as e:
    storage_client = None
    print(f"ERROR GCS Init: Error general al inicializar Google Cloud Storage client: {e}")
    print("GCS functions will be skipped.")

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Constantes para la conexión a la base de datos
DB_NAME = os.environ.get('DB_NAME')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_HOST = os.environ.get('DB_HOST')
DB_PORT = os.environ.get('DB_PORT', '5432') # Puerto por defecto de PostgreSQL

# Configuración de los valores por defecto del formulario
DEFAULT_FORM_VALUES = {
    'nombre_empresa': '', 'direccion': '', 'poblacion': '', 'provincia': '',
    'codigo_postal': '', 'telefono': '', 'email': '', 'sitio_web': '',
    'actividad_principal': '', 'actividades_secundarias': [], 'sector': '',
    'servicios': '', 'productos': '', 'descripcion': '', 'logo_url': '',
    'imagen_empresa_url': '', 'horario_apertura': '', 'horario_cierre': '',
    'dias_semana': [], 'redes_sociales_facebook': '',
    'redes_sociales_twitter': '', 'redes_sociales_linkedin': '',
    'redes_sociales_instagram': '', 'persona_contacto': '',
    'cargo_contacto': '', 'email_contacto': '', 'telefono_contacto': '',
    'cumplimiento_rgpd': False, 'acuerdo_terminos': False
}

# Configuración de los tipos de archivo permitidos para la subida
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Constantes para el token de administración
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

# Configuración de las credenciales de correo electrónico para el envío de formularios
EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() == 'true'

# Funciones de utilidad para el envío de correo electrónico
def send_email(subject, body, to_email):
    """Envía un correo electrónico."""
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_HOST_USER
    msg['To'] = to_email
    #print(f"DEBUG Email: Subject: {msg['Subject']}") # Depuración
    #print(f"DEBUG Email: To: {msg['To']}") # Depuración

    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            if EMAIL_USE_TLS:
                server.starttls()
            server.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
            server.send_message(msg)
        return True
    except socket.gaierror as e:
        print(f"ERROR Email: Error de dirección del servidor SMTP: {e}. Comprueba EMAIL_HOST.")
        return False
    except smtplib.SMTPAuthenticationError as e:
        print(f"ERROR Email: Error de autenticación SMTP: {e}. Comprueba EMAIL_HOST_USER y EMAIL_HOST_PASSWORD.")
        return False
    except Exception as e:
        print(f"ERROR Email: Error al enviar correo electrónico: {e}")
        return False

# Funciones de utilidad para Google Cloud Storage
def allowed_file(filename):
    """Verifica si la extensión del archivo está permitida."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Funciones de utilidad para Google Cloud Storage
def upload_to_gcs(file_stream, filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        # print("DEBUG GCS Upload: GCS client not initialized or bucket name not set. Skipping GCS upload.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        file_stream.seek(0) # Rebobinar el stream
        blob.upload_from_file(file_stream)
        # print(f"DEBUG GCS Upload: File {filename} uploaded to GCS.")
        return filename
    except Exception as e:
        print(f"ERROR GCS Upload: Error uploading {filename} to GCS: {e}")
        return None

# Función de utilidad para obtener una URL firmada de GCS
def generate_signed_url(filename, expiration_minutes=60):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        print("ERROR GCS URL: GCS client not initialized or bucket name not set. Cannot generate signed URL.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        # Generar una URL firmada que expira en 'expiration_minutes'
        # Esto es útil si las imágenes no son de acceso público
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiration_minutes),
            method="GET"
        )
        return url
    except Exception as e:
        print(f"ERROR GCS URL: Error generating signed URL for {filename}: {e}")
        return None


# Configuración de la conexión a la base de datos (PostgreSQL)
def get_db_connection():
    """Establece y devuelve una conexión a la base de datos."""
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    #print("DEBUG DB: Conexión a la base de datos establecida.") # Comentado
    return conn

# Función para obtener un token único para URL de administración
def generate_admin_token():
    return str(uuid.uuid4())

# Cargar actividades y sectores desde un archivo JSON (u otra fuente)
try:
    with open('data/actividades_y_sectores.json', 'r', encoding='utf-8') as f:
        ACTIVIDADES_Y_SECTORES = json.load(f)
except FileNotFoundError:
    print("ERROR: El archivo 'data/actividades_y_sectores.json' no se encontró.")
    ACTIVIDADES_Y_SECTORES = {}
except json.JSONDecodeError:
    print("ERROR: Error al decodificar 'data/actividades_y_sectores.json'. Asegúrate de que el formato JSON sea válido.")
    ACTIVIDADES_Y_SECTORES = {}


# --- RUTAS DE LA APLICACIÓN ---

# Ruta principal (home)
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    query = "SELECT * FROM empresas WHERE active = TRUE"
    params = []

    # Filtrar por provincia
    provincia_filtro = request.args.get('provincia')
    if provincia_filtro and provincia_filtro != 'Todas':
        query += " AND provincia = %s"
        params.append(provincia_filtro)
    
    # Filtrar por actividad
    actividad_filtro = request.args.get('actividad')
    if actividad_filtro and actividad_filtro != 'Todas':
        # Búsqueda que incluya actividad principal y actividades secundarias (si existe como texto separado por comas)
        # Utilizamos ILIKE para búsqueda insensible a mayúsculas/minúsculas y '%' para coincidencia parcial
        query += " AND (actividad_principal ILIKE %s OR actividades_secundarias ILIKE %s)"
        params.append(f"%{actividad_filtro}%")
        params.append(f"%{actividad_filtro}%") # Duplicamos para el segundo ILIKE

    # Filtrar por sector
    sector_filtro = request.args.get('sector')
    if sector_filtro and sector_filtro != 'Todos':
        query += " AND sector = %s"
        params.append(sector_filtro)

    # Filtrar por palabra clave en nombre o descripción
    busqueda_texto = request.args.get('busqueda')
    if busqueda_texto:
        query += " AND (nombre_empresa ILIKE %s OR descripcion ILIKE %s)"
        params.append(f"%{busqueda_texto}%")
        params.append(f"%{busqueda_texto}%")
    
    query += " ORDER BY fecha_publicacion DESC"

    #print(f"DEBUG Index Query: {query} con params: {params}") # Comentado
    cur.execute(query, params)
    empresas_db = cur.fetchall()
    cur.close()
    conn.close()

    empresas = []
    for empresa in empresas_db:
        empresa_dict = dict(empresa)
        if empresa_dict.get('logo_filename'):
            empresa_dict['logo_url'] = generate_signed_url(empresa_dict['logo_filename'])
        if empresa_dict.get('imagen_empresa_filename'):
            empresa_dict['imagen_empresa_url'] = generate_signed_url(empresa_dict['imagen_empresa_filename'])
        empresas.append(empresa_dict)

    # Preparar listas para los filtros en el template
    actividades_list = sorted(list(ACTIVIDADES_Y_SECTORES.keys()))
    #print(f"DEBUG Index Query: actividades_list antes del filtro: {actividades_list_temp}") # Depuración
    # Si actividad_filtro está presente en la lista, lo movemos al principio para mostrarlo primero
    if actividad_filtro and actividad_filtro != 'Todas' and actividad_filtro in actividades_list:
        actividades_list.remove(actividad_filtro)
        actividades_list.insert(0, actividad_filtro)
    #print(f"DEBUG Index Query: actividades_list después del filtro: {actividades_list}") # Depuración") # Depuración

    sectores_list = sorted(list(set(s for a in ACTIVIDADES_Y_SECTORES.values() for s in a)))
    #print(f"DEBUG Index Query: sect_filtros: {sectores_list}") # Depuración

    # Las provincias de España como una lista
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
    PROVINCIAS_ESPANA = sorted(PROVINCIAS_ESPANA) # Ordenar alfabéticamente

    return render_template('index.html', empresas=empresas, actividades=actividades_list, sectores=sectores_list, actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

# Ruta para la página de detalle de la empresa
@app.route('/empresa/<int:empresa_id>')
def detalle_empresa(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa:
        empresa_dict = dict(empresa)
        if empresa_dict.get('logo_filename'):
            empresa_dict['logo_url'] = generate_signed_url(empresa_dict['logo_filename'])
        if empresa_dict.get('imagen_empresa_filename'):
            empresa_dict['imagen_empresa_url'] = generate_signed_url(empresa_dict['imagen_empresa_filename'])
        return render_template('detalle_empresa.html', empresa=empresa_dict)
    else:
        flash('Empresa no encontrada.', 'danger')
        return redirect(url_for('index'))

# Ruta para el formulario de registro de empresa
@app.route('/registrar-empresa', methods=['GET', 'POST'])
def registrar_empresa():
    form_data = DEFAULT_FORM_VALUES.copy() # Usar una copia para no modificar el original
    if request.method == 'POST':
        # Obtener datos del formulario
        form_data.update(request.form.to_dict())

        # Manejo de casillas de verificación (checkboxes) que no se envían si no están marcadas
        form_data['cumplimiento_rgpd'] = 'cumplimiento_rgpd' in request.form
        form_data['acuerdo_terminos'] = 'acuerdo_terminos' in request.form
        form_data['dias_semana'] = request.form.getlist('dias_semana') # Obtener lista de días seleccionados

        # Obtener la IP del cliente (para auditoría o registro)
        ip_address = request.remote_addr
        #print(f"DEBUG Submit: IP: {ip_address}, Data: {form_data}") # Depuración

        # Validaciones del lado del servidor
        errors = {}
        if not form_data['nombre_empresa']: errors['nombre_empresa'] = 'El nombre de la empresa es obligatorio.'
        if not form_data['email'] or '@' not in form_data['email']: errors['email'] = 'El email es obligatorio y debe ser válido.'
        if not form_data['actividad_principal']: errors['actividad_principal'] = 'La actividad principal es obligatoria.'
        if not form_data['sector']: errors['sector'] = 'El sector es obligatorio.'
        if not form_data['cumplimiento_rgpd']: errors['cumplimiento_rgpd'] = 'Debe aceptar la política de privacidad.'
        if not form_data['acuerdo_terminos']: errors['acuerdo_terminos'] = 'Debe aceptar los términos y condiciones.'

        # Validar formato del teléfono y código postal si se proporcionan
        if form_data['telefono'] and not form_data['telefono'].isdigit():
            errors['telefono'] = 'El teléfono debe contener solo números.'
        if form_data['codigo_postal'] and (not form_data['codigo_postal'].isdigit() or len(form_data['codigo_postal']) != 5):
            errors['codigo_postal'] = 'El código postal debe ser numérico y de 5 dígitos.'

        # Validación personalizada: Verificar si la actividad principal seleccionada corresponde al sector
        actividad_seleccionada = form_data['actividad_principal']
        sector_seleccionado = form_data['sector']

        if actividad_seleccionada and sector_seleccionado:
            # Asegúrate de que ACTIVIDADES_Y_SECTORES esté cargado y sea accesible
            sectores_para_actividad = ACTIVIDADES_Y_SECTORES.get(actividad_seleccionada, [])
            if sector_seleccionado not in sectores_para_actividad:
                errors['actividad_principal'] = 'La actividad principal no corresponde al sector seleccionado.'


        if errors:
            for field, msg in errors.items():
                flash(msg, 'danger')
            return render_template('registrar_empresa.html', form_data=form_data, errores=errors, actividades_y_sectores=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


        # Manejo de subida de archivos (logo e imagen de empresa)
        logo_filename = None
        imagen_empresa_filename = None

        # Subida del logo
        if 'logo' in request.files and request.files['logo'].filename != '':
            file = request.files['logo']
            if allowed_file(file.filename):
                #print(f"DEBUG Submit: Archivo {file.filename} permitido.") # Depuración
                unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
                logo_filename = unique_filename
                upload_to_gcs(file, logo_filename)
            else:
                flash('Tipo de archivo de logo no permitido.', 'danger')
                return render_template('registrar_empresa.html', form_data=form_data, actividades_y_sectores=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)
        #else:
            #print("DEBUG Submit: No se recibió archivo para subir.") # Depuración

        # Subida de imagen de empresa
        if 'imagen_empresa' in request.files and request.files['imagen_empresa'].filename != '':
            file = request.files['imagen_empresa']
            if allowed_file(file.filename):
                unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
                imagen_empresa_filename = unique_filename
                upload_to_gcs(file, imagen_empresa_filename)
            else:
                flash('Tipo de archivo de imagen de empresa no permitido.', 'danger')
                return render_template('registrar_empresa.html', form_data=form_data, actividades_y_sectores=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

        conn = get_db_connection()
        cur = conn.cursor()
        
        insert_query = """
        INSERT INTO empresas (
            nombre_empresa, direccion, poblacion, provincia, codigo_postal, telefono, email, sitio_web,
            actividad_principal, actividades_secundarias, sector, servicios, productos, descripcion,
            logo_filename, imagen_empresa_filename, horario_apertura, horario_cierre, dias_semana,
            redes_sociales_facebook, redes_sociales_twitter, redes_sociales_linkedin, redes_sociales_instagram,
            persona_contacto, cargo_contacto, email_contacto, telefono_contacto,
            cumplimiento_rgpd, acuerdo_terminos, active
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE
        ) RETURNING id;
        """
        
        # Unir actividades_secundarias y dias_semana si son listas
        actividades_secundarias_str = ','.join(form_data['actividades_secundarias']) if isinstance(form_data['actividades_secundarias'], list) else form_data['actividades_secundarias']
        dias_semana_str = ','.join(form_data['dias_semana']) if isinstance(form_data['dias_semana'], list) else form_data['dias_semana']

        cur.execute(insert_query, (
            form_data['nombre_empresa'], form_data['direccion'], form_data['poblacion'], form_data['provincia'],
            form_data['codigo_postal'], form_data['telefono'], form_data['email'], form_data['sitio_web'],
            form_data['actividad_principal'], actividades_secundarias_str, form_data['sector'], form_data['servicios'],
            form_data['productos'], form_data['descripcion'], logo_filename, imagen_empresa_filename,
            form_data['horario_apertura'], form_data['horario_cierre'], dias_semana_str,
            form_data['redes_sociales_facebook'], form_data['redes_sociales_twitter'],
            form_data['redes_sociales_linkedin'], form_data['redes_sociales_instagram'],
            form_data['persona_contacto'], form_data['cargo_contacto'], form_data['email_contacto'],
            form_data['telefono_contacto'], form_data['cumplimiento_rgpd'], form_data['acuerdo_terminos']
        ))
        empresa_id = cur.fetchone()[0] # Obtener el ID de la empresa insertada
        conn.commit()
        cur.close()
        conn.close()

        flash('Empresa registrada con éxito. ¡Gracias!', 'success')
        return redirect(url_for('detalle_empresa', empresa_id=empresa_id))
    
    # Si es GET request o hay errores
    return render_template('registrar_empresa.html', form_data=form_data, actividades_y_sectores=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


# Ruta para editar empresa (accesible solo desde admin)
@app.route('/editar-empresa/<int:empresa_id>', methods=['GET', 'POST'])
def editar_empresa(empresa_id):
    admin_token = request.args.get('admin_token')
    if admin_token != ADMIN_TOKEN:
        return "Acceso denegado. Se requiere token de administrador.", 403

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        # Actualizar datos de la empresa
        form_data = request.form.to_dict()
        form_data['cumplimiento_rgpd'] = 'cumplimiento_rgpd' in request.form
        form_data['acuerdo_terminos'] = 'acuerdo_terminos' in request.form
        form_data['dias_semana'] = request.form.getlist('dias_semana')

        # Manejo de subida de archivos (logo e imagen de empresa)
        logo_filename = form_data.get('current_logo_filename')
        imagen_empresa_filename = form_data.get('current_imagen_empresa_filename')

        if 'logo' in request.files and request.files['logo'].filename != '':
            file = request.files['logo']
            if allowed_file(file.filename):
                unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
                logo_filename = unique_filename
                upload_to_gcs(file, logo_filename)
            else:
                flash('Tipo de archivo de logo no permitido.', 'danger')
                return redirect(url_for('editar_empresa', empresa_id=empresa_id, admin_token=admin_token))
        
        if 'imagen_empresa' in request.files and request.files['imagen_empresa'].filename != '':
            file = request.files['imagen_empresa']
            if allowed_file(file.filename):
                unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
                imagen_empresa_filename = unique_filename
                upload_to_gcs(file, imagen_empresa_filename)
            else:
                flash('Tipo de archivo de imagen de empresa no permitido.', 'danger')
                return redirect(url_for('editar_empresa', empresa_id=empresa_id, admin_token=admin_token))

        # Unir actividades_secundarias y dias_semana si son listas
        actividades_secundarias_str = ','.join(form_data['actividades_secundarias']) if isinstance(form_data['actividades_secundarias'], list) else form_data['actividades_secundarias']
        dias_semana_str = ','.join(form_data['dias_semana']) if isinstance(form_data['dias_semana'], list) else form_data['dias_semana']

        update_query = """
        UPDATE empresas SET
            nombre_empresa = %s, direccion = %s, poblacion = %s, provincia = %s, codigo_postal = %s,
            telefono = %s, email = %s, sitio_web = %s, actividad_principal = %s,
            actividades_secundarias = %s, sector = %s, servicios = %s, productos = %s, descripcion = %s,
            logo_filename = %s, imagen_empresa_filename = %s, horario_apertura = %s, horario_cierre = %s,
            dias_semana = %s, redes_sociales_facebook = %s, redes_sociales_twitter = %s,
            redes_sociales_linkedin = %s, redes_sociales_instagram = %s,
            persona_contacto = %s, cargo_contacto = %s, email_contacto = %s, telefono_contacto = %s,
            cumplimiento_rgpd = %s, acuerdo_terminos = %s
        WHERE id = %s;
        """
        cur.execute(update_query, (
            form_data['nombre_empresa'], form_data['direccion'], form_data['poblacion'], form_data['provincia'],
            form_data['codigo_postal'], form_data['telefono'], form_data['email'], form_data['sitio_web'],
            form_data['actividad_principal'], actividades_secundarias_str, form_data['sector'], form_data['servicios'],
            form_data['productos'], form_data['descripcion'], logo_filename, imagen_empresa_filename,
            form_data['horario_apertura'], form_data['horario_cierre'], dias_semana_str,
            form_data['redes_sociales_facebook'], form_data['redes_sociales_twitter'],
            form_data['redes_sociales_linkedin'], form_data['redes_sociales_instagram'],
            form_data['persona_contacto'], form_data['cargo_contacto'], form_data['email_contacto'],
            form_data['telefono_contacto'], form_data['cumplimiento_rgpd'], form_data['acuerdo_terminos'],
            empresa_id
        ))
        conn.commit()
        cur.close()
        conn.close()
        flash('Empresa actualizada con éxito.', 'success')
        return redirect(url_for('admin', admin_token=admin_token))

    # GET request: Cargar datos existentes
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa:
        form_data = dict(empresa)
        # Convertir cadenas de lista a listas reales
        if isinstance(form_data.get('actividades_secundarias'), str) and form_data['actividades_secundarias']:
            form_data['actividades_secundarias'] = form_data['actividades_secundarias'].split(',')
        else:
            form_data['actividades_secundarias'] = []
        if isinstance(form_data.get('dias_semana'), str) and form_data['dias_semana']:
            form_data['dias_semana'] = form_data['dias_semana'].split(',')
        else:
            form_data['dias_semana'] = []
        
        # Obtener URL firmadas para logos/imágenes existentes
        if form_data.get('logo_filename'):
            form_data['logo_url'] = generate_signed_url(form_data['logo_filename'])
        if form_data.get('imagen_empresa_filename'):
            form_data['imagen_empresa_url'] = generate_signed_url(form_data['imagen_empresa_filename'])

        return render_template('editar_empresa.html', empresa=form_data, admin_token=admin_token, actividades_y_sectores=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)
    else:
        flash('Empresa no encontrada.', 'danger')
        return redirect(url_for('admin', admin_token=admin_token))

# Ruta para cambiar el estado 'active' de una empresa
@app.route('/toggle-active/<int:empresa_id>', methods=['POST'])
def toggle_active(empresa_id):
    admin_token = request.args.get('admin_token') # O request.form.get si viene por formulario POST
    if admin_token != ADMIN_TOKEN:
        return "Acceso denegado. Se requiere token de administrador.", 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE empresas SET active = NOT active WHERE id = %s RETURNING active", (empresa_id,))
    new_status = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    flash(f'Estado de la empresa actualizado a {"Activa" if new_status else "Inactiva"}.', 'success')
    return redirect(url_for('admin', admin_token=admin_token))

# Ruta para eliminar empresa
@app.route('/eliminar-empresa/<int:empresa_id>', methods=['POST'])
def eliminar_empresa(empresa_id):
    admin_token = request.args.get('admin_token') # O request.form.get si viene por formulario POST
    if admin_token != ADMIN_TOKEN:
        return "Acceso denegado. Se requiere token de administrador.", 403
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Empresa eliminada con éxito.', 'success')
    return redirect(url_for('admin', admin_token=admin_token))


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


# Ruta de administración (necesita un token para ser accesible)
@app.route('/admin')
def admin():
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        #print(f"WARNING Admin: Intento de acceso no autorizado a /admin con token: {token}") # Depuración # Comentado
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
