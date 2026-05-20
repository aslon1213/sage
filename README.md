# sage

Agentic CLI that documents a service or web API by reading its source. Produces
Markdown and/or DOCX output structured for client developers, business
analysts, and system analysts, with Mermaid diagrams for system context and
key workflows.

Built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python),
[Click](https://click.palletsprojects.com/), and [Rich](https://rich.readthedocs.io/).

## Installation

### As a CLI (recommended)

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install .
```

With `pipx`:

```bash
pipx install .
```

Either installs the `sage` entry point on your `PATH`.

### From the published wheel

Each tagged release attaches a wheel to its GitHub Release. Grab the URL from
the [Releases page](https://github.com/aslon1213/sage/releases) and:

```bash
pip install "https://github.com/aslon1213/sage/releases/download/v0.1.0/system_analyst-0.1.0-py3-none-any.whl"
```

### Via Docker

The release workflow publishes the image to GitHub Container Registry:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD:/work" -w /work \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    ghcr.io/aslon1213/sage/sage:0.1.5 \
    --path . --format both
```

The image's entrypoint is `sage`, so any flags are passed straight through.

## Usage

```bash
sage --path <service-dir> [--output run.log] [--format md|docx|both]
```

| Flag       | Default                  | Description                                    |
| ---------- | ------------------------ | ---------------------------------------------- |
| `--path`   | _required_               | Path to the target service to document.        |
| `--output` | `system_analyst_run.log` | File the run transcript is written to.         |
| `--format` | `md`                     | Output format: `md`, `docx`, or `both`.        |

The agent writes `API_DOCUMENTATION.md` and/or `API_DOCUMENTATION.docx` into
the target directory and a transcript of the run to `--output`.

You can also invoke the package as a module:

```bash
python -m system_analyst --path .
```

## Releasing

Push a `v*.*.*` tag. The GitHub Actions workflow
([`.github/workflows/release.yml`](.github/workflows/release.yml)):

1. Builds the wheel and sdist (version pinned to the tag).
2. Builds the Docker image and pushes it to
   [`ghcr.io/aslon1213/sage/sage`](https://github.com/aslon1213/sage/pkgs/container/sage%2Fsage),
   tagged with both the version and `latest`.
3. Creates a [GitHub Release](https://github.com/aslon1213/sage/releases) with
   the wheel and sdist attached.

```bash
git tag v0.1.0
git push origin v0.1.0
```
