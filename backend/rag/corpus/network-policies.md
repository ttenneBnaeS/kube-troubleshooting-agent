---
title: "Network Policies"
source_url: "https://kubernetes.io/docs/concepts/services-networking/network-policies/"
---

# Network Policies

If you want to control traffic flow at the IP address or port level (OSI layer 3 or 4), NetworkPolicies allow you to specify rules for traffic flow within your cluster, and also between Pods and the outside world. Your cluster must use a network plugin that supports NetworkPolicy enforcement.

NetworkPolicies are an application-centric construct which allow you to specify how a pod is allowed to communicate with various network "entities" over the network. NetworkPolicies apply to a connection with a pod on one or both ends, and are not relevant to other connections.

The entities that a Pod can communicate with are identified through a combination of the following three identifiers:

1. Other pods that are allowed (exception: a pod cannot block access to itself)
2. Namespaces that are allowed
3. IP blocks (exception: traffic to and from the node where a Pod is running is always allowed, regardless of the IP address of the Pod or the node)

When defining a pod- or namespace-based NetworkPolicy, you use a selector to specify what traffic is allowed to and from the Pod(s) that match the selector. Meanwhile, when IP-based NetworkPolicies are created, we define policies based on IP blocks (CIDR ranges).

## Prerequisites

Network policies are implemented by the network plugin. To use network policies, you must be using a networking solution which supports NetworkPolicy. Creating a NetworkPolicy resource without a controller that implements it will have no effect.

## The two sorts of pod isolation

There are two sorts of isolation for a pod: isolation for egress, and isolation for ingress. They concern what connections may be established. "Isolation" here is not absolute, rather it means "some restrictions apply". The alternative, "non-isolated for $direction", means that no restrictions apply in the stated direction. The two sorts of isolation (or not) are declared independently, and are both relevant for a connection from one pod to another.

By default, a pod is non-isolated for egress; all outbound connections are allowed. A pod is isolated for egress if there is any NetworkPolicy that both selects the pod and has "Egress" in its `policyTypes`. When a pod is isolated for egress, the only allowed connections from the pod are those allowed by the `egress` list of some NetworkPolicy that applies to the pod for egress. Reply traffic for those allowed connections will also be implicitly allowed. The effects of those `egress` lists combine additively.

By default, a pod is non-isolated for ingress; all inbound connections are allowed. A pod is isolated for ingress if there is any NetworkPolicy that both selects the pod and has "Ingress" in its `policyTypes`. When a pod is isolated for ingress, the only allowed connections into the pod are those from the pod's node and those allowed by the `ingress` list of some NetworkPolicy that applies to the pod for ingress.

Network policies do not conflict; they are additive. If any policy or policies apply to a given pod for a given direction, the connections allowed in that direction from that pod is the union of what the applicable policies allow. Thus, order of evaluation does not affect the policy result.

For a connection from a source pod to a destination pod to be allowed, both the egress policy on the source pod and the ingress policy on the destination pod need to allow the connection. If either side does not allow the connection, it will not happen.

## The NetworkPolicy resource

An example NetworkPolicy might look like this:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-network-policy
  namespace: default
spec:
  podSelector:
    matchLabels:
      role: db
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - ipBlock:
        cidr: 172.17.0.0/16
        except:
        - 172.17.1.0/24
    - namespaceSelector:
        matchLabels:
          project: myproject
    - podSelector:
        matchLabels:
          role: frontend
    ports:
    - protocol: TCP
      port: 6379
  egress:
  - to:
    - ipBlock:
        cidr: 10.0.0.0/24
    ports:
    - protocol: TCP
      port: 5978
```

**Note:** POSTing this to the API server for your cluster will have no effect unless your chosen networking solution supports network policy.

**podSelector**: Each NetworkPolicy includes a `podSelector` which selects the grouping of pods to which the policy applies. The example policy selects pods with the label "role=db". An empty `podSelector` selects all pods in the namespace.

**policyTypes**: Each NetworkPolicy includes a `policyTypes` list which may include either `Ingress`, `Egress`, or both. If no `policyTypes` are specified on a NetworkPolicy then by default `Ingress` will always be set and `Egress` will be set if the NetworkPolicy has any egress rules.

**ingress**: Each NetworkPolicy may include a list of allowed `ingress` rules. Each rule allows traffic which matches both the `from` and `ports` sections.

**egress**: Each NetworkPolicy may include a list of allowed `egress` rules. Each rule allows traffic which matches both the `to` and `ports` sections.

So, the example NetworkPolicy:

1. isolates `role=db` pods in the `default` namespace for both ingress and egress traffic (if they weren't already isolated)
2. (Ingress rules) allows connections to all pods in the `default` namespace with the label `role=db` on TCP port 6379 from: any pod in the `default` namespace with the label `role=frontend`; any pod in a namespace with the label `project=myproject`; IP addresses in the ranges `172.17.0.0`–`172.17.0.255` and `172.17.2.0`–`172.17.255.255` (i.e. all of `172.17.0.0/16` except `172.17.1.0/24`)
3. (Egress rules) allows connections from any pod in the `default` namespace with the label `role=db` to CIDR `10.0.0.0/24` on TCP port 5978

## Behavior of `to` and `from` selectors

There are four kinds of selectors that can be specified in an `ingress` `from` section or `egress` `to` section:

**podSelector**: This selects particular Pods in the same namespace as the NetworkPolicy which should be allowed as ingress sources or egress destinations.

**namespaceSelector**: This selects particular namespaces for which all Pods should be allowed as ingress sources or egress destinations.

**namespaceSelector** _and_ **podSelector**: A single `to`/`from` entry that specifies both `namespaceSelector` and `podSelector` selects particular Pods within particular namespaces. Be careful to use correct YAML syntax:

```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        user: alice
    podSelector:
      matchLabels:
        role: client
