from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

ACTIVIDADES_Y_SECTORES = {
    "AGRICULTURA, GANADERÍA, SILVICULTURA Y PESCA": [
        "agricultura, ganadería, caza y servicios relacionados con las mismas",
        "silvicultura y explotación forestal",
        "pesca y acuicultura"
    ],
    "INDUSTRIAS EXTRACTIVAS": [
        "extracción de antracita, hulla, y lignito",
        "extracción de crudo de petróleo y gas natural",
        "extracción de minerales metálicos",
        "otras industrias extractivas",
        "actividades de apoyo a las industrias extractivas"
    ],
    "INDUSTRIA MANUFACTURERA": [
        "industria alimentaria",
        "fabricación de bebidas",
        "industria del tabaco",
        "industria textil",
        "confección de prendas de vestir",
        "industria del cuero y productos relacionados de otros materiales",
        "industria de la madera y del corcho, excepto muebles; cestería y espartería",
        "industria del papel",
        "artes gráficas y reproducción de soportes grabados",
        "coquerías y refino de petróleo",
        "industria química",
        "fabricación de productos farmacéuticos",
        "fabricación de productos de caucho y plásticos",
        "fabricación de otros productos minerales no metálicos",
        "metalurgia",
        "fabricación de productos metálicos, excepto maquinaria y equipo",
        "fabricación de productos informáticos, electrónicos y ópticos",
        "fabricación de material y equipo eléctrico",
        "fabricación de maquinaria y equipo n.c.o.p.",
        "fabricación de vehículos de motor, remolques y semirremolques",
        "fabricación de otro material de transporte",
        "fabricación de muebles",
        "otras industrias manufactureras",
        "reparación, mantenimiento e instalación de maquinaria y equipos"
    ],
    "SUMINISTRO DE ENERGIA ELECTRICA, GAS, VAPOR Y AIRE ACONDICIONADO": [
        "suministro de energía eléctrica, gas, vapor y aire acondicionado"
    ],
    "SUMINISTRO DE AGUA, ACTIVIDADES DE SANEAMIENTO, GESTIÓN DE RESIDUOS Y DESCONTAMINACIÓN": [
        "captación, depuración y distribución de agua",
        "recogida y tratamiento de aguas residuales",
        "actividades de recogida, tratamiento y eliminación de residuos",
        "actividades de descontaminación y otros servicios de gestión de residuos"
    ],
    "CONSTRUCCIÓN": [
        "construcción de edificios",
        "ingeniería civil",
        "actividades de construcción especializada"
    ],
    "COMERCIO AL POR MAYOR Y AL POR MENOR": [
        "comercio al por mayor",
        "comercio al por menor"
    ],
    "TRANSPORTE Y ALMACENAMIENTO": [
        "transporte terrestre y por tubería",
        "transporte marítimo y por vías navegables interiores",
        "transporte aéreo",
        "depósito, almacenamiento y actividades auxiliares del transporte",
        "actividades postales y de mensajería"
    ],
    "HOSTELERÍA": [
        "servicios de alojamiento",
        "servicios de comidas y bebidas"
    ],
    "ACTIVIDADES DE EDICIÓN, RADIODIFUSIÓN Y PRODUCCIÓN Y DISTRIBUCIÓN DE CONTENIDOS": [
        "edición",
        "producción cinematográfica, de vídeo y de programas de televisión, grabación de sonido y edición musical",
        "actividades de programación, radiodifusión, agencias de noticias y otras actividades de distribución de contenidos"
    ],
    "TELECOMUNICACIONES, PROGRAMACIÓN INFORMÁTICA, CONSULTORÍA, INFRAESTRUCTURA INFORMÁTICA Y OTROS SERVICIOS DE INFORMACIÓN": [
        "telecomunicaciones",
        "programación, consultoría y otras actividades relacionadas con la informática",
        "infraestructura informática, tratamiento de datos, hosting y otras actividades de servicios de información"
    ],
    "ACTIVIDADES FINANCIERAS Y DE SEGUROS": [
        "servicios financieros, excepto seguros y fondos de pensiones",
        "seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria",
        "actividades auxiliares a los servicios financieros y a los seguros"
    ],
    "ACTIVIDADES INMOBILIARIAS": [
        "actividades inmobiliarias"
    ],
    "ACTIVIDADES PROFESIONALES, CIENTÍFICAS Y TÉCNICAS": [
        "actividades jurídicas y de contabilidad",
        "actividades de las sedes centrales y consultoría de gestión empresarial",
        "servicios técnicos de arquitectura e ingeniería; ensayos y análisis técnicos",
        "investigación y desarrollo",
        "actividades de publicidad, estudios de mercado, relaciones públicas y comunicación",
        "otras actividades profesionales, científicas y técnicas",
        "actividades veterinarias"
    ],
    "ACTIVIDADES ADMINISTRATIVAS Y SERVICIOS AUXILIARES": [
        "actividades de alquiler",
        "actividades relacionadas con el empleo",
        "actividades de agencias de viajes, operadores turísticos, servicios de reservas y actividades relacionadas",
        "servicios de investigación y seguridad",
        "servicios a edificios y actividades de jardinería",
        "actividades administrativas de oficina y otras actividades auxiliares a las empresas"
    ],
    "ADMINISTRACIÓN PÚBLICA Y DEFENSA; SEGURIDAD SOCIAL OBLIGATORIA": [
        "administración pública y defensa; seguridad social obligatoria"
    ],
    "EDUCACIÓN": [
        "educación"
    ],
    "ACTIVIDADES SANITARIAS Y DE SERVICIOS SOCIALES": [
        "actividades sanitarias",
        "asistencia en establecimientos residenciales",
        "actividades de servicios sociales sin alojamiento"
    ],
    "ACTIVIDADES ARTÍSTICAS, DEPORTIVAS Y DE ENTRETENIMIENTO": [
        "actividades de creación artística y artes escénicas",
        "actividades de bibliotecas, archivos, museos y otras actividades culturales",
        "actividades de juegos de azar y apuestas",
        "actividades deportivas, recreativas y de entretenimiento"
    ],
    "OTROS SERVICIOS": [
        "actividades asociativas",
        "reparación y mantenimiento de ordenadores, artículos personales y enseres domésticos y vehículos de motor y motocicletas",
        "servicios personales"
    ],
    "ACTIVIDADES DE LOS HOGARES COMO EMPLEADORES DE PERSONAL DOMÉSTICO Y COMO PRODUCTORES DE BIENES Y SERVICIOS PARA USO PROPIO": [
        "actividades de los hogares como empleadores de personal doméstico",
        "actividades de los hogares como productores de bienes y servicios para uso propio"
    ],
    "ORGANISMOS EXTRATERRITORIALES": [
        "actividades de organizaciones y organismos extraterritoriales"
    ]
}

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

    return render_template('index.html', empresas=empresas, actividades=ACTIVIDADES_Y_SECTORES)

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

    return render_template('vender_empresa.html', actividades=ACTIVIDADES_Y_SECTORES)

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
    return render_template('editar.html', empresa=empresa, actividades=ACTIVIDADES_Y_SECTORES)

@app.route('/valorar-empresa')
def valorar_empresa():
    return render_template('valorar_empresa.html')

@app.route('/estudio-ahorros')
def estudio_ahorros():
    return render_template('estudio_ahorros.html')

@app.route('/contacto')
def contacto():
    return render_template('contacto.html')

# BLOQUE PARA RENDER
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)


