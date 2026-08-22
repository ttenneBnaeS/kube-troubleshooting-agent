---
title: "Secrets"
source_url: "https://kubernetes.io/docs/concepts/configuration/secret/"
---

# Secrets

A Secret is an object that contains a small amount of sensitive data such as a password, a token, or a key. Such information might otherwise be put in a Pod specification or in a container image. Using a Secret means that you don't need to include confidential data in your application code.

Because Secrets can be created independently of the Pods that use them, there is less risk of the Secret (and its data) being exposed during the workflow of creating, viewing, and editing Pods. Kubernetes, and applications that run in your cluster, can also take additional precautions with Secrets, such as avoiding writing sensitive data to nonvolatile storage.

Secrets are similar to ConfigMaps but are specifically intended to hold confidential data.

**Caution:** Kubernetes Secrets are, by default, stored unencrypted in the API server's underlying data store (etcd). Anyone with API access can retrieve or modify a Secret, and so can anyone with access to etcd. Additionally, anyone who is authorized to create a Pod in a namespace can use that access to read any Secret in that namespace; this includes indirect access such as the ability to create a Deployment.

In order to safely use Secrets, take at least the following steps:

1. Enable Encryption at Rest for Secrets.
2. Enable or configure RBAC rules with least-privilege access to Secrets.
3. Restrict Secret access to specific containers.
4. Consider using external Secret store providers.

## Uses for Secrets

You can use Secrets for purposes such as the following:

* Set environment variables for a container.
* Provide credentials such as SSH keys or passwords to Pods.
* Allow the kubelet to pull container images from private registries.

The Kubernetes control plane also uses Secrets; for example, bootstrap token Secrets are a mechanism to help automate node registration.

### Use case: dotfiles in a secret volume

You can make your data "hidden" by defining a key that begins with a dot. This key represents a dotfile or "hidden" file. For example, when the following Secret is mounted into a volume, `secret-volume`, the volume will contain a single file, called `.secret-file`, and the `dotfile-test-container` will have this file present at the path `/etc/secret-volume/.secret-file`.

**Note:** Files beginning with dot characters are hidden from the output of `ls -l`; you must use `ls -la` to see them when listing directory contents.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: dotfile-secret
data:
  .secret-file: dmFsdWUtMg0KDQo=
---
apiVersion: v1
kind: Pod
metadata:
  name: secret-dotfiles-pod
spec:
  volumes:
    - name: secret-volume
      secret:
        secretName: dotfile-secret
  containers:
    - name: dotfile-test-container
      image: registry.k8s.io/busybox
      command:
        - ls
        - "-l"
        - "/etc/secret-volume"
      volumeMounts:
        - name: secret-volume
          readOnly: true
          mountPath: "/etc/secret-volume"
