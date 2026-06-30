# Deep Agents FastAPI WebSocket Scaffold

This project exposes a LangChain Deep Agents coordinator through FastAPI HTTP and WebSocket endpoints.

## Endpoints

- `GET /health`
- `POST /invoke`
- `WebSocket /ws`

## Run locally

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## Run the React UI separately

```bash
cd frontend
npm install
npm run dev
```

The Vite app runs on http://localhost:5173 and expects the FastAPI backend on http://localhost:8000.

## Guía para colaboradores

1. Clona el repositorio:

```bash
git clone <repo-url> && cd opoagent
```

2. Prepara el entorno Python:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Configura las variables de entorno:

```bash
cp .env.example .env
# Edita .env con tus credenciales y el nombre del modelo
```

4. Levanta el backend:

```bash
MODEL=<tu-modelo> uvicorn main:app --reload
```

5. Levanta el frontend en una terminal separada:

```bash
cd frontend
npm install
npm run dev
```

6. Abre el navegador en:

- Backend: http://localhost:8000
- Frontend: http://localhost:5173

7. Para contribuir:

- Crea una rama nueva para tu cambio.
- Haz commits pequeños y descriptivos.
- Si modificas el backend, asegúrate de que el frontend siga funcionando.
- Si agregas nuevas rutas, actualiza también la documentación y las notas de uso.

8. Pruebas básicas:

- Verifica `GET /health` en el backend.
- Prueba `POST /invoke` con un prompt sencillo.
- Confirma que el frontend se conecta al backend y muestra la respuesta.

## Build Docker image

```bash
docker build -t deepagents-fastapi .
```

## Run container

```bash
docker run --rm -p 8000:8000 --env-file .env deepagents-fastapi
```

## Test WebSocket

Send JSON like:

```json
{"prompt": "Create a technical document describing the multi-agent architecture and deployment plan."}
```
