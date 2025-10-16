# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory, g
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
import requests 
import json 
import locale # Importa el módulo locale para formato numérico
import uuid 
from datetime import timedelta, datetime 
from decimal import Decimal, InvalidOperation
from functools import wraps 
from slugify import slugify 

# IMPORTACIONES PARA GOOGLE CLOUD STORAGE
from google.cloud import storage 

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# ==============================================================================
# --- CONFIGURACIÓN DE LOCALE Y FILTROS DE JINJA2 ---
# ==============================================================================

# Configura el locale para el formato de moneda (España, Euro)
# Nota: La codificación 'es_ES.UTF-8' puede fallar en Render/Linux. Usamos el alias.
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
except locale.Error:
    # Fallback para sistemas que no soportan es_ES.UTF-8 directamente (como Render)
    locale.setlocale(locale.LC_ALL, 'C') 

def format_euro(value):
    """Filtro de Jinja2 para formatear un número a formato de Euro (€)."""
    if value is None:
        return ""
    try:
        # Formatea el número con separadores de miles y el símbolo de Euro
        return locale.currency(value, symbol=True, grouping=True)
    except Exception:
        # Si el formato falla, devuelve el valor original con el símbolo de Euro
        return f"{value} €"

# Registra el filtro 'euro_format' en Jinja2
app.jinja_env.filters['euro_format'] = format_euro

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

def send_email_mailgun(to_email, subject, body, from_name="Contacto PyMeMarket"):
    """
    Envía un correo electrónico usando la API de Mailgun (a través de HTTPS/Puerto 443).
    """
    
    # Define la dirección de origen usando el dominio verificado
    from_address = f"{from_name} <info@{MAILGUN_DOMAIN}>" 
    
    # Solicitud HTTP POST a la API de Mailgun
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

    # Lanza una excepción para códigos de error HTTP (4xx o 5xx)
    response.raise_for_status() 
    return response

# ==============================================================================
# --- FIN DE LA FUNCIÓN DE MAILGUN ---
# ==============================================================================

# --- PROCESADOR DE CONTEXTO GLOBAL DE JINJA2 ---
# Esta función inyectará 'current_year' en todas las plantillas automáticamente.
@app.context_processor
def inject_global_variables():
    """Inyecta variables globales como el año actual en todas las plantillas."""
    return dict(current_year=datetime.now().year)

# ----------------- Funciones de Base de Datos -------------------

