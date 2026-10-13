# .devtools

Scratch rigs for exercising the `kube` commands without a real cluster to hand.
Not shipped — nothing in `src/` imports any of this.

## No cluster at all

```bash
uv run .devtools/fake_k8s.py                  # render all six `kube list` types
uv run .devtools/fake_k8s.py pods -p '^solr'  # any `sa kube list` args pass through
```

A stdlib `http.server` stands in for the API server, so only the *data* is
fake: kubeconfig parsing, the kubernetes client's HTTP and model
deserialization, and the table builders all run for real. Only
`get_current_context` is patched.

Fixtures deliberately cover the awkward shapes — a container with no
`resources` block, requests without limits, limits without requests, an
unbound PVC, a selector-less service, and a `spec: {}` route. `routes` works
because the fake server advertises the `route.openshift.io` API group, which
is exactly what `is_openshift_cluster()` checks for.

## A real throwaway cluster

```bash
kind create cluster --name solradm-test
kubectl apply -f .devtools/kind-fixtures.yaml   # namespace, workloads, services, Route CRD
kubectl apply -f .devtools/kind-routes.yaml     # Route objects (needs the CRD above first)
kubectl -n solr wait --for=condition=Ready pod --all --timeout=90s

sa context add kind-test -z localhost:2181 -k kind-solradm-test -n solr --no-interactive
sa kind-test                                    # needs ZooKeeper up: docker compose up -d

sa kube list statefulsets
```

Pods use `registry.k8s.io/pause` so they start instantly and pull nothing.
`kind-fixtures.yaml` installs a minimal `route.openshift.io` CRD, which is
enough to make `is_openshift_cluster()` return true and the `routes` path real.

Teardown:

```bash
kind delete cluster --name solradm-test
sa context remove kind-test
```

Note `oc` works against kind too (it is a kubectl superset), including
`oc get routes` once the CRD is applied. OpenShift-only APIs that are not
plain CRDs do not exist there: `oc get dc`, `imagestreams`, `oc login`.
