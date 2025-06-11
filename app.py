from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret")
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
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

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

@app.route('/', methods=['GET'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('index.html', empresas=empresas)

@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        datos = {key: request.form.get(key) for key in [
            'nombre', 'email_contacto', 'sector', 'pais', 'ubicacion', 'descripcion',
            'facturacion', 'numero_empleados', 'local_propiedad',
            'beneficio_impuestos', 'deuda', 'precio_venta'
        ]}
        imagen = request.files['imagen']
        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO empresas (
                nombre, email_contacto, sector, pais, ubicacion, descripcion,
                facturacion, numero_empleados, local_propiedad, beneficio_impuestos,
                deuda, precio_venta, imagen_url
            )
            VALUES (%(nombre)s, %(email_contacto)s, %(sector)s, %(pais)s, %(ubicacion)s,
                    %(descripcion)s, %(facturacion)s, %(numero_empleados)s, %(local_propiedad)s,
                    %(beneficio_impuestos)s, %(deuda)s, %(precio_venta)s, %s)
        """, {**datos, 'imagen_url': imagen_filename})
        conn.commit()
        cur.close()
        conn.close()

        enviar_email_interes(datos['nombre'], datos['email_contacto'])

        flash("Tu empresa ha sido publicada correctamente.", "success")
        return redirect('/')
    return render_template('vender_empresa.html')

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
        enviar_email_interes(empresa['nombre'], email_usuario)
        return render_template('detalle.html', empresa=empresa, enviado=True)
    return render_template('detalle.html', empresa=empresa, enviado=False)

@app.route('/admin')
def admin():
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso denegado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_panel.html', empresas=empresas)

@app.route('/editar/<int:empresa_id>', methods=['GET', 'POST'])
def editar_anuncio(empresa_id):
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso denegado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        if 'eliminar' in request.form:
            cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
            conn.commit()
            cur.close()
            conn.close()
            flash("Anuncio eliminado.", "warning")
            return redirect(url_for('admin', admin_token=token))
        datos = {key: request.form.get(key) for key in [
            'nombre', 'email_contacto', 'sector', 'pais', 'ubicacion', 'descripcion',
            'facturacion', 'numero_empleados', 'local_propiedad',
            'beneficio_impuestos', 'deuda', 'precio_venta'
        ]}
        imagen = request.files['imagen']
        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))
            cur.execute("""
                UPDATE empresas SET
                    nombre=%(nombre)s, email_contacto=%(email_contacto)s, sector=%(sector)s, pais=%(pais)s,
                    ubicacion=%(ubicacion)s, descripcion=%(descripcion)s, facturacion=%(facturacion)s,
                    numero_empleados=%(numero_empleados)s, local_propiedad=%(local_propiedad)s,
                    beneficio_impuestos=%(beneficio_impuestos)s, deuda=%(deuda)s, precio_venta=%(precio_venta)s,
                    imagen_url=%s
                WHERE id=%s
            """, {**datos, 'imagen_url': imagen_filename, 'id': empresa_id})
        else:
            cur.execute("""
                UPDATE empresas SET
                    nombre=%(nombre)s, email_contacto=%(email_contacto)s, sector=%(sector)s, pais=%(pais)s,
                    ubicacion=%(ubicacion)s, descripcion=%(descripcion)s, facturacion=%(facturacion)s,
                    numero_empleados=%(numero_empleados)s, local_propiedad=%(local_propiedad)s,
                    beneficio_impuestos=%(beneficio_impuestos)s, deuda=%(deuda)s, precio_venta=%(precio_venta)s
                WHERE id=%s
            """, {**datos, 'id': empresa_id})
        conn.commit()
        cur.close()
        conn.close()
        flash("Anuncio actualizado.", "success")
        return redirect(url_for('admin', admin_token=token))

    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('editar_anuncio.html', empresa=empresa)

# Ruta para contacto
@app.route('/contacto', methods=['GET', 'POST'])
def contacto():
    if request.method == 'POST':
        flash("Tu mensaje ha sido enviado correctamente.", "success")
        return redirect('/')
    return render_template('contacto.html')

# Ruta para política de cookies
@app.route('/politica-cookies')
def politica_cookies():
    return render_template('politica_cookies.html')

# Ruta placeholder para valorar empresa
@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

# Ruta para formulario de estudio de ahorros
@app.route('/estudio-ahorros', methods=['GET', 'POST'])
def estudio_ahorros():
    if request.method == 'POST':
        flash("Gracias por tu solicitud. Te contactaremos pronto.", "success")
        return redirect('/')
    return render_template('estudio_ahorros.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

