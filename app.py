# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras # Para usar DictCursor en las consultas
from werkzeug.utils import secure_filename
import smtplib
import socket
# import json # Ya no es necesario si las actividades/sectores están en el código
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos (UUIDs)
from datetime import datetime, timedelta, timezone
from flask_moment import Moment

# IMPORTACIONES PARA GOOGLE CLOUD STORAGE
from google.cloud import storage

# IMPORTACIONES ADICIONALES PARA EMAIL
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import logging

# Configura el logger global
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Inicialización de la aplicación Flask
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# Inicialización de Flask-Moment
moment = Moment(app)

# Configurar el locale para formato de moneda
try:
    locale.setlocale(locale.LC_ALL, 'es_ES.UTF-8')
except locale.Error:
    app.logger.warning("No se pudo configurar el locale 'es_ES.UTF-8'. Intentando 'es_ES'...")
    try:
        locale.setlocale(locale.LC_ALL, 'es_ES')
    except locale.Error:
        app.logger.warning("No se pudo configurar el locale 'es_ES'. El formato de moneda podría no ser el esperado.")

# Filtro personalizado para formatear euros
@app.template_filter('euro_format')
def euro_format_filter(value, decimal_places=2):
    try:
        num_value = float(value)
        formatted = locale.currency(num_value, grouping=True, symbol=True)
        if decimal_places == 0:
            return formatted.split(',')[0] + '€' if ',' in formatted else formatted + '€'
        return formatted
    except (ValueError, TypeError):
        return value


# ********************************************************************************
# DEFINICIONES DE ACTIVIDADES, SECTORES Y PROVINCIAS (REINTEGRADAS EN APP.PY)
# ********************************************************************************

ACTIVIDADES_Y_SECTORES = {
    "Servicios": ["Consultoría", "Marketing Digital", "Servicios Profesionales", "Recursos Humanos", "Limpieza", "Mantenimiento", "Asesoría", "Legal", "Gestoría", "Auditoría"],
    "Comercio": ["Retail (minorista)", "E-commerce", "Distribución (mayorista)", "Alimentación", "Textil", "Electrónica"],
    "Industria": ["Manufactura", "Metalurgia", "Automoción", "Alimentación y Bebidas", "Química", "Farmacéutica", "Construcción Naval", "Aeronáutica"],
    "Tecnología": ["Desarrollo de Software", "Ciberseguridad", "Hardware", "Inteligencia Artificial", "Big Data", "IoT", "Cloud Computing", "Telecomunicaciones"],
    "Hostelería y Turismo": ["Restauración", "Alojamiento", "Catering", "Ocio Nocturno", "Agencias de Viajes", "Tour Operadores"],
    "Salud y Bienestar": ["Clínicas", "Farmacias", "Laboratorios", "Residencias", "Fisioterapia", "Medicina Estética", "Gimnasios", "Balnearios"],
    "Educación": ["Academias", "Centros de Formación Profesional", "Universidades", "Educación Online", "Escuelas de Idiomas"],
    "Construcción e Inmobiliaria": ["Obra Civil", "Edificación", "Reformas", "Promoción Inmobiliaria", "Agencias Inmobiliarias", "Administración de Fincas"],
    "Agricultura y Ganadería": ["Explotación Agrícola", "Explotación Ganadera", "Viticultura", "Olivicultura", "Pesca", "Silvicultura"],
    "Transporte y Logística": ["Transporte de Mercancías", "Logística", "Mensajería", "Transporte de Pasajeros", "Almacenamiento"],
    "Energía y Medio Ambiente": ["Energías Renovables", "Gestión de Residuos", "Tratamiento de Aguas", "Eficiencia Energética", "Consultoría Ambiental"],
    "Finanzas y Seguros": ["Banca", "Seguros", "Asesoría Financiera", "Gestión de Activos", "Capital Riesgo", "Inversión"],
    "Otros": ["Arte y Cultura", "Medios de Comunicación", "ONGs", "Seguridad Privada", "Servicios Funerarios", "Veterinaria"]
}

