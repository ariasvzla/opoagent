import asyncio
import json
import re
import chainlit as cl
import websockets
from agents.tools import extract_text_from_file


EXTERNAL_WS_URL = "ws://127.0.0.1:8000/ws"
SUGGESTED_PROMPTS = [
    "Analiza esta convocatoria y estructura el temario en bloques relacionales.",
    "Coordina el flujo completo para generar un temario de oposiciones desde el mandato inicial.",
    "Revisa normativamente un bloque de temario y propón mejoras para la redacción.",
]


def parse_parallel_request(raw_prompt: str) -> tuple[str, list[str], int, int]:
    """Parse optional inline TEMAS/PARALLELISM/BATCH_SIZE directives from chat text."""
    text = raw_prompt or ""
    parallelism = 10
    batch_size = 10
    temas: list[str] = []

    parallel_match = re.search(r"(?im)^\s*parallelism\s*:\s*(\d+)\s*$", text)
    if parallel_match:
        try:
            parallelism = max(1, min(int(parallel_match.group(1)), 10))
        except ValueError:
            parallelism = 10

    batch_match = re.search(r"(?im)^\s*batch_size\s*:\s*(\d+)\s*$", text)
    if batch_match:
        try:
            batch_size = max(1, min(int(batch_match.group(1)), 50))
        except ValueError:
            batch_size = 10

    temas_block = re.search(r"(?ims)^\s*temas\s*:\s*$([\s\S]*)", text)
    if temas_block:
        block = temas_block.group(1)
        for line in block.splitlines():
            bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if bullet:
                temas.append(bullet.group(1))

        # Remove directives from the natural-language prompt.
        text = re.sub(r"(?im)^\s*parallelism\s*:\s*\d+\s*$", "", text)
        text = re.sub(r"(?im)^\s*batch_size\s*:\s*\d+\s*$", "", text)
        text = re.sub(r"(?ims)^\s*temas\s*:\s*$[\s\S]*", "", text)

    return text.strip(), temas, parallelism, batch_size


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
    render_lock = asyncio.Lock()
    loader_running = True
    loader_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    loader_index = 0

    async def render(final_text: str | None = None) -> None:
        nonlocal loader_index
        async with render_lock:
            if final_text is not None:
                msg_stream.content = final_text
                await msg_stream.update()
                return

            if loader_running:
                frame = loader_frames[loader_index % len(loader_frames)]
                loader_index += 1
                header = f"{frame} El agente esta trabajando..."
            else:
                header = "✅ Proceso finalizado"

            lines = [header]
            if stage_messages:
                lines.extend(["", *stage_messages])
            msg_stream.content = "\n".join(lines)
            await msg_stream.update()

    async def loader_loop() -> None:
        while loader_running:
            await render()
            await asyncio.sleep(0.35)

    loader_task = asyncio.create_task(loader_loop())

    try:
        attached_files = getattr(message, "elements", None) or []
        normalized_prompt, temas, parallelism, batch_size = parse_parallel_request(message.content)
        payload = json.dumps({
            "prompt": normalized_prompt,
            "files": serialize_uploaded_files(attached_files),
            "temas": temas,
            "parallelism": parallelism,
            "batch_size": batch_size,
        })
        await ws_client.send(payload)

        while True:
            response = await asyncio.wait_for(ws_client.recv(), timeout=84600.0)
            data = json.loads(response)
            event_type = data.get("type")

            if event_type in {
                "stage",
                "subagent_started",
                "subagent_completed",
                "subagent_failed",
                "tool_started",
                "tool_completed",
                "tool_failed",
            }:
                text = data.get("message", "")
                if text:
                    stage_messages.append(text)
                    await render()
                continue

            if event_type == "tool_output_delta":
                delta = data.get("message", "")
                if delta:
                    stage_messages.append(f"  ↳ {delta}")
                    await render()
                continue

            if event_type == "result":
                token = data.get("message", "")
                if token:
                    loader_running = False
                    if stage_messages:
                        await render("\n".join(stage_messages + ["", f"✅ Respuesta final:\n{token}"]))
                    else:
                        await render(token)
                break

            if event_type == "error":
                loader_running = False
                details = data.get("message", "Error desconocido")
                await render("\n".join(stage_messages + [f"\n[Error en la comunicación WS: {details}]"]))
                return

            if event_type == "status":
                continue

            token = data.get("message", data.get("text", ""))
            if token:
                await render(token)

    except asyncio.TimeoutError:
        loader_running = False
        await render("\n[La respuesta del agente no llego a tiempo.]")
    except Exception as e:
        loader_running = False
        await render(f"\n[Error en la comunicacion WS: {str(e)}]")
    finally:
        loader_running = False
        if loader_task:
            loader_task.cancel()
            try:
                await loader_task
            except asyncio.CancelledError:
                pass


@cl.on_chat_end
async def on_chat_end():
    """Se ejecuta cuando el usuario cierra la sesión."""
    ws_client = cl.user_session.get("ws_client")
    if ws_client:
        await ws_client.close()
