---
title: "Distribute Credentials Securely Using Secrets"
source_url: "https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/"
---

# Distribute Credentials Securely Using Secrets

This page shows how to securely inject sensitive data, such as passwords and encryption keys, into Pods.

## Convert your secret data to a base-64 representation

Suppose you want to have two pieces of secret data: a username `my-app` and a password `39528$vdg7Jb`. First, use a base64 encoding tool to convert your username and password to a base64 representation:

```
echo -n 'my-app' | base64
echo -n '39528$vdg7Jb' | base64
```

The output shows that the base-64 representation of your username is `bXktYXBw`, and the base-64 representation of your password is `Mzk1MjgkdmRnN0pi`.

**Caution:** Use a local tool trusted by your OS to decrease the security risks of external tools.

## Create a Secret

Here is a configuration file you can use to create a Secret that holds your username and password:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: test-secret
data:
  username: bXktYXBw
  password: Mzk1MjgkdmRnN0pi
```

Create the Secret with `kubectl apply -f secret.yaml`, then view details with `kubectl describe secret test-secret`:

```
Name:       test-secret
Namespace:  default
Type:   Opaque

Data
====
password:   13 bytes
username:   7 bytes
```

### Create a Secret directly with kubectl

If you want to skip the Base64 encoding step, you can create the same Secret using the `kubectl create secret` command:

```
kubectl create secret generic test-secret --from-literal='username=my-app' --from-literal='password=39528$vdg7Jb'
```

## Create a Pod that has access to the secret data through a Volume

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: secret-test-pod
spec:
  containers:
    - name: test-container
      image: nginx
      volumeMounts:
        - name: secret-volume
          mountPath: /etc/secret-volume
          readOnly: true
  volumes:
    - name: secret-volume
      secret:
        secretName: test-secret
```

The secret data is exposed to the Container through a Volume mounted under `/etc/secret-volume`. Listing that directory shows two files, one for each piece of secret data: `password` and `username`. Modify your image or command line so that the program looks for files in the `mountPath` directory. Each key in the Secret `data` map becomes a file name in this directory.

### Project Secret keys to specific file paths

You can also control the paths within the volume where Secret keys are projected. Use the `.spec.volumes[].secret.items` field to change the target path of each key:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  containers:
  - name: mypod
    image: redis
    volumeMounts:
    - name: foo
      mountPath: "/etc/foo"
      readOnly: true
  volumes:
  - name: foo
    secret:
      secretName: mysecret
      items:
      - key: username
        path: my-group/my-username
```

When you deploy this Pod, the `username` key from `mysecret` is available to the container at the path `/etc/foo/my-group/my-username` instead of at `/etc/foo/username`, and the `password` key from that Secret object is not projected.

If you list keys explicitly using `.spec.volumes[].secret.items`: only keys specified in `items` are projected; to consume all keys from the Secret, all of them must be listed in the `items` field; and all listed keys must exist in the corresponding Secret, otherwise the volume is not created.

### Set POSIX permissions for Secret keys

You can set the POSIX file access permission bits for a single Secret key. If you don't specify any permissions, `0644` is used by default. You can also set a default POSIX file mode for the entire Secret volume, and you can override per key if needed:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  containers:
  - name: mypod
    image: redis
    volumeMounts:
    - name: foo
      mountPath: "/etc/foo"
  volumes:
  - name: foo
    secret:
      secretName: mysecret
      defaultMode: 0400
```

**Note:** If you're defining a Pod or a Pod template using JSON, beware that the JSON specification doesn't support octal literals for numbers because JSON considers `0400` to be the _decimal_ value `400`. In JSON, use decimal values for the `defaultMode` instead. If you're writing YAML, you can write the `defaultMode` in octal.

## Define container environment variables using Secret data

You can consume the data in Secrets as environment variables in your containers. If a container already consumes a Secret in an environment variable, a Secret update will not be seen by the container unless it is restarted. There are third party solutions for triggering restarts when secrets change.

### Define a container environment variable with data from a single Secret

```
kubectl create secret generic backend-user --from-literal=backend-username='backend-admin'
```

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: env-single-secret
spec:
  containers:
  - name: envars-test-container
    image: nginx
    env:
    - name: SECRET_USERNAME
      valueFrom:
        secretKeyRef:
          name: backend-user
          key: backend-username
```

### Define container environment variables with data from multiple Secrets

Create multiple Secrets, then reference each with its own `secretKeyRef` entry under `env` in the Pod spec, one per Secret/key combination.

## Configure all key-value pairs in a Secret as container environment variables

```
kubectl create secret generic test-secret --from-literal=username='my-app' --from-literal=password='39528$vdg7Jb'
```

Use `envFrom` to define all of the Secret's data as container environment variables. Each key from the Secret becomes an environment variable name in the container:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: envfrom-secret
spec:
  containers:
  - name: envars-test-container
    image: nginx
    envFrom:
    - secretRef:
        name: test-secret
  restartPolicy: Never
```

## Example: Provide prod/test credentials to Pods using Secrets

This example illustrates a Pod that consumes credentials from a Secret to connect to a database.

```
kubectl create secret generic prod-db-secret --from-literal=username='produser' --from-literal=password='Y4nys7f11'
kubectl create secret generic test-db-secret --from-literal=username='testuser' --from-literal=password='1f6a7a2f'
```

Pod specification:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: backend-pod
spec:
  containers:
  - name: backend-container
    image: myimage
    env:
    - name: DB_USERNAME
      valueFrom:
        secretKeyRef:
          name: prod-db-secret
          key: username
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: prod-db-secret
          key: password
  restartPolicy: Never
```

This pattern lets the same Pod spec be pointed at different Secret objects (`prod-db-secret` vs `test-db-secret`) to switch between environments without changing application code.
