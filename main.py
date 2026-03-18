import os
import json
import imaplib
import email
import datetime
import time
import requests
import asyncio
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

# Credenciales y Variables
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

# Llave para el puente de Google Apps Script
GOOGLE_SCRIPT_URL = os.getenv("GOOGLE_SCRIPT_URL")

# Servidores IMAP (Solo para LEER los correos)
SERVERS = {
    "gmail": {"imap": "imap.gmail.com"},
    "outlook": {"imap": "outlook.office365.com"}
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
# 2. FUNCIONES DE APOYO (HTTP, BD, TWILIO)
# ==========================================

def enviar_respuesta_smtp(destinatario: str, asunto: str, cuerpo: str):
    """Envía el correo saltando el bloqueo de Render mediante un Webhook de Google."""
    try:
        print("🚀 Enviando correo vía HTTP a Google Apps Script...")
        
        payload = {
            "destinatario": destinatario,
            "asunto": asunto,
            "cuerpo": cuerpo
        }
        
        res = requests.post(GOOGLE_SCRIPT_URL, json=payload)
        
        if res.status_code == 200:
            print(f"✅ ¡ÉXITO TOTAL! Correo enviado exitosamente a {destinatario}")
            return True
        else:
            print(f"❌ Error en el puente de Google: {res.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error de conexión HTTP: {e}")
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
# 3. ENDPOINTS (EL CEREBRO CHISMOSO)
# ==========================================

@app.get("/")
def home():
    return {"mensaje": "Secretaria Virtual en línea y usando Google Bridge"}

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
                
                enviado = enviar_respuesta_smtp(correo["remitente"], correo["asunto"], correo["borrador_ia"])
                
                if enviado:
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
    """Busca correos nuevos y manda el aviso al cel (Versión con Logs Detallados)."""
    try:
        print("\n=======================================")
        print("🕵️ INICIANDO BÚSQUEDA DE CORREOS...")
        
        imap_server = SERVERS[EMAIL_PROVIDER]["imap"]
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select('inbox')

        # Usamos la fecha de ayer por si acaso hay problemas de zona horaria
        ayer = datetime.date.today() - datetime.timedelta(days=1)
        fecha = ayer.strftime("%d-%b-%Y")
        
        print(f"📅 Buscando correos NO LEÍDOS desde el {fecha}...")
        status, messages = mail.search(None, f'(UNSEEN SINCE "{fecha}")')
        
        if not messages[0]:
            print("📭 Bandeja limpia. No hay correos no leídos que coincidan.")
            mail.logout()
            return {"status": "sin correos"}

        email_ids = messages[0].split()
        print(f"📬 ¡Bingo! Encontrados {len(email_ids)} correos pendientes.")

        for e_id in email_ids:
            _, data = mail.fetch(e_id, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])
            
            # Decodificar el asunto de forma segura
            asunto_decode = email.header.decode_header(msg['Subject'])[0]
            asunto = asunto_decode[0]
            if isinstance(asunto, bytes): 
                try:
                    asunto = asunto.decode(asunto_decode[1] or 'utf-8')
                except:
                    asunto = asunto.decode('utf-8', errors='ignore')
                    
            remitente = msg.get('From')
            print(f"\n📨 Analizando correo de: {remitente}")
            print(f"📌 Asunto: {asunto}")
            
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode(errors='ignore')
            else:
                cuerpo = msg.get_payload(decode=True).decode(errors='ignore')

            prompt = f"{SYSTEM_PROMPT}\n\nDe: {remitente}\nAsunto: {asunto}\nCuerpo:\n{cuerpo}"
            raw_ia = model.generate_content(prompt)
            
            try:
                analisis = json.loads(raw_ia.text)
                decision = analisis.get("accion")
                print(f"🧠 Decisión de la IA: {decision} | Resumen: {analisis.get('resumen')}")
                
                if decision == "Responder":
                    guardado = guardar_correo_pendiente(remitente, asunto, analisis["borrador"])
                    if guardado:
                        print("💾 Guardado en Cloudflare D1. Avisando por WhatsApp...")
                        enviar_alerta_whatsapp(remitente, asunto, cuerpo, analisis["borrador"])
                    else:
                        print("❌ FALLA: No se pudo guardar en la base de datos.")
                else:
                    print("⏭️ La IA decidió no responder a este correo.")
            except Exception as e:
                print(f"❌ Error leyendo el JSON de la IA: {raw_ia.text} | Error: {e}")
            
        mail.logout()
        print("=======================================\n")
        return {"status": "terminado", "procesados": len(email_ids)}
        
    except Exception as e:
        print(f"🚨 ERROR CRÍTICO EN EL MOTOR DE CORREO: {e}")
        return {"error": str(e)}

# ==========================================
# 4. CRON (TAREA EN SEGUNDO PLANO)
# ==========================================

async def revision_automatica():
    while True:
        await asyncio.sleep(300) # Espera inicial
        await ejecutar_secretaria()

@app.on_event("startup")
async def iniciar_cron():
    asyncio.create_task(revision_automatica())
