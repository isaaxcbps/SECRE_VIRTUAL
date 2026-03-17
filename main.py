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
from fastapi import FastAPI, Form 
import google.generativeai as genai
from dotenv import load_dotenv
from twilio.rest import Client

# ==========================================
# 1. CONFIGURACIÓN E INICIALIZACIÓN
# ==========================================
load_dotenv()

# IA (Gemini)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel(
    'gemini-2.5-flash', 
    generation_config={"response_mime_type": "application/json"}
)

# Credenciales
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "outlook").lower()
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_DATABASE_ID = os.getenv("CLOUDFLARE_DATABASE_ID")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
MI_NUMERO_CELULAR = os.getenv("MI_NUMERO_CELULAR")

# 🚨 AQUÍ ESTÁ LA CORRECCIÓN PARA EL ERROR DE RED DE RENDER
SERVERS = {
    "gmail": {"imap": "imap.gmail.com", "smtp": "smtp.gmail.com"},
    "outlook": {"imap": "outlook.office365.com", "smtp": "smtp.office365.com"}
}

# Inicializar FastAPI con fix para evitar el 404
app = FastAPI(
    title="Secretaria Virtual",
    redirect_slashes=False 
)

SYSTEM_PROMPT = """
Eres una secretaria virtual experta. Analiza el correo y devuelve un JSON:
{
  "prioridad": "Alta",
  "resumen": "Resumen breve",
  "accion": "Responder",
  "borrador": "Texto sugerido de respuesta",
  "tiene_reunion": false,
  "fecha_reunion": "",
  "motivo_reunion": ""
}
"""

# ==========================================
# 2. FUNCIONES DE APOYO (SMTP, BD, TWILIO)
# ==========================================

def enviar_respuesta_smtp(destinatario: str, asunto: str, cuerpo: str):
    """Envía el correo final una vez aprobado."""
    try:
        smtp_server = SERVERS[EMAIL_PROVIDER]["smtp"]
        print(f"📧 Conectando al servidor postal: {smtp_server}...")
        
        msg = MIMEText(cuerpo)
        msg['Subject'] = f"Re: {asunto}"
        msg['From'] = EMAIL_ACCOUNT
        msg['To'] = destinatario

        # Agregamos timeout=15 para evitar que Render aborte la conexión
        with smtplib.SMTP(smtp_server, 587, timeout=15) as server:
            server.starttls()
            server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            server.send_message(msg)
            
        print(f"✅ ¡ÉXITO! Correo enviado exitosamente a {destinatario}")
        return True
    except Exception as e:
        print(f"❌ Error SMTP detallado: {e}")
        return False

def guardar_correo_pendiente(remitente: str, asunto: str, borrador: str):
    """Guarda en Cloudflare D1."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/query"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "sql": "INSERT INTO correos_pendientes (remitente, asunto, borrador_ia, estado) VALUES (?, ?, ?, 'PENDIENTE')",
        "params": [remitente, asunto, borrador]
    }
    res = requests.post(url, headers=headers, json=payload)
    return res.status_code == 200 and res.json().get("success")

def enviar_alerta_whatsapp(remitente: str, asunto: str, cuerpo: str, borrador: str):
    """Envía la notificación con el cuerpo del mensaje original."""
    cliente = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    mensaje = (
        f"🤖 *Secretaria Virtual*\n\n"
        f"👤 *De:* {remitente}\n"
        f"📌 *Asunto:* {asunto}\n"
        f"📝 *Original:* _{cuerpo[:150]}..._\n\n"
        f"💡 *Sugerencia:*\n{borrador}\n\n"
        f"¿Enviar respuesta? (Responde SI o NO)"
    )
    try:
        cliente.messages.create(from_=TWILIO_NUMBER, body=mensaje, to=MI_NUMERO_CELULAR)
        print("📱 WhatsApp enviado.")
        return True
    except Exception as e:
        print(f"❌ Error Twilio: {e}")
        return False

# ==========================================
# 3. ENDPOINTS (EL CEREBRO)
# ==========================================

@app.get("/")
def home():
    return {"mensaje": "Secretaria Virtual en línea"}

@app.get("/test")
def test_ruta():
    return {"mensaje": "Si ves esto, Render está actualizado y listo!"}

@app.post("/webhook-whatsapp")
@app.post("/webhook-whatsapp/")
async def recibir_whatsapp(Body: str = Form(...), From: str = Form(...)):
    """Escucha tu 'SÍ' o 'NO' desde WhatsApp."""
    orden = Body.strip().lower()
    print(f"\n📩 Webhook recibió: '{orden}' desde el celular")
    
    url_db = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/query"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}

    if orden in ["si", "sí", "yes", "ok"]:
        p_select = {"sql": "SELECT id, remitente, asunto, borrador_ia FROM correos_pendientes WHERE estado = 'PENDIENTE' ORDER BY id DESC LIMIT 1"}
        res = requests.post(url_db, headers=headers, json=p_select).json()
        
        try:
            results = res.get("result", [{}])[0].get("results", [])
            if results:
                correo = results[0]
                print(f"⏳ Aprobado. Procesando envío a {correo['remitente']}...")
                
                # 1. Enviar el correo de verdad (Aquí es donde brillará la corrección)
                enviado = enviar_respuesta_smtp(correo["remitente"], correo["asunto"], correo["borrador_ia"])
                
                if enviado:
                    # 2. Marcar como enviado en BD
                    p_update = {"sql": "UPDATE correos_pendientes SET estado = 'ENVIADO' WHERE id = ?", "params": [correo["id"]]}
                    requests.post(url_db, headers=headers, json=p_update)
                    print("💾 Base de datos actualizada a ENVIADO.")
            else:
                print("⚠️ No hay correos pendientes.")
        except Exception as e:
            print(f"❌ Error en aprobación: {e}")

    return {"status": "ok"}

@app.get("/ejecutar-secretaria")
async def ejecutar_secretaria():
    """Busca correos nuevos y manda el aviso al cel."""
    try:
        imap_server = SERVERS[EMAIL_PROVIDER]["imap"]
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select('inbox')

        fecha = datetime.date.today().strftime("%d-%b-%Y")
        _, messages = mail.search(None, f'(UNSEEN SINCE "{fecha}")')
        
        if not messages[0]:
            mail.logout()
            return {"status": "sin correos"}

        for e_id in messages[0].split():
            _, data = mail.fetch(e_id, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])
            asunto = email.header.decode_header(msg['Subject'])[0][0]
            if isinstance(asunto, bytes): asunto = asunto.decode()
            remitente = msg.get('From')
            
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode(errors='ignore')
            else:
                cuerpo = msg.get_payload(decode=True).decode(errors='ignore')

            prompt = f"{SYSTEM_PROMPT}\n\nDe: {remitente}\nAsunto: {asunto}\nCuerpo:\n{cuerpo}"
            raw_ia = model.generate_content(prompt)
            analisis = json.loads(raw_ia.text)
            
            if analisis.get("accion") == "Responder":
                if guardar_correo_pendiente(remitente, asunto, analisis["borrador"]):
                    enviar_alerta_whatsapp(remitente, asunto, cuerpo, analisis["borrador"])
            
        mail.logout()
        return {"status": "terminado"}
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 4. CRON (TAREA EN SEGUNDO PLANO)
# ==========================================

async def revision_automatica():
    while True:
        await asyncio.sleep(300) # Espera inicial
        print("\n⏳ [CRON] Revisando correos...")
        await ejecutar_secretaria()

@app.on_event("startup")
async def iniciar_cron():
    asyncio.create_task(revision_automatica())
