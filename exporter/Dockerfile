FROM python:3.13-alpine
RUN pip install --no-cache-dir "prometheus-client>=0.21"
WORKDIR /app
COPY exporter.py .
USER nobody
EXPOSE 9711
CMD ["python", "-u", "exporter.py"]
