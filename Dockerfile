FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Модель распознавания. С PRELOAD_MODEL=1 она скачивается при сборке,
# и запуск контейнера не зависит от сети.
# TODO(Ярослав): включить по умолчанию, когда появится pipeline/asr.py
ARG WHISPER_MODEL=small
ARG PRELOAD_MODEL=0
ENV WHISPER_MODEL=${WHISPER_MODEL} \
    WHISPER_DOWNLOAD_ROOT=/srv/models
RUN if [ "$PRELOAD_MODEL" = "1" ]; then \
      python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8', download_root='/srv/models')"; \
    fi

COPY app ./app
COPY webapp ./webapp
COPY data ./data
COPY prompts ./prompts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