PROVINCIAS_ESPANA = [
    "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila", "Badajoz", "Barcelona",
    "Burgos", "Cáceres", "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba", "Cuenca",
    "Girona", "Granada", "Guadalajara", "Guipúzcoa", "Huelva", "Huesca", "Islas Baleares",
    "Jaén", "La Coruña", "La Rioja", "Las Palmas", "León", "Lleida", "Lugo", "Madrid", "Málaga",
    "Murcia", "Navarra", "Ourense", "Palencia", "Pontevedra", "Salamanca", "Santa Cruz de Tenerife",
    "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid",
    "Vizcaya", "Zamora", "Zaragoza", "Ceuta", "Melilla"
]

# ********************************************************************************
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE ACTIVIDADES, SECTORES Y PROVINCIAS
# ********************************************************************************


# Funciones de utilidad para la base de datos
def get_db_connection():
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

# Función para obtener empresa por ID
def get_empresa_by_id(empresa_id):
    conn = None
    empresa = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al obtener empresa por ID {empresa_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
    return empresa

# Funciones para Google Cloud Storage
def upload_to_gcs(file, folder_name):
    try:
        bucket_name = os.environ.get('GCS_BUCKET_NAME')
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)

        filename = secure_filename(file.filename)
        unique_filename = f"{folder_name}/{uuid.uuid4()}_{filename}"
        blob = bucket.blob(unique_filename)
        file.seek(0)
        blob.upload_from_file(file, content_type=file.content_type)

        return blob.public_url, unique_filename
    except Exception as e:
        app.logger.error(f"Error al subir archivo a GCS: {e}", exc_info=True)
        return None, None

def delete_from_gcs(blob_name):
    try:
        bucket_name = os.environ.get('GCS_BUCKET_NAME')
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()
        return True
    except Exception as e:
        app.logger.error(f"Error al eliminar archivo de GCS: {e}", exc_info=True)
        return False

# Función para enviar correos SMTP externos
def enviar_correo_smtp_externo(destinatario, asunto, cuerpo_html, cuerpo_texto=None):
    try:
        smtp_server = os.environ.get('SMTP_SERVER')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_user = os.environ.get('SMTP_USERNAME')
        smtp_password = os.environ.get('SMTP_PASSWORD')
        sender_email = os.environ.get('SMTP_SENDER_EMAIL')

        msg = MIMEMultipart("alternative")
        msg['Subject'] = Header(asunto, 'utf-8')
        msg['From'] = sender_email
        msg['To'] = destinatario

        if cuerpo_texto:
            part1 = MIMEText(cuerpo_texto, 'plain', 'utf-8')
            msg.attach(part1)
        if cuerpo_html:
            part2 = MIMEText(cuerpo_html, 'html', 'utf-8')
            msg.attach(part2)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(sender_email, destinatario, msg.as_string())
        return True
    except smtplib.SMTPException as e:
        app.logger.error(f"Error SMTP al enviar correo: {e}", exc_info=True)
        return False
    except socket.error as e:
        app.logger.error(f"Error de conexión al servidor SMTP: {e}", exc_info=True)
        return False
    except Exception as e:
        app.logger.error(f"Error inesperado al enviar correo: {e}", exc_info=True)
        return False


# RUTAS DE LA APLICACIÓN FLASK

