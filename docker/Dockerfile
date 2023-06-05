FROM tiangolo/uvicorn-gunicorn-fastapi:python3.9

RUN mkdir /kube

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./api /app
RUN cd /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
