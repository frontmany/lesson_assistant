# Образ приложения. Собирается из корня репозитория: в образ идут backend/ и webapp/.
#   docker compose up --build
#   docker build -f backend/Dockerfile -t lesson-assistant .
# Что не попадает в образ — backend/Dockerfile.dockerignore
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Та же раскладка, что в репозитории: /srv/backend и /srv/webapp
WORKDIR /srv/backend

COPY backend/requirements.txt .
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

COPY backend/app ./app
COPY backend/data ./data
COPY backend/prompts ./prompts
COPY backend/certs ./certs
COPY webapp /srv/webapp

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
