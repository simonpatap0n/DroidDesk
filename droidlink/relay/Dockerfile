FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DROIDLINK_DATA_DIR=/data \
    DROIDLINK_PORT=8080 \
    DROIDLINK_REQUIRE_HTTPS=1

RUN addgroup -S relay && adduser -S -G relay relay \
    && mkdir -p /data /opt/droidlink-relay \
    && chown -R relay:relay /data /opt/droidlink-relay

WORKDIR /opt/droidlink-relay
COPY --chown=relay:relay server.py ./server.py
USER relay
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python3", "-B", "server.py"]
