# Tek imaj, iki servis: REST API (varsayılan komut, 8000) ve Streamlit paneli (8501).
# docker-compose.yml aynı imajı iki farklı komutla çalıştırır.
#
# Model, imaj derlenirken eğitilir (python train.py, sentetik veri): imaj kendi kendine yeterlidir
# ve ilk istek eğitim beklemez. Bedeli: daha uzun derleme süresi ve imaja giren veri/model dosyaları.
# Model sürümü değişirse imajı yeniden derleyin (docker compose build). Derleme sırasında eğitim
# istemiyorsanız `RUN python train.py` satırını silin; panel ilk açılışta, API ise `python train.py`
# çalıştırılana kadar 503 verir.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Root olmayan kullanıcı; /app ona ait olsun ki data/ ve models/ yazılabilsin
RUN useradd --create-home --uid 10001 app \
    && mkdir /app \
    && chown app:app /app
WORKDIR /app

# Bağımlılıklar önce (katman önbelleği için): panel + API
COPY requirements.txt requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

USER app
COPY --chown=app:app . .

# Modeli ve panel için demo verisini üret (data/ ve models/ imajın içinde kalır)
RUN python train.py

EXPOSE 8000 8501

# Varsayılan: API. Panel için: streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saglik', timeout=4)"]
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