```

### Use case: Secret visible to one container in a Pod

Consider a program that needs to handle HTTP requests, do some complex business logic, and then sign some messages with an HMAC. Because it has complex application logic, there might be an unnoticed remote file reading exploit in the server, which could expose the private key to an attacker.

This could be divided into two processes in two containers: a frontend container which handles user interaction and business logic, but which cannot see the private key; and a signer container that can see the private key, and responds to simple signing requests from the frontend (for example, over localhost networking).

With this partitioned approach, an attacker now has to trick the application server into doing something rather arbitrary, which may be harder than getting it to read a file.

### Alternatives to Secrets

Rather than using a Secret to protect confidential data, you can pick from alternatives:

* If your cloud-native component needs to authenticate to another application that you know is running within the same Kubernetes cluster, you can use a ServiceAccount and its tokens to identify your client.
* There are third-party tools that you can run, either within or outside your cluster, that manage sensitive data.
* For authentication, you can implement a custom signer for X.509 certificates, and use CertificateSigningRequests to let that custom signer issue certificates to Pods that need them.
* You can use a device plugin to expose node-local encryption hardware to a specific Pod.

## Types of Secret

When creating a Secret, you can specify its type using the `type` field of the Secret resource, or certain equivalent `kubectl` command line flags (if available). The Secret type is used to facilitate programmatic handling of the Secret data.

| Built-in Type | Usage |
| --- | --- |
| `Opaque` | arbitrary user-defined data |
| `kubernetes.io/service-account-token` | ServiceAccount token |
| `kubernetes.io/dockercfg` | serialized `~/.dockercfg` file |
| `kubernetes.io/dockerconfigjson` | serialized `~/.docker/config.json` file |
| `kubernetes.io/basic-auth` | credentials for basic authentication |
| `kubernetes.io/ssh-auth` | credentials for SSH authentication |
| `kubernetes.io/tls` | data for a TLS client or server |
| `bootstrap.kubernetes.io/token` | bootstrap token data |

You can define and use your own Secret type by assigning a non-empty string as the `type` value for a Secret object (an empty string is treated as an `Opaque` type).

If you are defining a type of Secret that's for public use, follow the convention and structure the Secret type to have your domain name before the name, separated by a `/`. For example: `cloud-hosting.example.net/cloud-api-credentials`.

### Opaque Secrets

`Opaque` is the default Secret type if you don't explicitly specify a type in a Secret manifest. When you create a Secret using `kubectl`, you must use the `generic` subcommand to indicate an `Opaque` Secret type:

```
kubectl create secret generic empty-secret
kubectl get secret empty-secret
```

The output looks like:

```
NAME           TYPE     DATA   AGE
empty-secret   Opaque   0      2m6s
```

### ServiceAccount token Secrets

A `kubernetes.io/service-account-token` type of Secret is used to store a token credential that identifies a ServiceAccount. This is a legacy mechanism that provides long-lived ServiceAccount credentials to Pods.

In Kubernetes v1.22 and later, the recommended approach is to obtain a short-lived, automatically rotating ServiceAccount token by using the `TokenRequest` API instead.

### Docker config Secrets

You can use one of the following `type` values to create a Secret to store the credentials for accessing a Docker registry:

* `kubernetes.io/dockercfg`
* `kubernetes.io/dockerconfigjson`

The `kubernetes.io/dockercfg` is a reserved type to store a serialized Docker config file. The config file is base64 encoded.

The `kubernetes.io/dockerconfigjson` type is designed for the same purpose, but uses the serialized JSON format of the Docker config file.

### Basic authentication Secret

The `kubernetes.io/basic-auth` type is provided for storing credentials needed for basic authentication. When using this Secret type, the `data` field of the Secret must contain one of the following keys:

* `username`: the user name for authentication
* `password`: the password or token for authentication

### SSH authentication Secrets

The `kubernetes.io/ssh-auth` type is for use with SSH authentication. When using this Secret type, you must specify a `ssh-privatekey` key-value pair in the `data` (or `stringData`) field, where the value is your SSH private key.

### TLS Secrets

Kubernetes provides a built-in Secret type `kubernetes.io/tls` for storing a certificate and its associated key that are typically used for TLS. When using this Secret type, the `tls.crt` and `tls.key` must be provided in the `data` (or `stringData`) field of the Secret configuration.

### Bootstrap token Secrets

A bootstrap token Secret can be created by explicitly specifying the Secret `type` to `bootstrap.kubernetes.io/token`. This type of Secret is designed for node bootstrap token use. It stores tokens used to sign well-known ConfigMaps.

Bootstrap token Secrets are usually created in the `kube-system` namespace and named in the form `bootstrap-token-<token-id>`, where `<token-id>` is a 6 character string used as the token identifier.

## Working with Secrets

### Creating a Secret

There are several options to create a Secret: using `kubectl`, from a configuration file, or using `kustomize`.

```
kubectl create secret generic my-secret --from-literal=key1=supersecret --from-literal=key2=topsecret
```

You can also use a JSON or YAML manifest to define a Secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mysecret
type: Opaque
stringData:
  config.yaml: |
    apiUrl: "https://my.api.com/api/v1"
    username: <username>
    password: <password>
```

### Using a Secret

Secrets can be used in three ways within Pods:

1. As files in a volume mounted on one or more of its containers.
2. As container environment variables.
3. By the kubelet when pulling images for the Pod.

#### Using Secrets as files from a Pod

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
```

In this example, the Secret named `mysecret` is mounted at `/etc/foo`, and each key in the Secret becomes a file in that directory.

#### Using Secrets as environment variables

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: secret-env-pod
spec:
  containers:
  - name: mycontainer
    image: redis
    env:
    - name: SECRET_USERNAME
      valueFrom:
        secretKeyRef:
          name: mysecret
          key: username
    - name: SECRET_PASSWORD
      valueFrom:
        secretKeyRef:
          name: mysecret
          key: password
  restartPolicy: Never
```

#### Container image pull Secrets

If you need to pull a container image from a private registry, you can use an image pull Secret:

```
kubectl create secret docker-registry regcred --docker-server=<your-registry-server> --docker-username=<your-name> --docker-password=<your-pword> --docker-email=<your-email>
```

Then reference the Secret in the Pod spec:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: private-reg-pod
spec:
  containers:
  - name: private-reg-container
    image: <your-private-image>
  imagePullSecrets:
  - name: regcred
```

#### Using Secrets with static Pods

Static Pods do not support the use of Secrets.

## Immutable Secrets

Kubernetes provides the ability to mark specific Secrets (and ConfigMaps) as immutable. Preventing changes to the data of a Secret stops accidental (or unwanted) updates as well as any internal changes that would allow complete cluster takeover. Once a Secret is marked as immutable, there is no way to revert this change or to modify the contents of the `data` field. You can only delete and recreate the Secret.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: my-secret
type: Opaque
immutable: true
data:
  key1: <base64-encoded-value>
```

## Information security for Secrets

### Configure least-privilege access to Secrets

Least-privilege access means that you limit the permissions of an identity to the minimum set of permissions that is required to perform the desired action. Applying the principle of least privilege to access Secrets involves:

* Granting minimal RBAC permissions to Secrets.
* Restricting Secret access to specific containers.
* Encrypting Secret data at rest in etcd.
