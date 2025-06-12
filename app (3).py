
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

# Diccionario simulado (reemplazar por el real en producción)
ACTIVIDADES_Y_SECTORES = {
    "INDUSTRIA MANUFACTURERA": ["Fabricación de muebles", "Industria alimentaria"],
    "SERVICIOS": ["Consultoría", "Educación", "Publicidad"]
}
ACTIVIDADES_Y_SECTORES = json.loads(json.dumps(ACTIVIDADES_Y_SECTORES))

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

    todos_los_sectores = sorted({sec for lst in ACTIVIDADES_Y_SECTORES.values() for sec in lst})

    return render_template('index.html',
                           empresas=empresas,
                           actividades=list(ACTIVIDADES_Y_SECTORES.keys()),
                           sectores=todos_los_sectores,
                           actividades_dict=ACTIVIDADES_Y_SECTORES)
