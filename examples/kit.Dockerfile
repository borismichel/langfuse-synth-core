# syntax=docker/dockerfile:1.7
#
# Example: how a demo kit installs the PRIVATE langfuse-synth-core git dependency
# using a BuildKit build secret, so the token is never baked into an image layer.
# Mirrors contracts/Dockerfile.kit-reference in the portal repo (non-root uid 10001).
#
# Build:
#   DOCKER_BUILDKIT=1 docker build \
#     --secret id=git_token,src=<(infisical run -- printenv GIT_TOKEN) \
#     -t my-kit:dev .
#
# Verify no leak:
#   docker history --no-trunc my-kit:dev | grep -i token   # expect: no match

FROM python:3.12-slim

# Non-root user (uid/gid 10001) — job & live containers never run as root.
RUN groupadd --gid 10001 synth \
 && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth

WORKDIR /app
COPY . .

# The private lib is pinned in the kit's pyproject.toml as:
#   "langfuse-synth-core @ git+https://github.com/borismichel/langfuse-synth-core@<tag>"
# The build secret rewrites it with an auth token for THIS RUN only. The token is read
# from /run/secrets and never persists in the layer.
RUN --mount=type=secret,id=git_token \
    GIT_TOKEN="$(cat /run/secrets/git_token)" \
    git config --global url."https://x-access-token:${GIT_TOKEN}@github.com/".insteadOf "https://github.com/" \
 && pip install --no-cache-dir -e '.[playground]' \
 && git config --global --unset url."https://x-access-token:${GIT_TOKEN}@github.com/".insteadOf

USER synth
# The portal supplies `synth <step> --config {config}` at container-create time.
