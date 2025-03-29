FROM python:3.12-alpine

WORKDIR /app
ADD main.py /app
ADD locale /app/locale
RUN find /app/locale -name "*.po" -type f -delete
ADD db_migrate /app/db_migrate
ADD requirements.txt /app

RUN mkdir /app/data
RUN pip install -r requirements.txt
RUN rm requirements.txt

ENV TOKEN=""
ENV GROUP_ID=""
ENV LANGUAGE="en_US"
ENV TG_API=""

CMD python -u /app/main.py -token "$TOKEN" -group_id "$GROUP_ID" -language "$LANGUAGE" -TG_API "$TG_API"