# Importaciones necesarias para la aplicación Flask
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from email.message import EmailMessage
import smtplib
import socket
import json # Importa el módulo json para cargar las actividades y sectores

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')
# Carpeta donde se guardarán las imágenes subidas
app.config['UPLOAD_FOLDER'] = 'static/uploads'
# Extensiones de archivo permitidas para las imágenes
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Carga de variables de entorno para la conexión a la base de datos y el envío de emails
DATABASE_URL = os.environ.get('DATABASE_URL')
EMAIL_ORIGEN = os.environ.get('EMAIL_ORIGEN')
EMAIL_DESTINO = os.environ.get('EMAIL_DESTINO')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

# Definición de actividades y sectores en formato JSON (como una cadena de texto)
# Luego se parsea a un diccionario de Python
ACTIVIDADES_Y_SECTORES = '''
{
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
    "Industria alimentaria",
    "Fabricación de bebidas",
    "Industria del tabaco",
    "Industria textil",
    "Confección de prendas de vestir",
    "Industria del cuero y productos relacionados de otros materiales",
    "Industria de la madera y del corcho, excepto muebles; cestería y espartería",
    "Industria del papel",
    "Artes gráficas y reproducción de soportes grabados",
    "Coquerías y refino de petróleo",
    "Industria química",
    "Fabricación de productos farmacéuticos",
    "Fabricación de productos de caucho y plásticos",
    "Fabricación de otros productos minerales no metálicos",
    "Metalurgia",
    "Fabricación de productos metálicos, excepto maquinaria y equipo",
    "Fabricación de productos informáticos, electrónicos y ópticos",
    "Fabricación de material y equipo eléctrico",
    "Fabricación de maquinaria y equipo n.c.o.p.",
    "Fabricación de vehículos de motor, remolques y semirremolques",
    "Fabricación de otro material de transporte",
    "Fabricación de muebles",
    "Otras industrias manufactureras",
    "Reparación, mantenimiento e instalación de maquinaria y equipos"
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
    "Construcción de edificios",
    "Ingeniería civil",
    "Actividades de construcción especializada"
  ],
  "COMERCIO AL POR MAYOR Y AL POR MENOR": [
    "Comercio al por mayor",
    "Comercio al por menor"
  ],
  "TRANSPORTE Y ALMACENAMIENTO": [
    "Transporte terrestre y por tubería",
    "Transporte marítimo y por vías navegables interiores",
    "Transporte aéreo",
    "Depósito, almacenamiento y actividades auxiliares del transporte",
    "Actividades postales y de mensajería"
  ],
  "HOSTELERÍA": [
    "Servicios de alojamiento",
    "Servicios de comidas y bebidas"
  ],
  "ACTIVIDADES DE EDICIÓN, RADIODIFUSIÓN Y PRODUCCIÓN Y DISTRIBUCIÓN DE CONTENIDOS": [
    "Edición",
    "Producción cinematográfica, de vídeo y de programas de televisión, grabación de sonido y edición musical",
    "Actividades de programación, radiodifusión, agencias de noticias y otras actividades de distribución de contenidos"
  ],
  "TELECOMUNICACIONES, PROGRAMACIÓN INFORMÁTICA, CONSULTORÍA, INFRAESTRUCTURA INFORMÁTICA Y OTROS SERVICIOS DE INFORMACIÓN": [
    "Telecomunicaciones",
    "Programación, consultoría y otras actividades relacionadas con la informática",
    "Infraestructura informática, tratamiento de datos, hosting y otras actividades de servicios de información"
  ],
  "ACTIVIDADES FINANCIERAS Y DE SEGUROS": [
    "Servicios financieros, excepto seguros y fondos de pensiones",
    "Seguros, reaseguros y planes de pensiones, excepto seguridad social obligatoria",
    "Actividades auxiliares a los servicios financieros y a los seguros"
  ],
  "ACTIVIDADES INMOBILIARIAS": [
    "Actividades inmobiliarias"
  ],
  "ACTIVIDADES PROFESIONALES, CIENTÍFICAS Y TÉCNICAS": [
    "Actividades jurídicas y de contabilidad",
    "Actividades de las sedes centrales y consultoría de gestión empresarial",
    "Servicios técnicos de arquitectura e ingeniería; ensayos y análisis técnicos",
    "Investigación y desarrollo",
    "Actividades de publicidad, estudios de mercado, relaciones públicas y comunicación",
    "Otras actividades profesionales, científicas y técnicas",
    "Actividades veterinarias"
  ],
  "ACTIVIDADES ADMINISTRATIVAS Y SERVICIOS AUXILIARES": [
    "Actividades de alquiler",
    "Actividades relacionadas con el empleo",
    "Actividades de agencias de viajes, operadores turísticos, servicios de reservas y actividades relacionadas",
    "Servicios de investigación y seguridad",
    "Servicios a edificios y actividades de jardinería",
    "Actividades administrativas de oficina y otras actividades auxiliares a las empresas"
  ],
