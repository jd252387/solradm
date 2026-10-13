#!/usr/bin/env python
"""Exercise `sa kube list` against a stdlib fake API server — no cluster required.

    uv run .devtools/fake_k8s.py            # render all six resource types
    uv run .devtools/fake_k8s.py pods -p 0  # pass through any `sa kube list` args

Only `get_current_context` is patched. Everything downstream is the real thing:
kubeconfig parsing, the kubernetes client's HTTP + model deserialization, and the
table builders. `routes` works because advertising the route.openshift.io API group
is exactly what `is_openshift_cluster()` looks for.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

NS = "solr"

SOLR_CONTAINERS = [
    {"name": "solrcloud", "image": "solr:9.9",
     "resources": {"requests": {"cpu": "1", "memory": "4Gi"},
                   "limits": {"cpu": "2", "memory": "8Gi"}}},
    {"name": "exporter", "image": "solr-exporter:1.4"},  # no resources at all
]
ZK_CONTAINERS = [
    {"name": "zookeeper", "image": "zookeeper:3.9",
     "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}}},  # requests only
]


def _owner(kind, name, controller=True):
    return [{"apiVersion": "v1", "kind": kind, "name": name,
             "uid": f"uid-{name}", "controller": controller}]


def _workload(name, labels, containers, replicas, ready, owner=None):
    return {"metadata": {"name": name, "namespace": NS, "labels": labels,
                         "ownerReferences": owner},
            "spec": {"replicas": replicas, "selector": {"matchLabels": labels},
                     "template": {"metadata": {"labels": labels}, "spec": {"containers": containers}}},
            "status": {"replicas": replicas, "readyReplicas": ready}}


def _pod(name, labels, containers, node, phase, statuses, owner=None):
    return {"metadata": {"name": name, "namespace": NS, "labels": labels,
                         "ownerReferences": owner},
            "spec": {"nodeName": node, "containers": containers},
            "status": {"phase": phase, "containerStatuses": statuses}}


def _cs(name, ready, restarts):
    return {"name": name, "ready": ready, "restartCount": restarts, "image": "x",
            "imageID": "y", "state": {"running": {"startedAt": "2026-10-13T00:00:00Z"}}}


DATA = {
    "statefulsets": [
        _workload("solr-cloud", {"app": "solr", "tier": "data"}, SOLR_CONTAINERS, 3, 2,
                  _owner("SolrCloud", "solr-cloud")),
        _workload("zookeeper", {"app": "zookeeper", "tier": "data"}, ZK_CONTAINERS, 3, 3,
                  _owner("ZookeeperCluster", "zookeeper")),
    ],
    "deployments": [
        _workload("solr-operator", {"app": "solr-operator"},
                  [{"name": "manager", "image": "solr-operator:0.8",
                    "resources": {"limits": {"cpu": "200m", "memory": "256Mi"}}}], 1, 1),
    ],
    "pods": [
        _pod("solr-cloud-0", {"app": "solr"}, SOLR_CONTAINERS, "worker-1", "Running",
             [_cs("solrcloud", True, 0), _cs("exporter", True, 2)],
             _owner("StatefulSet", "solr-cloud")),
        _pod("solr-cloud-1", {"app": "solr"}, SOLR_CONTAINERS, "worker-2", "Running",
             [_cs("solrcloud", True, 0), _cs("exporter", False, 7)],
             _owner("StatefulSet", "solr-cloud")),
        _pod("zookeeper-0", {"app": "zookeeper"}, ZK_CONTAINERS, None, "Pending", None,
             _owner("StatefulSet", "zookeeper")),
    ],
    "services": [
        {"metadata": {"name": "solr-headless", "namespace": NS, "labels": {"app": "solr"}},
         "spec": {"type": "ClusterIP", "clusterIP": "None", "selector": {"app": "solr"},
                  "ports": [{"port": 8983, "targetPort": 8983, "protocol": "TCP"}]}},
        {"metadata": {"name": "solr-external", "namespace": NS, "labels": {"app": "solr"}},
         "spec": {"type": "LoadBalancer", "clusterIP": "10.96.1.7",
                  "ports": [{"port": 80, "targetPort": 8983, "protocol": "TCP"},
                            {"port": 443, "targetPort": 8983, "protocol": "TCP"}]}},  # no selector
    ],
    "persistentvolumeclaims": [
        {"metadata": {"name": "data-solr-cloud-0", "namespace": NS, "labels": {"app": "solr"},
                      "ownerReferences": _owner("StatefulSet", "solr-cloud")
                                         + _owner("SolrCloud", "solr-cloud", controller=False)},
         "spec": {"storageClassName": "gp3", "volumeName": "pv-aaa", "accessModes": ["ReadWriteOnce"]},
         "status": {"phase": "Bound", "capacity": {"storage": "100Gi"}}},
        {"metadata": {"name": "data-solr-cloud-1", "namespace": NS, "labels": {"app": "solr"}},
         "spec": {"storageClassName": "gp3", "accessModes": ["ReadWriteOnce"]},
         "status": {"phase": "Pending"}},  # unbound: no capacity, no volume
    ],
    "routes": [
        {"metadata": {"name": "solr-ui", "namespace": NS, "labels": {"app": "solr"},
                      "ownerReferences": _owner("SolrCloud", "solr-cloud")},
         "spec": {"host": "solr.apps.example.com", "to": {"name": "solr-external"},
                  "alternateBackends": [{"name": "solr-canary"}],
                  "port": {"targetPort": 8983}, "tls": {"termination": "edge"}}},
        {"metadata": {"name": "minimal", "namespace": NS, "labels": {}}, "spec": {}},
    ],
}

KINDS = {"statefulsets": ("apps/v1", "StatefulSetList"), "deployments": ("apps/v1", "DeploymentList"),
         "pods": ("v1", "PodList"), "services": ("v1", "ServiceList"),
         "persistentvolumeclaims": ("v1", "PersistentVolumeClaimList"),
         "routes": ("route.openshift.io/v1", "RouteList")}

APIS = {"kind": "APIGroupList", "apiVersion": "v1", "groups": [
    {"name": "apps", "versions": [{"groupVersion": "apps/v1", "version": "v1"}],
     "preferredVersion": {"groupVersion": "apps/v1", "version": "v1"}},
    {"name": "route.openshift.io",
     "versions": [{"groupVersion": "route.openshift.io/v1", "version": "v1"}],
     "preferredVersion": {"groupVersion": "route.openshift.io/v1", "version": "v1"}},
]}


def _matches(item, selector):
    """Honor labelSelector so the --label path is genuinely exercised."""
    labels = item["metadata"].get("labels") or {}
    for clause in selector.split(","):
        key, _, value = clause.partition("=")
        if labels.get(key.strip()) != value.strip():
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") == "/apis":
            return self._send(APIS)

        resource = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if resource not in DATA:
            return self._send({"kind": "Status", "message": f"unknown: {parsed.path}"}, 404)

        items = DATA[resource]
        selector = parse_qs(parsed.query).get("labelSelector", [None])[0]
        if selector:
            items = [i for i in items if _matches(i, selector)]

        api_version, kind = KINDS[resource]
        self._send({"apiVersion": api_version, "kind": kind,
                    "metadata": {"resourceVersion": "1"}, "items": items})

    def _send(self, body, status=200):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    kubeconfig = os.path.join(tempfile.mkdtemp(), "config")
    with open(kubeconfig, "w") as f:
        f.write(f"""apiVersion: v1
kind: Config
current-context: fake
clusters:
- name: fake
  cluster:
    server: http://127.0.0.1:{port}
contexts:
- name: fake
  context:
    cluster: fake
    user: fake
    namespace: {NS}
users:
- name: fake
  user: {{}}
""")
    os.environ["KUBECONFIG"] = kubeconfig

    from typer.testing import CliRunner
    from solradm.commands import kube as kube_module
    from solradm.config.context import Context

    kube_module.get_current_context = lambda: Context(None, "", kubecontext="fake", namespace=NS)

    args = sys.argv[1:]
    invocations = [[t] for t in ("statefulsets", "deployments", "pods", "services", "pvc", "routes")] \
        if not args else [args]

    runner = CliRunner()
    failed = False
    for invocation in invocations:
        print(f"\n$ sa kube list {' '.join(invocation)}")
        result = runner.invoke(kube_module.app, ["list", *invocation], env={"COLUMNS": "150"})
        print(result.output, end="")
        if result.exit_code != 0:
            failed = True
            print(f"  exit_code={result.exit_code}")
            if result.exception:
                import traceback
                traceback.print_exception(result.exception)

    server.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
