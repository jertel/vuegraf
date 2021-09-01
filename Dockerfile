# Fully qualified container name prevents public registry typosquatting
FROM docker.io/library/python:3-alpine

ARG UID=1012
ARG GID=1012

RUN addgroup -S -g $GID vuegraf
RUN adduser  -S -g $GID -u $UID -h /opt/vuegraf vuegraf

WORKDIR /opt/vuegraf

# Install pip dependencies with minimal container layer size growth
COPY src/requirements.txt ./
RUN set -x && \
    apk add --no-cache build-base libffi-dev rust cargo openssl-dev && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del build-base libffi-dev rust cargo openssl-dev && \
    rm -rf /var/cache/apk /opt/vuegraf/requirements.txt

# Copying code in after requirements are built optimizes rebuild
# time, with only a marginal increate in image layer size; chmod
# is superfluous if "git update-index --chmod=+x ..." is done.
COPY src/vuegraf/*.py ./
RUN  chmod a+x *.py

# A numeric UID is required for runAsNonRoot=true to succeed
USER $UID

VOLUME /opt/vuegraf/conf

ENTRYPOINT ["/opt/vuegraf/vuegraf.py" ]
CMD ["/opt/vuegraf/conf/vuegraf.json"]

