# CFE CLI Usage Guide

## Installation

First, install CFE in your Python environment:

```bash
# Using pip
pip install -e .

# Or using uv
uv pip install -e .
```

## Getting Started

### 1. Check Your Project

Navigate to your Django project directory and run:

```bash
cfe init
```

This will show you:
- Project type detected
- Package manager being used
- Configuration files found

### 2. List Available Components

See what components you can add:

```bash
cfe list
```

### 3. Add a Component

Add Tailwind CSS to your Django project:

```bash
cfe add tailwind
```

What this does:
- Creates `tailwind.config.js`
- Creates `package.json` with build scripts
- Creates `static/src/input.css` with Tailwind directives
- Creates `templates/base.html` with Tailwind setup
- Creates reusable components (`button.html`, `card.html`)

After adding Tailwind, run:
```bash
npm install
npm run tailwind:watch
```

### 4. Add HTMX

```bash
cfe add htmx
```

What this does:
- Creates/updates `templates/base.html` with HTMX script
- Creates `templates/components/htmx_example.html` with examples

## Component Structure

Components are organized by framework:

```
cfe/components/
├── tailwind/
│   ├── component.json       # Component metadata
│   ├── django/
│   │   ├── tailwind.config.js
│   │   ├── package.json
│   │   ├── static/
│   │   │   └── src/
│   │   │       └── input.css
│   │   └── templates/
│   │       ├── base.html
│   │       └── components/
│   │           ├── button.html
│   │           └── card.html
│   ├── fastapi/
│   └── flask/
└── htmx/
    ├── component.json
    └── django/
        └── templates/
            ├── base.html
            └── components/
                └── htmx_example.html
```

## Using the Components

### Tailwind Button Component

```django
{% include "components/button.html" with text="Click me" variant="primary" %}
{% include "components/button.html" with text="Secondary" variant="secondary" %}
{% include "components/button.html" with text="Link" variant="primary" href="/action" %}
```

### Tailwind Card Component

```django
{% include "components/card.html" with title="Card Title" content="Card content here" %}

{# Or with custom content #}
{% include "components/card.html" with title="Custom Card" %}
    {% block card_content %}
        <p>Custom content goes here</p>
    {% endblock %}
{% endinclude %}
```

### HTMX Examples

Check `templates/components/htmx_example.html` for working examples of:
- Simple GET requests
- Form submissions
- Live search
- Delete confirmations
- Infinite scroll

## Authentication

```bash
cfe login          # same as cfe auth login
```

Prints a one-time code like `BCDF-GHJK`, then press Enter to open the approval page in your browser (use `--no-browser` on servers or in SSH sessions and open the printed URL yourself). Once you approve, the CLI saves a token to `~/.config/cfe/hosts.json` (permissions `0600`) and prints `Logged in as you@example.com`.

```bash
cfe whoami         # host, email, token prefix, subscription, lifetime, courses, roadmaps (cfe auth status)
cfe auth token     # raw token on stdout, for $(cfe auth token)
cfe logout         # revokes the token on the server, then removes it locally (cfe auth logout)
```

Notes:
- `whoami` exits with code 1 when you are not logged in or the token was revoked.
- Set `CFE_TOKEN` to override the stored token (useful in CI). Set `CFE_HOST` or pass `--host` to talk to another server, for example `--host http://127.0.0.1:8800`.
- Tokens do not expire; revoke one with `cfe logout` or from your account page.

## Updating

```bash
cfe update           # or cfe upgrade
cfe update --check   # report only
```

cfe checks for a new version at most once a day and prints a notice after your command finishes. Set `CFE_NO_UPDATE_CHECK=1` to turn that off.

## Tips

1. **Template Organization**: The components create a `templates/components/` directory. You can extend this pattern for your own reusable components.

2. **Tailwind Customization**: Edit `static/src/input.css` to add your own custom components and utilities.

3. **Base Template**: The generated `base.html` is a starting point. Feel free to customize it for your needs.

4. **Package Manager**: CFE detects your package manager automatically (uv, poetry, pip). Make sure you're in your project directory.

## Troubleshooting

### "Could not detect project type"

Make sure you're in a Django project directory with:
- `manage.py` file
- A Django settings file

### Components not showing up

Make sure your Django settings are configured to find templates:
```python
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Add this
        'APP_DIRS': True,
        ...
    },
]
```

### Tailwind CSS not working

1. Make sure you ran `npm install`
2. Run `npm run tailwind:watch` to build CSS
3. Check that `static/dist/output.css` was created
4. Configure Django static files in settings:
```python
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
```

## Framework Support

Currently, CFE only supports Django projects. Support for FastAPI and Flask is coming soon!

Want support for your framework? Send me a message on X: [@justinmitchel](https://x.com/justinmitchel)

