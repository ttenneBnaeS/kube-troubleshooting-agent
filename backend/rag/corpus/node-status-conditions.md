---
title: "Node Status and Conditions"
source_url: "https://kubernetes.io/docs/concepts/architecture/nodes/"
---

# Node Status, Heartbeats, and Controller

## Node status

A Node's status contains the following information: Addresses, Conditions, Capacity and Allocatable, Info, and Declared Features.

You can use `kubectl` to view a Node's status and other details:

```
kubectl describe node <insert-node-name-here>
```

## Addresses

The usage of these fields varies depending on your cloud provider or bare metal configuration.

* HostName: The hostname as reported by the node's kernel. Can be overridden via the kubelet `--hostname-override` parameter.
* ExternalIP: Typically the IP address of the node that is externally routable (available from outside the cluster).
* InternalIP: Typically the IP address of the node that is routable only within the cluster.

## Conditions

The `conditions` field describes the status of all `Running` nodes. Examples of conditions include:

| Node Condition | Description |
|---|---|
| `Ready` | `True` if the node is healthy and ready to accept pods, `False` if the node is not healthy and is not accepting pods, and `Unknown` if the node controller has not heard from the node in the last `node-monitor-grace-period` (default is 50 seconds) |
| `DiskPressure` | `True` if pressure exists on the disk size — that is, if the disk capacity is low; otherwise `False` |
| `MemoryPressure` | `True` if pressure exists on the node memory — that is, if the node memory is low; otherwise `False` |
| `PIDPressure` | `True` if pressure exists on the processes — that is, if there are too many processes on the node; otherwise `False` |
| `NetworkUnavailable` | `True` if the network for the node is not correctly configured, otherwise `False` |

**Note:** If you use command-line tools to print details of a cordoned Node, the Condition includes `SchedulingDisabled`. `SchedulingDisabled` is not a Condition in the Kubernetes API; instead, cordoned nodes are marked Unschedulable in their spec.

In the Kubernetes API, a node's condition is represented as part of the `.status` of the Node resource. For example, the following JSON structure describes a healthy node:

```json
"conditions": [
  {
    "type": "Ready",
    "status": "True",
    "reason": "KubeletReady",
    "message": "kubelet is posting ready status",
    "lastHeartbeatTime": "2019-06-05T18:38:35Z",
    "lastTransitionTime": "2019-06-05T11:41:27Z"
  }
]
```

When problems occur on nodes, the Kubernetes control plane automatically creates taints that match the conditions affecting the node. An example of this is when the `status` of the Ready condition remains `Unknown` or `False` for longer than the kube-controller-manager's `NodeMonitorGracePeriod`, which defaults to 50 seconds. This will cause either a `node.kubernetes.io/unreachable` taint, for an `Unknown` status, or a `node.kubernetes.io/not-ready` taint, for a `False` status, to be added to the Node.

These taints affect pending pods as the scheduler takes the Node's taints into consideration when assigning a pod to a Node. Existing pods scheduled to the node may be evicted due to the application of `NoExecute` taints. Pods may also have tolerations that let them schedule to and continue running on a Node even though it has a specific taint.

## Capacity and Allocatable

Describes the resources available on the node: CPU, memory, and the maximum number of pods that can be scheduled onto the node.

The fields in the capacity block indicate the total amount of resources that a Node has. The allocatable block indicates the amount of resources on a Node that is available to be consumed by normal Pods.

## Info

Describes general information about the node, such as kernel version, Kubernetes version (kubelet and kube-proxy version), container runtime details, and which operating system the node uses. The kubelet gathers this information from the node and publishes it into the Kubernetes API.

## Node heartbeats

Heartbeats, sent by Kubernetes nodes, help your cluster determine the availability of each node, and to take action when failures are detected.

For nodes there are two forms of heartbeats:

* Updates to the `.status` of a Node.
* Lease objects within the `kube-node-lease` namespace. Each Node has an associated Lease object.

Compared to updates to `.status` of a Node, a Lease is a lightweight resource. Using Leases for heartbeats reduces the performance impact of these updates for large clusters.

The kubelet is responsible for creating and updating the `.status` of Nodes, and for updating their related Leases.

* The kubelet updates the node's `.status` either when there is change in status or if there has been no update for a configured interval. The default interval for `.status` updates to Nodes is 5 minutes, which is much longer than the 40 second default timeout for unreachable nodes.
* The kubelet creates and then updates its Lease object every 10 seconds (the default update interval). Lease updates occur independently from updates to the Node's `.status`. If the Lease update fails, the kubelet retries, using exponential backoff that starts at 200 milliseconds and capped at 7 seconds.

## Node controller

The node controller is a Kubernetes control plane component that manages various aspects of nodes.

The node controller has multiple roles in a node's life. The first is assigning a CIDR block to the node when it is registered (if CIDR assignment is turned on).

The second is keeping the node controller's internal list of nodes up to date with the cloud provider's list of available machines. When running in a cloud environment and whenever a node is unhealthy, the node controller asks the cloud provider if the VM for that node is still available. If not, the node controller deletes the node from its list of nodes.

The third is monitoring the nodes' health. The node controller is responsible for:

* In the case that a node becomes unreachable, updating the `Ready` condition in the Node's `.status` field. In this case the node controller sets the `Ready` condition to `Unknown`.
* If a node remains unreachable: triggering API-initiated eviction of all the Pods scheduled to the unreachable node.

By default, the node controller checks the state of each node every 5 seconds. This period can be configured using the `--node-monitor-period` flag on the `kube-controller-manager`.

### Rate limits on eviction

In most cases, the node controller limits the eviction rate to `--node-eviction-rate` (default 0.1) per second. This means it will not evict pods from more than 1 node per 10 seconds.

The node eviction behavior changes when a node in a given availability zone becomes unhealthy. The node controller checks what percentage of nodes in the zone are unhealthy (Ready condition is `Unknown` or `False`) at the same time:

* If the fraction of unhealthy nodes is at least `--unhealthy-zone-threshold` (default 0.45) then the eviction rate is reduced.
* If the fraction unhealthy nodes is less than the zone threshold, then the eviction rate is normal.

These policies are applied per availability zone. This makes the node controller more resilient to zone-specific issues while still removing pods from unhealthy nodes.

For example, if you have 50 nodes in a single availability zone, the node controller will not evict pods if 22 or fewer nodes are unhealthy (0.45 or less of 50). If 23 or more nodes are unhealthy, the node controller will begin evicting pods.

## Resource capacity tracking

Node objects track information about the Node's resource capacity: for example, the amount of memory and the number of CPUs. Nodes that self-register report their capacity when they register themselves. If you manually add a Node, then you need to set the node's capacity information when you add it.

The Kubernetes scheduler ensures that there are enough resources for all the Pods on a Node. The scheduler checks that the sum of the requests of containers on the node does not exceed the node's capacity. The sum includes all containers managed by the kubelet, but excludes any containers started directly by the container runtime, and also excludes any processes running outside of the kubelet's control.
