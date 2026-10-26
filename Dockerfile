FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 PORT=8000 TRAFFIC_ENV=production RESULTS_DIR=/data/results
WORKDIR /app
COPY requirements-web.txt /app/
RUN pip install --no-cache-dir -r requirements-web.txt \
    && useradd --uid 10001 --create-home traffic \
    && mkdir -p /data/results && chown -R traffic:traffic /data
COPY adaptive_traffic /app/adaptive_traffic
COPY main.py /app/
COPY web /app/web
USER traffic
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+os.getenv('PORT','8000')+'/healthz',headers={'Host':os.environ['ALLOWED_HOSTS'].split(',')[0]}),timeout=2)"
CMD ["python", "-m", "adaptive_traffic.serve"]