def get_db_connection():
    if 'db' not in g:
        try:
            database_url = os.environ.get('DATABASE_URL')
            if not database_url:
                raise ValueError("DATABASE_URL no está configurada")
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            g.db = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.DictCursor)
        except Exception as e:
            print(f"Error al conectar con la base de datos: {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# ----------------- Funciones de Google Cloud Storage -------------------

def get_gcs_client():
    if 'gcs_client' not in g:
        g.gcs_client = storage.Client()
    return g.gcs_client


def upload_to_gcs(file, filename, bucket_name):
    try:
        gcs_client = get_gcs_client()
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(filename)
        blob.upload_from_file(file, content_type=file.content_type)
        return blob.public_url # Retorna la URL pública del archivo
    except Exception as e:
        print(f"Error al subir el archivo a GCS: {e}")
        return None

# Función para generar la URL pública del archivo
def get_public_file_url(filename, bucket_name):
    try:
        gcs_client = get_gcs_client()
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(filename)
        if blob.exists():
             return f"https://storage.googleapis.com/{bucket_name}/{filename}"
        return None
    except Exception as e:
        print(f"Error al generar la URL pública de GCS: {e}")
        return None
    
# ----------------- Funciones de Autenticación -------------------

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = os.environ.get('ADMIN_TOKEN')
        user_token = request.args.get('admin_token')
        
        if not admin_token or admin_token != user_token:
            flash('Acceso denegado. Token de administrador inválido.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


# ----------------- Rutas de la Aplicación -------------------

@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC LIMIT 6")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('index.html', empresas=empresas)

@app.route('/empresas')
def lista_empresas():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY nombre")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('empresas.html', empresas=empresas)

@app.route('/empresa/<slug>')
def empresa_detalle(slug):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas WHERE slug = %s", (slug,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa is None:
        return render_template('404.html'), 404
    
    imagen_url = get_public_file_url(empresa['imagen_principal'], os.environ.get('GCS_BUCKET_NAME')) if empresa['imagen_principal'] else None

    return render_template('empresa_detalle.html', empresa=empresa, imagen_url=imagen_url)


@app.route('/contacto', methods=('GET', 'POST'))
def contacto():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        mensaje = request.form.get('mensaje')
        
        if not nombre or not email or not mensaje:
            flash('Por favor, rellena todos los campos.', 'danger')
            return redirect(url_for('contacto'))

        # --- LÓGICA DE ENVÍO DE CORREO USANDO LA API DE MAILGUN ---
        subject = f"Nuevo Contacto de {nombre}"
        body = f"Has recibido un mensaje de contacto a través de la web:\n\nNombre: {nombre}\nEmail: {email}\nMensaje:\n{mensaje}"
        
        try:
            send_email_mailgun(
                to_email="info@pymemarket.es", # Correo de destino
                subject=subject,
                body=body,
                from_name=nombre
            )
            
            flash('¡Mensaje enviado con éxito!', 'success')
            return redirect(url_for('contacto'))
        
        except requests.exceptions.RequestException as e:
            print(f"Error al enviar correo por API: {e}") 
            flash('Error al enviar el mensaje. Intente de nuevo más tarde.', 'danger')
            return redirect(url_for('contacto'))
        # --- FIN LÓGICA MAILGUN ---

    return render_template('contacto.html')


# RUTA ESPECÍFICA: Evita que /favicon.ico caiga en la ruta genérica.
@app.route('/favicon.ico')
def favicon():
    """Sirve el archivo favicon.ico directamente desde el directorio static."""
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/<nombre_ruta>')
def ruta_generica(nombre_ruta):
    # Intenta renderizar la plantilla con el mismo nombre que la ruta.
    try:
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
    urls.append({'loc': url_for('lista_empresas', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'weekly', 'priority': '0.8'})
    urls.append({'loc': url_for('contacto', _external=True), 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'monthly', 'priority': '0.5'})
    
    # Añadir rutas dinámicas de empresas
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT slug, last_modified FROM empresas ORDER BY last_modified DESC") 
        empresas = cur.fetchall()

        for empresa in empresas:
            lastmod_str = empresa['last_modified'].strftime('%Y-%m-%d') if empresa['last_modified'] else datetime.now().strftime('%Y-%m-%d')
            urls.append({
                'loc': url_for('empresa_detalle', slug=empresa['slug'], _external=True), 
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
        xml_content += f'        <lastmod>{url_data["lastmod"]}</lastmod>\n'
        xml_content += f'        <changefreq>{url_data["changefreq"]}</changefreq>\n'
        xml_content += f'        <priority>{url_data["priority"]}</priority>\n'
        xml_content += '    </url>\n'
    xml_content += '</urlset>'

    return Response(xml_content, mimetype='application/xml')

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
    return render_template('admin/admin_dashboard.html', empresas=empresas)

@app.route('/admin/crear', methods=('GET', 'POST'))
@admin_required
def crear_empresa():
    # Lógica para manejar GET y POST para crear una nueva empresa
    if request.method == 'POST':
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        actividad = request.form['actividad']
        sector = request.form['sector']
        imagen_principal = request.files.get('imagen_principal')
        
        # Generar slug automáticamente
        base_slug = slugify(nombre)
        slug = base_slug
        
        conn = get_db_connection()
        cur = conn.cursor()

        # Comprobar si el slug ya existe, añadir un sufijo si es necesario
        i = 1
        while True:
            cur.execute("SELECT COUNT(*) FROM empresas WHERE slug = %s", (slug,))
            if cur.fetchone()[0] == 0:
                break
            slug = f"{base_slug}-{i}"
            i += 1
        
        # Manejo de la subida de imagen a GCS
        imagen_filename = None
        if imagen_principal and imagen_principal.filename:
            extension = os.path.splitext(imagen_principal.filename)[1]
            unique_filename = str(uuid.uuid4()) + extension
            
            imagen_principal.seek(0)
            
            bucket_name = os.environ.get('GCS_BUCKET_NAME')
            if bucket_name:
                upload_status = upload_to_gcs(imagen_principal, unique_filename, bucket_name)
                if upload_status:
                    imagen_filename = unique_filename
                else:
                    flash('Error al subir la imagen a Google Cloud Storage.', 'danger')
                    conn.close()
                    return redirect(url_for('crear_empresa'))
            else:
                flash('Falta configurar el nombre del bucket de GCS.', 'danger')
                conn.close()
                return redirect(url_for('crear_empresa'))


        # Insertar la nueva empresa en la base de datos
        try:
            cur.execute(
                "INSERT INTO empresas (nombre, descripcion, actividad, sector, imagen_principal, slug, created_at, last_modified) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                (nombre, descripcion, actividad, sector, imagen_filename, slug)
            )
            conn.commit()
            flash(f'Empresa "{nombre}" creada con éxito!', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            conn.rollback()
            flash(f'Error de base de datos al crear empresa: {e}', 'danger')
            return redirect(url_for('crear_empresa'))
        finally:
            cur.close()
            conn.close()

    # Cargar actividades y sectores para el formulario
    try:
        with open('actividades_sectores.json', 'r', encoding='utf-8') as f:
            datos_json = json.load(f)
            actividades = sorted(datos_json.get("actividades", []))
            sectores = sorted(datos_json.get("sectores", []))
    except Exception as e:
        print(f"Error al cargar actividades_sectores.json: {e}")
        actividades = []
        sectores = []

    return render_template('admin/crear_empresa.html', actividades=actividades, sectores=sectores)


@app.route('/admin/editar/<int:id>', methods=('GET', 'POST'))
@admin_required
def editar_empresa(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Cargar actividades y sectores para el formulario
    try:
        with open('actividades_sectores.json', 'r', encoding='utf-8') as f:
            datos_json = json.load(f)
            actividades = sorted(datos_json.get("actividades", []))
            sectores = sorted(datos_json.get("sectores", []))
    except Exception as e:
        print(f"Error al cargar actividades_sectores.json: {e}")
        actividades = []
        sectores = []

    if request.method == 'POST':
        # Lógica para actualizar la empresa
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        actividad = request.form['actividad']
        sector = request.form['sector']
        imagen_principal_file = request.files.get('imagen_principal')
        
        # Obtener el nombre de archivo existente
        cur.execute("SELECT imagen_principal, slug FROM empresas WHERE id = %s", (id,))
        empresa_existente = cur.fetchone()
        imagen_filename = empresa_existente['imagen_principal']
        current_slug = empresa_existente['slug']

        # Regenerar slug solo si el nombre ha cambiado
        new_slug = current_slug
        base_slug = slugify(nombre)
        if base_slug != current_slug and not base_slug.startswith(current_slug):
            new_slug = base_slug
            i = 1
            while True:
                cur.execute("SELECT COUNT(*) FROM empresas WHERE slug = %s AND id != %s", (new_slug, id))
                if cur.fetchone()[0] == 0:
                    break
                new_slug = f"{base_slug}-{i}"
                i += 1

        # Manejo de la subida de imagen a GCS
        if imagen_principal_file and imagen_principal_file.filename:
            # Eliminar la imagen antigua si existe
            if imagen_filename:
                try:
                    gcs_client = get_gcs_client()
                    bucket = gcs_client.bucket(os.environ.get('GCS_BUCKET_NAME'))
                    blob = bucket.blob(imagen_filename)
                    if blob.exists():
                         blob.delete()
                except Exception as e:
                     print(f"Advertencia: No se pudo eliminar la imagen antigua de GCS: {e}")

            # Subir la nueva imagen
            extension = os.path.splitext(imagen_principal_file.filename)[1]
            unique_filename = str(uuid.uuid4()) + extension
            imagen_principal_file.seek(0)
            
            bucket_name = os.environ.get('GCS_BUCKET_NAME')
            if bucket_name:
                upload_status = upload_to_gcs(imagen_principal_file, unique_filename, bucket_name)
                if upload_status:
                    imagen_filename = unique_filename
                else:
                    flash('Error al subir la nueva imagen a Google Cloud Storage.', 'danger')
                    conn.close()
                    return redirect(url_for('editar_empresa', id=id))
            else:
                flash('Falta configurar el nombre del bucket de GCS.', 'danger')
                conn.close()
                return redirect(url_for('editar_empresa', id=id))
        
        # Actualizar la empresa en la base de datos
        try:
            cur.execute(
                "UPDATE empresas SET nombre = %s, descripcion = %s, actividad = %s, sector = %s, imagen_principal = %s, slug = %s, last_modified = NOW() WHERE id = %s",
                (nombre, descripcion, actividad, sector, imagen_filename, new_slug, id)
            )
            conn.commit()
            flash(f'Empresa "{nombre}" actualizada con éxito!', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            conn.rollback()
            flash(f'Error de base de datos al actualizar empresa: {e}', 'danger')
            return redirect(url_for('editar_empresa', id=id))
        finally:
            cur.close()
            conn.close()

    # Lógica para GET (mostrar el formulario de edición)
    cur.execute("SELECT * FROM empresas WHERE id = %s", (id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa is None:
        return render_template('404.html'), 404

    return render_template('admin/editar_empresa.html', empresa=empresa, actividades=actividades, sectores=sectores)


@app.route('/admin/eliminar/<int:id>', methods=('POST',))
@admin_required
def eliminar_empresa(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Obtener el nombre del archivo para eliminarlo de GCS
    try:
        cur.execute("SELECT imagen_principal FROM empresas WHERE id = %s", (id,))
        empresa = cur.fetchone()
        imagen_filename = empresa['imagen_principal'] if empresa else None
    except Exception:
        imagen_filename = None

    # 2. Eliminar el registro de la base de datos
    try:
        cur.execute("DELETE FROM empresas WHERE id = %s", (id,))
        conn.commit()
        flash('Empresa eliminada con éxito de la base de datos.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error al eliminar la empresa de la base de datos: {e}', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('admin'))
    finally:
        cur.close()
        conn.close()

    # 3. Eliminar la imagen de GCS (si existe)
    if imagen_filename:
        try:
            gcs_client = get_gcs_client()
            bucket = gcs_client.bucket(os.environ.get('GCS_BUCKET_NAME'))
            blob = bucket.blob(imagen_filename)
            if blob.exists():
                blob.delete()
            flash('Imagen eliminada de Google Cloud Storage.', 'info')
        except Exception as e:
            flash(f'Advertencia: Error al eliminar la imagen de GCS: {e}', 'warning')

    return redirect(url_for('admin'))


# Punto de entrada principal para ejecutar la aplicación Flask (BLOQUE ORIGINAL RESTAURADO)
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
