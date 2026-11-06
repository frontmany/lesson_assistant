FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Модели распознавания скачиваются при сборке, и запуск контейнера не зависит
# от сети. PRELOAD_MODEL=0 — скачать при первом уроке (сборка быстрее)
ARG WHISPER_MODEL_TEACHER=gigaam-v3-e2e-ctc
ARG WHISPER_MODEL_STUDENT=gigaam-v3-e2e-ctc
ARG PRELOAD_MODEL=1
ENV WHISPER_MODEL_TEACHER=${WHISPER_MODEL_TEACHER} \
    WHISPER_MODEL_STUDENT=${WHISPER_MODEL_STUDENT} \
    WHISPER_DOWNLOAD_ROOT=/srv/models
RUN if [ "$PRELOAD_MODEL" = "1" ]; then \
      python -c "import onnx_asr; from faster_whisper import WhisperModel; [onnx_asr.load_model(m, '/srv/models/' + m) if m.startswith('gigaam') else WhisperModel(m, device='cpu', compute_type='int8', download_root='/srv/models') for m in {'${WHISPER_MODEL_TEACHER}', '${WHISPER_MODEL_STUDENT}'}]"; \
    fi

COPY app ./app
COPY webapp ./webapp
COPY data ./data
COPY prompts ./prompts
COPY certs ./certs

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
