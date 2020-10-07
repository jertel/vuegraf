FROM python:3

ARG UID=1012
ARG GID=1012

RUN addgroup --gid $GID vuegraf
RUN adduser --system --home /opt/vuegraf --gid $GID --uid $UID vuegraf

WORKDIR /opt/vuegraf

COPY src/* ./

RUN pip install --no-cache-dir -r requirements.txt

# A numeric UID is required for runAsNonRoot=true to succeed
USER $UID
VOLUME /opt/vuegraf/conf

ENTRYPOINT ["/opt/vuegraf/vuegraf.py" ]
CMD ["/opt/vuegraf/conf/vuegraf.json"]

