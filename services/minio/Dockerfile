# Build the official source: upstream no longer publishes public images.
FROM golang:1.24.9@sha256:8b788ecf5fddb91cd04e532205d6411de68a08b510885bb8fb1c93f54c03f737 AS build
WORKDIR /src
ADD https://github.com/minio/minio/archive/refs/tags/RELEASE.2025-10-15T17-29-55Z.tar.gz /tmp/minio.tar.gz
RUN tar -xzf /tmp/minio.tar.gz --strip-components=1 && CGO_ENABLED=0 go build -trimpath -o /minio .
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /minio /usr/local/bin/minio
ENTRYPOINT ["minio"]
