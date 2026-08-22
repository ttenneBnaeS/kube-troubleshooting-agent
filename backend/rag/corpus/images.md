---
title: "Images"
source_url: "https://kubernetes.io/docs/concepts/containers/images/"
---

# Images

A container image represents binary data that encapsulates an application and all its software dependencies. Container images are executable software bundles that can run standalone and that make very well-defined assumptions about their runtime environment.

You typically create a container image of your application and push it to a registry before referring to it in a Pod.

## Image names

Container images are usually given a name such as `pause`, `example/mycontainer`, or `kube-apiserver`. Images can also include a registry hostname; for example: `fictional.registry.example/imagename`, and possibly a port number as well; for example: `fictional.registry.example:10443/imagename`.

If you don't specify a registry hostname, Kubernetes assumes that you mean the Docker public registry. You can change this behavior by setting a default image registry in the container runtime configuration.

After the image name part you can add a _tag_ or _digest_ (in the same way you would when using with commands like `docker` or `podman`). Tags let you identify different versions of the same series of images. Digests are a unique identifier for a specific version of an image. Digests are hashes of the image's content, and are immutable. Tags can be moved to point to different images, but digests are fixed.

Image tags consist of lowercase and uppercase letters, digits, underscores (`_`), periods (`.`), and dashes (`-`). A tag can be up to 128 characters long, and must conform to the regex pattern `[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}`. If you don't specify a tag, Kubernetes assumes you mean the tag `latest`.

Image digests consist of a hash algorithm (such as `sha256`) and a hash value. For example: `sha256:1ff6c18fbef2045af6b9c16bf034cc421a29027b800e4f9b68ae9b1cb3e9ae07`.

Some image name examples that Kubernetes can use are:

* `busybox` — Image name only, no tag or digest. Kubernetes will use the Docker public registry and latest tag. Equivalent to `docker.io/library/busybox:latest`.
* `busybox:1.32.0` — Image name with tag. Equivalent to `docker.io/library/busybox:1.32.0`.
* `registry.k8s.io/pause:latest` — Image name with a custom registry and latest tag.
* `registry.k8s.io/pause:3.5` — Image name with a custom registry and non-latest tag.
* `registry.k8s.io/pause@sha256:1ff6c18fbef2045af6b9c16bf034cc421a29027b800e4f9b68ae9b1cb3e9ae07` — Image name with digest.
* `registry.k8s.io/pause:3.5@sha256:1ff6c18fbef2045af6b9c16bf034cc421a29027b800e4f9b68ae9b1cb3e9ae07` — Image name with tag and digest. Only the digest will be used for pulling.

## Updating images

When you first create a Deployment, StatefulSet, Pod, or other object that includes a PodTemplate, and a pull policy was not explicitly specified, then by default the pull policy of all containers in that Pod will be set to `IfNotPresent`. This policy causes the kubelet to skip pulling an image if it already exists.

### Image pull policy

The `imagePullPolicy` for a container and the tag of the image both affect _when_ the kubelet attempts to pull (download) the specified image.

Here's a list of the values you can set for `imagePullPolicy` and the effects these values have:

**`IfNotPresent`**: the image is pulled only if it is not already present locally.

**`Always`**: every time the kubelet launches a container, the kubelet requests the container runtime to pull the image. The container runtime contacts the registry, resolves the image tag or name to a digest, and downloads any layers that are not already cached locally. If all layers are already present, the container runtime uses the cached image without downloading it again.

**`Never`**: the kubelet does not try fetching the image. If the image is somehow already present locally, the kubelet attempts to start the container; otherwise, startup fails.

**Note:** You should avoid using the `:latest` tag when deploying containers in production as it is harder to track which version of the image is running and more difficult to roll back properly. Instead, specify a meaningful tag such as `v1.42.0` and/or a digest. To make sure the Pod always uses the same version of a container image, you can specify the image's digest; replace `<image-name>:<tag>` with `<image-name>@<digest>`.

#### Default image pull policy

When you (or a controller) submit a new Pod to the API server, your cluster sets the `imagePullPolicy` field when specific conditions are met:

* if you omit the `imagePullPolicy` field, and you specify the digest for the container image, the `imagePullPolicy` is automatically set to `IfNotPresent`.
* if you omit the `imagePullPolicy` field, and the tag for the container image is `:latest`, `imagePullPolicy` is automatically set to `Always`.
* if you omit the `imagePullPolicy` field, and you don't specify the tag for the container image, `imagePullPolicy` is automatically set to `Always`.
* if you omit the `imagePullPolicy` field, and you specify a tag for the container image that isn't `:latest`, the `imagePullPolicy` is automatically set to `IfNotPresent`.

**Note:** The value of `imagePullPolicy` of the container is always set when the object is first _created_, and is not updated if the image's tag or digest later changes. You must manually change the pull policy of any object after its initial creation.

#### Required image pull

If you would like to always force a pull, you can do one of the following:

* Set the `imagePullPolicy` of the container to `Always`.
* Omit the `imagePullPolicy` and use `:latest` as the tag for the image to use; Kubernetes will set the policy to `Always` when you submit the Pod.
* Omit the `imagePullPolicy` and the tag for the image to use; Kubernetes will set the policy to `Always` when you submit the Pod.
* Enable the AlwaysPullImages admission controller.

### ImagePullBackOff

When a kubelet starts creating containers for a Pod using a container runtime, it might be possible the container is in Waiting state because of `ImagePullBackOff`.

