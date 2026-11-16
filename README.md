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