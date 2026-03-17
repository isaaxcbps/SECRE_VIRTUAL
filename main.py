import os
import json
import imaplib
import smtplib
import email
import datetime
import time
import requests
import asyncio
from email.mime.text import MIMEText
from fastapi import FastAPI, HTTPException
import google.generativeai as genai
from dotenv import load_dotenv
from twilio.rest import Client

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y CREDENCIALES
# ==========================================
load_dotenv()

# IA (Gemini)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Variables de Correo
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "outlook").lower()

# Variables de Base de Datos (Cloudflare D1)
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_DATABASE_ID = os.getenv("CLOUDFLARE_DATABASE_ID")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

# Variables de WhatsApp (Twilio)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
MI_NUMERO_CELULAR = os.getenv("MI_NUMERO_CELULAR")

SERVERS = {
    "gmail": {"imap": "imap.gmail.com", "smtp": "smtp.gmail.com"},
    "outlook": {"imap": "imap-mail.outlook.com", "smtp": "smtp-mail.outlook.com"}
}

app = FastAPI(title="Secretaria Virtual - The Zero-Budget Agent")

# Configurar modelo de IA forzando salida JSON pura
model = genai.GenerativeModel(
    'gemini-2.5-flash',
    generation_config={"response_mime_type": "application/json"}
)

SYSTEM_PROMPT = """
Eres una secretaria virtual experta. Analiza el siguiente correo.
Debes devolver un JSON con esta estructura exacta:
{
  "prioridad": "Alta", // o Media, Baja
  "resumen": "Resumen de una sola línea",
  "accion": "Responder", // o Ignorar, Agendar
  "borrador": "Si la accion es Responder, escribe aquí el texto del correo. Si no, déjalo vacío.",
  "tiene_reunion": true, // o false
  "fecha_reunion": "YYYY-MM-DD HH:MM", // Extrae la fecha si aplica, o deja vacío
  "motivo_reunion": "De qué trata la reunión"
}
"""

# ==========================================
# 2. HERRAMIENTAS (CORREO, BD, WHATSAPP)
# ==========================================

def enviar_respuesta_smtp(destinatario: str, asunto: str, cuerpo: str):
    """Envía un correo usando el protocolo SMTP."""
    smtp_server = SERVERS[EMAIL_PROVIDER]["smtp"]
    msg = MIMEText(cuerpo)
    msg['Subject'] = f"Re: {asunto}"
    msg['From'] = EMAIL_ACCOUNT
    msg['To'] = destinatario

    with smtplib.SMTP(smtp_server, 587) as server:
        server.starttls()
        server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        server.send_message(msg)
    print(f"✅ Correo enviado a {destinatario}")

def guardar_correo_pendiente(remitente: str, asunto: str, borrador: str):
    """Guarda el borrador en Cloudflare D1 esperando aprobación."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Payload ajustado a formato de diccionario (más compatible con la API REST)
    payload = {
        "sql": "INSERT INTO correos_pendientes (remitente, asunto, borrador_ia, estado) VALUES (?, ?, ?, 'PENDIENTE')",
        "params": [remitente, asunto, borrador]
    }
    
    try:
        respuesta = requests.post(url, headers=headers, json=payload)
        # Verificamos si la respuesta fue exitosa
        if respuesta.status_code == 200 and respuesta.json().get("success"):
            print(f"💾 Borrador de {remitente} guardado en BD exitosamente.")
            return True
        else:
            # AQUÍ ESTÁ EL CHISME: Si falla, nos dirá por qué
            print(f"❌ Error de Cloudflare ({respuesta.status_code}): {respuesta.text}")
            return False
    except Exception as e:
        print(f"❌ Error crítico de conexión con la BD: {e}")
        return False

def enviar_alerta_whatsapp(remitente: str, asunto: str, borrador: str):
    """Envía un mensaje de WhatsApp al usuario para pedir aprobación."""
    cliente = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    mensaje = (
        f"🤖 *Secretaria Virtual*\n\n"
        f"Tienes un nuevo correo importante.\n"
        f"👤 *De:* {remitente}\n"
        f"📌 *Asunto:* {asunto}\n\n"
        f"💡 *Sugerencia de respuesta:*\n{borrador}\n\n"
        f"¿Deseas que envíe esta respuesta? (Responde SÍ o NO)"
    )
    try:
        message = cliente.messages.create(
            from_=TWILIO_NUMBER,
            body=mensaje,
            to=MI_NUMERO_CELULAR
        )
        print(f"📱 WhatsApp enviado con éxito! (ID: {message.sid})")
        return True
    except Exception as e:
        print(f"❌ Error enviando WhatsApp por Twilio: {e}")
        return False

# ==========================================
# 3. EL CEREBRO PRINCIPAL
# ==========================================

@app.get("/ejecutar-secretaria")
async def ejecutar_secretaria():
    try:
        imap_server = SERVERS[EMAIL_PROVIDER]["imap"]
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select('inbox')

        fecha_hoy = datetime.date.today().strftime("%d-%b-%Y")
        criterio_busqueda = f'(UNSEEN SINCE "{fecha_hoy}")'
        status, messages = mail.search(None, criterio_busqueda)
        
        if not messages[0]:
            mail.logout()
            return {"status": "ok", "mensaje": "No hay correos nuevos."}

        email_ids = messages[0].split()
        resultados = []

        for e_id in email_ids:
            status, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    asunto_decode = email.header.decode_header(msg['Subject'])[0]
                    asunto = asunto_decode[0]
                    if isinstance(asunto, bytes):
                        asunto = asunto.decode(asunto_decode[1] or 'utf-8')
                        
                    remitente = msg.get('From')
                    cuerpo = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                cuerpo = part.get_payload(decode=True).decode(errors='ignore')
                    else:
                        cuerpo = msg.get_payload(decode=True).decode(errors='ignore')

                    print(f"⏳ Analizando correo de: {remitente}...")
                    prompt_completo = f"{SYSTEM_PROMPT}\n\nDe: {remitente}\nAsunto: {asunto}\nCuerpo:\n{cuerpo}"
                    respuesta_ia = model.generate_content(prompt_completo)
                    analisis = json.loads(respuesta_ia.text)
                    
                    if analisis.get("accion") == "Responder" and analisis.get("borrador"):
                        guardado = guardar_correo_pendiente(remitente, asunto, analisis["borrador"])
                        if guardado:
                            enviado_wa = enviar_alerta_whatsapp(remitente, asunto, analisis["borrador"])
                            if enviado_wa:
                                analisis["estado"] = "Esperando tu orden por WhatsApp"
                            else:
                                analisis["estado"] = "Guardado en BD, pero falló el aviso de WhatsApp"
                        else:
                            analisis["estado"] = "Falló el guardado en la Base de Datos"
                    
                    resultados.append({
                        "remitente": remitente,
                        "asunto": asunto,
                        "decision": analisis
                    })
            time.sleep(4) 

        mail.logout()
        return {"status": "ok", "procesados": len(resultados), "detalles": resultados}
    except Exception as e:
        print(f"❌ Error principal en la ejecución: {e}")
        return {"error": str(e)}

# ==========================================
# 4. TAREAS EN SEGUNDO PLANO (CRON LOCAL)
# ==========================================

async def revision_automatica():
    while True:
        print("\n⏳ [CRON] Buscando correos nuevos...")
        await ejecutar_secretaria()
        await asyncio.sleep(300) 

@app.on_event("startup")
async def iniciar_cron():
    asyncio.create_task(revision_automatica())

@app.get("/")
def ruta_principal():
    return {"mensaje": "Secretaria Virtual en línea y lista para operar."}