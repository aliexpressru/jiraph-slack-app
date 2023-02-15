FROM python:3.10

WORKDIR /usr/src/app
COPY . .

RUN pip install --no-cache-dir --upgrade -r requirements.txt

ENV PYTHONPATH "${PYTHONPATH}:${PWD}"
ENTRYPOINT ["python"]

CMD ["-m", "jiraph_bot"]
