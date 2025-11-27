# Command reference

The `solradm` CLI exposes a large collection of sub‑commands. The lists below describe each command and all available options in detail.

## `context`
Manage contexts that describe how the CLI connects to Solr and ZooKeeper.

### `current`
Print the currently active context.

### `switch <name>`
Activate an existing context.

*Arguments*
- `<name>` – name of the context to use. Autocompletes from known contexts.

### `open-config`
Open the configuration directory in your operating system's file explorer and highlight the settings file.

### Repository management
The nested `repo` commands handle context repositories shared across users.

#### `repo create <path>`
Create a new repository file and register it.
- `<path>` – filesystem path where the repository YAML will be created.

#### `repo add <path>`
Register a new repository file.
- `<path>` – filesystem path to a repository YAML file. Must exist and contain a `contexts.available` list.

#### `repo remove <path>`
Remove a previously configured repository.
- `<path>` – repository file to unregister.

#### `repo list`
Display every configured repository with its name, location, and the contexts it provides.

#### `repo open <path>`
Open the repository location in the file explorer.
- `<path>` – path of the repository to open. Must already be configured.

### `config-dir <path>`
Update the default directory that holds configuration templates used by other commands.
- `<path>` – directory that contains `root` and `configsets` subdirectories.

### `connect <zk> [--kubecontext <name>]`
Connect to an arbitrary ZooKeeper host without saving it.
- `<zk>` – ZooKeeper address of the temporary context.
- `--kubecontext <name>` – optional Kubernetes context to associate with the connection. Autocompletes from local kubeconfig.

### `connect-current`
Resolve the current Kubernetes context, find the `zk-nodeport` service in that namespace and connect using its externally exposed port. Useful when clusters are accessed through NodePort services.

### `save <name>`
Persist the current temporary context under a new name.
- `<name>` – desired context name. Fails if the current context is already persistent.

### `add <name> --zk <host> [--kubecontext <name>] [--interactive]`
Create a new named context and store it locally.
- `<name>` – name of the context.
- `--zk, -z <host>` – ZooKeeper address for the cluster.
- `--kubecontext, -k <name>` – optional Kubernetes context to map to the cluster.
- `--interactive` – start an interactive wizard that prompts for all fields.

### `edit <name> [--zk <host>] [--kubecontext <name>]`
Modify an existing context. The command automatically updates local configuration or the appropriate repository depending on where the context originated.
- `<name>` – context to update.
- `--zk, -z <host>` – new ZooKeeper host. If omitted the existing address is kept.
- `--kubecontext, -k <name>` – new Kubernetes context.

### `delete <name>`
Remove a saved context from local configuration or its source repository.
- `<name>` – name of the context to delete.

### `upload <name> --repo <path>`
Copy a local context into a configured repository.
- `<name>` – name of the context to upload.
- `--repo, -r <path>` – path of the target repository. Must already be listed by `repo add`.

### `list`
Print all known contexts and the locations from which they were loaded. The table marks the location that currently takes precedence with an asterisk.

## `auth`
Store credentials used to authenticate against Solr clusters.

### `edit`
Interactively prompt for username and password and save them to the configuration file.

### `view`
Print the stored credentials. Use with caution in shared terminals.

## `coll`
Interact with the Solr Collections API.

Most collection commands accept a rich set of filtering options provided by the CLI's filter system: `--collection`, `--shard`, `--replica-type`, `--replica-state` and `--replica-position` can be used to narrow down the affected replicas. The examples below focus on the specific options for each sub‑command.

### `depopulate`
Remove replicas from the selected collections.

### `populate [--node <regex>] [--exclude-node <regex>]`
Add replicas for the chosen collection across nodes.
- `--node` – regular expression selecting nodes that should receive replicas.
- `--exclude-node` – regular expression of nodes to skip.

### `create <name> --shards <count> (--conf <name> | --upload-conf <path>) [--populate] [--node <regex>]`
Create an empty collection.
- `<name>` – new collection name.
- `--shards` – number of shards to create.
- `--conf` – name of an existing configuration in ZooKeeper.
- `--upload-conf` – path to a configset to upload before creation; cannot be combined with `--conf`.
- `--populate` – if supplied the command immediately calls `populate` using the optional `--node` filter.

### `delete <pattern>`
Delete all collections matching a regular expression. Automatically removes replicas by calling `depopulate` first.
- `<pattern>` – regex against collection names.

### `reload [--coordinators/--no-coordinators]`
Reload cores for filtered replicas and optionally coordinator nodes.
- `--coordinators` – reload only coordinator nodes.
- `--no-coordinators` – reload only data nodes. If neither option is given both types are reloaded.