@app.route('/')
def index():
    conn = None
    empresas = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Selecciona solo empresas activas y que no hayan expirado
        cur.execute("""
            SELECT * FROM empresas
            WHERE active = TRUE AND (token_expiracion IS NULL OR token_expiracion > %s)
            ORDER BY created_at DESC
        """, (datetime.now(timezone.utc),))
        empresas = cur.fetchall()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al cargar empresas en index: {e}", exc_info=True)
        flash("Hubo un problema al cargar los anuncios.", "danger")
    finally:
        if conn:
            conn.close()
    # Pasa ACTIVIDADES_Y_SECTORES directamente a la plantilla como 'actividades_dict'
    return render_template('index.html', empresas=empresas, actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


# Ruta para publicar un anuncio (Equivalente a "Vender mi empresa")
@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    conn = None
    if request.method == 'POST':
        try:
            nombre_empresa = request.form['nombre_empresa']
            actividad = request.form['actividad']
            sector = request.form['sector']
            provincia = request.form['provincia']
            
            try:
                facturacion = float(request.form['facturacion'].replace('.', '').replace(',', '.'))
                ebitda = float(request.form['ebitda'].replace('.', '').replace(',', '.'))
                precio = float(request.form['precio'].replace('.', '').replace(',', '.'))
            except ValueError:
                flash("Por favor, introduce valores numéricos válidos para Facturación, EBITDA y Precio.", "danger")
                # Vuelve a renderizar el formulario con los datos cargados
                return render_template('publicar.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                                       sectores=ACTIVIDADES_Y_SECTORES.get(actividad, []), # Mantén los sectores si la actividad ya fue seleccionada
                                       actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


            descripcion = request.form['descripcion']
            telefono = request.form['telefono']
            email = request.form['email']
            
            imagen_url = None
            imagen_blob_name = None

            if 'imagen' in request.files:
                imagen_file = request.files['imagen']
                if imagen_file and imagen_file.filename != '':
                    imagen_url, imagen_blob_name = upload_to_gcs(imagen_file, 'anuncios')
                    if not imagen_url:
                        flash("Error al subir la imagen. Por favor, inténtalo de nuevo.", "danger")
                        return render_template('publicar.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                                               sectores=ACTIVIDADES_Y_SECTORES.get(actividad, []),
                                               actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)
            
            token = str(uuid.uuid4())
            token_expiracion = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(days=30)
            created_at = datetime.utcnow().replace(tzinfo=timezone.utc)
            active = True

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO empresas (
                    nombre_empresa, actividad, sector, provincia, facturacion, ebitda, precio,
                    descripcion, telefono, email, imagen_url, imagen_blob_name,
                    token, token_expiracion, created_at, active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (nombre_empresa, actividad, sector, provincia, facturacion, ebitda, precio,
                  descripcion, telefono, email, imagen_url, imagen_blob_name,
                  token, token_expiracion, created_at, active))
            
            empresa_id = cur.fetchone()[0]
            conn.commit()
            cur.close()

            editar_url = url_for('editar_anuncio_anunciante', empresa_id=empresa_id, token=token, _external=True)
            borrar_url = url_for('borrar_anuncio_anunciante', empresa_id=empresa_id, token=token, _external=True)

            asunto_anunciante = "Tu anuncio en Pyme Market ha sido publicado"
            cuerpo_html_anunciante = render_template('email/anuncio_publicado.html',
                                                     nombre_empresa=nombre_empresa,
                                                     editar_url=editar_url,
                                                     borrar_url=borrar_url)
            cuerpo_texto_anunciante = f"""
            Hola,

            Tu anuncio '{nombre_empresa}' ha sido publicado en Pyme Market.

            Puedes editar tu anuncio aquí: {editar_url}
            Puedes borrar tu anuncio aquí: {borrar_url}

            Estos enlaces son válidos por 30 días. Por favor, guárdalos.

            Gracias,
            El equipo de Pyme Market
            """
            enviar_correo_smtp_externo(email, asunto_anunciante, cuerpo_html_anunciante, cuerpo_texto=cuerpo_texto_anunciante)
            
            flash("Tu anuncio se ha publicado con éxito y se ha enviado un enlace de edición/borrado a tu correo.", "success")
            return redirect(url_for('detalle', empresa_id=empresa_id))

        except Exception as e:
            app.logger.error(f"Error al publicar anuncio: {e}", exc_info=True)
            flash("Hubo un problema al publicar tu anuncio. Por favor, inténtalo de nuevo.", "danger")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    # Si es GET, o si POST falla, renderiza el formulario con los datos de las constantes
    return render_template('publicar.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                           sectores=[], # Inicialmente vacío hasta que se seleccione una actividad
                           actividades_dict=ACTIVIDADES_Y_SECTORES, provincias=PROVINCIAS_ESPANA)


# Ruta para editar un anuncio (accesible por anunciante con token)
@app.route('/editar-anuncio/<int:empresa_id>/<string:token>', methods=['GET', 'POST'])
def editar_anuncio_anunciante(empresa_id, token):
    conn = None
    empresa = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s AND token = %s", (empresa_id, token))
        empresa = cur.fetchone()
        cur.close()

        if not empresa:
            flash("Anuncio no encontrado o token inválido.", "danger")
            return redirect(url_for('index'))

        if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow().replace(tzinfo=timezone.utc):
            flash("El enlace para editar el anuncio ha expirado. Por favor, crea un nuevo anuncio.", "danger")
            return redirect(url_for('index'))

        if request.method == 'POST':
            nombre_empresa = request.form['nombre_empresa']
            actividad = request.form['actividad']
            sector = request.form['sector']
            provincia = request.form['provincia']
            
            try:
                facturacion = float(request.form['facturacion'].replace('.', '').replace(',', '.'))
                ebitda = float(request.form['ebitda'].replace('.', '').replace(',', '.'))
                precio = float(request.form['precio'].replace('.', '').replace(',', '.'))
            except ValueError:
                flash("Por favor, introduce valores numéricos válidos para Facturación, EBITDA y Precio.", "danger")
                # Vuelve a renderizar el formulario con los datos cargados y constantes
                return render_template('editar_anuncio_anunciante.html',
                                       empresa=empresa,
                                       actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                                       sectores=ACTIVIDADES_Y_SECTORES.get(actividad, []), # Sectores de la actividad actual
                                       actividades_dict=ACTIVIDADES_Y_SECTORES,
                                       provincias=PROVINCIAS_ESPANA,
                                       empresa_id=empresa_id,
                                       token=token)


            descripcion = request.form['descripcion']
            telefono = request.form['telefono']
            email = request.form['email']

            imagen_url = empresa['imagen_url']
            imagen_blob_name = empresa['imagen_blob_name']

            if 'imagen' in request.files and request.files['imagen'].filename != '':
                new_image_file = request.files['imagen']
                if new_image_file:
                    if imagen_blob_name and delete_from_gcs(imagen_blob_name):
                        app.logger.info(f"Antigua imagen {imagen_blob_name} eliminada de GCS.")
                    
                    new_imagen_url, new_imagen_blob_name = upload_to_gcs(new_image_file, 'anuncios')
                    if new_imagen_url:
                        imagen_url = new_imagen_url
                        imagen_blob_name = new_imagen_blob_name
                    else:
                        flash("Error al subir la nueva imagen.", "danger")
                        imagen_url = empresa['imagen_url']
                        imagen_blob_name = empresa['imagen_blob_name'] # Mantener la antigua si falla la subida

            cur = conn.cursor()
            cur.execute("""
                UPDATE empresas SET
                    nombre_empresa = %s,
                    actividad = %s,
                    sector = %s,
                    provincia = %s,
                    facturacion = %s,
                    ebitda = %s,
                    precio = %s,
                    descripcion = %s,
                    telefono = %s,
                    email = %s,
                    imagen_url = %s,
                    imagen_blob_name = %s
                WHERE id = %s AND token = %s
            """, (nombre_empresa, actividad, sector, provincia, facturacion, ebitda, precio,
                  descripcion, telefono, email, imagen_url, imagen_blob_name, empresa_id, token))
            conn.commit()
            cur.close()
            flash("Anuncio actualizado con éxito.", "success")
            return redirect(url_for('detalle', empresa_id=empresa_id))

    except Exception as e:
        app.logger.error(f"Error en la ruta editar_anuncio_anunciante para ID {empresa_id}: {e}", exc_info=True)
        flash("Hubo un problema al editar el anuncio.", "danger")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    # Si es GET o si POST falla, renderiza el formulario con los datos de las constantes
    # Asegúrate de pasar los sectores correctos para la actividad actual de la empresa
    current_activity_sectors = ACTIVIDADES_Y_SECTORES.get(empresa['actividad'], [])
    return render_template('editar_anuncio_anunciante.html',
                           empresa=empresa,
                           actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                           sectores=current_activity_sectors,
                           actividades_dict=ACTIVIDADES_Y_SECTORES,
                           provincias=PROVINCIAS_ESPANA,
                           empresa_id=empresa_id,
                           token=token)

# Ruta para borrar un anuncio (primero confirma, luego borra)
@app.route('/borrar-anuncio/<int:empresa_id>/<string:token>', methods=['GET', 'POST'])
def borrar_anuncio_anunciante(empresa_id, token):
    conn = None
    empresa = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s AND token = %s", (empresa_id, token))
        empresa = cur.fetchone()
        cur.close()

        if not empresa:
            flash("Anuncio no encontrado o token inválido.", "danger")
            return redirect(url_for('index'))

        if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow().replace(tzinfo=timezone.utc):
            flash("El enlace para borrar el anuncio ha expirado. Por favor, contacta con soporte si necesitas eliminarlo.", "danger")
            return redirect(url_for('index'))

        if request.method == 'POST':
            if request.form.get('confirmar_borrado') == 'yes':
                if empresa['imagen_blob_name']:
                    if delete_from_gcs(empresa['imagen_blob_name']):
                        app.logger.info(f"Antigua imagen {empresa['imagen_blob_name']} eliminada de GCS.")
                    else:
                        app.logger.warning(f"No se pudo eliminar la imagen {empresa['imagen_blob_name']} de GCS.")

                cur = conn.cursor()
                cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
                conn.commit()
                cur.close()
                flash("Anuncio eliminado con éxito.", "success")
                return redirect(url_for('index'))
            else:
                flash("Borrado cancelado.", "info")
                return redirect(url_for('detalle', empresa_id=empresa_id))

    except Exception as e:
        app.logger.error(f"Error en la ruta borrar_anuncio_anunciante para ID {empresa_id}: {e}", exc_info=True)
        flash("Hubo un problema al intentar borrar el anuncio.", "danger")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return render_template('confirmar_borrado_anunciante.html', empresa=empresa)


# Ruta para ver detalles de un anuncio
@app.route('/detalle/<int:empresa_id>')
def detalle(empresa_id):
    conn = None
    empresa = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()
        cur.close()

        if not empresa:
            flash("Anuncio no encontrado.", "danger")
            return redirect(url_for('index'))

    except Exception as e:
        app.logger.error(f"Error al cargar detalle del anuncio {empresa_id}: {e}", exc_info=True)
        flash("Hubo un problema al cargar los detalles del anuncio.", "danger")
    finally:
        if conn:
            conn.close()

    return render_template('detalle.html', empresa=empresa)


# Ruta para contactar
@app.route('/contacto', methods=['GET', 'POST'])
def contacto():
    if request.method == 'POST':
        nombre_cliente = request.form['nombre']
        email_cliente = request.form['email']
        mensaje_cliente = request.form['mensaje']
        telefono_cliente = request.form.get('telefono', 'No proporcionado')

        correo_recepcion = os.environ.get('RECEPTION_EMAIL')
        asunto_empresa = f"Nuevo mensaje de contacto de {nombre_cliente} - {email_cliente}"
        cuerpo_html_empresa = render_template('email/contacto_empresa.html',
                                               nombre=nombre_cliente,
                                               email=email_cliente,
                                               mensaje=mensaje_cliente,
                                               telefono=telefono_cliente)
        cuerpo_texto_empresa = f"""
        Nuevo mensaje de contacto desde la web:
        Nombre: {nombre_cliente}
        Email: {email_cliente}
        Mensaje: {mensaje_cliente}
        Teléfono: {telefono_cliente}
        """

        asunto_cliente = "Confirmación de mensaje de contacto - Pyme Market"
        cuerpo_html_cliente = render_template('email/confirmacion_contacto_cliente.html', nombre=nombre_cliente)
        cuerpo_texto_cliente = f"""
        Hola {nombre_cliente},

        Hemos recibido tu mensaje en Pyme Market. Nos pondremos en contacto contigo lo antes posible.

        Este es un mensaje automático, por favor, no respondas a este correo.

        Gracias,
        El equipo de Pyme Market
        """

        if enviar_correo_smtp_externo(correo_recepcion, asunto_empresa, cuerpo_html_empresa, cuerpo_texto=cuerpo_texto_empresa) and \
           enviar_correo_smtp_externo(email_cliente, asunto_cliente, cuerpo_html_cliente, cuerpo_texto=cuerpo_texto_cliente):
            flash("Tu mensaje ha sido enviado con éxito y hemos enviado una confirmación a tu correo.", "success")
        else:
            flash("Hubo un error al enviar tu mensaje. Por favor, inténtalo de nuevo más tarde.", "danger")
        return redirect(url_for('contacto'))
    return render_template('contacto.html')


# Ruta para las políticas de cookies y nota legal
@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')

@app.route('/nota-legal')
def nota_legal():
    return render_template('nota_legal.html')

# Ruta para "Estudio de ahorros"
@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

# Ruta para "Valorar mi empresa"
@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

# Ruta para el Panel de Administración
@app.route('/admin-panel')
def admin_panel():
    # Aquí puedes añadir tu propia lógica de autenticación para el administrador
    conn = None
    empresas = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas ORDER BY id DESC")
        empresas = cur.fetchall()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al cargar empresas para el panel de administración: {e}", exc_info=True)
        flash("Hubo un problema al cargar los anuncios en el panel de administración.", "danger")
    finally:
        if conn:
            conn.close()
    return render_template('admin_panel.html', empresas=empresas)


# Punto de entrada principal para ejecutar la aplicación Flask
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
