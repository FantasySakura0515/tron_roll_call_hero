FROM python:3.12-slim

# OCR is installed by default so the FJU (captcha) account can log in
# unattended. Pass --build-arg INSTALL_OCR=0 for a slim image when no
# captcha-gated school is used.
ARG INSTALL_OCR=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY tron_roll_call_hero ./tron_roll_call_hero

RUN pip install --no-cache-dir . && \
    if [ "$INSTALL_OCR" = "1" ]; then pip install --no-cache-dir '.[ocr]'; fi

# config.yaml and state/ are mounted as volumes at run time — never baked in.
CMD ["python", "-m", "tron_roll_call_hero.tron", "bot", "discord-gateway", "--supervisor"]
