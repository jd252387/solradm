# Context System

solradm revolves around the idea of **contexts**. A context encapsulates the connection information for a specific Solr cluster: the ZooKeeper address that holds its state and, optionally, the Kubernetes context used to connect to pods. By naming these environments you can switch between clusters without repeatedly typing connection strings.

## Local configuration

When solradm runs for the first time it creates a configuration file in the user's application data directory. This file stores:

- a list of _available_ contexts that live on your machine
- the name or connection details of the _current_ context
- global options such as authentication credentials and the default directory for configuration files

Every local context is described by a name and ZooKeeper host. The configuration file can be edited manually, but most users rely on `solradm context` commands such as `add`, `edit`, `delete` and `switch` to manage the data. Local contexts are persisted automatically whenever changes are made.

The file is intentionally limited to data that is specific to your workstation. Contexts coming from external repositories are not written back here, keeping the file focused and conflict‑free. solradm also stores any credentials entered through the `auth` command and remembers the default directory where configuration templates live.

### Configuration file layout

The configuration lives in a YAML document typically placed under your user configuration directory—for example `~/.config/solradm/config.yaml` on Linux or `%APPDATA%/solradm/config.yaml` on Windows. It contains several top‑level sections:

```yaml
currentContext: dev
contexts:
  dev:
    zk: zk-dev:2181
    kubeContext: kind-dev
  prod:
    zk: zk-prod:2181
repos:
  - /share/solr/contexts.yaml
auth:
  user: alice
```

Each entry under `contexts` defines the ZooKeeper connection string and, optionally, the Kubernetes context name. Additional keys such as custom labels or notes can be added without breaking solradm. While editing the file manually is allowed, the CLI validates structure and prevents typos, so most users rely on the provided subcommands.

### Working directly with the file

Advanced users sometimes version control their local configuration. Because it is plain text it can be backed up or templated. Be mindful that credentials may be stored here if entered via the `auth` command; encrypt or exclude the file from version control when necessary.

## Context repositories

Teams frequently need to share connection information. Instead of copying local configuration files between users, solradm supports **context repositories**. A repository is simply a YAML file, usually stored on a network drive, that contains a list of contexts. Any user can point the CLI at one or more repository paths. The contents of the repositories are merged with the local configuration at runtime so all contexts appear as if they were defined locally.

Repositories make collaboration painless: when one administrator adds or edits a context in a shared repository, everyone else sees the change immediately. solradm respects precedence rules—contexts defined later in the configuration chain override earlier ones—so a user can still keep a local context with the same name but different connection details if needed.

You can manage repositories with `solradm context repo` subcommands:

- `repo add <path>` registers a repository file. The command validates that the file is structured correctly before enabling it.
- `repo list` prints each configured repository and the contexts it exposes.
- `repo remove <path>` unregisters a repository.
- `repo open <path>` opens the repository location in your file explorer so it can be edited.

solradm keeps repository paths in the global configuration and automatically re‑loads them whenever the program starts. Changes made in a repository are never written back to the local settings file; instead the CLI updates the repository itself when you run commands like `upload` or `edit` against a context that lives there.

### Repository file format

A repository file mirrors the structure of the local configuration but usually omits credentials. It may look like:

```yaml
contexts:
  staging:
    zk: zk-staging:2181
    description: Shared staging cluster
  prod-eu:
    zk: zk-prod-eu:2181
```

Administrators can store repository files on a shared drive or in version control. Because the format is simple YAML, team members can review and track changes over time.

### Precedence and overrides

When solradm starts, it loads the local configuration first and then merges in each repository in the order they were registered. If two contexts share the same name, the later definition wins. This allows you to keep a private variant of a context while still syncing most settings from a shared repository.

### Security considerations

Repositories normally avoid sensitive information. Authentication details remain in each administrator's local configuration. If a repository must include secrets, restrict file permissions and consider using encryption tools or secured network shares.

## Switching and persisting

The `context switch` command activates a named context. If the context exists locally, solradm immediately sets it as current and verifies the ZooKeeper connection. If the context comes from a repository the tool remembers which repository supplied it so you can later upload updates back to the same location. The `current` command prints the full definition of the active context.

Temporary connections are also supported. `context connect <zk-host>` points the CLI to an arbitrary ZooKeeper host without saving it to disk. This is useful for one‑off tasks. After connecting you may run `context save <name>` to persist the temporary configuration under a permanent name.

## Uploading and sharing

Any local context can be published to a repository with `context upload`. The command verifies that the target repository is configured and that no context with the same name already exists. Once uploaded, the context becomes available to all collaborators using that repository.

## Practical workflow

1. A new administrator installs solradm. On first run they may define an initial context and optionally register the team's shared repository.
2. The administrator adds additional local contexts using `context add` or interacts with temporary contexts via `context connect`.
3. When a context is useful to others, `context upload` publishes it to the repository. Coworkers automatically gain access the next time they use solradm.
4. Repository entries can be modified with `context edit` and removed with `context delete`. Local and remote changes stay in sync.

The context system allows seamless movement between clusters and promotes a single source of truth for connection information. By understanding local configuration and repositories you can tailor solradm to personal and team workflows alike.
