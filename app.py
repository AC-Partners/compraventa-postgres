from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket
import json

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

ACTIVIDADES_Y_SECTORES = '{...}'  # Coloca aquí el JSON completo
ACTIVIDADES_Y_SECTORES = json.loads(ACTIVIDADES_Y_SECTORES)

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
    msg['Subject'] = f"\U0001F4E9 Nueva empresa publicada: {empresa_nombre}"
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
    provincia = request.args.get('provincia')
    pais = request.args.get('pais', 'España')
    actividad = request.args.get('actividad')
    sector = request.args.get('sector')
    min_fact = request.args.get('min_facturacion', 0, type=float)
    max_fact = request.args.get('max_facturacion', 1e12, type=float)
    max_precio = request.args.get('max_precio', 1e12, type=float)

    conn = get_db_connection()
    cur = conn.cursor()

    query = "SELECT * FROM empresas WHERE facturacion BETWEEN %s AND %s AND precio_venta <= %s"
    params = [min_fact, max_fact, max_precio]

    if provincia:
        query += " AND provincia = %s"
        params.append(provincia)
    if pais:
        query += " AND pais = %s"
        params.append(pais)
    if actividad:
        query += " AND actividad = %s"
        params.append(actividad)
    if sector:
        query += " AND sector = %s"
        params.append(sector)

    cur.execute(query, tuple(params))
    empresas = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('index.html', empresas=empresas, actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES)

@app.route('/publicar', methods=['GET', 'POST'])
def publicar():
    if request.method == 'POST':
        nombre = request.form['nombre']
        email_contacto = request.form['email_contacto']
        actividad = request.form['actividad']
        sector = request.form['sector']
        pais = request.form['pais']
        ubicacion = request.form['ubicacion']
        descripcion = request.form['descripcion']
        facturacion = request.form['facturacion']
        numero_empleados = request.form.get('numero_empleados')
        local_propiedad = request.form['local_propiedad']
        beneficio_impuestos = request.form.get('beneficio_impuestos')
        deuda = request.form.get('deuda')
        precio_venta = request.form['precio_venta']
        imagen = request.files['imagen']

        imagen_filename = ''
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO empresas (nombre, email_contacto, actividad, sector, pais, ubicacion, descripcion, facturacion,
                                  numero_empleados, local_propiedad, beneficio_impuestos, deuda, precio_venta, imagen_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (nombre, email_contacto, actividad, sector, pais, ubicacion, descripcion, facturacion, numero_empleados,
              local_propiedad, beneficio_impuestos, deuda, precio_venta, imagen_filename))
        conn.commit()
        cur.close()
        conn.close()

        enviar_email_interes(nombre, email_contacto)

        flash('Empresa publicada correctamente', 'success')
        return redirect(url_for('index'))

    return render_template('vender_empresa.html', actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES)

@app.route('/editar/<int:empresa_id>', methods=['GET', 'POST'])
def editar_anuncio(empresa_id):
    token = request.args.get('admin_token')
    if token != ADMIN_TOKEN:
        return "Acceso denegado", 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,))
    empresa = cur.fetchone()

    if request.method == 'POST':
        if 'eliminar' in request.form:
            cur.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
            conn.commit()
            cur.close()
            conn.close()
            flash('Anuncio eliminado correctamente', 'success')
            return redirect(url_for('admin', admin_token=token))

        campos = [
            'nombre', 'email_contacto', 'actividad', 'sector', 'pais', 'ubicacion',
            'descripcion', 'facturacion', 'numero_empleados',
            'local_propiedad', 'beneficio_impuestos', 'deuda', 'precio_venta'
        ]
        nuevos_valores = [request.form.get(campo) for campo in campos]

        imagen = request.files['imagen']
        if imagen and allowed_file(imagen.filename):
            imagen_filename = secure_filename(imagen.filename)
            imagen.save(os.path.join(app.config['UPLOAD_FOLDER'], imagen_filename))
            nuevos_valores.append(imagen_filename)
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, actividad = %s, sector = %s, pais = %s, ubicacion = %s,
                    descripcion = %s, facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    beneficio_impuestos = %s, deuda = %s, precio_venta = %s, imagen_url = %s
                WHERE id = %s
            """, (*nuevos_valores, empresa_id))
        else:
            cur.execute("""
                UPDATE empresas SET
                    nombre = %s, email_contacto = %s, actividad = %s, sector = %s, pais = %s, ubicacion = %s,
                    descripcion = %s, facturacion = %s, numero_empleados = %s, local_propiedad = %s,
                    beneficio_impuestos = %s, deuda = %s, precio_venta = %s
                WHERE id = %s
            """, (*nuevos_valores, empresa_id))

        conn.commit()
        flash('Anuncio actualizado correctamente', 'success')
        return redirect(url_for('admin', admin_token=token))

    cur.close()
    conn.close()
    return render_template('editar.html', empresa=empresa, actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES)

@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

@app.route('/contacto')
def contacto():
    return render_template('contacto.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

