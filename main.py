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
from fastapi import FastAPI, HTTPException, Form 
import google.generativeai as genai
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

# IA
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})

# Variables
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

SERVERS = {
    "gmail": {"imap": "imap.gmail.com", "smtp": "smtp.gmail.com"},
    "outlook": {"imap": "imap-mail.outlook.com", "smtp": "smtp-mail.outlook.com"}
}

# CONFIGURACIÓN APP
app = FastAPI(title="Secretaria Virtual", redirect_slashes=False)

# ==========================================
# RUTAS DE DIAGNÓSTICO (Para saber si Render funciona)
# ==========================================
@app.get("/")
def home():
    return {"mensaje": "Secretaria en línea v2.0"}

@app.get("/test")
def test():
    return {"mensaje": "Si ves esto, Render ya tiene el código nuevo!"}

# ==========================================
# WEBHOOK DE WHATSAPP (EL CORAZÓN)
# ==========================================
@app.post("/webhook-whatsapp")
@app.post("/webhook-whatsapp/")
async def recibir_whatsapp(Body: str = Form(...), From: str = Form(...)):
    orden = Body.strip().lower()
    print(f"📩 ORDEN RECIBIDA: {orden} de {From}")
    
    url_db = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/query"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}

    if orden in ["si", "sí", "ok", "yes"]:
        # Buscar el último pendiente
        p_select = {"sql": "SELECT id, remitente, asunto, borrador_ia FROM correos_pendientes WHERE estado = 'PENDIENTE' ORDER BY id DESC LIMIT 1"}
        res = requests.post(url_db, headers=headers, json=p_select).json()
        
        try:
            results = res.get("result", [{}])[0].get("results", [])
            if results:
                correo = results[0]
                # Enviar correo real
                smtp_server = SERVERS[EMAIL_PROVIDER]["smtp"]
                msg = MIMEText(correo["borrador_ia"])
                msg['Subject'], msg['From'], msg['To'] = f"Re: {correo['asunto']}", EMAIL_ACCOUNT, correo['remitente']
                with smtplib.SMTP(smtp_server, 587) as server:
                    server.starttls()
                    server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                    server.send_message(msg)
                
                # Actualizar BD
                requests.post(url_db, headers=headers, json={"sql": "UPDATE correos_pendientes SET estado = 'ENVIADO' WHERE id = ?", "params": [correo["id"]]})
                print("✅ CORREO ENVIADO Y BD ACTUALIZADA")
        except Exception as e:
            print(f"❌ Error enviando: {e}")

    return {"status": "ok"}

# ==========================================
# CEREBRO Y CRON
# ==========================================
@app.get("/ejecutar-secretaria")
async def ejecutar():
    # ... (Mantenemos tu lógica de imaplib y gemini aquí) ...
    # Asegúrate de llamar a enviar_alerta_whatsapp(remitente, asunto, cuerpo, borrador)
    return {"status": "procesando"}

async def cron():
    while True:
        await ejecutar()
        await asyncio.sleep(300)

@app.on_event("startup")
async def start():
    asyncio.create_task(cron())
