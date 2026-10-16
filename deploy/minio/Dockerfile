# Pinned upstream community source; old MinIO binary/image downloads are unavailable.
FROM golang:1.24.7-bookworm AS build
ARG MINIO_COMMIT=07c3a429bfed433e49018cb0f78a52145d4bedeb
WORKDIR /src
RUN git init . && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin ${MINIO_COMMIT} && git checkout --detach FETCH_HEAD
RUN CGO_ENABLED=0 go build -trimpath -o /minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* && mkdir /data && chown 1000:1000 /data
COPY --from=build /minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/doc/minio/LICENSE
USER 1000:1000
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
