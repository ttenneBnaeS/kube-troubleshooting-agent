---
title: "Pod Lifecycle"
source_url: "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/"
---

# Pod Lifecycle

This page describes the lifecycle of a Pod. Pods follow a defined lifecycle, starting in the `Pending` phase, moving through `Running` if at least one of its primary containers starts OK, and then through either the `Succeeded` or `Failed` phases depending on whether any container in the Pod terminated in failure.

While a Pod runs, the kubelet manages containers and translates the Pod's spec for the container runtime. The kubelet also manages executing probes that track the health of your application.

Like individual application containers, Pods are considered to be relatively ephemeral (rather than durable) entities. Pods are created, assigned a unique ID (UID), and scheduled to run on nodes where they remain until termination (according to restart policy) or deletion. If a Node dies, the Pods running on (or scheduled to run on) that node are marked for deletion. The control plane marks the Pods for removal after a timeout period.

## Pod lifetime

While a Pod is running, the kubelet is able to restart containers to handle some kind of faults. Within a Pod, Kubernetes tracks different container states and determines what action to take to make the Pod healthy again. This is done in a polling loop that periodically reconciles the desired state (a Pod spec) with the actual state of the running containers.

In the Kubernetes API, Pods have both a specification and an actual status. The status for a Pod object consists of a set of Pod conditions. You can also inject custom readiness information into the condition data for a Pod, if that is useful to your application.

Pods are only scheduled once in their lifetime; assigning a Pod to a specific node is called _binding_, and the process of selecting which node to use is called _scheduling_. Once a Pod has been scheduled and is bound to a node, Kubernetes tries to run that Pod on the node. The Pod runs on that node until it stops, or until the Pod is terminated; if Kubernetes isn't able to start the Pod on the selected node (for example, if the node crashes before the Pod starts), then that particular Pod never starts.

You can use Pod Scheduling Readiness to delay scheduling for a Pod until all its _scheduling gates_ are removed. For example, you might want to define a set of Pods but only trigger scheduling once all the Pods have been created.

### Pods and fault recovery

If one of the containers in the Pod fails, then Kubernetes may try to restart that specific container.

Pods can however fail in a way that the cluster cannot recover from, and in that case Kubernetes does not attempt to heal the Pod further; instead, Kubernetes deletes the Pod and relies on other components to provide automatic healing.

If a Pod is scheduled to a node and that node then fails, the Pod is treated as unhealthy and Kubernetes eventually deletes the Pod. A Pod won't survive an eviction due to a lack of resources or Node maintenance.

Kubernetes uses a higher-level abstraction, called a controller, that handles the work of managing the relatively disposable Pod instances.

A given Pod (as defined by a UID) is never "rescheduled" to a different node; instead, that Pod can be replaced by a new, near-identical Pod. If you make a replacement Pod, it can even have same name (as in `.metadata.name`) that the old Pod had, but the replacement would have a different `.metadata.uid` from the old Pod.

Kubernetes does not guarantee that a replacement for an existing Pod would be scheduled to the same node as the old Pod that was being replaced.

### Associated lifetimes

When something is said to have the same lifetime as a Pod, such as a volume, that means that the thing exists as long as that specific Pod (with that exact UID) exists. If that Pod is deleted for any reason, and even if an identical replacement is created, the related thing (a volume, in this example) is also destroyed and created anew.

## Pod phase

A Pod's `status` field is a PodStatus object, which has a `phase` field.

The phase of a Pod is a simple, high-level summary of where the Pod is in its lifecycle. The phase is not intended to be a comprehensive rollup of observations of container or Pod state, nor is it intended to be a comprehensive state machine.

The number and meanings of Pod phase values are tightly guarded. Other than what is documented here, nothing should be assumed about Pods that have a given `phase` value.

Here are the possible values for `phase`:

| Value | Description |
|-------|-------------|
| `Pending` | The Pod has been accepted by the Kubernetes cluster, but one or more of the containers has not been set up and made ready to run. This includes time a Pod spends waiting to be scheduled as well as the time spent downloading container images over the network. |
| `Running` | The Pod has been bound to a node, and all of the containers have been created. At least one container is still running, or is in the process of starting or restarting. |
| `Succeeded` | All containers in the Pod have terminated in success, and will not be restarted. |
| `Failed` | All containers in the Pod have terminated, and at least one container has terminated in failure. That is, the container either exited with non-zero status or was terminated by the system, and is not set for automatic restarting. |
| `Unknown` | For some reason the state of the Pod could not be obtained. This phase typically occurs due to an error in communicating with the node where the Pod should be running. |

**Note:** When a pod is failing to start repeatedly, `CrashLoopBackOff` may appear in the `Status` field of some kubectl commands. Similarly, when a pod is being deleted, `Terminating` may appear in the `Status` field of some kubectl commands.

Make sure not to confuse _Status_, a kubectl display field for user intuition, with the pod's `phase`. Pod phase is an explicit part of the Kubernetes data model and of the Pod API.

