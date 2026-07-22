import asyncio
import json
import chainlit as cl
import websockets
from agents.tools import extract_text_from_file


EXTERNAL_WS_URL = "ws://127.0.0.1:8000/ws"
SUGGESTED_PROMPTS = [
    "Analiza esta convocatoria y estructura el temario en bloques relacionales.",
    "Coordina el flujo completo para generar un temario de oposiciones desde el mandato inicial.",
    "Revisa normativamente un bloque de temario y propón mejoras para la redacción.",
]


async def connect_to_backend():
    last_error = None
    for attempt in range(8):
        try:
            return await asyncio.wait_for(
                websockets.connect(
                    EXTERNAL_WS_URL,
                    open_timeout=10,
                    ping_interval=None,
                    ping_timeout=None,
                ),
                timeout=12,
            )
        except Exception as exc:
            last_error = exc
            if attempt < 7:
                await asyncio.sleep(1)
            else:
                raise last_error


def serialize_uploaded_files(uploaded_files):
    if not uploaded_files:
        return []

    serialized = []
    for file_response in uploaded_files:
        path = getattr(file_response, "path", None)
        if not path:
            continue

        serialized.append({
            "name": getattr(file_response, "name", None) or "uploaded_file",
            "content": extract_text_from_file(path),
            "mime_type": getattr(file_response, "mime", None) or getattr(file_response, "type", None) or "",
        })
    return serialized


@cl.on_chat_start
async def on_chat_start():
    """Se ejecuta cuando un usuario abre la interfaz de chat."""
    try:
        ws_client = await connect_to_backend()
        cl.user_session.set("ws_client", ws_client)

        cl.user_session.set("suggested_prompts", SUGGESTED_PROMPTS)

        welcome = cl.Message(content=(
            "## Asistente de documentación y temarios\n"
            "Puedo ayudarte a analizar una convocatoria, estructurar el temario, coordinar el flujo de trabajo y preparar un documento final coherente.\n\n"
            "Puedes adjuntar archivos directamente desde el cuadro de entrada del chat y yo los analizaré junto con tu mensaje."
        ))
        await welcome.send()

    except Exception as e:
        await cl.Message(
            content=f"No se pudo conectar con el servidor WebSocket. Asegúrate de que el backend esté levantado. Detalle: {str(e)}"
        ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Se ejecuta cada vez que el usuario envía un mensaje de texto."""
    ws_client = cl.user_session.get("ws_client")

    if not ws_client:
        await cl.Message(content="No hay una conexión WebSocket activa.").send()
        return

    msg_stream = cl.Message(content="🤔 Estoy preparando tu respuesta...")
    await msg_stream.send()

    stage_messages = []

    try:
        attached_files = getattr(message, "elements", None) or []
        payload = json.dumps({
            "prompt": message.content,
            "files": serialize_uploaded_files(attached_files),
        })
        await ws_client.send(payload)

        while True:
            response = await asyncio.wait_for(ws_client.recv(), timeout=300.0)
            data = json.loads(response)
            event_type = data.get("type")

            if event_type == "stage":
                text = data.get("message", "")
                if text:
                    stage_messages.append(text)
                    msg_stream.content = "\n".join(stage_messages)
                    await msg_stream.update()
                continue

            if event_type == "result":
                token = data.get("message", "")
                if token:
                    if stage_messages:
                        msg_stream.content = "\n".join(stage_messages + ["", f"✅ Respuesta final:\n{token}"])
                    else:
                        msg_stream.content = token
                    await msg_stream.update()
                break

            if event_type == "error":
                details = data.get("message", "Error desconocido")
                msg_stream.content = "\n".join(stage_messages + [f"\n[Error en la comunicación WS: {details}]"])
                await msg_stream.update()
                return

            if event_type == "status":
                continue

            token = data.get("message", data.get("text", ""))
            if token:
                msg_stream.content = token
                await msg_stream.update()

    except asyncio.TimeoutError:
        msg_stream.content = "\n[La respuesta del agente no llegó a tiempo.]"
        await msg_stream.update()
    except Exception as e:
        msg_stream.content = f"\n[Error en la comunicación WS: {str(e)}]"
        await msg_stream.update()


@cl.on_chat_end
async def on_chat_end():
    """Se ejecuta cuando el usuario cierra la sesión."""
    ws_client = cl.user_session.get("ws_client")
    if ws_client:
        await ws_client.close()
