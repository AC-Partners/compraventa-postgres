from flask import Flask, render_template, request, redirect, url_for
import os
import psycopg2
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

def get_db_connection():
    # Forzar uso de IPv4 para evitar errores de red en Render
    orig_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *args, **kwargs: [
        info for info in orig_getaddrinfo(*args, **kwargs) if info[0] == socket.AF_INET
    ]
    
    # Conexión segura con sslmode para Neon
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def enviar_email_interes(empresa_nombre, email_usuario):
    msg = EmailMessage()
    msg.set_content(f"El usuario {email_usuario} está interesado en la empresa: {empresa_nombre}")
    msg['Subject'] = f"Interés en {empresa_nombre}"
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = EMAIL_DESTINO
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
        smtp.send_message(msg)

@app.route('/', methods=['GET'])
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

@app.route('/nueva', methods=['GET', 'POST'])
def nueva():
    if request.method == 'POST':
        nombre = request.form['nombre']
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
        cur.execute("INSERT INTO empresas (nombre, provincia, actividad, facturacion, descripcion, imagen_url) VALUES (%s, %s, %s, %s, %s, %s)",
                    (nombre, provincia, actividad, facturacion, descripcion, imagen_filename))
        conn.commit()
        cur.close()
        conn.close()
        return redirect('/')
    return render_template('nueva_empresa.html')

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

        # Guardar en la base de datos
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO empresas (nombre, provincia, actividad, facturacion, descripcion, imagen_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nombre, provincia, actividad, facturacion, descripcion, imagen_filename))
        conn.commit()
        cur.close()
        conn.close()

        # Enviar correo al administrador
        enviar_email_interes(nombre, email_contacto)

        return redirect('/')
    return render_template('publicar.html')


# 🟢 Esto es necesario para que Render sepa cómo ejecutar la app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
