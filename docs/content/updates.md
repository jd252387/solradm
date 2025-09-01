+++
title = "Update Checks"
+++

SolrADM checks for new releases on PyPI and caches the result for an hour. When a newer version is available, a highlighted message reminds you to upgrade:

```
A new version of solradm (0.9.1) is available. You are using 0.9.0. Upgrade using: pip install --upgrade solradm
```

The check runs silently at shutdown and stores results in `~/.cache/solradm/update.json`.
