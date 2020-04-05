FROM python:3

ARG UID=1012
ARG GID=1012

RUN addgroup --gid $GID vuegraf
RUN adduser --system --home /opt/vuegraf --gid $GID --uid $UID vuegraf

WORKDIR /opt/vuegraf

COPY src/* ./

RUN pip install --no-cache-dir -r requirements.txt

USER vuegraf
VOLUME /opt/vuegraf/conf

ENTRYPOINT ["python", "/opt/vuegraf/vuegraf.py"]
CMD ["/opt/vuegraf/conf/vuegraf.json"]