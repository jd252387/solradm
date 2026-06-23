# solradm

**Powerful Solr Administration CLI** — Context-aware management of Solr clusters and ZooKeeper.

## Features

- 🧠 **Context System** — Maintain multiple cluster connections and share them via repositories
- ⚙️ **Comprehensive Commands** — Manage collections, backups, nodes, Kubernetes workloads, and more
- 🎨 **Rich UX** — Colorful output, progress tables, and a polished interface

## Installation

Requires Python 3.13 or higher.

```bash
pip install solradm
```

Or install from source:

```bash
git clone https://github.com/jd252387/solradm.git
cd solradm
pip install .
```

## Quick Start

### Set Up a Context

Connect to a Solr cluster by creating a context:

```bash
solradm context add dev --zk localhost:2181
```

Switch between contexts:

```bash
solradm context switch dev
```

### Common Commands

```bash
# Show cluster status
solradm status

# List all contexts
solradm context list

# Create a collection
solradm coll create my-collection --shards 2 --conf _default

# Query a collection
solradm coll query my-collection "*:*" --rows 10

# Upload configsets to ZooKeeper
solradm zoo upload ./configsets --reload

# Take a backup
solradm backup take --collection my-collection

# Stream Kubernetes pod logs
solradm kube logs solr-.*
```

## Command Reference

| Command | Description |
|---------|-------------|
| `context` | Manage contexts for connecting to Solr and ZooKeeper |
| `auth` | Store credentials for Solr authentication |
| `coll` | Interact with the Solr Collections API |
| `backup` | Create or restore index backups |
| `node` | Work with individual Solr nodes |
| `kube` | Interact with Kubernetes workloads |
| `state` | Export or import cluster state |
| `status` | Summarize replica health across the cluster |
| `zoo` | Work directly with ZooKeeper |

For detailed command documentation, see the [Commands Reference](docs/commands.md).

## Context System

solradm uses a context system to manage connections to multiple Solr clusters:

```yaml
# ~/.config/solradm/config.yaml
currentContext: dev
contexts:
  dev:
    zk: zk-dev:2181
    kubeContext: kind-dev
  prod:
    zk: zk-prod:2181
```

### Context Repositories

Share contexts across your team using repositories:

```bash
# Create a shared repository
solradm context repo create /shared/contexts.yaml

# Add an existing repository
solradm context repo add /shared/contexts.yaml

# Upload a context to a repository
solradm context upload dev --repo /shared/contexts.yaml
```

For more details, see the [Context System Documentation](docs/context-system.md).

## Development

### Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Docker and Docker Compose (for local development)

### Setup

```bash
# Install dependencies
uv sync

# Start local Solr and ZooKeeper
docker compose up -d

# Run tests
pytest
```

### Documentation

The documentation site is built with VitePress:

```bash
npm install
npm run docs:dev
```

## License

This project is open source. See the repository for license details.
