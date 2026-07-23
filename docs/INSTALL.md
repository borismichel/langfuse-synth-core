# Installing `langfuse-synth-core`

The library is a **public** repo (consistent with the public kits it is the shared DNA
of) consumed as a **git-pinned dependency**. Kits pin it to a tag/SHA in their own
`pyproject.toml` and upgrade deliberately — no build-time auth needed.

## Pin it in a kit

```toml
# kit's pyproject.toml
dependencies = [
    "langfuse-synth-core @ git+https://github.com/borismichel/langfuse-synth-core@v0.1.0",
]
```

Pin to a **tag or a full SHA** — never a moving branch — so a kit's vendor-approved
output can never be silently rewritten by a lib change.

## Install in a kit's Docker image

Because the repo is public, the install is a plain `pip install` with no secret — see
`examples/kit.Dockerfile`:

```dockerfile
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e '.[playground]'
```

The pinned lib is fetched over HTTPS from the public git URL during the build; nothing
to authenticate.

## Runtime vs authoring install

- **Runtime** (deployed kit / portal): `pip install langfuse-synth-core` (or the git pin
  above). Carries none of the authoring toolchain's dependencies.
- **Authoring** (a kit author's dev box): `pip install 'langfuse-synth-core[authoring]'`
  to get `synth new / validate / freeze` and the kit-dev skills.

## If the lib is ever made private again

A private git dependency needs build-time auth. Use a **BuildKit build secret** so the
token is mounted for one `RUN` and never written into an image layer (the infra runs
Infisical to supply it):

```dockerfile
# syntax=docker/dockerfile:1.7
RUN --mount=type=secret,id=git_token \
    GIT_TOKEN="$(cat /run/secrets/git_token)" \
    pip install --no-cache-dir \
      "langfuse-synth-core @ git+https://x-access-token:${GIT_TOKEN}@github.com/borismichel/langfuse-synth-core@v0.1.0"
```

This path is **not needed while the repo is public** and is documented only so a future
privacy change has a known-good pattern.
