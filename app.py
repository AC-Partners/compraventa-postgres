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
import locale # Importa el módulo locale para formato numérico
import uuid # Para generar nombres de archivo únicos en GCS y tokens
from datetime import timedelta, datetime # Necesario para generar URLs firmadas temporales y manejar fechas
from decimal import Decimal, InvalidOperation

# IMPORTACIONES PARA GOOGLE CLOUD STORAGE
from google.cloud import storage # Importa la librería cliente de GCS

# IMPORTACIÓN PARA LOGGING
import logging

# Inicialización de la aplicación Flask
app = Flask(__name__)
# Configuración de la clave secreta para la seguridad de Flask (sesiones, mensajes flash, etc.)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-secret-key')

# --- Configuración del logger global ---
logger = logging.getLogger(__name__)
# Para producción, establece el nivel a INFO. Los mensajes DEBUG no se mostrarán.
# Para depuración local, podrías cambiarlo a logging.DEBUG.
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
# ---------------------------------------

# --- PROCESADOR DE CONTEXTO GLOBAL DE JINJA2 ---
# Esta función inyectará 'current_year' en todas las plantillas automáticamente.
@app.context_processor
def inject_global_variables():
    """Inyecta variables globales como el año actual en todas las plantillas."""
    return dict(current_year=datetime.now().year)

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# Asegúrate de que estas variables de entorno están configuradas en Render
CLOUD_STORAGE_BUCKET = os.environ.get('CLOUD_STORAGE_BUCKET')

# Inicializar el cliente de Cloud Storage
storage_client = None # Inicializar a None por defecto
try:
    if CLOUD_STORAGE_BUCKET and os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON'):
        credentials_json = os.environ.get('GCP_SERVICE_ACCOUNT_KEY_JSON')
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"GCS Init: Longitud de GCP_SERVICE_ACCOUNT_KEY_JSON: {len(credentials_json) if credentials_json else 0}")
        try:
            credentials_dict = json.loads(credentials_json)
            storage_client = storage.Client.from_service_account_info(credentials_dict)
            logger.info("Google Cloud Storage client initialized successfully from environment variable.")
        except json.JSONDecodeError as jde:
            logger.error(f"GCS Init: No se pudo parsear GCP_SERVICE_ACCOUNT_KEY_JSON. Error: {jde}")
            storage_client = None
        except Exception as e:
            logger.error(f"GCS Init: Error inesperado al inicializar con from_service_account_info: {e}")
            storage_client = None
    elif CLOUD_STORAGE_BUCKET:
        logger.warning("CLOUD_STORAGE_BUCKET está configurado, pero GCP_SERVICE_ACCOUNT_KEY_JSON no. Intentando credenciales por defecto.")
        storage_client = storage.Client()
    else:
        logger.warning("El nombre del bucket de Google Cloud Storage no está configurado. Las funciones de GCS serán omitidas.")

    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"GCS Init: storage_client está inicializado: {storage_client is not None}")
    if not CLOUD_STORAGE_BUCKET:
        logger.debug("GCS Init: CLOUD_STORAGE_BUCKET no está definido.")

except Exception as e:
    storage_client = None
    logger.error(f"GCS Init: Error general al inicializar Google Cloud Storage client: {e}")
    logger.error("Las funciones de GCS serán omitidas debido al error general de inicialización.")


# Funciones de utilidad para Google Cloud Storage
def upload_to_gcs(file_stream, filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS Upload: Cliente GCS no inicializado o nombre de bucket no configurado. Omitiendo la subida a GCS.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        file_stream.seek(0) # Rebobinar el stream
        blob.upload_from_file(file_stream)
        logger.info(f"GCS Upload: Archivo {filename} subido a GCS.")
        return filename
    except Exception as e:
        logger.error(f"GCS Upload: Error al subir {filename} a GCS: {e}")
        return None

def generate_signed_url(filename):
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS URL: Cliente GCS no inicializado o nombre de bucket no configurado. No se puede generar URL firmada.")
        return None
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        # La URL firmada será válida por 7 días
        url = blob.generate_signed_url(expiration=timedelta(days=7), version='v4')
        # Convertido a debug. No se mostrará en INFO.
        logger.debug(f"GCS URL: URL firmada generada para {filename}.")
        return url
    except Exception as e:
        logger.error(f"GCS URL: Error al generar URL firmada para {filename}: {e}")
        return None

def delete_from_gcs(filename):
    # Convertido a debug. No se mostrará en INFO.
    logger.debug(f"GCS Delete: Intentando eliminar de GCS el archivo: {filename}")
    if not storage_client or not CLOUD_STORAGE_BUCKET:
        logger.warning("GCS Delete: Cliente GCS no inicializado o nombre de bucket no configurado. Omitiendo la eliminación de GCS.")
        return
    try:
        bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
        blob = bucket.blob(filename)
        if blob.exists():
            blob.delete()
            logger.info(f"GCS Delete: Archivo {filename} eliminado de GCS.")
        else:
            logger.warning(f"GCS Delete: Archivo {filename} no encontrado en GCS. No se necesita eliminación.")
    except Exception as e:
        logger.error(f"GCS Delete: Error al eliminar {filename} de GCS: {e}")

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE GOOGLE CLOUD STORAGE
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# INICIO DE LA SECCIÓN DE CONFIGURACIÓN DE LA BASE DE DATOS (PostgreSQL)
# ---------------------------------------------------------------

# Accede a la URL de conexión de la base de datos desde las variables de entorno de Render
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    # Convertido a debug. No se mostrará en INFO.
    logger.debug("DB: Conexión a la base de datos establecida.")
    return conn

# ---------------------------------------------------------------
# FIN DE LA SECCIÓN DE CONFIGURACIÓN DE LA BASE DE DATOS
# ---------------------------------------------------------------

# Cargar actividades y sectores desde el archivo JSON
# Esto debería ser un archivo que subas con tu código.
try:
    with open('actividades_sectores.json', 'r', encoding='utf-8') as f:
        ACTIVIDADES_Y_SECTORES = json.load(f)
except FileNotFoundError:
    logger.error("Error: actividades_sectores.json no encontrado.")
    ACTIVIDADES_Y_SECTORES = {} # Asegura que esté definido incluso si falla

# Provincias de España (pueden cargarse desde un archivo o definirse aquí)
PROVINCIAS_ESPANA = [
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila", "Badajoz", "Barcelona",
    "Bizkaia", "Burgos", "Cáceres", "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba", "Cuenca",
    "Gipuzkoa", "Girona", "Granada", "Guadalajara", "Huelva", "Huesca", "Illes Balears", "Jaén", "La Rioja",
    "Las Palmas", "León", "Lleida", "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense", "Palencia",
    "Pontevedra", "Salamanca", "Santa Cruz de Tenerife", "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel",
    "Toledo", "Valencia", "Valladolid", "Zamora", "Zaragoza", "
