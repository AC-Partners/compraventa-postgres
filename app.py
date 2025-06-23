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

# Obtener el nombre del bucket de GCS desde las variables de entorno
BUCKET_NAME = os.environ.get('GCS_BUCKET_NAME')

if BUCKET_NAME is None:
    print("Error: La variable de entorno 'GCS_BUCKET_NAME' no está configurada.")
    exit(1)

# --- Configuración de Credenciales para Google Cloud Storage ---
# Cargar credenciales desde una variable de entorno si se despliega
# ¡IMPORTANTE: Usamos la variable que has confirmado: GCP_SERVICE_ACCOUNT_KEY_JSON!
if os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'):
    # Crear un archivo temporal para las credenciales JSON
    credentials_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
    temp_credentials_path = '/tmp/gcs_credentials.json' # Onde Render permite escritura temporal
    
    try:
        with open(temp_credentials_path, 'w') as f:
            f.write(credentials_json)
        # Establecer la variable de entorno que la librería de GCS espera
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = temp_credentials_path
        print("Credenciales de Google Cloud Storage cargadas desde variable de entorno.")
    except Exception as e:
        print(f"Error al crear archivo de credenciales temporal: {e}")
        exit(1) # Si no podemos escribir las credenciales, no podemos continuar

# Inicializar el cliente de Google Cloud Storage.
# La autenticación se maneja automáticamente si se despliega en Google Cloud
# (por ejemplo, Cloud Run, App Engine) o si la variable de entorno GOOGLE_APPLICATION_CREDENTIALS
# está configurada localmente.
try:
    storage_client = storage.Client()
    print("Google Cloud Storage client initialized successfully.")
except Exception as e:
    print(f"Error al inicializar el cliente de Google Cloud Storage: {e}")
    # Considera manejar este error de forma más elegante en un entorno de producción.
    exit(1)


# --------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# --------------------------------------------------------------

# --- Configuración de la base de datos ---
def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST"),
        database=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        port=os.environ.get("DB_PORT")
    )
    return conn

# --- Variables globales o de configuración ---
# Token de administrador (¡cambia esto por un valor seguro en producción!)
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'tu_token_seguro_aqui')

# Diccionario de actividades y sectores
# Esto podría cargarse desde un archivo JSON para mayor flexibilidad
ACTIVIDADES_Y_SECTORES = {
    "Tecnología": ["Software", "Hardware", "Consultoría IT", "E-commerce", "SaaS"],
    "Hostelería y Restauración": ["Restaurantes", "Bares", "Hoteles", "Catering", "Discotecas y Pubs"],
    "Comercio al por menor": ["Tiendas de ropa", "Supermercados", "Joyas", "Electrónica", "Librerías"],
    "Servicios profesionales": ["Consultoría", "Asesoría Legal", "Marketing Digital", "Formación", "Recursos Humanos"],
    "Salud y Bienestar": ["Clínicas", "Farmacias", "Gimnasios", "Centros de Estética", "Parafarmacia"],
    "Manufactura e Industria": ["Alimentos y Bebidas", "Textil", "Automotriz", "Maquinaria", "Química"],
    "Construcción e Inmobiliaria": ["Promoción Inmobiliaria", "Reformas", "Arquitectura", "Gestión de Propiedades", "Materiales de Construcción"],
    "Educación": ["Academias", "Escuelas Infantiles", "Plataformas Online", "Idiomas", "Refuerzo Escolar"],
    "Transporte y Logística": ["Transporte de mercancías", "Mensajería", "Logística de Almacenamiento", "Transporte de pasajeros", "Distribución"],
    "Agricultura y Pesca": ["Explotaciones Agrícolas", "Ganadería", "Pesca", "Silvicultura", "Productos Agrícolas"],
    "Energía y Medio Ambiente": ["Energías Renovables", "Gestión de Residuos", "Consultoría Ambiental", "Suministro Energético", "Eficiencia Energética"],
    "Entretenimiento y Ocio": ["Cines y Teatros", "Parques Temáticos", "Eventos", "Centros de Ocio Infantil", "Deportes"],
    "Comunicación y Medios": ["Editoriales", "Agencias de Publicidad", "Producción Audiovisual", "Radios y Televisiones", "Medios Digitales"],
    "Financiero y Seguros": ["Asesorías Financieras", "Corredurías de Seguros", "Inversión", "Banca", "Gestión de Activos"],
    "Automoción": ["Concesionarios", "Talleres mecánicos", "Alquiler de vehículos", "Lavaderos de coches", "Venta de recambios"],
    "Belleza y Estética": ["Peluquerías", "Centros de estética", "Spas", "Clínicas dentales estéticas", "Maquillaje profesional"],
    "Turismo": ["Agencias de viajes", "Tour operadores", "Alojamientos turísticos", "Guías turísticos", "Transporte turístico"],
    "Servicios a Domicilio": ["Limpieza", "Cuidado de mayores/niños", "Reparaciones", "Jardinería", "Comida a domicilio"]
}

PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Cantabria", "Asturias", "Ávila", "Badajoz", "Barcelona",
    "Bizkaia", "Burgos", "Cáceres", "Cádiz", "Castellón", "Ciudad Real", "Córdoba", "Cuenca", "Gipuzkoa", "Girona",
    "Granada", "Guadalajara", "Huelva", "Huesca", "Illes Balears", "Jaén", "León", "Lleida", "Lugo", "Madrid",
    "Málaga", "Murcia", "Navarra", "Ourense", "Palencia", "Las Palmas", "Pontevedra", "La Rioja", "Salamanca",
    "Santa Cruz de Tenerife", "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid",
    "Zamora", "Zaragoza", "Ceuta", "Melilla"
]


# Filtro de Jinja para formato de moneda europea
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

        # Asegurarse de que la parte decimal tenga siempre dos dígitos si no es un entero
        if not is_integer_value and len(decimal_part_str) < 2:
            decimal_part_str = decimal_part_str.ljust(2, '0')


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
        print(f"Error en euro_format para valor '{value}' (Tipo: {type(value)}): {e}") # Mantener temporalmente para depuración
        return "N/A"
    except Exception as e:
        print(f"Error inesperado en euro_format para valor '{value}' (Tipo: {type(value)}): {e}") # Otro tipo de error
        return "N/A"

# Rutas de la aplicación
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor) # Usa DictCursor para acceder por nombre de columna

    # Parámetros de búsqueda y filtros
    min_precio = request.args.get('min_precio', type=int)
    max_precio = request.args.get('max_precio', type=int)
    actividad_seleccionada = request.args.get('actividad')
    sector_seleccionado = request.args.get('sector')
    provincia_seleccionada = request.args.get('provincia')
    
    # Construcción de la consulta SQL base
    query = "SELECT * FROM empresas WHERE 1=1"
    params = []

    if min_precio is not None:
        query += " AND precio_venta >= %s"
        params.append(min_precio)
    if max_precio is not None:
        query += " AND precio_venta <= %s"
        params.append(max_precio)
    if actividad_seleccionada and actividad_seleccionada != 'Todas':
        query += " AND actividad = %s"
        params.append(actividad_seleccionada)
    if sector_seleccionado and sector_seleccionado != 'Todos':
        query += " AND sector = %s"
        params.append(sector_seleccionado)
    if provincia_seleccionada and provincia_seleccionada != 'Todas':
        query += " AND ubicacion = %s"
        params.append(provincia_seleccionada)
    
    query += " ORDER BY fecha_publicacion DESC" # Ordenar por fecha para ver los más recientes

    cur.execute(query, params)
    empresas = cur.fetchall()

    cur.close()
    conn.close()

    actividades_list = list(ACTIVIDADES_Y_SECTORES.keys())
    # No es necesario pasar la variable current_year aquí gracias al context_processor
    return render_template('index.html', empresas=empresas, actividades=actividades_list, sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)

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
    # La variable current_year ya no necesita pasarse aquí explícitamente gracias al context_processor
    return render_template('admin.html', empresas=empresas, admin_token=token)


