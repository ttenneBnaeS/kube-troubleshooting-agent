---
title: "Debugging DNS Resolution"
source_url: "https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/"
---

# Debugging DNS Resolution

This page provides hints on diagnosing DNS problems.

## Before you begin

Your cluster must be configured to use the CoreDNS addon or its precursor, kube-dns. Your Kubernetes server must be at or later than version v1.6.

### Create a simple Pod to use as a test environment

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dnsutils
  namespace: default
spec:
  containers:
  - name: dnsutils
    image: registry.k8s.io/e2e-test-images/agnhost:2.39
    imagePullPolicy: IfNotPresent
  restartPolicy: Always
```

**Note:** This example creates a pod in the `default` namespace. DNS name resolution for services depends on the namespace of the pod.

Use that manifest to create a Pod:

```bash
kubectl apply -f https://k8s.io/examples/admin/dns/dnsutils.yaml
```

…and verify its status:

```bash
kubectl get pods dnsutils
```

```
NAME       READY     STATUS    RESTARTS   AGE
dnsutils   1/1       Running   0          <some-time>
```

Once that Pod is running, you can exec `nslookup` in that environment. If you see something like the following, DNS is working correctly.

```bash
kubectl exec -i -t dnsutils -- nslookup kubernetes.default
```

```
Server:    10.0.0.10
Address 1: 10.0.0.10

Name:      kubernetes.default
Address 1: 10.0.0.1
```

If the `nslookup` command fails, check the following.

### Check the local DNS configuration first

Take a look inside the resolv.conf file:

```bash
kubectl exec -ti dnsutils -- cat /etc/resolv.conf
```

Verify that the search path and name server are set up like the following (note that search path may vary for different cloud providers):

```
search default.svc.cluster.local svc.cluster.local cluster.local google.internal c.gce_project_id.internal
nameserver 10.0.0.10
options ndots:5
```

Errors such as the following indicate a problem with the CoreDNS (or kube-dns) add-on or with associated Services:

```bash
kubectl exec -i -t dnsutils -- nslookup kubernetes.default
```

```
Server:    10.0.0.10
Address 1: 10.0.0.10

nslookup: can't resolve 'kubernetes.default'
```

### Check if the DNS pod is running

```bash
kubectl get pods --namespace=kube-system -l k8s-app=kube-dns
```

```
NAME                       READY     STATUS    RESTARTS   AGE
coredns-7b96bf9f76-5hsxb   1/1       Running   0           1h
coredns-7b96bf9f76-mvmmt   1/1       Running   0           1h
```

**Note:** The value for label `k8s-app` is `kube-dns` for both CoreDNS and kube-dns deployments.

If you see that no CoreDNS Pod is running or that the Pod has failed/completed, the DNS add-on may not be deployed by default in your current environment and you will have to deploy it manually.

### Check for errors in the DNS pod

```bash
kubectl logs --namespace=kube-system -l k8s-app=kube-dns
```

Here is an example of a healthy CoreDNS log:

```
.:53
2018/08/15 14:37:17 [INFO] CoreDNS-1.2.2
2018/08/15 14:37:17 [INFO] linux/amd64, go1.10.3, 2e322f6
CoreDNS-1.2.2
linux/amd64, go1.10.3, 2e322f6
2018/08/15 14:37:17 [INFO] plugin/reload: Running configuration MD5 = 24e6c59e83ce706f07bcc82c31b1ea1c
```

See if there are any suspicious or unexpected messages in the logs.

### Is DNS service up?

```bash
kubectl get svc --namespace=kube-system
```

```
NAME         TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)             AGE
kube-dns     ClusterIP   10.0.0.10      <none>        53/UDP,53/TCP        1h
```

**Note:** The service name is `kube-dns` for both CoreDNS and kube-dns deployments.

### Are DNS endpoints exposed?

```bash
kubectl get endpointslice -l kubernetes.io/service-name=kube-dns --namespace=kube-system
```

```
NAME             ADDRESSTYPE   PORTS   ENDPOINTS                  AGE
kube-dns-zxoja   IPv4          53      10.180.3.17,10.180.3.17    1h
```

If you do not see the endpoints, see the endpoints section in the debugging Services documentation.

### Are DNS queries being received/processed?

You can verify if queries are being received by CoreDNS by adding the `log` plugin to the CoreDNS configuration (aka Corefile). The CoreDNS Corefile is held in a ConfigMap named `coredns`. To edit it, use the command:

```bash
kubectl -n kube-system edit configmap coredns
```

Then add `log` in the Corefile section per the example below:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        log
        errors
        health
        kubernetes cluster.local in-addr.arpa ip6.arpa {
          pods insecure
          upstream
          fallthrough in-addr.arpa ip6.arpa
        }
        prometheus :9153
        forward . /etc/resolv.conf
        cache 30
        loop
        reload
        loadbalance
    }
```

