# CFE CLI

Build your Django apps faster with battle-tested components, templates, and more.

## What is CFE?

CFE is like shadcn-ui but for Python/Django projects. Instead of manually setting up Tailwind CSS, HTMX, or other integrations, CFE automatically adds the necessary files, configurations, and templates to your project.

## Features

- 🚀 **Quick Setup**: Add components with a single command
- 📦 **Auto Package Management**: Works with uv, pip, poetry
- 🎨 **Ready-to-Use Templates**: Get styled components and examples
- 🔍 **Smart Detection**: Automatically detects your project type and package manager
- 🎯 **Django First**: Currently supporting Django (more frameworks coming soon!)

## Installation

```bash
# Using pip
pip install cfe

# Using uv
uv pip install cfe
```

## Quick Start

Navigate to your Django project and add components:

```bash
# Add Tailwind CSS with pre-configured templates
cfe add tailwind

# Add HTMX for dynamic interactions
cfe add htmx
```

## Available Commands

### `cfe add <component>`

Add a component to your project with automatic configuration.

```bash
cfe add tailwind
```

What it does:
- Copies component files (templates, CSS, config files)
- Installs necessary packages
- Creates ready-to-use templates
- Provides next steps

### `cfe list`

List all available components.

```bash
cfe list
```

### `cfe init`

Detect and display your project information.

```bash
cfe init
```

### `cfe auth`

Sign in to your CodingForEntrepreneurs account (like `gh auth login`). No password is handled by the CLI: it shows a one-time code, you approve it in the browser, and a token is saved locally.

```bash
cfe auth login            # opens your browser; --no-browser prints the URL instead
cfe auth status           # who you are, token prefix, subscription and course access
cfe auth token            # print the token for scripts: curl -H "Authorization: Bearer $(cfe auth token)" ...
cfe auth logout           # revoke the token on the server and delete the local copy
```

Options and environment:
- `--host` / `CFE_HOST`: server URL (default `https://www.codingforentrepreneurs.com`).
- `CFE_TOKEN`: use this token instead of the stored one (CI, containers).
- `CFE_CONFIG_DIR`: where `hosts.json` lives (default `~/.config/cfe`, or `$XDG_CONFIG_HOME/cfe`). The directory is `0700` and the file is `0600`.
- `cfe auth login --force` re-authenticates without asking if you are already logged in.

## Available Components

### Tailwind CSS

Add Tailwind CSS with automatic setup:
- `tailwind.config.js`
- `package.json` with build scripts
- Input CSS with custom components
- Base template with Tailwind CDN
- Button and Card components

```bash
cfe add tailwind
```

Then run:
```bash
npm install
npm run tailwind:watch
```

### HTMX

Add HTMX for dynamic HTML interactions:
- Base template with HTMX script
- Example templates showing common patterns
- No build step required!

```bash
cfe add htmx
```

## CFE Stack

`cfe.stack` is a module system for Django: enable services with one env var (`CFE_MODULES=postgres,redis,celery,s3,smtp,stripe`) load it with `stack = cfe.stack.load()` in `settings.py`, and assign its values explicitly (`DATABASES = stack.databases(...)`). Env vars load through `cfe.env`, a bundled fork of python-decouple. Swap local open-source services for managed ones by changing env vars only.

```bash
pip install "cfe[all]"
```

See [docs/stack.md](docs/stack.md).

## Framework Support

**Currently Supported:**
- ✅ Django

**Coming Soon:**
- FastAPI
- Flask

Want support for your framework? Send me a message on X: [@justinmitchel](https://x.com/justinmitchel)

## Development

```bash
# Clone the repo
git clone https://github.com/yourusername/cfe-cli
cd cfe-cli

# Install in development mode
pip install -e .

# Run the CLI
cfe --help
```

## Contributing

Contributions are welcome! Feel free to open issues or submit PRs.

## License

MIT