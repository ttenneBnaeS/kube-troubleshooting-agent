---
title: "Troubleshooting Clusters"
source_url: "https://kubernetes.io/docs/tasks/debug/debug-cluster/"
---

# Troubleshooting Clusters

Debugging common cluster issues.

This doc is about cluster troubleshooting; we assume you have already ruled out your application as the root cause of the problem you are experiencing.

## Listing your cluster

The first thing to debug in your cluster is if your nodes are all registered correctly.

```
kubectl get nodes
```

Verify that all of the nodes you expect to see are present and that they are all in the `Ready` state.

To get detailed information about the overall health of your cluster, you can run:

```
kubectl cluster-info dump
```

### Example: debugging a down/unreachable node

Sometimes when debugging it can be useful to look at the status of a node — for example, because you've noticed strange behavior of a Pod that's running on the node, or to find out why a Pod won't schedule onto the node. As with Pods, you can use `kubectl describe node` and `kubectl get node -o yaml` to retrieve detailed information about nodes. Here's what you'll see if a node is down (disconnected from the network, or kubelet dies and won't restart, etc.). Notice the events that show the node is NotReady, and also notice that the pods are no longer running (they are evicted after five minutes of NotReady status).

```
kubectl get nodes
```

```
NAME                     STATUS       ROLES     AGE     VERSION
kube-worker-1            NotReady     <none>    1h      v1.23.3
kubernetes-node-bols     Ready        <none>    1h      v1.23.3
kubernetes-node-st6x     Ready        <none>    1h      v1.23.3
kubernetes-node-unaj     Ready        <none>    1h      v1.23.3
```

```
kubectl describe node kube-worker-1
```

This command provides detailed information about the node's conditions, resource allocation, and running pods.

## Looking at logs

Digging deeper into the cluster requires logging into the relevant machines. Here are the locations of the relevant log files. On systemd-based systems, you may need to use `journalctl` instead of examining log files.

### Control Plane nodes

* `/var/log/kube-apiserver.log` — API Server, responsible for serving the API
* `/var/log/kube-scheduler.log` — Scheduler, responsible for making scheduling decisions
* `/var/log/kube-controller-manager.log` — a component that runs most Kubernetes built-in controllers, with the notable exception of scheduling (the kube-scheduler handles scheduling).

### Worker Nodes

* `/var/log/kubelet.log` — logs from the kubelet, responsible for running containers on the node
* `/var/log/kube-proxy.log` — logs from `kube-proxy`, which is responsible for directing traffic to Service endpoints

## Cluster failure modes

This is an incomplete list of things that could go wrong, and how to adjust your cluster setup to mitigate the problems.

### Contributing causes

* VM(s) shutdown
* Network partition within cluster, or between cluster and users
* Crashes in Kubernetes software
* Data loss or unavailability of persistent storage (e.g. GCE PD or AWS EBS volume)
* Operator error, for example, misconfigured Kubernetes software or application software

### Specific scenarios

* **API server VM shutdown or apiserver crashing**
  * Results: unable to stop, update, or start new pods, services, replication controller. Existing pods and services should continue to work normally unless they depend on the Kubernetes API.
* **API server backing storage lost**
  * Results: the kube-apiserver component fails to start successfully and become healthy. kubelets will not be able to reach it but will continue to run the same pods and provide the same service proxying. Manual recovery or recreation of apiserver state necessary before apiserver is restarted.
* **Supporting services (node controller, replication controller manager, scheduler, etc) VM shutdown or crashes**
  * Currently those are colocated with the apiserver, and their unavailability has similar consequences as apiserver downtime.
  * Scheduler failures: new pods won't be scheduled, but existing ones continue to run. Replication controller manager failures: replication controllers won't be working, but existing pods continue to run.
* **Individual node (VM or physical machine) shutdown**
  * Results: pods on that node stop running.
* **Network partition**
  * Partition A: assuming the master is in partition A, nodes will lose contact with master after some time. The master will mark the nodes as down, and the replication controller will spin up new pods on the remaining nodes in partition A.
  * Partition B: pods will continue to run but the master cannot manage them or discover that they have stopped.

### Mitigations

* **Action:** use IaaS provider's automatic VM reboot feature for IaaS VMs. **Mitigates:** VM shutdown.
* **Action:** use replicated storage. **Mitigates:** backing storage loss.
* **Action:** use multiple replicas of critical components (cluster DNS, kube-apiserver, kube-controller-manager). **Mitigates:** component software crashes.
* **Action:** use node affinity or anti-affinity so that critical pods spread across nodes. **Mitigates:** individual node shutdown.
