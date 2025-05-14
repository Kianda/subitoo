FROM python:3.10-alpine

COPY ./src /app

WORKDIR /app

RUN pip install -r requirements.txt
#RUN pip install ipdb

ENTRYPOINT [ "python", "app.py" ]