# Punto de publicación de un nuevo anuncio
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
        
        # Convertir campos numéricos a Decimal
        try:
            facturacion = Decimal(request.form['facturacion'])
            numero_empleados = int(request.form['numero_empleados'])
            resultado_antes_impuestos = Decimal(request.form['resultado_antes_impuestos'])
            deuda = Decimal(request.form['deuda'])
            precio_venta = Decimal(request.form['precio_venta'])
        except (ValueError, InvalidOperation) as e:
            flash(f'Error en un campo numérico: {e}. Por favor, introduce solo números.', 'danger')
            # Recargar el formulario con los datos ya introducidos (excepto los numéricos erróneos)
            # Para esto, pasarías request.form a la plantilla o la lógica de Flask-WTF
            return render_template('vender_empresa.html', actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES, form_data=request.form)

        local_propiedad = request.form['local_propiedad']
        acepto_condiciones = 'acepto_condiciones' in request.form

        imagen_url = None
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename:
                # Generar un nombre de archivo único para evitar colisiones
                filename = secure_filename(file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1] # Añade un UUID al nombre
                
                try:
                    bucket = storage_client.bucket(BUCKET_NAME)
                    blob = bucket.blob(unique_filename)
                    blob.upload_from_file(file)
                    
                    # Generar una URL pública firmada y temporal para la imagen
                    # La URL expirará después de un tiempo, lo cual es bueno para seguridad
                    # Para URLs públicas permanentes, considera blob.make_public() y blob.public_url
                    imagen_url = blob.generate_signed_url(expiration=timedelta(days=365 * 10)) # URL válida por 10 años
                    
                    flash('Imagen subida correctamente.', 'success')
                except Exception as e:
                    flash(f'Error al subir la imagen a Google Cloud Storage: {e}', 'danger')
                    print(f"Error GCS: {e}") # Para depuración en logs

        if not acepto_condiciones:
            flash('Debes aceptar las condiciones de uso.', 'danger')
            # Aquí podrías recargar el formulario con los datos pre-rellenados
            return render_template('vender_empresa.html', actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)

        conn = get_db_connection()
        cur = conn.cursor()
        
        # Generar un token de edición único
        token_edicion = str(uuid.uuid4())

        try:
            cur.execute(
                """INSERT INTO empresas (nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, 
                                        descripcion, facturacion, numero_empleados, local_propiedad, 
                                        resultado_antes_impuestos, deuda, precio_venta, imagen_url, 
                                        token_edicion, fecha_publicacion) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, 
                 descripcion, facturacion, numero_empleados, local_propiedad, 
                 resultado_antes_impuestos, deuda, precio_venta, imagen_url, token_edicion)
            )
            conn.commit()
            flash('Tu anuncio ha sido publicado con éxito y está pendiente de revisión.', 'success')
            # Opcional: enviar un correo al propietario con el token de edición
            # send_edit_link_email(email_contacto, token_edicion)
            return redirect(url_for('confirmacion_publicacion', edit_token=token_edicion))
        except Exception as e:
            conn.rollback()
            flash(f'Error al guardar el anuncio en la base de datos: {e}', 'danger')
            print(f"Error DB: {e}") # Para depuración
            return render_template('vender_empresa.html', actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)
        finally:
            cur.close()
            conn.close()

    # Si es GET request o si hay un error en POST y se vuelve a cargar la página
    return render_template('vender_empresa.html', actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)

# Nueva ruta de confirmación de publicación
@app.route('/confirmacion_publicacion/<edit_token>')
def confirmacion_publicacion(edit_token):
    return render_template('confirmacion_publicacion.html', edit_token=edit_token)

# Ruta para la edición de un anuncio existente
@app.route('/editar/<edit_token>', methods=['GET', 'POST'])
def editar(edit_token):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Primero, obtenemos la información de la empresa usando el token de edición
    # Necesitamos el ID de la empresa y la URL de la imagen si existe
    cur.execute("SELECT id, imagen_url FROM empresas WHERE token_edicion = %s", (edit_token,))
    empresa = cur.fetchone()

    if empresa is None:
        flash('Anuncio no encontrado o token de edición inválido.', 'danger')
        cur.close()
        conn.close()
        return redirect(url_for('index')) # Redirige a la página principal o a donde sea apropiado

    # Si es una solicitud POST, procesamos el formulario
    if request.method == 'POST':
        # --- INICIO DE LA LÓGICA AÑADIDA PARA ELIMINAR EL ANUNCIO ---
        if request.form.get('eliminar') == 'true':
            try:
                # 1. Obtener el nombre del blob (archivo) de la imagen de GCS
                imagen_url = empresa['imagen_url']
                if imagen_url:
                    # Extraer el nombre del blob de la URL de GCS
                    # Asegúrate de que BUCKET_NAME sea la variable global con el nombre de tu bucket
                    if BUCKET_NAME and BUCKET_NAME in imagen_url: # Añadida comprobación de BUCKET_NAME para mayor seguridad
                        # Dividir la URL para obtener el nombre del blob.
                        # Asume que la URL es del formato "https://storage.googleapis.com/BUCKET_NAME/nombre_del_blob"
                        # o "https://storage.cloud.google.com/BUCKET_NAME/nombre_del_blob"
                        # La expresión regular sería más robusta, pero split es suficiente si el formato es consistente.
                        parts = imagen_url.split(f'{BUCKET_NAME}/')
                        if len(parts) > 1:
                            blob_name = parts[1].split('?')[0] # Elimina parámetros de consulta si los hay (e.g., ?X-Goog-...)
                        else:
                            blob_name = None # URL no tiene el formato esperado
                            print(f"Advertencia: La URL de la imagen no parece contener el nombre del bucket de GCS de la forma esperada: {imagen_url}")

                    else:
                        print(f"Advertencia: URL de imagen no parece de GCS o BUCKET_NAME no definido/presente: {imagen_url}")
                        blob_name = None # No intentar borrar si la URL es dudosa

                    if blob_name:
                        bucket = storage_client.bucket(BUCKET_NAME)
                        blob = bucket.blob(blob_name)
                        if blob.exists():
                            blob.delete()
                            flash('Imagen asociada eliminada de Google Cloud Storage.', 'info')
                        else:
                            flash('Advertencia: La imagen asociada no se encontró en Google Cloud Storage.', 'warning')
                    else:
                        flash('No hay imagen asociada para eliminar de Google Cloud Storage o URL inválida.', 'info')
                else:
                    flash('No hay imagen asociada para eliminar de Google Cloud Storage.', 'info')

                # 2. Eliminar el registro de la base de datos
                cur.execute("DELETE FROM empresas WHERE id = %s", (empresa['id'],))
                conn.commit()
                flash('Anuncio eliminado correctamente.', 'success')
                # Redirige al panel de admin o a la página principal después de la eliminación
                cur.close()
                conn.close()
                return redirect(url_for('admin', admin_token=ADMIN_TOKEN))
            except Exception as e:
                conn.rollback() # En caso de error, deshaz cualquier cambio en la DB
                flash(f'Error al eliminar el anuncio: {e}', 'danger')
                # Opcional: registrar el error en los logs del servidor
                print(f"Error al eliminar anuncio {empresa['id']}: {e}")
                cur.close()
                conn.close()
                # Quédate en la página de edición si la eliminación falló
                return redirect(url_for('editar', edit_token=edit_token))

        # --- FIN DE LA LÓGICA AÑADIDA PARA ELIMINAR EL ANUNCIO ---

        # --- Lógica para ACTUALIZAR el anuncio (si no se ha solicitado la eliminación) ---
        # Si llegamos aquí, significa que el botón "Eliminar Ahora" NO fue presionado,
        # por lo tanto, es una solicitud para actualizar el anuncio.
        nombre = request.form['nombre']
        email_contacto = request.form['email_contacto']
        actividad = request.form['actividad']
        sector = request.form['sector']
        pais = request.form['pais']
        ubicacion = request.form['ubicacion']
        tipo_negocio = request.form['tipo_negocio']
        descripcion = request.form['descripcion']
        
        # Convertir campos numéricos a Decimal
        try:
            facturacion = Decimal(request.form['facturacion'])
            numero_empleados = int(request.form['numero_empleados'])
            resultado_antes_impuestos = Decimal(request.form['resultado_antes_impuestos'])
            deuda = Decimal(request.form['deuda'])
            precio_venta = Decimal(request.form['precio_venta'])
        except (ValueError, InvalidOperation) as e:
            flash(f'Error en un campo numérico: {e}. Por favor, introduce solo números.', 'danger')
            cur.close()
            conn.close()
            # Pasa los datos del formulario de vuelta a la plantilla para que el usuario no pierda su entrada
            # Combina 'empresa' existente con los datos de 'request.form' para repopular
            updated_empresa = dict(empresa) # Copia los datos originales de la empresa
            for key, value in request.form.items():
                if key not in ['eliminar', 'imagen']: # No sobrescribir eliminar o imagen con el string
                    updated_empresa[key] = value
            return render_template('editar.html', empresa=updated_empresa, actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)

        local_propiedad = request.form['local_propiedad']
        
        # Manejo de la actualización de la imagen
        nueva_imagen_url = empresa['imagen_url'] # Mantener la imagen actual por defecto

        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename:
                # Eliminar la imagen antigua si existe y es diferente de la nueva
                if empresa['imagen_url']:
                    try:
                        # Asegúrate de que BUCKET_NAME está definido globalmente
                        if BUCKET_NAME and BUCKET_NAME in empresa['imagen_url']:
                            parts = empresa['imagen_url'].split(f'{BUCKET_NAME}/')
                            if len(parts) > 1:
                                old_blob_name = parts[1].split('?')[0]
                                bucket = storage_client.bucket(BUCKET_NAME)
                                old_blob = bucket.blob(old_blob_name)
                                if old_blob.exists():
                                    old_blob.delete()
                                    flash('Imagen antigua eliminada de Google Cloud Storage.', 'info')
                                else:
                                    flash('Advertencia: La imagen antigua no se encontró en GCS al intentar reemplazarla.', 'warning')
                            else:
                                print(f"Advertencia: URL de imagen antigua no parece contener el nombre del bucket de GCS: {empresa['imagen_url']}")
                        else:
                            print(f"Advertencia: BUCKET_NAME no definido o URL de imagen antigua no es de GCS: {empresa['imagen_url']}")
                    except Exception as e:
                        flash(f'Error al eliminar la imagen antigua de GCS: {e}', 'danger')
                        print(f"Error GCS al eliminar antigua imagen: {e}")

                # Subir la nueva imagen
                filename = secure_filename(file.filename)
                unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
                try:
                    bucket = storage_client.bucket(BUCKET_NAME)
                    blob = bucket.blob(unique_filename)
                    blob.upload_from_file(file)
                    nueva_imagen_url = blob.generate_signed_url(expiration=timedelta(days=365 * 10))
                    flash('Nueva imagen subida correctamente.', 'success')
                except Exception as e:
                    flash(f'Error al subir la nueva imagen a Google Cloud Storage: {e}', 'danger')
                    print(f"Error GCS al subir nueva imagen: {e}")
                    nueva_imagen_url = empresa['imagen_url'] # Mantener la antigua si falla la nueva subida

        try:
            cur.execute(
                """UPDATE empresas SET nombre=%s, email_contacto=%s, actividad=%s, sector=%s, 
                                        pais=%s, ubicacion=%s, tipo_negocio=%s, descripcion=%s, 
                                        facturacion=%s, numero_empleados=%s, local_propiedad=%s, 
                                        resultado_antes_impuestos=%s, deuda=%s, precio_venta=%s, 
                                        imagen_url=%s
                   WHERE id = %s""",
                (nombre, email_contacto, actividad, sector, pais, ubicacion, tipo_negocio, 
                 descripcion, facturacion, numero_empleados, local_propiedad, 
                 resultado_antes_impuestos, deuda, precio_venta, nueva_imagen_url, empresa['id'])
            )
            conn.commit()
            flash('Anuncio actualizado correctamente.', 'success')
            cur.close()
            conn.close()
            return redirect(url_for('detalle', empresa_id=empresa['id'])) # Redirige a la página de detalle o a admin

        except Exception as e:
            conn.rollback()
            flash(f'Error al actualizar el anuncio en la base de datos: {e}', 'danger')
            print(f"Error DB al actualizar: {e}")
            cur.close()
            conn.close()
            # Vuelve a renderizar la plantilla con los datos que causaron el error para que el usuario los corrija
            # Esto es más robusto si pasas request.form o combinas con los datos existentes
            updated_empresa = dict(request.form) # Convertir MultiDict a dict
            updated_empresa['imagen_url'] = nueva_imagen_url # Asegura que la URL de la imagen se mantenga si se subió
            return render_template('editar.html', empresa=updated_empresa, actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)


    # Si es una solicitud GET (cuando se carga la página de edición por primera vez)
    cur.close()
    conn.close()
    return render_template('editar.html', empresa=empresa, actividades=ACTIVIDADES_Y_SECTORES.keys(), provincias=PROVINCIAS_ESPANA, actividades_dict=ACTIVIDADES_Y_SECTORES)

# Ruta para ver detalles de un negocio
@app.route('/negocio/<int:empresa_id>')
def detalle(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()

    if empresa is None:
        flash('Anuncio no encontrado.', 'danger')
        return redirect(url_for('index'))
    return render_template('detalle.html', empresa=empresa)


if __name__ == '__main__':
    # La configuración para el puerto de Render ya se maneja a través de Gunicorn o similar
    # En desarrollo local, puedes usar app.run(debug=True, port=os.environ.get('PORT', 5000))
    app.run(debug=True) # Para desarrollo local