```
NAMESPACE               NAME               READY   STATUS             RESTARTS   AGE
alessandras-namespace   alessandras-pod    0/1     CrashLoopBackOff   200        2d9h
```

A Pod is granted a term to terminate gracefully, which defaults to 30 seconds. You can use the flag `--force` to terminate a Pod by force.

Since Kubernetes 1.27, the kubelet transitions deleted Pods to a terminal phase (`Failed` or `Succeeded` depending on the exit statuses of the pod containers) before their deletion from the API server, with two exceptions:

* static Pods (which are managed directly by the kubelet and represented by mirror Pods)
* force-deleted Pods without a finalizer

If a node dies or is disconnected from the rest of the cluster, Kubernetes applies a policy for setting the `phase` of all Pods on the lost node to Failed.

## Container states

As well as the phase of the Pod overall, Kubernetes tracks the state of each container inside a Pod. You can use container lifecycle hooks to trigger events to run at certain points in a container's lifecycle.

Once the scheduler assigns a Pod to a Node, the kubelet starts creating containers for that Pod using a container runtime. There are three possible container states: `Waiting`, `Running`, and `Terminated`.

To check the state of a Pod's containers, you can use `kubectl describe pod <name-of-pod>`. The output shows the state for each container within that Pod.

### `Waiting`

If a container is not in either the `Running` or `Terminated` state, it is `Waiting`. A container in the `Waiting` state is still running the operations it requires in order to complete start up: for example, pulling the container image from a container image registry, or applying Secret data.

When you use `kubectl` to query a Pod with a container that is `Waiting`, you also see a `Reason` field to summarize why the container is waiting.

### `Running`

The `Running` state indicates that a container is executing without issues. If there was a `postStart` hook configured, it has already executed and completed. When you use `kubectl` to query a Pod with a container that is `Running`, you also see information about when the container entered the `Running` state.

### `Terminated`

A container in the `Terminated` state began execution and then either ran to completion or failed for some reason. When you use `kubectl` to query a Pod with a container that is `Terminated`, you see a reason, an exit code, and the start and finish time for that container's period of execution.

If a container has a `preStop` hook configured, this hook runs before the container enters the `Terminated` state.

## How Pods handle problems with containers

Pods can have a restart policy that governs how containers in the Pod are restarted. The restart policy is applied by the kubelet on the node where the Pod is scheduled.

The restart policy for a Pod is specified in the Pod spec via the `restartPolicy` field, which has the following possible values:

* `Always`: Restart the container if it exits, regardless of the exit code. This is the default restart policy.
* `OnFailure`: Only restart the container if it exits with a non-zero exit code.
* `Never`: Do not automatically restart the container.

The kubelet uses exponential back-off delay (starting at 100ms, capped at 5 minutes) when restarting a container.

### Container restarts

The `restartCount` in the Pod status is incremented each time the kubelet restarts a container. You can use this value to determine how many times a container in a Pod has been restarted. The container restart count is reset to 0 when the Pod is deleted.

### Reduced container restart delay

Since Kubernetes 1.26, when a container in a Pod exits with an exit code of 0 (which typically indicates success), the kubelet does not apply the exponential back-off delay for that container's next restart. By default, the kubelet immediately restarts the container.

This behavior applies even if the `restartPolicy` is set to `OnFailure`. In this case, an exit code of 0 does not trigger a restart under the `OnFailure` policy, so the container does not restart at all.

### Configurable container restart delay

If you need different restart behavior, you can configure a custom restart delay using the `restartDelaySeconds` and `restartDelayDuration` fields in the `restartPolicy` configuration of the Pod spec (this feature requires the `PodRestartPolicy` feature gate to be enabled).

## Pod conditions

A Pod has a `status.conditions` array in which each element is a PodCondition object.

Each condition in a Pod status is represented by a `type` and `status` field. The `type` field describes a condition that may apply to a Pod, and the `status` field shows whether that condition applies, with possible values "`True`", "`False`", or "`Unknown`".

Each Pod condition has two metadata fields:

* `lastProbeTime`: timestamp of when the Pod condition was last probed
* `lastTransitionTime`: timestamp of when the Pod last transitioned from one status to another

Some Pod conditions are defined in the Pod spec, while others are managed by the system. System-managed conditions have names reserved by Kubernetes.

Here are some examples of Pod conditions:

* `PodScheduled`: the Pod has been scheduled to a node
* `ContainersReady`: all containers in the Pod are ready
* `Initialized`: all init containers have started successfully
* `Ready`: the Pod is able to receive traffic

### Pod readiness

A Pod is considered ready when all of its containers are ready, which is when the `ContainersReady` condition is set to `True`. However, you can add custom readiness information to a Pod by setting readiness gates in the Pod spec.

### Status for Pod readiness

If you define a readiness gate in a Pod spec via the `readinessGates` field, the Pod's `Ready` condition is determined by both the `ContainersReady` condition and the status of any custom conditions you defined in your readiness gates.

