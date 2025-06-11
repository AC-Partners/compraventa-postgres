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

# Diccionario completo de actividades y sectores
ACTIVIDADES_Y_SECTORES = {
    "AGRICULTURA, GANADERÍA, SILVICULTURA Y PESCA": [
        "Agricultura, ganadería, caza y servicios relacionados con las mismas",
        "Silvicultura y explotación forestal",
        "Pesca y acuicultura"
    ],
    "INDUSTRIAS EXTRACTIVAS": [
        "Extracción de antracita, hulla, y lignito",
        "Extracción de crudo de petróleo y gas natural",
        "Extracción de minerales metálicos",
        "Otras industrias extractivas",
        "Actividades de apoyo a las industrias extractivas"
    ],
    "INDUSTRIA MANUFACTURERA": [
        "Industria alimentaria", "Fabricación de bebidas", "Industria del tabaco",
        "Industria textil", "Confección de prendas de vestir",
        "Industria del cuero y productos relacionados de otros materiales",
        "Industria de la madera y del corcho, excepto muebles; cestería y espartería",
        "Industria del papel", "Artes gráficas y reproducción de soportes grabados",
        "Coquerías y refino de petróleo", "Industria química",
        "Fabricación de productos farmacéuticos", "Fabricación de productos de caucho y plásticos",
        "Fabricación de otros productos minerales no metálicos", "Metalurgia",
        "Fabricación de productos metálicos, excepto maquinaria y equipo",
        "Fabricación de productos informáticos, electrónicos y ópticos",
        "Fabricación de material y equipo eléctrico",
        "Fabricación de maquinaria y equipo n.c.o.p.",
        "Fabricación de vehículos de motor, remolques y semirremolques",
        "Fabricación de otro material de transporte", "Fabricación de muebles",
        "Otras industrias manufactureras", "Reparación, mantenimiento e instalación de maquinaria y equipos"
    ],
    "SUMINISTRO DE ENERGIA ELECTRICA, GAS, VAPOR Y AIRE ACONDICIONADO": [
        "Suministro de energía eléctrica, gas, vapor y aire acondicionado"
    ],
    "SUMINISTRO DE AGUA, ACTIVIDADES DE SANEAMIENTO, GESTIÓN DE RESIDUOS Y DESCONTAMINACIÓN": [
        "Captación, depuración y distribución de agua",
        "Recogida y tratamiento de aguas residuales",
        "Actividades de recogida, tratamiento y eliminación de residuos",
        "Actividades de descontaminación y otros servicios de gestión de residuos"
    ],
    "CONSTRUCCIÓN": [
        "Construcción de edificios", "Ingeniería civil", "Actividades de construcción especializada"
    ],
    "COMERCIO AL POR MAYOR Y AL POR MENOR": [
        "Comercio al por mayor", "Comercio al por menor"
    ],
    "TRANSPORTE Y ALMACENAMIENTO": [
        "Transporte terrestre y por tubería", "Transporte marítimo y por vías navegables interiores",
        "Transporte aéreo", "Depósito, almacenamiento y actividades auxiliares del transporte",
        "Actividades postales y de mensajería"
    ],
    "HOSTELERÍA": [
        "Servicios de alojamiento", "Servicios de comidas y bebidas"
    ],
    "ACTIVIDADES DE EDICIÓN, RADIODIFUSIÓN Y PRODUCCIÓN Y DISTRIBUCIÓN DE CONTENIDOS": [
        "Edición", "Producción cinematográfica, de vídeo y de programas de televisión, grabación de sonido y edición musical",
        "Actividades de programación, radiodifusión, agencias de noticias y otras actividades de distribución de contenidos"
    ],
    "TELECOMUNICACIONES, PROGRAMACIÓN INFORMÁTICA, CONSULTORÍA, INFRAESTRUCTURA INFORMÁTICA Y OTROS SERVICIOS DE INFORMACIÓN": [
        "Telecomunicaciones", "Programación, consultoría y otras actividades relacionadas con la informática",
        "Infraestructura informática, tratamiento de datos, hosting y otras actividades de servicios de información"
    ],
    "ACTIVIDADES FINANCIERAS Y DE SEGUROS": [
        "Servicios financieros, excepto seguros y fondos de pensiones",
        "Seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria",
        "Actividades auxiliares a los servicios financieros y a los seguros"
    ],
    "ACTIVIDADES INMOBILIARIAS": ["Actividades inmobiliarias"],
    "ACTIVIDADES PROFESIONALES, CIENTÍFICAS Y TÉCNICAS": [
        "Actividades jurídicas y de contabilidad", "Actividades de las sedes centrales y consultoría de gestión empresarial",
        "Servicios técnicos de arquitectura e ingeniería; ensayos y análisis técnicos", "Investigación y desarrollo",
        "Actividades de publicidad, estudios de mercado, relaciones públicas y comunicación",
        "Otras actividades profesionales, científicas y técnicas", "Actividades veterinarias"
    ],
    "ACTIVIDADES ADMINISTRATIVAS Y SERVICIOS AUXILIARES": [
        "Actividades de alquiler", "Actividades relacionadas con el empleo",
        "Actividades de agencias de viajes, operadores turísticos, servicios de reservas y actividades relacionadas",
        "Servicios de investigación y seguridad", "Servicios a edificios y actividades de jardinería",
        "Actividades administrativas de oficina y otras actividades auxiliares a las empresas"
    ],
    "ADMINISTRACIÓN PÚBLICA Y DEFENSA; SEGURIDAD SOCIAL OBLIGATORIA": [
        "Administración pública y defensa; seguridad social obligatoria"
    ],
    "EDUCACIÓN": ["Educación"],
    "ACTIVIDADES SANITARIAS Y DE SERVICIOS SOCIALES": [
        "Actividades sanitarias", "Asistencia en establecimientos residenciales", "Actividades de servicios sociales sin alojamiento"
    ],
    "ACTIVIDADES ARTÍSTICAS, DEPORTIVAS Y DE ENTRETENIMIENTO": [
        "Actividades de creación artística y artes escénicas", "Actividades de bibliotecas, archivos, museos y otras actividades culturales",
        "Actividades de juegos de azar y apuestas", "Actividades deportivas, recreativas y de entretenimiento"
    ],
    "OTROS SERVICIOS": [
        "Actividades asociativas",
        "Reparación y mantenimiento de ordenadores, artículos personales y enseres domésticos y vehículos de motor y motocicletas",
        "Servicios personales"
    ],
    "ACTIVIDADES DE LOS HOGARES COMO EMPLEADORES DE PERSONAL DOMÉSTICO Y COMO PRODUCTORES DE BIENES Y SERVICIOS PARA USO PROPIO": [
        "Actividades de los hogares como empleadores de personal doméstico",
        "Actividades de los hogares como productores de bienes y servicios para uso propio"
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

    return render_template('index.html', empresas=empresas, actividades=ACTIVIDADES_Y_SECTORES.keys(), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES)

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
        beneficio_impuestos = request.form

