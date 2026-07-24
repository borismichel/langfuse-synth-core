# Example: how a demo kit installs langfuse-synth-core (a PUBLIC git dependency).
# Mirrors contracts/Dockerfile.kit-reference in the portal repo (non-root uid 10001).
# The lib is pinned in the kit's pyproject.toml as:
#   "langfuse-synth-core @ git+https://github.com/borismichel/langfuse-synth-core@<tag>"
# Because the repo is public, the install is a plain pip install — no build secret.
#
# Build:  docker build -t my-kit:dev .

FROM python:3.12-slim

# git: python:*-slim ships without it, but pip needs it to fetch the git-pinned lib.
# The repo is public, so this is a plain HTTPS fetch — no build secret required.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (uid/gid 10001) — job & live containers never run as root.
RUN groupadd --gid 10001 synth \
 && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth

WORKDIR /app
COPY . .

# Fetches the pinned public lib over HTTPS during the build; nothing to authenticate.
RUN pip install --no-cache-dir -e '.[playground]'

USER synth
# The portal supplies `synth <step> --config {config}` at container-create time.

# NOTE: if langfuse-synth-core is ever made private again, this install needs build-time
# auth via a BuildKit build secret — see docs/INSTALL.md for the known-good pattern.