### `query <collection> <q> [--rows <n>] [--fl <fields>] [--start <n>] [--fq <query>] [--param <k=v>] [--debug]`
Execute a Lucene query and pretty‑print the results. Requests target coordinator nodes when available, falling back to the overseer.
- `<collection>` – target collection.
- `<q>` – query string.
- `--rows` – number of rows to return (default 10).
- `--fl` – comma‑separated list of fields to display (default `*`).
- `--start` – starting offset within the result set (default 0).
- `--fq` – repeatable filter query passed to Solr.
- `--param` – repeatable arbitrary query parameter of the form `name=value`.
- `--debug` – include the debug section from Solr in the output.

### `reindex --source <collection> --target <collection> [--source-context <ctx>] [--handler <path>] [--fq <query>] [--source-shard <shard>]`
Reindex documents from one collection into another using the DataImportHandler.
- `--source` – collection to read from.
- `--target` – collection to write to.
- `--source-context` – optional context in which the source collection resides.
- `--handler` – path of the DataImportHandler, default `/dataimport`.
- `--fq` – repeatable filter query passed to the handler.
- `--source-shard` – limit reindexing to specific shards.

## `backup`
Create or restore index backups via the Replication API. All backup commands honour the same filtering options as collection commands.

### `take [--location <path>] [--number-to-keep <n>] [--create-directories/--no-create-directories]`
Create backups for the selected replicas.
- `--location` – base directory on each node where backups will be stored (default `/mnt/backups`).
- `--number-to-keep` – if set, old backups beyond this number are removed.
- `--create-directories/--no-create-directories` – automatically create required directories using the configured Kubernetes context (default enabled).

### `restore --location <path>`
Restore backups for the selected collection. Exactly one collection must be filtered.
- `--location` – directory containing shard subdirectories like `shard1`, `shard2`, each with the backup to restore.

## `node`
Work with individual Solr nodes.

### `drain [--node <regex>] [--exclude-node <regex>]`
Remove replicas and stray index directories from nodes that are not part of the selected collections.
- `--node` – regex selecting nodes to drain.
- `--exclude-node` – regex for nodes to leave untouched.

## `kube`
Interact with the Kubernetes cluster that hosts Solr pods.

### `logs <pattern> [--node] [--container <name>]`
Stream logs from matching pods.
- `<pattern>` – regex to match pod names; with `--node` it matches node names instead.
- `--node` – treat the pattern as a node name and stream logs from pods on that node.
- `--container, -c` – if a pod has multiple containers, limit the stream to the specified one.

### `disk <pattern> [--node]`
Show disk usage of `/var/solr` for matching pods.
- `<pattern>` – pod name regex or node name when `--node` is used.
- `--node` – interpret the pattern as a node name.

### `suspend <regex> [--state-file <file>]`
Scale workloads to zero replicas and record their previous state.
- `<regex>` – regular expression matching deployment or statefulset names.
- `--state-file` – optional path to write the state JSON (defaults to an application data file).

### `resume [--state-file <file>]`
Restore workloads that were previously suspended.
- `--state-file` – path to the saved state file. Defaults to the same location used by `suspend`.

## `state`
Export or import the logical state of an entire Solr cluster.

### `export <file>`
Serialize the cluster state to the given file.
- `<file>` – destination path. The format is JSON unless the filename ends in `.yaml` or `.yml`.

### `import <file>`
Read a snapshot and create any missing collections or replicas.
- `<file>` – snapshot file in JSON or YAML format.

## `status`
Summarize replica health across the cluster.

`status` accepts optional filters and output controls.
- `--severity, -s <state>` – only display replicas whose state matches any of the provided severities.
- `--show <n>` – maximum number of rows to display (default 20).

## `zoo`
Work directly with ZooKeeper.

### `edit [path] [--sync-interval <seconds>] [--no-data] [--no-vscode] [--reload]`
Interactively copy a znode subtree to a temporary directory, edit it locally and sync changes back to ZooKeeper.
- `path` – znode path to edit (default `/configs`).
- `--sync-interval, -s` – how often to sync local changes back to ZooKeeper in seconds (default 5).
- `--no-data` – copy only the structure, skipping file contents.
- `--no-vscode` – do not automatically launch VSCode.
- `--reload` – automatically reload collections whose configuration changes.

### `upload <paths...> [--znode-path <path>] [--only-used/--all] [--reload] [--exclude <name>] [--skip-confirm]`
Upload local files or directories into ZooKeeper.
- `<paths...>` – one or more files or directories to upload. If omitted defaults to the configsets in the configured directory.
- `--znode-path` – target znode path (default `/configs`).
- `--only-used/--all` – by default only configurations referenced by collections are uploaded; `--all` forces upload of everything.
- `--reload` – reload collections whose configuration was uploaded.
- `--exclude` – repeatable option listing collections to exclude from reloading.
- `--skip-confirm, -y` – skip the confirmation prompt before uploading.

These commands together form a comprehensive toolkit for administering Solr clusters. Combine them with the context system described earlier to automate everyday tasks and share configurations across your team.
