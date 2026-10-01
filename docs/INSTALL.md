# Installing `langfuse-synth-core`

The library is a **public** repo (consistent with the public kits it is the shared DNA
of) consumed as a **git-pinned dependency**. Kits pin it to a tag/SHA in their own
`pyproject.toml` and upgrade deliberately — no build-time auth needed.

## Pin it in a kit

```toml
# kit's pyproject.toml
dependencies = [
    "langfuse-synth-core @ git+https://github.com/borismichel/langfuse-synth-core@v4.1.1",
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
to authenticate. Note that `pip` needs the `git` binary to resolve a `git+https://`
dependency, and the `python:*-slim` base images don't ship it — install it first
(`apt-get install -y --no-install-recommends git`), as `examples/kit.Dockerfile` does.

## Runtime vs authoring install

- **Runtime** (deployed kit / portal): the git-pinned dependency
  above. Carries none of the authoring toolchain's dependencies.
- **Authoring** (a kit author's dev box): the same git pin with the `[authoring]` extra
  to get `synth-authoring new / validate / freeze` and the kit-dev skills.


```bash
pip install 'langfuse-synth-core[authoring] @ git+https://github.com/borismichel/langfuse-synth-core@v4.1.1'
```

`v4.1.1` is a published git tag, not a PyPI install. Keep an existing kit's agreed
pin when working on it. Features documented on an unreleased branch require a
local checkout (`pip install -e '.[authoring]'`) until they receive a release tag.

## Bootstrap your coding agent

Run from the kit directory after installing core's authoring extra:

```bash
synth-authoring skills --install --agent claude  # .claude/skills (also the default)
synth-authoring skills --install --agent codex   # .agents/skills
synth-authoring skills --status --agent codex
synth-authoring skills --update --agent codex
```

The project targets follow [Claude Code's skill discovery](https://code.claude.com/docs/en/skills)
and [Codex's skill discovery](https://learn.chatgpt.com/docs/build-skills).
`--dest DIR` overrides either agent's destination for install, status and update;
ensure your agent is configured to discover that directory. For a personal Codex
install, for example, pass `--dest ~/.agents/skills` to each command.

Each installed skill has a `.synth-authoring.json` receipt recording the source
core version and SHA-256 identity of every bundled file. `--status` reports:

- `current`: receipt, installed files and bundled files match.
- `stale`: files still match their receipt, but the running core version or content changed.
- `locally-modified`: a file was edited, added or removed since installation.
- `unmanaged`: an existing copy has no readable installation receipt.
- `missing`: no installed copy exists.

Status is read-only and exits successfully after reporting; it is not a readiness
gate. Update uses the **currently installed core**, without fetching or upgrading
packages. Install your desired released git pin first, then run `--update`. Current
copies are left alone; unedited stale copies are refreshed and missing copies installed.

Modified and unmanaged copies block updates. Inspect them before choosing
`--update --force`; that command preserves the previous directory in
`.synth-skill-backups/` beside the destination directory before replacing it. Forced
installation uses the same backup policy. Copy any wanted edits from the preserved
backup into the new skill, or restore the backup after moving the new copy aside.
Backups remain until you remove them. Symlinked installation targets are left untouched.

Install, update and status also look for `langfuse/SKILL.md` in the destination,
selected agent's project locations up to the repository root, and its personal
skills directory (plus `/etc/codex/skills` for Codex). If absent, install your
Langfuse skill there or use your agent's skill manager; no dependency is installed
automatically. This filesystem check does not inspect plugin registration, synced
account skills, additional directories or disabled-skill settings. Confirm the
Langfuse skill is enabled in the agent before authoring.

## If the lib is ever made private again

A private git dependency needs build-time auth. Use a **BuildKit build secret** so the
token is mounted for one `RUN` and never written into an image layer (the infra runs
Infisical to supply it):

```dockerfile
# syntax=docker/dockerfile:1.7
RUN --mount=type=secret,id=git_token \
    GIT_TOKEN="$(cat /run/secrets/git_token)" \
    pip install --no-cache-dir \
      "langfuse-synth-core @ git+https://x-access-token:${GIT_TOKEN}@github.com/borismichel/langfuse-synth-core@v4.1.1"
```

This path is **not needed while the repo is public** and is documented only so a future
privacy change has a known-good pattern.
