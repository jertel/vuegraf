FROM python:3.13-slim as builder

LABEL description="Vuegraf Official Image"
LABEL maintainer="Jason Ertel"

COPY . /tmp/vuegraf

RUN rm -fr /tmp/vuegraf/dist && \
    cd /tmp/vuegraf && \
    pip install --root-user-action=ignore --upgrade pip && \
    pip install --root-user-action=ignore build && \
    python -m build

FROM docker.io/library/python:3-alpine

ARG GID=1012
ARG UID=1012
ARG USERNAME=vuegraf

COPY --from=builder /tmp/vuegraf/dist/*.tar.gz /tmp/

RUN mkdir -p /opt/vuegraf
RUN addgroup -S -g $GID vuegraf
RUN adduser  -S -g $GID -u $UID -h /opt/vuegraf vuegraf

WORKDIR /opt/vuegraf

RUN set -x && \
    apk add --no-cache build-base libffi-dev rust cargo openssl-dev && \
    pip install --root-user-action=ignore --upgrade pip && \
    pip install --root-user-action=ignore /tmp/*.tar.gz && \
    rm -rf /tmp/* && \
    apk del build-base libffi-dev rust cargo openssl-dev && \
    rm -rf /var/cache/apk

USER $UID

ENTRYPOINT ["vuegraf" ]
CMD ["/opt/vuegraf/conf/vuegraf.json"]
