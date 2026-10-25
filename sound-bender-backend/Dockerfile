FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg rubberband-cli libsndfile1 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY sound-bender-backend-requirements.txt .
RUN pip install --no-cache-dir -r sound-bender-backend-requirements.txt
COPY sound-bender-backend-server.py .
EXPOSE 8080
CMD ["uvicorn", "sound-bender-backend-server:app", "--host", "0.0.0.0", "--port", "8080"]
