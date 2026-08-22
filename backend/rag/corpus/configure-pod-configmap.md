---
title: "Configure a Pod to Use a ConfigMap"
source_url: "https://kubernetes.io/docs/tasks/configure-pod-container/configure-pod-configmap/"
---

# Configure a Pod to Use a ConfigMap

Many applications rely on configuration which is used during either application initialization or runtime. Most times, there is a requirement to adjust values assigned to configuration parameters. ConfigMaps are a Kubernetes mechanism that let you inject configuration data into application pods.

The ConfigMap concept allows you to decouple configuration artifacts from image content to keep containerized applications portable.

## Create a ConfigMap

You can use either `kubectl create configmap` or a ConfigMap generator in `kustomization.yaml` to create a ConfigMap.

```
kubectl create configmap <map-name> <data-source>
```

where `<map-name>` is the name you want to assign to the ConfigMap and `<data-source>` is the directory, file, or literal value to draw the data from. The name of a ConfigMap object must be a valid DNS subdomain name.

When you are creating a ConfigMap based on a file, the key in the `<data-source>` defaults to the basename of the file, and the value defaults to the file content.

### Create a ConfigMap from a directory

You can use `kubectl create configmap` to create a ConfigMap from multiple files in the same directory. When you are creating a ConfigMap based on a directory, kubectl identifies files whose filename is a valid key in the directory and packages each of those files into the new ConfigMap. Any directory entries except regular files are ignored (for example: subdirectories, symlinks, devices, pipes, and more).

**Note:** Each filename being used for ConfigMap creation must consist of only acceptable characters: letters (`A` to `Z` and `a` to `z`), digits (`0` to `9`), '-', '\_', or '.'. If you use `kubectl create configmap` with a directory where any of the file names contains an unacceptable character, the `kubectl` command may fail. The `kubectl` command does not print an error when it encounters an invalid filename.

```
kubectl create configmap game-config --from-file=configure-pod-container/configmap/
```

The above command packages each file in the directory into the game-config ConfigMap. You can display details of the ConfigMap using `kubectl describe configmaps game-config`. The output shows a `Data` section listing each file's key and content.

### Create ConfigMaps from files

You can use `kubectl create configmap` to create a ConfigMap from an individual file, or from multiple files:

```
kubectl create configmap game-config-2 --from-file=configure-pod-container/configmap/game.properties
```

You can pass in the `--from-file` argument multiple times to create a ConfigMap from multiple data sources:

```
kubectl create configmap game-config-2 --from-file=configure-pod-container/configmap/game.properties --from-file=configure-pod-container/configmap/ui.properties
```

Use the option `--from-env-file` to create a ConfigMap from an env-file. Env-files contain a list of environment variables: each line has to be in `VAR=VAL` format, lines beginning with `#` are ignored, blank lines are ignored, and there is no special handling of quotation marks (they become part of the value).

```
kubectl create configmap game-config-env-file \
       --from-env-file=configure-pod-container/configmap/game-env-file.properties
```

Starting with Kubernetes v1.23, `kubectl` supports the `--from-env-file` argument to be specified multiple times to create a ConfigMap from multiple data sources.

### Create a ConfigMap from generator

`kubectl` supports generating ConfigMaps from generators. You can generate a ConfigMap using `kustomization.yaml` with a `ConfigMapGenerator`. For example, the following `kustomization.yaml` file references a `game.properties` file:

```yaml
configMapGenerator:
- name: game-config
  files:
  - configure-pod-container/configmap/game.properties
```

Apply this kustomization directory with `kubectl apply -k configure-pod-container/configmap/`.

You can also generate a ConfigMap from literal values:

```yaml
configMapGenerator:
- name: game-config
  literals:
  - enemies=aliens
  - lives=3
  - secret.code.passphrase=UUDDLRLRBABAS
```

## Define container environment variables using ConfigMap data

### Define a container environment variable with data from a single ConfigMap

```
kubectl create configmap special-config --from-literal=special.how=very
```

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dapi-test-pod
spec:
  containers:
    - name: test-container
      image: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
      command: ["/bin/echo", "$(SPECIAL_LEVEL_KEY)"]
      env:
        - name: SPECIAL_LEVEL_KEY
          valueFrom:
            configMapKeyRef:
              name: special-config
              key: special.how
  restartPolicy: Never
```

Checking the Pod's logs with `kubectl logs dapi-test-pod` shows the output `very`.

### Define container environment variables with data from multiple ConfigMaps

Create multiple ConfigMaps, then reference each with its own `configMapKeyRef` entry under `env` in the Pod spec — the same pattern as above, extended with additional `env` entries pointing at different ConfigMap names/keys.

## Configure all key-value pairs in a ConfigMap as container environment variables

Create a ConfigMap that contains multiple key-value pairs:

```
kubectl create configmap special-config --from-literal=SPECIAL_LEVEL=very --from-literal=SPECIAL_TYPE=charm
```

Create a Pod that uses `envFrom` to define all the ConfigMap's data as container environment variables:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dapi-test-pod
spec:
  containers:
    - name: test-container
      image: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
      command: ["/bin/sh", "-c", "echo $SPECIAL_LEVEL and $SPECIAL_TYPE"]
      envFrom:
        - configMapRef:
            name: special-config
  restartPolicy: Never
```

## Use ConfigMap-defined environment variables in Pod commands

You can use ConfigMap-defined environment variables in the `command` and `args` of a container using the `$(VAR_NAME)` Kubernetes substitution syntax:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dapi-test-pod
spec:
  containers:
    - name: test-container
      image: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
      command: ["/bin/echo"]
      args: ["$(SPECIAL_LEVEL_KEY) and $(SPECIAL_TYPE_KEY)"]
      env:
        - name: SPECIAL_LEVEL_KEY
          valueFrom:
            configMapKeyRef:
              name: special-config
              key: SPECIAL_LEVEL
        - name: SPECIAL_TYPE_KEY
          valueFrom:
            configMapKeyRef:
              name: special-config
              key: SPECIAL_TYPE
  restartPolicy: Never
```

## Add ConfigMap data to a Volume

### Populate a Volume with data stored in a ConfigMap

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dapi-test-pod
spec:
  containers:
    - name: test-container
      image: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
      command: ["/bin/sh", "-c", "cat /etc/config/special.level"]
      volumeMounts:
      - name: config-volume
        mountPath: /etc/config
  volumes:
  - name: config-volume
    configMap:
      name: special-config
  restartPolicy: Never
```

### Add ConfigMap data to a specific path in the Volume

Use the `path` field to specify the desired file path for specific ConfigMap items:

```yaml
volumes:
- name: config-volume
  configMap:
    name: special-config
    items:
    - key: SPECIAL_LEVEL
      path: keys
```

### Optional references

A ConfigMap reference can be marked as optional. This allows the Pod to start even if the ConfigMap does not exist. By default, ConfigMaps are required.

```yaml
volumes:
- name: config-volume
  configMap:
    name: special-config
    optional: true
```

## Understanding ConfigMaps and Pods

### Restrictions

A ConfigMap must be created before it is consumed in a Pod specification, unless it is marked `optional`.

If you use `envFrom` to define environment variables from a ConfigMap, keys that are considered invalid environment variable names will be skipped. The Pod will be allowed to start.

ConfigMaps reside in a specific namespace. A ConfigMap can only be referenced by Pods in the same namespace.

**Note:** The kubelet does not support use of ConfigMaps for Pods that are not created through the API server. Pods created manually or created indirectly (for example through a Deployment, through a Job), can use ConfigMaps; but static Pods cannot use ConfigMaps.
