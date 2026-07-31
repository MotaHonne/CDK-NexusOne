"""
whatsapp_api.py  --  Envio por la Graph API de Meta (modulo COMPARTIDO)
-----------------------------------------------------------------------
Centraliza el envio de WhatsApp (texto e imagen) y la lectura del token de Meta
desde SSM, para que lo usen TANTO 'message-processor-kidzania' COMO
'proactive-sender-kidzania' sin duplicar codigo (decision de arquitectura D3).

El 'phone_number_id' NO se configura aqui: se pasa como argumento en cada envio.
  - En message-processor llega en el evento entrante de Meta.
  - En proactive-sender se lee del item ORDER en DynamoDB (se guardo al cotizar).
Asi la respuesta siempre sale por el mismo numero por el que entro la conversacion.

Este modulo debe incluirse en el paquete de despliegue de AMBAS Lambdas.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()

REGION = os.environ.get("AWS_REGION_ENV", "us-east-1")
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v25.0")
META_TOKEN_PARAM = os.environ.get("SSM_GRAPH_API_TOKEN_NAME")

_ssm = boto3.client("ssm", region_name=REGION)
_meta_token_cache = None


def _get_meta_token() -> str | None:
    """Lee el token de la Graph API de Meta desde SSM (cacheado por contenedor)."""
    global _meta_token_cache
    if _meta_token_cache is not None:
        return _meta_token_cache
    try:
        resp = _ssm.get_parameter(Name=META_TOKEN_PARAM, WithDecryption=True)
        _meta_token_cache = resp["Parameter"]["Value"]
        return _meta_token_cache
    except ClientError as e:
        logger.error("No se pudo leer el token de Meta desde SSM: %s", e)
        return None


def send_whatsapp_message(phone_number_id: str, to_wa_id: str, text: str) -> bool:
    """
    Envia un mensaje de texto a la familia usando la Graph API de Meta.

    Args:
        phone_number_id: ID del numero de WhatsApp Business.
        to_wa_id:        numero de la familia (wa_id).
        text:            texto de la respuesta.
    """
    token = _get_meta_token()
    if not token:
        logger.error("Sin token de Meta; no se puede enviar la respuesta.")
        return False

    url = (
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            logger.info("Mensaje enviado a %s. Respuesta de Meta: %s",
                        to_wa_id, resp_body)
            return True
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", errors="replace")
        logger.error("Meta rechazo el envio (HTTP %s): %s", e.code, detalle)
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo el envio a la Graph API de Meta: %s", e)
        return False


def send_whatsapp_image(phone_number_id: str, to_wa_id: str,
                        png_bytes: bytes, filename: str,
                        caption: str = None) -> bool:
    """
    Envia una imagen PNG (p. ej. el QR de boletos) a la familia por WhatsApp:
      1. Sube los bytes a /media de Meta y obtiene un media_id.
      2. Envia un mensaje type=image con ese media_id y un caption.

    Los bytes del PNG ya vienen en memoria (desde la tool de boletos o desde el
    proactive-sender), asi que se suben directo a Meta sin pasar por S3.

    Devuelve True si Meta acepto el envio.
    """
    token = _get_meta_token()
    if not token:
        logger.error("Sin token de Meta; no se puede enviar la imagen.")
        return False

    # --- 1. Subir el PNG a /media de Meta como multipart/form-data ---
    media_url = (
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/"
        f"{phone_number_id}/media"
    )
    boundary = f"----kai{int(time.time())}"

    body_parts = []
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(
        b'Content-Disposition: form-data; name="messaging_product"\r\n'
        b"\r\nwhatsapp"
    )
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(
        b'Content-Disposition: form-data; name="type"\r\n'
        b"\r\nimage/png"
    )
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode()
        + png_bytes
    )
    body_parts.append(f"--{boundary}--".encode())
    body = b"\r\n".join(body_parts)

    req = urllib.request.Request(
        media_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            media_id = data.get("id")
            if not media_id:
                logger.error("Meta no devolvio media_id: %s", data)
                return False
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", errors="replace")
        logger.error("Meta rechazo /media (HTTP %s): %s", e.code, detalle)
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo la subida del PNG a /media de Meta: %s", e)
        return False

    # --- 2. Enviar el mensaje de tipo image ---
    msg_url = (
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "image",
        "image": {
            "id": media_id,
            **({"caption": caption} if caption else {}),
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        msg_url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Imagen QR enviada a %s (media_id=%s)",
                        to_wa_id, media_id)
            return True
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", errors="replace")
        logger.error("Meta rechazo el image (HTTP %s): %s", e.code, detalle)
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo el envio del mensaje image a Meta: %s", e)
        return False