After saving the changes, it may take up to a minute or two for Kubernetes to propagate these changes to the CoreDNS pods.

Next, make some queries and view the logs per the sections above in this document. If CoreDNS pods are receiving the queries, you should see them in the logs.

Here is an example of a query in the log:

```
172.17.0.18:41675 - [07/Sep/2018:15:29:11 +0000] 59925 "A IN kubernetes.default.svc.cluster.local. udp 54 false 512" NOERROR qr,aa,rd,ra 106 0.000066649s
```

### Does CoreDNS have sufficient permissions?

CoreDNS must be able to list service and endpointslice related resources to properly resolve service names.

Sample error message:

```
2022-03-18T07:12:15.699431183Z [INFO] 10.96.144.227:52299 - 3686 "A IN serverproxy.contoso.net.cluster.local. udp 52 false 512" SERVFAIL qr,aa,rd 145 0.000091221s
```

First, get the current ClusterRole of `system:coredns`:

```bash
kubectl describe clusterrole system:coredns -n kube-system
```

Expected output:

```
PolicyRule:
  Resources                        Non-Resource URLs  Resource Names  Verbs
  ---------                        -----------------  --------------  -----
  endpoints                        []                 []              [list watch]
  namespaces                       []                 []              [list watch]
  pods                             []                 []              [list watch]
  services                         []                 []              [list watch]
  endpointslices.discovery.k8s.io  []                 []              [list watch]
```

If any permissions are missing, edit the ClusterRole to add them:

```bash
kubectl edit clusterrole system:coredns -n kube-system
```

Example insertion of EndpointSlices permissions:

```yaml
- apiGroups:
  - discovery.k8s.io
  resources:
  - endpointslices
  verbs:
  - list
  - watch
```

### Are you in the right namespace for the service?

DNS queries that don't specify a namespace are limited to the pod's namespace.

If the namespace of the pod and service differ, the DNS query must include the namespace of the service.

This query is limited to the pod's namespace:

```bash
kubectl exec -i -t dnsutils -- nslookup <service-name>
```

This query specifies the namespace:

```bash
kubectl exec -i -t dnsutils -- nslookup <service-name>.<namespace>
```

## Known issues

Some Linux distributions (e.g. Ubuntu) use a local DNS resolver by default (systemd-resolved). Systemd-resolved moves and replaces `/etc/resolv.conf` with a stub file that can cause a fatal forwarding loop when resolving names in upstream servers. This can be fixed manually by using kubelet's `--resolv-conf` flag to point to the correct `resolv.conf` (with `systemd-resolved`, this is `/run/systemd/resolve/resolv.conf`).

On some distributions, `/etc/resolv.conf` is a symlink to `/run/systemd/resolve/resolv.conf`. In any case, the DNS resolution on the node must use a proper DNS server (not the local resolver's stub file). Use `resolvectl status` to look at the configured DNS servers.

If you are running CoreDNS on Kubernetes version 1.6 or earlier, you may need to specify the flag value of `--dns-ip` of the kubelet as the IP of your DNS pod. By default, Kubernetes will generate the flag value of `--cluster-dns` to be the IP of the CoreDNS Service.