```

This policy contains a single `from` element allowing connections from Pods with the label `role=client` in namespaces with the label `user=alice`. But the following policy is different — two separate `from` entries, matched with OR logic instead of AND:

```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        user: alice
  - podSelector:
      matchLabels:
        role: client
```

It contains two elements in the `from` array, and allows connections from Pods in the local Namespace with the label `role=client`, _or_ from any Pod in any namespace with the label `user=alice`. When in doubt, use `kubectl describe` to see how Kubernetes has interpreted the policy.

**ipBlock**: This selects particular IP CIDR ranges to allow as ingress sources or egress destinations. These should be cluster-external IPs, since Pod IPs are ephemeral and unpredictable.

Cluster ingress and egress mechanisms commonly require rewriting of source or destination IPs. In cases where this happens, it is not defined whether this happens before or after NetworkPolicy processing, and the behavior may be different for different combinations of network plugin, cloud provider, `Service` implementation, etc. For egress, this means you cannot necessarily rely on Pods being able to reach IPs within the `ipBlock` range, since the destination IP may have been rewritten by the time the request leaves the node.

## Default policies

By default, if no policies exist in a namespace, then all ingress and all egress traffic is allowed to and from pods in that namespace.

### Default deny all ingress traffic

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
spec:
  podSelector: {}
  policyTypes:
  - Ingress
```

This ensures that even pods that aren't selected by any other NetworkPolicy will not be allowed ingress traffic.

### Allow all ingress traffic

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-all-ingress
spec:
  podSelector: {}
  ingress:
  - {}
  policyTypes:
  - Ingress
```

### Default deny all egress traffic

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
spec:
  podSelector: {}
  policyTypes:
  - Egress
```

### Allow all egress traffic

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-all-egress
spec:
  podSelector: {}
  egress:
  - {}
  policyTypes:
  - Egress
```

### Default deny all ingress and all egress traffic

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
```

## Network traffic filtering

Kubernetes network policies filter traffic at layer 3 and 4 of the OSI model. Specifically, they can restrict the protocol (TCP, UDP, or SCTP), the destination port, and, through the `to`/`from` selectors, the source and destination IP addresses (or the pods and namespaces).

Kubernetes network policies cannot filter traffic based on layer 7 (application layer) information, such as HTTP headers or other application-specific attributes. If you need this level of control, you may need to use a service mesh or other tools.

## Targeting a range of ports

You can use the `endPort` field to specify a port range. Both the `port` and `endPort` fields must be defined for a valid port range. The range is inclusive on both ends.

```yaml
ports:
- protocol: TCP
  port: 6379
  endPort: 6388
```

## Targeting multiple namespaces by label

You can write a NetworkPolicy that targets multiple namespaces by using multiple `namespaceSelector` entries in the `from` or `to` fields:

```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        project: myproject
  - namespaceSelector:
      matchLabels:
        project: otherproject
```

## Targeting a Namespace by its name

Kubernetes does not have a built-in mechanism that allows you to target namespaces by their names in NetworkPolicy. However, you can use standard labels to identify namespaces - the Kubernetes control plane sets an immutable label `kubernetes.io/metadata.name` on all namespaces, whose value is the namespace's name:

```yaml
ingress:
- from:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: myns
```

## Pod lifecycle

A NetworkPolicy can be applied to a pod at any time, including before the pod is created. When a pod is created, any applicable NetworkPolicies are immediately applied to it. NetworkPolicies that apply to a pod may change during the pod's lifetime; the effects of these changes are reflected immediately. When a pod is deleted, any NetworkPolicies that were applied to it cease to apply.

## NetworkPolicy and `hostNetwork` pods

Pods that have `hostNetwork: true` bypass the network namespace and use the node's network instead. NetworkPolicies do not apply to traffic in the host network namespace. In clusters where you have nodes running with `hostNetwork: true`, you should restrict what traffic those pods can send and receive by using OS-level firewalls or other mechanisms.

## What you can't do with network policies (at least, not yet)

The following features are not supported by NetworkPolicy, but you might be able to work around them using OS-level components (such as SELinux, firewalls, or other mechanisms) or layer 7 proxies (such as a service mesh):

- Forcing traffic through a proxy
- Anything TLS-related
- Selecting pods or namespaces by names other than labels
- Creating policies which apply to traffic between Services
- Creating policies which apply to traffic generated by the system itself (like kubelet, kube-proxy or the DNS server)

## NetworkPolicy's impact on existing connections

NetworkPolicies apply to new connections as they are established. Existing connections are typically allowed to continue, even if a NetworkPolicy would have prevented the connection from being established.

The implementation behavior varies by network plugin - some network plugins may reset existing connections when a new policy is applied that would disallow those connections, but this behavior is not guaranteed. The safest approach is to stop sending traffic on a connection as soon as you apply a NetworkPolicy that would disallow that traffic.