The status `ImagePullBackOff` means that a container could not start because Kubernetes could not pull a container image (for reasons such as invalid image name, or pulling from a private registry without `imagePullSecret`). The `BackOff` part indicates that Kubernetes will keep trying to pull the image, with an increasing back-off delay.

Kubernetes raises the delay between each attempt until it reaches a compiled-in limit, which is 300 seconds (5 minutes).

## Serial and parallel image pulls

By default, the kubelet pulls images serially. In other words, the kubelet sends only one image pull request to the container runtime at a time, and waits for that request to complete before sending the next one. This avoids overloading the node or the network.

### Maximum parallel image pulls

`Kubernetes v1.27 [beta]`

When using the kubelet command-line option `--serialize-image-pulls` set to false (default is true), the kubelet will pull multiple images in parallel.

```
kubelet --serialize-image-pulls=false
```

Or in the kubelet configuration file:
```yaml
serializeImagePulls: false
```

By default, Kubernetes does not limit the number of parallel image pulls. You can configure the maximum number of parallel image pulls using the `maxParallelImagePulls` kubelet setting:

```
kubelet --serialize-image-pulls=false --max-parallel-image-pulls 5
```

## Multi-architecture images with image indexes

Container registries can host multiple versions of an image (each optimized for different processor architectures). This allows a single image reference to work across different machine types. Docker calls these image indexes, and Kubernetes can work with any image index implementation that conforms to the OCI Image Index specification.

When you reference an image by tag (for example, `busybox:1.32.0`), the container runtime will check if a manifest exists for your node's architecture, and if so, the container runtime performs the image pull and execution of that image version.

If a multi-architecture image index is not available for the specified tag on the container registry, the kubelet falls back to pulling the first manifest listed in the image index. Most multi-architecture images on Docker Hub and other registries follow the convention of listing the most compatible image (often the amd64 version) first.

## Using a private registry

Private registries may require keys to read images from them. Credentials can be provided in several ways:

* Configuring nodes to authenticate to a private registry
* Using ImagePullSecrets
* Kubelet credential provider
* Vendor-specific or cloud-provider extensions

### Specifying `imagePullSecrets` on a Pod

Kubernetes supports specifying container image registry credentials on a Pod. The `imagePullSecrets` field is a list of references to Secrets in the same namespace. You can use an `imagePullSecrets` field to pass a secret that contains a Docker (or other container) image registry password.

You need to create a Secret with type `kubernetes.io/dockercfg` or `kubernetes.io/dockerconfigjson`:

```bash
kubectl create secret docker-registry myregistrykey --docker-server=DOCKER_REGISTRY_SERVER --docker-username=DOCKER_USER --docker-password=DOCKER_PASSWORD --docker-email=DOCKER_EMAIL
```

If you already have a Docker credentials file, you can import that instead:

```bash
kubectl create secret docker-registry myregistrykey \
  --from-file=.dockerconfigjson=<path/to/.docker/config.json> \
  --type=kubernetes.io/dockerconfigjson
```

This is particularly useful when you are pulling images from multiple private container registries, as `kubectl create secret docker-registry` creates a Secret that only works with a single private registry.

Now you can create Pods which reference that secret by adding an `imagePullSecrets` section to a Pod definition:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: foo
  namespace: awesomens
spec:
  containers:
    - name: foo
      image: janedoe/awesomeapp:v1
  imagePullSecrets:
    - name: myregistrykey
```

Each Pod needs to have the `imagePullSecrets` value set for each private registry used.

### Configuring nodes to authenticate to a private registry

All Pods can read any images configured in the node's container runtime configuration, regardless of the Pod's `imagePullPolicy` setting. For improved security, and if supported by your container runtime, you can configure per-registry credentials on the node.

The kubelet uses the container runtime to pull images. When using containerd or CRI-O, the kubelet delegates image pulling to the container runtime.

If you are using `containerd` as the container runtime, you can configure it to authenticate to private registries by editing the `/etc/containerd/config.toml` configuration file. For example:

```toml
[plugins."io.containerd.grpc.v1.cri".registry.configs."docker.io".auth]
username = "my_username"
password = "my_password"
```

### Kubelet credential provider for authenticated image pulls

`Kubernetes v1.26 [stable]`

The kubelet can dynamically fetch credentials for private image registries using plugins. This is useful when image registries rotate credentials frequently, or when you want to avoid storing credentials in the kubelet's configuration files.

To use this feature: write a credential provider executable (or use a pre-built one), place it on the node at `/var/lib/kubelet/credential-plugins/`, and configure the kubelet to use the plugin via the `--image-credential-provider-config` and `--image-credential-provider-bin-dir` flags.

### Pre-pulled images

By default, the kubelet tries to pull each image from the specified registry. However, if the `imagePullPolicy` property of the container is set to `IfNotPresent` or `Never`, the local image is used, preferentially or exclusively.

If you would like to rely on pre-pulled images as a replacement for registry authentication, you must ensure all nodes in the cluster have the pre-pulled images cached. This can be useful to load particular images faster, or as an alternative to authenticating to a private registry.

### Use cases

There are many solutions for configuring private registries. Some common use cases and suggested approaches:

1. **Cluster running proprietary software:** Use Kubelet credential provider or pre-pulled images.
2. **Company with a private registry that shares images across teams:** Use `imagePullSecrets` with Secrets stored in each namespace.
3. **Multiple registries with different credentials:** Use node-level Docker/containerd configuration or Kubelet credential provider.
4. **Migrating from Docker to another container runtime:** Use containerd or CRI-O configuration for registry authentication.
