# Deliberately minimal and purpose-built rather than a general-purpose
# webhook runner — this container gets Docker socket access (see
# docker-compose.yml's updater service), which is host-root-equivalent,
# so every extra dependency here is extra attack surface. See server.py's
# own header comment for the full risk writeup.
FROM docker:27-cli

RUN apk add --no-cache docker-cli-compose python3

COPY server.py /server.py
# Azure Container Apps has no Docker socket: there the same image runs as a
# manually started job with azure_update.py as its command instead.
COPY azure_update.py /azure_update.py

EXPOSE 9999

ENTRYPOINT ["python3", "/server.py"]
