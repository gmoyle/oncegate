FROM python:3.12-alpine

LABEL org.opencontainers.image.title="OnceGate" \
      org.opencontainers.image.description="Single-use invite links in front of any web app" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/gmoyle/oncegate"

RUN adduser -D -H -u 10001 oncegate && mkdir -p /data /secrets && chown oncegate /data
COPY oncegate.py /app/oncegate.py

ENV ONCEGATE_DATA_DIR=/data \
    ONCEGATE_ADMIN_PASSWORD_FILE=/secrets/admin-password \
    PYTHONUNBUFFERED=1

USER oncegate
EXPOSE 9099 9098
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:9099/_once/auth >/dev/null 2>&1 || [ $? -eq 8 ]
ENTRYPOINT ["python", "/app/oncegate.py"]
CMD ["serve"]
