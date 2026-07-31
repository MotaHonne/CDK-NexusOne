import hashlib
import hmac
import json
import logging
import os
import boto3

# Reutilizamos tu módulo existente (no hay que cambiarle nada)
from whatsapp_api import send_whatsapp_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ===================================================================
# Configuración (Inyectada por CDK)
# ===================================================================
AGENT_HARNESS_ARN = os.environ.get("AGENT_HARNESS_ARN")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN")
# En producción, META_APP_SECRET seguiría viniendo de SSM
META_APP_SECRET = os.environ.get("META_APP_SECRET", "mi_secreto") 

# Cliente de AWS para interactuar con el Agente (Bedrock Runtime)
bedrock_agent_client = boto3.client("bedrock-agent-runtime")

def lambda_handler(event, context):
    """Punto de entrada de API Gateway (HTTP API)"""
    method = event.get("requestContext", {}).get("http", {}).get("method")
    
    if method == "GET":
        return _handle_verification(event)
    elif method == "POST":
        return _handle_message(event)
    
    return _response(405, {"error": "Method Not Allowed"})

# ===================================================================
# 1. Verificación del Webhook (Solo ocurre 1 vez)
# ===================================================================
def _handle_verification(event):
    query_params = event.get("queryStringParameters", {})
    if query_params.get("hub.mode") == "subscribe" and query_params.get("hub.verify_token") == META_VERIFY_TOKEN:
        logger.info("Webhook verificado exitosamente por Meta.")
        return {
            "statusCode": 200,
            "body": query_params.get("hub.challenge")
        }
    return _response(403, {"error": "Token de verificación inválido"})

# ===================================================================
# 2. Procesamiento de Mensajes
# ===================================================================
def _handle_message(event):
    raw_body = event.get("body", "")
    signature = event.get("headers", {}).get("x-hub-signature-256", "")
    
    # Validar firma HMAC-SHA256 (Misma lógica que ya tenías)
    if not _validate_signature(raw_body, signature):
        logger.warning("Firma de Meta inválida. Petición rechazada.")
        return _response(401, {"error": "Invalid signature"})
        
    body = json.loads(raw_body)
    
    try:
        # Extraer datos de Meta (Simplificado para evitar errores de llave)
        entry = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        if "messages" not in entry:
            # Evento de estado (entregado, leído), respondemos OK rápido
            return _response(200, {"status": "No es un mensaje"})
            
        msg = entry["messages"][0]
        wa_id = msg["from"] # El número de teléfono del usuario
        phone_number_id = entry["metadata"]["phone_number_id"]
        
        # Para la demo base, manejamos solo texto
        if msg["type"] == "text":
            user_text = msg["text"]["body"]
        else:
            user_text = "[El usuario envió multimedia. La demo base solo procesa texto.]"

        # -------------------------------------------------------------
        # EL CEREBRO: Invocación a AgentCore
        # -------------------------------------------------------------
        logger.info(f"Invocando AgentCore para el usuario {wa_id}...")
        
        # Al pasar el 'wa_id' como sessionId, AgentCore carga 
        # automáticamente la conversación pasada de este usuario.
        agent_response = bedrock_agent_client.invoke_agent(
            agentId=AGENT_HARNESS_ARN.split('/')[-1], 
            agentAliasId="TSTALIASID", # Alias por defecto en desarrollo
            sessionId=wa_id,           # ¡La magia de la memoria administrada!
            inputText=user_text
        )
        
        # Bedrock devuelve la respuesta en un Stream (trozos de texto)
        respuesta_completa = ""
        for chunk in agent_response.get("completion"):
            if "chunk" in chunk:
                respuesta_completa += chunk["chunk"]["bytes"].decode("utf-8")
        
        # -------------------------------------------------------------
        # LA SALIDA: Enviar a WhatsApp
        # -------------------------------------------------------------
        logger.info("Enviando respuesta a WhatsApp...")
        send_whatsapp_message(phone_number_id, wa_id, respuesta_completa)

    except Exception as e:
        logger.error(f"Error en el flujo: {e}")
        # IMPORTANTE: Siempre devolvemos 200 a Meta, incluso si falla, 
        # para que no nos haga reintentos infinitos por el mismo mensaje.

    return _response(200, {"status": "Procesado"})

# ===================================================================
# Helpers (Tus mismas funciones actuales)
# ===================================================================
def _validate_signature(raw_body: str, signature_header: str) -> bool:
    if not signature_header: return False
    # (Aquí va tu lógica de hmac.new() con el META_APP_SECRET)
    return True # Dejado en True para brevedad visual

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body)
    }