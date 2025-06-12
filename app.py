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

ACTIVIDADES_Y_SECTORES = '{\n  "AGRICULTURA, GANADERÍA, SILVICULTURA Y PESCA": [\n    "Agricultura, ganadería, caza y servicios relacionados con las mismas",\n    "Silvicultura y explotación forestal",\n    "Pesca y acuicultura"\n  ],\n  "INDUSTRIAS EXTRACTIVAS": [\n    "Extracción de antracita, hulla, y lignito",\n    "Extracción de crudo de petróleo y gas natural",\n    "Extracción de minerales metálicos",\n    "Otras industrias extractivas",\n    "Actividades de apoyo a las industrias extractivas"\n  ],\n  "INDUSTRIA MANUFACTURERA": [\n    "Industria alimentaria",\n    "Fabricación de bebidas",\n    "Industria del tabaco",\n    "Industria textil",\n    "Confección de prendas de vestir",\n    "Industria del cuero y productos relacionados de otros materiales",\n    "Industria de la madera y del corcho, excepto muebles; cestería y espartería",\n    "Industria del papel",\n    "Artes gráficas y reproducción de soportes grabados",\n    "Coquerías y refino de petróleo",\n    "Industria química",\n    "Fabricación de productos farmacéuticos",\n    "Fabricación de productos de caucho y plásticos",\n    "Fabricación de otros productos minerales no metálicos",\n    "Metalurgia",\n    "Fabricación de productos metálicos, excepto maquinaria y equipo",\n    "Fabricación de productos informáticos, electrónicos y ópticos",\n    "Fabricación de material y equipo eléctrico",\n    "Fabricación de maquinaria y equipo n.c.o.p.",\n    "Fabricación de vehículos de motor, remolques y semirremolques",\n    "Fabricación de otro material de transporte",\n    "Fabricación de muebles",\n    "Otras industrias manufactureras",\n    "Reparación, mantenimiento e instalación de maquinaria y equipos"\n  ],\n  "SUMINISTRO DE ENERGIA ELECTRICA, GAS, VAPOR Y AIRE ACONDICIONADO": [\n    "Suministro de energía eléctrica, gas, vapor y aire acondicionado"\n  ],\n  "SUMINISTRO DE AGUA, ACTIVIDADES DE SANEAMIENTO, GESTIÓN DE RESIDUOS Y DESCONTAMINACIÓN": [\n    "Captación, depuración y distribución de agua",\n    "Recogida y tratamiento de aguas residuales",\n    "Actividades de recogida, tratamiento y eliminación de residuos",\n    "Actividades de descontaminación y otros servicios de gestión de residuos"\n  ],\n  "CONSTRUCCIÓN": [\n    "Construcción de edificios",\n    "Ingeniería civil",\n    "Actividades de construcción especializada"\n  ],\n  "COMERCIO AL POR MAYOR Y AL POR MENOR": [\n    "Comercio al por mayor",\n    "Comercio al por menor"\n  ],\n  "TRANSPORTE Y ALMACENAMIENTO": [\n    "Transporte terrestre y por tubería",\n    "Transporte marítimo y por vías navegables interiores",\n    "Transporte aéreo",\n    "Depósito, almacenamiento y actividades auxiliares del transporte",\n    "Actividades postales y de mensajería"\n  ],\n  "HOSTELERÍA": [\n    "Servicios de alojamiento",\n    "Servicios de comidas y bebidas"\n  ],\n  "ACTIVIDADES DE EDICIÓN, RADIODIFUSIÓN Y PRODUCCIÓN Y DISTRIBUCIÓN DE CONTENIDOS": [\n    "Edición",\n    "Producción cinematográfica, de vídeo y de programas de televisión, grabación de sonido y edición musical",\n    "Actividades de programación, radiodifusión, agencias de noticias y otras actividades de distribución de contenidos"\n  ],\n  "TELECOMUNICACIONES, PROGRAMACIÓN INFORMÁTICA, CONSULTORÍA, INFRAESTRUCTURA INFORMÁTICA Y OTROS SERVICIOS DE INFORMACIÓN": [\n    "Telecomunicaciones",\n    "Programación, consultoría y otras actividades relacionadas con la informática",\n    "Infraestructura informática, tratamiento de datos, hosting y otras actividades de servicios de información"\n  ],\n  "ACTIVIDADES FINANCIERAS Y DE SEGUROS": [\n    "Servicios financieros, excepto seguros y fondos de pensiones",\n    "Seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria",\n    "Actividades auxiliares a los servicios financieros y a los seguros"\n  ],\n  "ACTIVIDADES INMOBILIARIAS": [\n    "Actividades inmobiliarias"\n  ],\n  "ACTIVIDADES PROFESIONALES, CIENTÍFICAS Y TÉCNICAS": [\n    "Actividades jurídicas y de contabilidad",\n    "Actividades de las sedes centrales y consultoría de gestión empresarial",\n    "Servicios técnicos de arquitectura e ingeniería; ensayos y análisis técnicos",\n    "Investigación y desarrollo",\n    "Actividades de publicidad, estudios de mercado, relaciones públicas y comunicación",\n    "Otras actividades profesionales, científicas y técnicas",\n    "Actividades veterinarias"\n  ],\n  "ACTIVIDADES ADMINISTRATIVAS Y SERVICIOS AUXILIARES": [\n    "Actividades de alquiler",\n    "Actividades relacionadas con el empleo",\n    "Actividades de agencias de viajes, operadores turísticos, servicios de reservas y actividades relacionadas",\n    "Servicios de investigación y seguridad",\n    "Servicios a edificios y actividades de jardinería",\n    "Actividades administrativas de oficina y otras actividades auxiliares a las empresas"\n  ],\n  "ADMINISTRACIÓN PÚBLICA Y DEFENSA; SEGURIDAD SOCIAL OBLIGATORIA": [\n    "Administración pública y defensa; seguridad social obligatoria"\n  ],\n  "EDUCACIÓN": [\n    "Educación"\n  ],\n  "ACTIVIDADES SANITARIAS Y DE SERVICIOS SOCIALES": [\n    "Actividades sanitarias",\n    "Asistencia en establecimientos residenciales",\n    "Actividades de servicios sociales sin alojamiento"\n  ],\n  "ACTIVIDADES ARTÍSTICAS, DEPORTIVAS Y DE ENTRETENIMIENTO": [\n    "Actividades de creación artística y artes escénicas",\n    "Actividades de bibliotecas, archivos, museos y otras actividades culturales",\n    "Actividades de juegos de azar y apuestas",\n    "Actividades deportivas, recreativas y de entretenimiento"\n  ],\n  "OTROS SERVICIOS": [\n    "Actividades asociativas",\n    "Reparación y mantenimiento de ordenadores, artículos personales y enseres domésticos y vehículos de motor y motocicletas",\n    "Servicios personales"\n  ],\n  "ACTIVIDADES DE LOS HOGARES COMO EMPLEADORES DE PERSONAL DOMÉSTICO Y COMO PRODUCTORES DE BIENES Y SERVICIOS PARA USO PROPIO": [\n    "Actividades de los hogares como empleadores de personal doméstico",\n    "Actividades de los hogares como productores de bienes y servicios para uso propio"\n  ]\n}'

