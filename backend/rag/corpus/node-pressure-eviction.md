---
title: "Node-pressure Eviction"
source_url: "https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/"
---

# Node-pressure Eviction

Node-pressure eviction is the process by which the kubelet proactively terminates pods to reclaim resource on nodes.

The kubelet monitors resources like memory, disk space, and filesystem inodes on your cluster's nodes. When one or more of these resources reach specific consumption levels, the kubelet can proactively fail one or more pods on the node to reclaim resources and prevent starvation.

During a node-pressure eviction, the kubelet sets the phase for the selected pods to `Failed`, and terminates the Pod.

Node-pressure eviction is not the same as API-initiated eviction.

The kubelet does not respect your configured PodDisruptionBudget or the pod's `terminationGracePeriodSeconds`. If you use soft eviction thresholds, the kubelet respects your configured `eviction-max-pod-grace-period`. If you use hard eviction thresholds, the kubelet uses a `0s` grace period (immediate shutdown) for termination.

## Self healing behavior

The kubelet attempts to reclaim node-level resources before it terminates end-user pods. For example, it removes unused container images when disk resources are starved.

If the pods are managed by a workload management object (such as StatefulSet or Deployment) that replaces failed pods, the control plane (`kube-controller-manager`) creates new pods in place of the evicted pods.

### Self healing for static pods

If you are running a static pod on a node that is under resource pressure, the kubelet may evict that static Pod. The kubelet then tries to create a replacement, because static Pods always represent an intent to run a Pod on that node.

The kubelet takes the _priority_ of the static pod into account when creating a replacement. If the static pod manifest specifies a low priority, and there are higher-priority Pods defined within the cluster's control plane, and the node is under resource pressure, the kubelet may not be able to make room for that static pod. The kubelet continues to attempt to run all static pods even when there is resource pressure on a node.

## Eviction signals and thresholds

The kubelet uses various parameters to make eviction decisions, like the following:

* Eviction signals
* Eviction thresholds
* Monitoring intervals

### Eviction signals

Eviction signals are the current state of a particular resource at a specific point in time. The kubelet uses eviction signals to make eviction decisions by comparing the signals to eviction thresholds, which are the minimum amount of the resource that should be available on the node.

The kubelet uses the following eviction signals:

| Eviction Signal | Description |
|---|---|
| `memory.available` | `memory.available` := `node.status.capacity[memory]` - `node.stats.memory.workingSet` |
| `nodefs.available` | `nodefs.available` := `node.stats.fs.available` |
| `nodefs.inodesFree` | `nodefs.inodesFree` := `node.stats.fs.inodesFree` (Linux only) |
| `imagefs.available` | `imagefs.available` := `node.stats.runtime.imagefs.available` |
| `imagefs.inodesFree` | `imagefs.inodesFree` := `node.stats.runtime.imagefs.inodesFree` (Linux only) |
| `containerfs.available` | `containerfs.available` := `node.stats.runtime.containerfs.available` (Linux only) |
| `containerfs.inodesFree` | `containerfs.inodesFree` := `node.stats.runtime.containerfs.inodesFree` (Linux only) |
| `pid.available` | `pid.available` := `node.stats.rlimit.maxpid` - `node.stats.rlimit.curproc` (Linux only) |

Each signal supports either a percentage or a literal value. The kubelet calculates the percentage value relative to the total capacity associated with the signal.

#### Memory signals

On Linux nodes, the value for `memory.available` is derived from the cgroupfs instead of tools like `free -m`. This is important because `free -m` does not work in a container, and if users use the node allocatable feature, out of resource decisions are made local to the end user Pod part of the cgroup hierarchy as well as the root node. The kubelet excludes inactive_file (the number of bytes of file-backed memory on the inactive LRU list) from its calculation, as it assumes that memory is reclaimable under pressure.

On Windows nodes, the value for `memory.available` is derived from the node's global memory commit levels (queried through the `GetPerformanceInfo()` system call) by subtracting the node's global `CommitTotal` from the node's `CommitLimit`. Note that `CommitLimit` can change if the node's page-file size changes.

#### Filesystem signals

The kubelet recognizes three specific filesystem identifiers that can be used with eviction signals (`<identifier>.inodesFree` or `<identifier>.available`):

1. `nodefs`: The node's main filesystem, used for local disk volumes, emptyDir volumes not backed by memory, log storage, ephemeral storage, and more. For example, `nodefs` contains `/var/lib/kubelet`.
2. `imagefs`: An optional filesystem that container runtimes can use to store container images (which are the read-only layers). If there is no separate `containerfs`, the image filesystem also stores container writable layers.
3. `containerfs`: An optional filesystem that container runtimes can use to store container writable layers. When `containerfs` is used, the `imagefs` filesystem can be split to only store images (read-only layers) and nothing else.

These identifiers describe the filesystems as the kubelet observes them. They do not always mean three different mount points: in common layouts, two or all three identifiers can refer to the same underlying filesystem.

The kubelet supports three common layouts for container filesystems:

* Everything is on the single `nodefs`, also referred to as "rootfs" or simply "root". In this layout, `nodefs`, `imagefs`, and `containerfs` all refer to the same underlying filesystem.
* Container runtime storage is on a dedicated disk, separate from the root filesystem. In this layout, `imagefs` and `containerfs` refer to the same underlying filesystem, which stores both image layers and container writable layers. This is often referred to as "split disk" (or "separate disk") filesystem.
* Container writable layers are on `nodefs`, and the container images (read-only layers) are stored on a separate `imagefs`. In this layout, `containerfs` and `nodefs` refer to the same underlying filesystem. This is often referred to as a "split image" filesystem.

The kubelet will attempt to auto-discover these filesystems with their current configuration directly from the underlying container runtime and will ignore other local node filesystems.

The kubelet does not support other container filesystems or storage configurations, and it does not currently support multiple filesystems for images and containers.

### Eviction thresholds

You can specify custom eviction thresholds for the kubelet to use when it makes eviction decisions. You can configure soft and hard eviction thresholds.

Eviction thresholds have the form `[eviction-signal][operator][quantity]`, where:

* `eviction-signal` is the eviction signal to use.
* `operator` is the relational operator you want, such as `<` (less than).
* `quantity` is the eviction threshold amount, such as `1Gi`. The value of `quantity` must match the quantity representation used by Kubernetes. You can use either literal values or percentages (`%`).

For example, if a node has 10GiB of total memory and you want trigger eviction if the available memory drops below 1GiB, you would use the eviction threshold `memory.available<1Gi` or `memory.available<10%`.
