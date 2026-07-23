# Installing `langfuse-synth-core` (private git dependency)

The library is a **private** repo consumed as a **git-pinned dependency**. Kits pin it to
a tag/SHA in their own `pyproject.toml` and upgrade deliberately.

## Pin it in a kit

```toml
# kit's pyproject.toml
dependencies = [
    "langfuse-synth-core @ git+https://github.com/borismichel/langfuse-synth-core@v0.1.0",
]
```

Pin to a **tag or a full SHA** — never a moving branch — so a kit's vendor-approved
output can never be silently rewritten by a lib change.

## Build-time auth via a Docker build secret (no credential in any layer)

The private install needs a token at build time only. Use a **BuildKit build secret** so
the token is mounted for one `RUN` and never written into an image layer. The infra
already runs Infisical to supply the token; see `examples/kit.Dockerfile`.

```dockerfile
# syntax=docker/dockerfile:1.7
RUN --mount=type=secret,id=git_token \
    GIT_TOKEN="$(cat /run/secrets/git_token)" \
    pip install --no-cache-dir \
      "langfuse-synth-core @ git+https://x-access-token:${GIT_TOKEN}@github.com/borismichel/langfuse-synth-core@v0.1.0"
```

Build it:

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=git_token,src=<(infisical run -- printenv GIT_TOKEN) \
  -t my-kit:dev .
```

Verify no leak (the token must not appear in any layer):

```bash
docker history --no-trunc my-kit:dev | grep -i token   # expect: no match
```

## Runtime vs authoring install

- **Runtime** (deployed kit / portal): `pip install langfuse-synth-core` (or the git pin
  above). Carries none of the authoring toolchain's dependencies.
- **Authoring** (a kit author's dev box): `pip install 'langfuse-synth-core[authoring]'`
  to get `synth new / validate / freeze` and the kit-dev skills.
