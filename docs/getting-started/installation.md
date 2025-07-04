# Installation

## Prerequisites

- Python 3.10 or higher
- Claude CLI installed and configured

## Installation Methods

### Using pip

```bash
pip install claude-code-proxy-api
```

### Using uv (Recommended)

```bash
uv add claude-code-proxy-api
```

### From Source

```bash
git clone https://github.com/CaddyGlow/claude-code-proxy-api.git
cd claude-code-proxy-api
uv sync
```

## Configuration

After installation, you need to configure the Claude CLI:

```bash
claude auth login
```

## Quick Start

Run the server:

```bash
claude-code-proxy-api
```

The server will start on `http://localhost:8000` by default.