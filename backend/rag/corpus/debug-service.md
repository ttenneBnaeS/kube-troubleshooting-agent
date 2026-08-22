---
title: "Debug Services"
source_url: "https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/"
---

# Debug Services

An issue that comes up rather frequently for new installations of Kubernetes is that a Service is not working properly. You've run your Pods through a Deployment (or other workload controller) and created a Service, but you get no response when you try to access it. This document will hopefully help you to figure out what's going wrong.

## Running commands in a Pod

For many steps here you will want to see what a Pod running in the cluster sees. The simplest way to do this is to run an interactive busybox Pod:

```
kubectl run -it --rm --restart=Never busybox --image=registry.k8s.io/busybox:1.27.2 sh
```

If you already have a running Pod that you prefer to use, you can run a command in it using:

```
kubectl exec <POD-NAME> -c <CONTAINER-NAME> -- <COMMAND>
```

## Setup

For the purposes of this walk-through, let's run some Pods:

```
kubectl create deployment hostnames --image=registry.k8s.io/serve_hostname
kubectl scale deployment hostnames --replicas=3
```

Confirm your Pods are running:

```
kubectl get pods -l app=hostnames
```

Confirm that your Pods are serving. Get the list of Pod IP addresses and test them directly:

```
kubectl get pods -l app=hostnames \
    -o go-template='{{range .items}}{{.status.podIP}}{{"\n"}}{{end}}'
```

From within a pod:

```
for ep in 10.244.0.5:9376 10.244.0.6:9376 10.244.0.7:9376; do
    wget -qO- $ep
done
```

If you are not getting the responses you expect at this point, your Pods might not be healthy or might not be listening on the port you think they are. You might find `kubectl logs` to be useful for seeing what is happening, or perhaps you need to `kubectl exec` directly into your Pods and debug from there.

## Does the Service exist?

This is a step that sometimes gets forgotten, and is the first thing to check. If you tried to access a non-existent Service by name, you'd get something like:

```
wget -O- hostnames
```
```
Resolving hostnames (hostnames)... failed: Name or service not known.
wget: unable to resolve host address 'hostnames'
```

Check whether the Service actually exists:

```
kubectl get svc hostnames
```
```
No resources found.
Error from server (NotFound): services "hostnames" not found
```

Create the Service:

```
kubectl expose deployment hostnames --port=80 --target-port=9376
```

And read it back:

```
kubectl get svc hostnames
```
```
NAME        TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE
hostnames   ClusterIP   10.0.1.175   <none>        80/TCP    5s
```

## Any Network Policy Ingress rules affecting the target Pods?

If you have deployed any Network Policy Ingress rules which may affect incoming traffic to the target Pods, these need to be reviewed.

## Does the Service work by DNS name?

One of the most common ways that clients consume a Service is through a DNS name. From a Pod in the same Namespace:

```
nslookup hostnames
```
```
Address 1: 10.0.0.10 kube-dns.kube-system.svc.cluster.local

Name:      hostnames
Address 1: 10.0.1.175 hostnames.default.svc.cluster.local
```

If this fails, perhaps your Pod and Service are in different Namespaces — try a namespace-qualified name:

```
nslookup hostnames.default
```

If this works, you'll need to adjust your app to use a cross-namespace name, or run your app and Service in the same Namespace. If this still fails, try a fully-qualified name:

```
nslookup hostnames.default.svc.cluster.local
```

Note the suffix here: "default.svc.cluster.local". The "default" is the Namespace you're operating in. The "svc" denotes that this is a Service. The "cluster.local" is your cluster domain, which could be different in your own cluster.

If you are able to do a fully-qualified name lookup but not a relative one, you need to check that your `/etc/resolv.conf` file in your Pod is correct. From within a Pod:

```
cat /etc/resolv.conf
```

You should see something like:

```
nameserver 10.0.0.10
search default.svc.cluster.local svc.cluster.local cluster.local example.com
options ndots:5
```

The `nameserver` line must indicate your cluster's DNS Service (passed into `kubelet` with the `--cluster-dns` flag). The `search` line must include an appropriate suffix for you to find the Service name (up to 6 total, the cluster suffix is passed into `kubelet` with the `--cluster-domain` flag). The `options` line must set `ndots` high enough that your DNS client library considers search paths at all — Kubernetes sets this to 5 by default.

### Does any Service work by DNS name?

If the above still fails, DNS lookups are not working for your Service at all. The Kubernetes master Service should always work. From within a Pod:

```
nslookup kubernetes.default
```

If this fails, go debug the DNS Service (kube-proxy / CoreDNS) itself instead of your own Service.

## Does the Service work by IP?

Assuming you have confirmed that DNS works, the next thing to test is whether your Service works by its IP address. From a Pod in your cluster, access the Service's IP:

```
for i in $(seq 1 3); do 
    wget -qO- 10.0.1.175:80
done
```

If your Service is working, you should get correct responses. If not, there are a number of things that could be going wrong.

## Is the Service defined correctly?

It might sound silly, but you should really double and triple check that your Service is correct and matches your Pod's port. Read back your Service and verify it:

```
kubectl get service hostnames -o json
```

Check that the `selector` on the Service matches labels actually present on the Pods, and that `targetPort` matches the port the container is actually listening on.
