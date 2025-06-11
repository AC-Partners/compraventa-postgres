from flask import Flask, render_template, request, redirect, url_for
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket

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

@app.route('/')
def index():
    provincia = request.args.get('provincia')
    actividad = request.args.get('actividad')
    min_fact = request.args.get('min_facturacion', 0, type=float)
    max_fact = request.args.get('max_facturacion', 1e12, type=float)

    conn = get_db_connection()
    cur = conn.cursor()
    query = "SELECT * FROM empresas WHERE facturacion BETWEEN %s AND %s"
    params = [min_fact, max_fact]
    if provincia:
        query += " AND provincia = %s"
        params.append(provincia)
    if actividad:
        query += " AND actividad = %s"
        params.append(actividad)
    cur.execute(query, tuple(params))
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
        enviar_email_interes(empresa['nombre'], email_usuario)
        return render_template('detalle.html', empresa=empresa, enviado=True)
    return render_template('detalle.html', empresa=empresa, enviado=False)

@app.route('/admin')
def admin_panel():
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "No autorizado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas ORDER BY id DESC")
    empresas = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', empresas=empresas)

@app.route('/editar/<int:empresa_id>', methods=['GET', 'POST'])
def editar_anuncio(empresa_id):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        if 'eliminar' in request.form:
            cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
            conn.commit()
            cur.close()
            conn.close()
            return redirect('/admin?admin_token=' + ADMIN_TOKEN)

        campos = ['nombre', 'email_contacto', 'sector', 'pais', 'ubicacion',
                  'descripcion', 'facturacion', 'numero_empleados', 'local_propiedad',
                  'beneficio_impuestos', 'deuda', 'precio_venta']
        valores = [request.form.get(c) for c in campos]

        imagen = request.files.get('imagen')
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))
            valores.append(imagen_filename)
            update_query = """
                UPDATE empresas SET nombre=%s, email_contacto=%s, sector=%s, pais=%s, ubicacion=%s,
                descripcion=%s, facturacion=%s, numero_empleados=%s, local_propiedad=%s,
                beneficio_impuestos=%s, deuda=%s, precio_venta=%s, imagen_url=%s WHERE id=%s
            """
        else:
            update_query = """
                UPDATE empresas SET nombre=%s, email_contacto=%s, sector=%s, pais=%s, ubicacion=%s,
                descripcion=%s, facturacion=%s, numero_empleados=%s, local_propiedad=%s,
                beneficio_impuestos=%s, deuda=%s, precio_venta=%s WHERE id=%s
            """
        valores.append(empresa_id)
        cur.execute(update_query, tuple(valores))
        conn.commit()
        cur.close()
        conn.close()
        return redirect('/admin?admin_token=' + ADMIN_TOKEN)
    else:
        cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
        empresa = cur.fetchone()
        cur.close()
        conn.close()
        return render_template('editar.html', empresa=empresa)

@app.route('/eliminar/<int:empresa_id>')
def eliminar_anuncio(empresa_id):
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "No autorizado", 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect('/admin?admin_token=' + ADMIN_TOKEN)

@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        campos = ['nombre', 'email_contacto', 'sector', 'pais', 'ubicacion', 'descripcion',
                  'facturacion', 'numero_empleados', 'local_propiedad',
                  'beneficio_impuestos', 'deuda', 'precio_venta']
        valores = [request.form.get(c) for c in campos]

        imagen = request.files['imagen']
        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO empresas (nombre, email_contacto, sector, pais, ubicacion,
            descripcion, facturacion, numero_empleados, local_propiedad,
            beneficio_impuestos, deuda, precio_venta, imagen_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, tuple(valores + [imagen_filename]))
        conn.commit()
        cur.close()
        conn.close()

        enviar_email_interes(valores[0], valores[1])
        return redirect('/')
    return render_template('publicar.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