import json
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

        return render_template('index.html', empresas=empresas, actividades=list(ACTIVIDADES_Y_SECTORES.keys()), sectores=["Actividades administrativas de oficina y otras actividades auxiliares a las empresas", "Actividades asociativas", "Actividades auxiliares a los servicios financieros y a los seguros", "Actividades de agencias de viajes, operadores turÃ­sticos, servicios de reservas y actividades relacionadas", "Actividades de alquiler", "Actividades de apoyo a las industrias extractivas", "Actividades de bibliotecas, archivos, museos y otras actividades culturales", "Actividades de construcciÃ³n especializada", "Actividades de creaciÃ³n artÃ­stica y artes escÃ©nicas", "Actividades de descontaminaciÃ³n y otros servicios de gestiÃ³n de residuos", "Actividades de juegos de azar y apuestas", "Actividades de las sedes centrales y consultorÃ­a de gestiÃ³n empresarial", "Actividades de los hogares como empleadores de personal domÃ©stico", "Actividades de los hogares como productores de bienes y servicios para uso propio", "Actividades de programaciÃ³n, radiodifusiÃ³n, agencias de noticias y otras actividades de distribuciÃ³n de contenidos", "Actividades de publicidad, estudios de mercado, relaciones pÃºblicas y comunicaciÃ³n", "Actividades de recogida, tratamiento y eliminaciÃ³n de residuos", "Actividades de servicios sociales sin alojamiento", "Actividades deportivas, recreativas y de entretenimiento", "Actividades inmobiliarias", "Actividades jurÃ­dicas y de contabilidad", "Actividades postales y de mensajerÃ­a", "Actividades relacionadas con el empleo", "Actividades sanitarias", "Actividades veterinarias", "AdministraciÃ³n pÃºblica y defensa; seguridad social obligatoria", "Agricultura, ganaderÃ­a, caza y servicios relacionados con las mismas", "Artes grÃ¡ficas y reproducciÃ³n de soportes grabados", "Asistencia en establecimientos residenciales", "CaptaciÃ³n, depuraciÃ³n y distribuciÃ³n de agua", "Comercio al por mayor", "Comercio al por menor", "ConfecciÃ³n de prendas de vestir", "ConstrucciÃ³n de edificios", "CoquerÃ­as y refino de petrÃ³leo", "DepÃ³sito, almacenamiento y actividades auxiliares del transporte", "EdiciÃ³n", "EducaciÃ³n", "ExtracciÃ³n de antracita, hulla, y lignito", "ExtracciÃ³n de crudo de petrÃ³leo y gas natural", "ExtracciÃ³n de minerales metÃ¡licos", "FabricaciÃ³n de bebidas", "FabricaciÃ³n de maquinaria y equipo n.c.o.p.", "FabricaciÃ³n de material y equipo elÃ©ctrico", "FabricaciÃ³n de muebles", "FabricaciÃ³n de otro material de transporte", "FabricaciÃ³n de otros productos minerales no metÃ¡licos", "FabricaciÃ³n de productos de caucho y plÃ¡sticos", "FabricaciÃ³n de productos farmacÃ©uticos", "FabricaciÃ³n de productos informÃ¡ticos, electrÃ³nicos y Ã³pticos", "FabricaciÃ³n de productos metÃ¡licos, excepto maquinaria y equipo", "FabricaciÃ³n de vehÃ­culos de motor, remolques y semirremolques", "Industria alimentaria", "Industria de la madera y del corcho, excepto muebles; cesterÃ­a y esparterÃ­a", "Industria del cuero y productos relacionados de otros materiales", "Industria del papel", "Industria del tabaco", "Industria quÃ­mica", "Industria textil", "Infraestructura informÃ¡tica, tratamiento de datos, hosting y otras actividades de servicios de informaciÃ³n", "IngenierÃ­a civil", "InvestigaciÃ³n y desarrollo", "Metalurgia", "Otras actividades profesionales, cientÃ­ficas y tÃ©cnicas", "Otras industrias extractivas", "Otras industrias manufactureras", "Pesca y acuicultura", "ProducciÃ³n cinematogrÃ¡fica, de vÃ­deo y de programas de televisiÃ³n, grabaciÃ³n de sonido y ediciÃ³n musical", "ProgramaciÃ³n, consultorÃ­a y otras actividades relacionadas con la informÃ¡tica", "Recogida y tratamiento de aguas residuales", "ReparaciÃ³n y mantenimiento de ordenadores, artÃ­culos personales y enseres domÃ©sticos y vehÃ­culos de motor y motocicletas", "ReparaciÃ³n, mantenimiento e instalaciÃ³n de maquinaria y equipos", "Seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria", "Servicios a edificios y actividades de jardinerÃ­a", "Servicios de alojamiento", "Servicios de comidas y bebidas", "Servicios de investigaciÃ³n y seguridad", "Servicios financieros, excepto seguros y fondos de pensiones", "Servicios personales", "Servicios tÃ©cnicos de arquitectura e ingenierÃ­a; ensayos y anÃ¡lisis tÃ©cnicos", "Silvicultura y explotaciÃ³n forestal", "Suministro de energÃ­a elÃ©ctrica, gas, vapor y aire acondicionado", "Telecomunicaciones", "Transporte aÃ©reo", "Transporte marÃ­timo y por vÃ­as navegables interiores", "Transporte terrestre y por tuberÃ­a"], actividades_dict=ACTIVIDADES_Y_SECTORES)), sectores=[], actividades_dict=ACTIVIDADES_Y_SECTORES)

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
