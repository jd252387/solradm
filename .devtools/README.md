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
kubectl apply -f .devtools/kind-fixtures.yaml           # namespace, workloads, services, CRDs
kubectl apply -f .devtools/kind-custom-resources.yaml  # Routes + SolrCloud CR (needs the CRDs above)
kubectl -n solr wait --for=condition=Ready pod --all --timeout=90s

# point the solr-cloud statefulset at the SolrCloud CR, so `Owner` is populated
# the way the Solr operator would set it. Needs the CR's real uid, which is why
# this is not static yaml -- a bogus uid makes the GC delete the statefulset.
CR_UID=$(kubectl -n solr get solrcloud solr-cloud -o jsonpath='{.metadata.uid}')
kubectl -n solr patch statefulset solr-cloud --type=merge -p "{\"metadata\":{\"ownerReferences\":[{\"apiVersion\":\"solr.apache.org/v1beta1\",\"kind\":\"SolrCloud\",\"name\":\"solr-cloud\",\"uid\":\"$CR_UID\",\"controller\":true}]}}"

sa context add kind-test -z localhost:2181 -k kind-solradm-test -n solr --no-interactive
sa kind-test                                    # needs ZooKeeper up: docker compose up -d

sa kube list statefulsets
```

Pods use `registry.k8s.io/pause` so they start instantly and pull nothing.
`kind-fixtures.yaml` installs minimal `route.openshift.io` and `solr.apache.org`
CRDs: the former is enough to make `is_openshift_cluster()` return true and the
`routes` path real, the latter gives the `Owner` column a realistic
`SolrCloud/<name>` to show. Custom resources need a second `apply` because a CR
cannot be created in the same pass that registers its CRD.

Note the PVCs come out unowned (`-`). That is real behaviour, not a gap:
StatefulSet `volumeClaimTemplates` deliberately set no `ownerReference`, so the
claims survive deleting the statefulset. `fake_k8s.py` carries a two-owner PVC
instead, to exercise the multi-line and controller-first paths.

Teardown:

```bash
kind delete cluster --name solradm-test
sa context remove kind-test
```

Note `oc` works against kind too (it is a kubectl superset), including
`oc get routes` once the CRD is applied. OpenShift-only APIs that are not
plain CRDs do not exist there: `oc get dc`, `imagestreams`, `oc login`.
