# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras # Para usar DictCursor en las consultas
from werkzeug.utils import secure_filename
import smtplib
import socket
import json # Importa el módulo json para cargar las actividades y sectores
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos (UUIDs)
from datetime import datetime, timedelta, timezone # <-- Asegúrate de que 'timezone' esté aquí
from flask_moment import Moment # <-- AÑADE ESTA IMPORTACIÓN

# IMPORTACIONES AÑADIDAS PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

# IMPORTACIONES ADICIONALES PARA EMAIL (SI YA EXISTEN, SE MANTIENEN)
from email.mime.text import MIMEText # Para crear mensajes HTML/texto plano
from email.mime.multipart import MIMEMultipart # Para mensajes con múltiples partes (HTML y texto)
from email.header import Header # Para manejar encabezados con caracteres especiales (UTF-8)
import logging

# Configura el logger global para ver mensajes en los logs de Render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
# Se mantiene la variable de entorno FLASK_SECRET_KEY, con un valor por defecto para desarrollo.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# Inicialización de Flask-Moment
moment = Moment(app) # <-- AÑADE ESTA LÍNEA

# Funciones de utilidad para la base de datos (Ejemplo, si no las tienes ya)
def get_db_connection():
    """Establece una conexión a la base de datos PostgreSQL."""
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

# Función para obtener empresa por ID (Necesaria para editar/borrar)
def get_empresa_by_id(empresa_id):
    """
    Recupera los detalles de una empresa por su ID desde la base de datos.
    """
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

# ... (El resto de tus funciones de utilidad, como upload_to_gcs, delete_from_gcs, etc.) ...
# Aquí voy a asumir que tienes esas funciones definidas en tu app.py o importadas.
# Si no las tienes, tendrás que añadirlas.

# Asumiendo que ya tienes una función para subir archivos a GCS
def upload_to_gcs(file, folder_name):
    try:
        bucket_name = os.environ.get('GCS_BUCKET_NAME')
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)

        filename = secure_filename(file.filename)
        unique_filename = f"{folder_name}/{uuid.uuid4()}_{filename}"
        blob = bucket.blob(unique_filename)
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

