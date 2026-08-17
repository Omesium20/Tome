# python:3.12, not the newer 3.13/3.14 the FastAPI docs default to — chromadb and
# sentence-transformers (-> torch) lag on prebuilt wheels for brand-new Python
# releases, and slim can't fall back to compiling those from source cheaply.
FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Imports in this app (e.g. `from api.routes import ...`, `from logging_config import ...`)
# are relative to backend/ as root, same assumption pytest.ini makes — so the whole
# backend/ tree is copied in as-is rather than nesting it under an app/ subfolder.
COPY . .

EXPOSE 8000

CMD ["fastapi", "run", "api/main.py", "--port", "8000"]
