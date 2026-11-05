# Build the official source: upstream no longer publishes public images.
FROM golang:1.24.9 AS build
WORKDIR /src
ADD https://github.com/minio/minio/archive/refs/tags/RELEASE.2025-10-15T17-29-55Z.tar.gz /tmp/minio.tar.gz
RUN tar -xzf /tmp/minio.tar.gz --strip-components=1 && CGO_ENABLED=0 go build -trimpath -o /minio .
FROM debian:bookworm-slim
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /minio /usr/local/bin/minio
ENTRYPOINT ["minio"]