The kubelet manages the `ContainersReady` condition based on the container readiness status. If you add custom readiness gates to a Pod, the `Ready` condition of the Pod depends on both the `ContainersReady` and the custom readiness gate conditions.

### Pod readiness to start containers

Since Kubernetes 1.28, you can specify that containers should not start until all readiness gates are satisfied. To use this feature, set the `restartPolicy` to `Always` and define a readiness gate that will be set to `True` only when you want containers to start. This allows you to control when containers should start running.

## Resizing Pods

You can resize the CPU and memory requests and limits for containers in a running Pod. This can be done in-place without requiring Pod replacement.

### In-place Pod resize

To resize the CPU and memory requests and limits in-place, modify the `resources` section of the container spec in the Pod definition. The kubelet will update the cgroup limits accordingly.

For a Pod to be resizable, all containers in the Pod must have requests and limits set. Additionally, resize is only possible when the node has sufficient available resources.

### Resizing by launching replacement Pods

If in-place resizing is not possible (for example, the node does not have sufficient resources, or resizing is not supported), you can instead delete the Pod and launch a new Pod with the desired resource configuration.

## Container probes

A probe is a diagnostic performed periodically by the kubelet on a container. To perform a diagnostic, the kubelet either executes code within the container, or makes a network request.

### Startup probe

The kubelet uses startup probes to know when a container application has started. If such a probe is configured, it disables liveness and readiness checks until the startup probe succeeds, making sure those probes don't interfere with the application startup. This can be used to adopt liveness checks on slow starting containers, avoiding them getting killed before they are up and running.

### Liveness probe

The kubelet uses liveness probes to know when to restart a container. For example, liveness probes could catch a deadlock, where an application is running, but unable to make progress. Restarting a container in such a state can help to make the application more available despite bugs.

### Readiness probe

The kubelet uses readiness probes to know when a container is ready to start accepting traffic. A Pod is considered ready when all of its containers are ready. One use of this signal is to control which Pods are used as backends for Services. When a Pod is not ready, it is removed from Service load balancers.

## Termination of Pods

Because Pods represent running processes on nodes in the cluster, it is important to allow those processes to gracefully terminate when they are no longer needed (rather than being abruptly stopped with a KILL signal and having no chance to clean up).

The design aim is for you to be able to request deletion, have that request granted, but then persist until the process has terminated naturally, release those resources, and then the deletion is complete. You can request a Pod to be deleted, and a grace period for termination is set (the default grace period is 30 seconds), allowing the process time to perform cleanup.

### Stop Signals

When you request deletion of a Pod, the requested graceful shutdown period is honored (the default is 30 seconds). A grace period allows the Pod time to do the following: save state to a persistent volume, communicate back to a client about the Pod's termination, and the like.

The kubelet will send a TERM signal to the main process in each container. Once the grace period expires, the kubelet will send the KILL signal to any remaining processes and the Pod will be removed from the API server.

### Defining custom stop signals

You can configure a custom termination grace period and a custom stop signal for containers in your Pod by using the `terminationGracePeriodSeconds` and `terminationMessagePath` fields in the Pod spec.

### Pod Termination Flow

When you request the deletion of a Pod, the following sequence occurs:

1. The Pod is marked as "terminating" and removed from service endpoints.
2. As soon as the Pod is marked terminating, the kubelet on the node running the Pod will begin the graceful shutdown process.
3. At the same time that the Pod is marked terminating, the kubelet starts graceful shutdown of the containers in the Pod.
4. If the containers do not shut down by the end of the grace period, the kubelet will forcibly terminate the container processes.

### Forced Pod termination

By default, all deletions are graceful within 30 seconds. The `kubectl delete` command supports the `--grace-period=<seconds>` option which allows you to override the default and specify your own value for a given Pod.

Setting the grace period to `0` forcibly and immediately deletes the Pod from the cluster. If you must delete a Pod immediately, use the `--force --grace-period=0` command.

### Pod shutdown and sidecar containers

Since Kubernetes 1.29, if you have defined any sidecar containers (containers with `restartPolicy: Always`) in a Pod that is being shut down, the kubelet will not send the termination signal to sidecar containers. Sidecar containers will continue to run during the Pod's graceful termination period and after other containers have exited.

### Garbage collection of Pods

When you delete a Pod, you can specify whether the Pod's dependent objects are also deleted automatically. The garbage collector removes objects that no longer have an owner reference.

When you delete a Pod, if you do not delete the Pod's dependent objects, those dependent objects are orphaned. The garbage collector will eventually clean up orphaned objects.

## Pod behavior during kubelet restarts

The kubelet is able to track Pods and restart them if the node where the Pod is running is rebooted or the kubelet process itself is restarted. When the kubelet restarts, it has information about all Pods that were previously scheduled on that node.

### Detection of kubelet restarts

When a kubelet restarts, any Pods that were previously running on the node are re-created by the kubelet if they should be restarted according to their restart policies.