# Asumo que también tienes una función para enviar correos SMTP externos
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
    # ... (tu lógica para la ruta index) ...
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
        """, (datetime.now(timezone.utc),)) # Compara con la hora actual UTC-aware
        empresas = cur.fetchall()
        cur.close()
    except Exception as e:
        app.logger.error(f"Error al cargar empresas en index: {e}", exc_info=True)
        flash("Hubo un problema al cargar los anuncios.", "danger")
    finally:
        if conn:
            conn.close()
    return render_template('index.html', empresas=empresas)


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

        # Verifica si el token ha expirado (usando datetime.utcnow().replace(tzinfo=timezone.utc))
        if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow().replace(tzinfo=timezone.utc):
            flash("El enlace para editar el anuncio ha expirado. Por favor, crea un nuevo anuncio.", "danger")
            # Opcional: podrías desactivar el anuncio aquí si quieres
            # cur = conn.cursor()
            # cur.execute("UPDATE empresas SET active = FALSE WHERE id = %s", (empresa_id,))
            # conn.commit()
            # cur.close()
            return redirect(url_for('index'))

        if request.method == 'POST':
            nombre_empresa = request.form['nombre_empresa']
            actividad = request.form['actividad']
            sector = request.form['sector']
            provincia = request.form['provincia']
            facturacion = float(request.form['facturacion'].replace('.', '').replace(',', '.'))
            ebitda = float(request.form['ebitda'].replace('.', '').replace(',', '.'))
            precio = float(request.form['precio'].replace('.', '').replace(',', '.'))
            descripcion = request.form['descripcion']
            telefono = request.form['telefono']
            email = request.form['email']

            imagen_url = empresa['imagen_url']
            imagen_blob_name = empresa['imagen_blob_name']

            # Manejo de la subida de nueva imagen
            if 'imagen' in request.files and request.files['imagen'].filename != '':
                new_image_file = request.files['imagen']
                if new_image_file:
                    # Eliminar imagen antigua si existe
                    if imagen_blob_name and delete_from_gcs(imagen_blob_name):
                        app.logger.info(f"Antigua imagen {imagen_blob_name} eliminada de GCS.")
                    
                    # Subir nueva imagen
                    new_imagen_url, new_imagen_blob_name = upload_to_gcs(new_image_file, 'anuncios')
                    if new_imagen_url:
                        imagen_url = new_imagen_url
                        imagen_blob_name = new_imagen_blob_name
                    else:
                        flash("Error al subir la nueva imagen.", "danger")
                        # Mantener la imagen existente si hay un error en la subida de la nueva
                        imagen_url = empresa['imagen_url']
                        imagen_blob_name = empresa['imagen_blob_name']

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
            return redirect(url_for('detalle_anuncio', empresa_id=empresa_id))

    except Exception as e:
        app.logger.error(f"Error en la ruta editar_anuncio_anunciante para ID {empresa_id}: {e}", exc_info=True)
        flash("Hubo un problema al editar el anuncio.", "danger")
        if conn: # Asegurarse de que si hay un error antes del commit, se haga rollback
            conn.rollback()
    finally:
        if conn:
            conn.close()

    # Cargar actividades y sectores para los desplegables del formulario
    try:
        with open('actividades_sectores.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            actividades = data.get('actividades', [])
            sectores = data.get('sectores', [])
    except FileNotFoundError:
        actividades = []
        sectores = []
        app.logger.error("Error: actividades_sectores.json no encontrado.")
        flash("Error al cargar las opciones de actividad y sector.", "danger")

    return render_template('editar_anuncio_anunciante.html',
                           empresa=empresa,
                           actividades=actividades,
                           sectores=sectores,
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

        # Verifica si el token ha expirado (usando datetime.utcnow().replace(tzinfo=timezone.utc))
        if empresa.get('token_expiracion') and empresa['token_expiracion'] < datetime.utcnow().replace(tzinfo=timezone.utc):
            flash("El enlace para borrar el anuncio ha expirado. Por favor, contacta con soporte si necesitas eliminarlo.", "danger")
            return redirect(url_for('index'))

        if request.method == 'POST':
            if request.form.get('confirmar_borrado') == 'yes':
                # Opcional: Eliminar imagen asociada en GCS
                if empresa['imagen_blob_name']:
                    if delete_from_gcs(empresa['imagen_blob_name']):
                        app.logger.info(f"Imagen {empresa['imagen_blob_name']} eliminada de GCS.")
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
                return redirect(url_for('detalle_anuncio', empresa_id=empresa_id))

    except Exception as e:
        app.logger.error(f"Error en la ruta borrar_anuncio_anunciante para ID {empresa_id}: {e}", exc_info=True)
        flash("Hubo un problema al intentar borrar el anuncio.", "danger")
        if conn: # Asegurarse de que si hay un error antes del commit, se haga rollback
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return render_template('confirmar_borrado_anunciante.html', empresa=empresa)


# Ruta para ver detalles de un anuncio
@app.route('/detalle/<int:empresa_id>')
def detalle_anuncio(empresa_id):
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
        # ... (tu lógica para la ruta de contacto, asumo que ya está funcionando) ...
        nombre_cliente = request.form['nombre']
        email_cliente = request.form['email']
        mensaje_cliente = request.form['mensaje']
        telefono_cliente = request.form.get('telefono', 'No proporcionado')

        # Correo para la empresa/administrador
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

        # Correo de confirmación para el cliente
        asunto_cliente = "Confirmación de mensaje de contacto - VentaEmpresa.es"
        cuerpo_html_cliente = render_template('email/confirmacion_contacto_cliente.html', nombre=nombre_cliente)
        cuerpo_texto_cliente = f"""
        Hola {nombre_cliente},

        Hemos recibido tu mensaje en VentaEmpresa.es. Nos pondremos en contacto contigo lo antes posible.

        Este es un mensaje automático, por favor, no respondas a este correo.

        Gracias,
        El equipo de VentaEmpresa.es
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

# Ruta para el Panel de Administración (ejemplo)
@app.route('/admin-panel')
def admin_panel():
    # Aquí puedes añadir tu propia lógica de autenticación para el administrador
    # Por ejemplo: if request.args.get('token') != ADMIN_TOKEN: ...
    conn = None
    empresas = []
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM empresas ORDER BY id DESC") # Obtiene todas las empresas
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
    # Establece debug=False para producción por seguridad.
    # Si usas Gunicorn u otro servidor WSGI, esta línea se ignorará.
    app.run(host='0.0.0.0', port=port, debug=False)
