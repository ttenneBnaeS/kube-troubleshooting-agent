---
title: "kubectl Cheat Sheet"
source_url: "https://kubernetes.io/docs/reference/kubectl/cheatsheet/"
---

# kubectl Cheat Sheet (Troubleshooting Focus)

## Viewing and finding resources

```bash
# Get commands with basic output
kubectl get services                          # List all services in the namespace
kubectl get pods --all-namespaces             # List all pods in all namespaces
kubectl get pods -o wide                      # List all pods in the current namespace, with more details
kubectl get deployment my-dep                 # List a particular deployment
kubectl get pods                              # List all pods in the namespace
kubectl get pod my-pod -o yaml                # Get a pod's YAML

# Describe commands with verbose output
kubectl describe nodes my-node
kubectl describe pods my-pod

# List Services Sorted by Name
kubectl get services --sort-by=.metadata.name

# List pods Sorted by Restart Count
kubectl get pods --sort-by='.status.containerStatuses[0].restartCount'

# List PersistentVolumes sorted by capacity
kubectl get pv --sort-by=.spec.capacity.storage

# Get the version label of all pods with label app=cassandra
kubectl get pods --selector=app=cassandra -o \
  jsonpath='{.items[*].metadata.labels.version}'

# Get all worker nodes (exclude control-plane)
kubectl get node --selector='!node-role.kubernetes.io/control-plane'

# Get all running pods in the namespace
kubectl get pods --field-selector=status.phase=Running

# Get ExternalIPs of all nodes
kubectl get nodes -o jsonpath='{.items[*].status.addresses[?(@.type=="ExternalIP")].address}'

# Show labels for all pods
kubectl get pods --show-labels

# Check which nodes are ready
JSONPATH='{range .items[*]}{@.metadata.name}:{range @.status.conditions[*]}{@.type}={@.status};{end}{end}' \
 && kubectl get nodes -o jsonpath="$JSONPATH" | grep "Ready=True"

# Check which nodes are ready with custom-columns
kubectl get node -o custom-columns='NODE_NAME:.metadata.name,STATUS:.status.conditions[?(@.type=="Ready")].status'

# Output decoded secrets without external tools
kubectl get secret my-secret -o go-template='{{range $k,$v := .data}}{{"### "}}{{$k}}{{"\n"}}{{$v|base64decode}}{{"\n\n"}}{{end}}'

# List all Secrets currently in use by a pod
kubectl get pods -o json | jq '.items[].spec.containers[].env[]?.valueFrom.secretKeyRef.name' | grep -v null | sort | uniq

# List Events sorted by timestamp
kubectl get events --sort-by=.metadata.creationTimestamp

# List all warning events
kubectl events --types=Warning

# Compares current state against manifest
kubectl diff -f ./my-manifest.yaml
```

## Interacting with running Pods

```bash
# Get logs from a pod
kubectl logs my-pod                                    # dump pod logs, stdout
kubectl logs -l name=myLabel                           # dump pod logs, label selector
kubectl logs my-pod --previous                         # dump the previous pod logs (useful for crashes)
kubectl logs my-pod -c my-container                    # dump logs from a specific container
kubectl logs my-pod -c my-container --timestamps=true  # dump logs from a specific container with timestamps
kubectl logs -f deployment/my-deployment               # stream logs from a deployment
kubectl logs --tail=20 my-pod                          # dump the last 20 lines of logs
kubectl logs --since=1h my-pod                         # dump logs since 1 hour
kubectl logs --since=10m my-pod                        # dump logs since 10 minutes

# Get interactive shell to a container
kubectl exec -it my-pod -- /bin/sh
kubectl exec -it my-pod -c my-container -- /bin/sh

# List environment variables defined
kubectl exec my-pod env

# Monitor Deployment rollout status until it's done
kubectl rollout status deployment/nginx-deployment

# Get deployment rollout history
kubectl rollout history deployment/nginx-deployment
kubectl rollout history deployment/nginx-deployment --revision=2

# Rollback to the previous deployment
kubectl rollout undo deployment/nginx-deployment
kubectl rollout undo deployment/nginx-deployment --to-revision=0
kubectl rollout pause deployment/nginx-deployment
kubectl rollout resume deployment/nginx-deployment

# Get Pod/container metrics
kubectl top node my-node                    # Show metrics for a given node
kubectl top pod my-pod --containers         # Show metrics for a given pod and its containers
```

## Copying files and directories to and from containers

```bash
kubectl cp /tmp/foo_dir my-pod:/tmp/bar_dir            # local dir to remote pod
kubectl cp /tmp/foo my-pod:/tmp/bar -c my-container    # local file to remote pod container
kubectl cp my-namespace/my-pod:/tmp/foo /tmp/bar       # remote pod to local
```

## Deleting resources

```bash
kubectl delete pod,service baz foo                    # Delete pods and services with same names "baz" and "foo"
kubectl delete pods,services -l name=myLabel          # Delete pods and services with label name=myLabel
kubectl delete pod my-pod --grace-period=120          # Delete a pod with a grace period of 120 seconds
kubectl delete node my-node                           # Mark my-node as unschedulable and drain of the workloads
kubectl patch mypod -p '{"metadata":{"finalizers":null}}'  # Delete a pod bypassing graceful deletion

# Force delete a pod on a dead node
kubectl delete pod my-pod --grace-period=0 --force
```

## Interacting with Nodes and cluster

```bash
kubectl cordon my-node                                      # Mark my-node as unschedulable
kubectl uncordon my-node                                    # Mark my-node as schedulable
kubectl drain my-node                                       # Drain my-node in preparation for maintenance
kubectl drain my-node --ignore-daemonsets --delete-emptydir-data

kubectl taint nodes my-node key=value:NoSchedule           # Taint a node
kubectl taint nodes my-node key=value:NoSchedule-          # Remove the taint

kubectl get nodes -A                                        # Get a list of all nodes in the cluster
```

## Resource types

```bash
kubectl api-resources                       # Print the supported API resources on the server
kubectl api-resources --namespaced=true     # Print the supported namespaced resources
kubectl api-resources --namespaced=false    # Print the supported cluster-scoped resources
```

## Formatting output

```bash
kubectl get pods -o json
kubectl get pod my-pod -o yaml
kubectl get pod my-pod -o wide
kubectl get pod my-pod -o custom-columns=NAME:.metadata.name,RSRC:.metadata.resourceVersion
kubectl get pod my-pod -o custom-columns=NAME:.metadata.name,RSRC:.metadata.resourceVersion --sort-by=.metadata.name
```

## kubectl output verbosity and debugging

```bash
kubectl logs my-pod --timestamps=true
kubectl describe pod my-pod
kubectl get pod my-pod -o yaml
kubectl explain pods                           # get the documentation for pod manifests
kubectl explain pods.spec.containers           # documentation for a specific field

# Increase verbosity — shows request/response
kubectl delete pod my-pod -v=8
```
