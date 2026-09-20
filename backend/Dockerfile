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

# Bring the schema up to date before whatever command runs, so a self-hoster
# who pulls a new version gets the migration applied without a manual step.
#
# `-n local` is not optional: this image is the client plane, and the only
# schema it may touch is the user's own (collection, decks). The shared card
# corpus is migrated by maintainers with `-n knowledge`, never from here.
# There is no unnamed [alembic] section in alembic.ini, so a bare
# `alembic upgrade head` fails rather than guessing.
#
# This is ENTRYPOINT rather than part of CMD on purpose: Dockercompose.dev.yaml
# overrides `command:` to run `fastapi dev`, and an override replaces CMD but
# not ENTRYPOINT — so dev containers stay migrated too.
#
# `sh -c '... exec "$@"' --` runs the migration, then replaces the shell with
# CMD (or the compose override), keeping the server as PID 1 so signals and
# graceful shutdown still work.
ENTRYPOINT ["sh", "-c", "alembic -n local upgrade head && exec \"$@\"", "--"]

CMD ["fastapi", "run", "api/main.py", "--port", "8000"]
