+++
title = "Autocomplete"
+++

Shell completion is supported for many arguments. The CLI suggests values by querying the Solr cluster and ZooKeeper for current state, including:

- Collection names, shard numbers, replica types and states.
- Node names grouped by role.
- Configuration and context names.
- Kubernetes contexts for cloud deployments.

Completion helpers filter suggestions based on the partially typed value to speed up interactive use.
