# Thin wrapper around the official image, kept as its own Dockerfile (rather than
# referencing `postgres:16-alpine` directly in Dockercompose.yaml) so init scripts
# or config can be added here later without touching the compose file.
#
# Anything placed in /docker-entrypoint-initdb.d/ (e.g. COPY ./init/ ...) runs once,
# only against a freshly-initialized (empty) data directory.
FROM postgres:16-alpine
