# Deliberately minimal and purpose-built rather than a general-purpose
# webhook runner — this container gets Docker socket access (see
# docker-compose.yml's updater service), which is host-root-equivalent,
# so every extra dependency here is extra attack surface. See server.py's
# own header comment for the full risk writeup.
FROM docker:27-cli

RUN apk add --no-cache docker-cli-compose python3

COPY server.py /server.py

EXPOSE 9999

ENTRYPOINT ["python3", "/server.py"]
