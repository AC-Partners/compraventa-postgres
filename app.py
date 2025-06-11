from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')


def get_db_connection():
    orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *args, **kwargs: [
        info for info in orig_getaddrinfo(*args, **kwargs) if info[0] == socket.AF_INET
    ]
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def enviar_email_interes(empresa_nombre, email_usuario):
    msg = EmailMessage()
    msg['Subject'] = f"📩 Nueva empresa publicada: {empresa_nombre}"
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = EMAIL_DESTINO
    msg.set_content(f"""
¡Se ha publicado una nueva empresa en el portal!

Nombre: {empresa_nombre}
Contacto: {email_usuario}
""")
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
        smtp.send_message(msg)


@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}


@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('index.html', empresas=empresas)


@app.route('/empresa/<int:id>', methods=['GET', 'POST'])
def detalle(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas WHERE id = %s", (id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()
    if request.method == 'POST':
        email_usuario = request.form['email']
        enviar_email_interes(empresa[1], email_usuario)
        return render_template('detalle.html', empresa=empresa, enviado=True)
    return render_template('detalle.html', empresa=empresa, enviado=False)


@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        nombre = request.form['nombre']
        email_contacto = request.form['email_contacto']
        provincia = request.form['provincia']
        actividad = request.form['actividad']
        facturacion = request.form['facturacion']
        descripcion = request.form['descripcion']
        imagen = request.files['imagen']
        imagen_filename = ''

        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO empresas (nombre, provincia, actividad, facturacion, descripcion, imagen_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nombre, provincia, actividad, facturacion, descripcion, imagen_filename))
        conn.commit()
        cur.close()
        conn.close()

        enviar_email_interes(nombre, email_contacto)
        flash('¡Tu empresa ha sido publicada!', 'success')
        return redirect('/')
    return render_template('publicar.html')


@app.route('/nota-legal')
def nota_legal():
    return render_template('nota_legal.html')


@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')


@app.route('/admin/anuncios')
def admin_panel():
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso no autorizado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, actividad AS sector, provincia AS ubicacion, facturacion, descripcion, imagen_url FROM empresas ORDER BY id DESC")
    anuncios = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_panel.html', anuncios=anuncios)


@app.route('/admin/eliminar/<int:empresa_id>', methods=['POST'])
def eliminar_anuncio_admin(empresa_id):
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso no autorizado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return f"Error al eliminar: {e}", 500
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_panel', admin_token=token))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
