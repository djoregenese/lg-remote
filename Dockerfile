FROM python:3.12-slim
WORKDIR /app
COPY lg_remote.py lg_remote_web.py ./
EXPOSE 8888
CMD ["python3", "-u", "lg_remote_web.py"]
