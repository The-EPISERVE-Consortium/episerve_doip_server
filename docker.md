# Docker

Build and run the DOIP server in a container.

## Build
From the project root:
```bash
docker build -f docker/Dockerfile -t mardi-doip-server .
```
By default the image generates a self-signed cert (CN=localhost) during build. To skip generation, set:
```bash
docker build -f docker/Dockerfile --build-arg GENERATE_SELF_SIGNED=false -t mardi-doip-server .
```

## Run
Expose the default DOIP port (plaintext) and the compatibility listener:
```bash
docker run --rm -p 3567:3567 -p 3568:3568 mardi-doip-server
```

Inject configuration by mounting `config.yaml` or providing environment variables. Example with env overrides and certificates:
```bash
docker run --rm \
  -p 3567:3567 -p 3568:3568 \
  -e FDO_API="https://fdo.example.org/fdo/" \
  -e LAKEFS_URL="https://lakefs.internal" \
  -e LAKEFS_USER=admin -e LAKEFS_PASSWORD=secret \
  -v $(pwd)/certs:/app/certs \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  mardi-doip-server
```

### Logging
If you are experiencing lost logger messages, try 
```bash
docker logs <container> 
```

### TLS
The server auto-enables TLS when both `certs/server.crt` and `certs/server.key` are present.

With Docker, mount your certificate directory into `/app/certs` so the container can detect and load them:
```bash
docker run --rm -p 3567:3567 -p 3568:3568 \
  -v $(pwd)/certs:/app/certs \
  mardi-doip-server
```

Inside the container, the entrypoint checks for `/app/certs/server.crt` and `/app/certs/server.key` and, if found, starts TLS listeners on 3567/3568. Without the mount, the server stays in plaintext mode.

The container entrypoint runs `python -m doip_server.main`.
