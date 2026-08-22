---
title: "Pull an Image from a Private Registry"
source_url: "https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/"
---

# Pull an Image from a Private Registry

This page shows how to create a Pod that uses a Secret to pull an image from a private container image registry or repository. There are many private registries in use. This task uses Docker Hub as an example registry.

## Log in to Docker Hub

On your laptop, you must authenticate with a registry in order to pull a private image.

```
docker login
```

When prompted, enter your Docker ID, and then the credential you want to use (access token, or the password for your Docker ID).

The login process creates or updates a `config.json` file that holds an authorization token.

View the `config.json` file:

```
cat ~/.docker/config.json
```

The output contains a section similar to this:

```
{
    "auths": {
        "https://index.docker.io/v1/": {
            "auth": "c3R...zE2"
        }
    }
}
```

**Note:** If you use a Docker credentials store, you won't see that `auth` entry but a `credsStore` entry with the name of the store as value. In that case, you can create a secret directly.

## Create a Secret based on existing credentials

A Kubernetes cluster uses the Secret of `kubernetes.io/dockerconfigjson` type to authenticate with a container registry to pull a private image.

If you already ran `docker login`, you can copy that credential into Kubernetes:

```
kubectl create secret generic regcred \
    --from-file=.dockerconfigjson=<path/to/.docker/config.json> \
    --type=kubernetes.io/dockerconfigjson
```

If you need more control (for example, to set a namespace or a label on the new secret) then you can customise the Secret before storing it. Be sure to:

* set the name of the data item to `.dockerconfigjson`
* base64 encode the Docker configuration file and then paste that string, unbroken as the value for field `data[".dockerconfigjson"]`
* set `type` to `kubernetes.io/dockerconfigjson`

If you get the error message `error: no objects passed to create`, it may mean the base64 encoded string is invalid. If you get an error message like `Secret "myregistrykey" is invalid: data[.dockerconfigjson]: invalid value ...`, it means the base64 encoded string in the data was successfully decoded, but could not be parsed as a `.docker/config.json` file.

## Create a Secret by providing credentials on the command line

Create this Secret, naming it `regcred`:

```
kubectl create secret docker-registry regcred --docker-server=<your-registry-server> --docker-username=<your-name> --docker-password=<your-pword> --docker-email=<your-email>
```

where:

* `<your-registry-server>` is your Private Docker Registry FQDN. Use `https://index.docker.io/v1/` for DockerHub.
* `<your-name>` is your Docker username.
* `<your-pword>` is your Docker password.
* `<your-email>` is your Docker email.

**Note:** Typing secrets on the command line may store them in your shell history unprotected, and those secrets might also be visible to other users on your PC during the time that `kubectl` is running.

## Inspecting the Secret `regcred`

To understand the contents of the `regcred` Secret you created, start by viewing the Secret in YAML format:

```
kubectl get secret regcred --output=yaml
```

The value of the `.dockerconfigjson` field is a base64 representation of your Docker credentials.

To understand what is in the `.dockerconfigjson` field, convert the secret data to a readable format:

```
kubectl get secret regcred --output="jsonpath={.data.\.dockerconfigjson}" | base64 --decode
```

The output is similar to this:

```
{"auths":{"your.private.registry.example.com":{"username":"janedoe","password":"xxxxxxxxxxx","email":"jdoe@example.com","auth":"c3R...zE2"}}}
```

## Create a Pod that uses your Secret

Here is a manifest for an example Pod that needs access to your Docker credentials in `regcred`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: private-reg
spec:
  containers:
  - name: private-reg-container
    image: <your-private-image>
  imagePullSecrets:
  - name: regcred
```

In file `my-private-reg-pod.yaml`, replace `<your-private-image>` with the path to an image in a private registry such as:

```
your.private.registry.example.com/janedoe/jdoe-private:v1
```

To pull the image from the private registry, Kubernetes needs credentials. The `imagePullSecrets` field in the configuration file specifies that Kubernetes should get the credentials from a Secret named `regcred`.

Create a Pod that uses your Secret, and verify that the Pod is running:

```
kubectl apply -f my-private-reg-pod.yaml
kubectl get pod private-reg
```

**Note:** To use image pull secrets for a Pod (or a Deployment, or other object that has a pod template that you are using), you need to make sure that the appropriate Secret does exist in the right namespace. The namespace to use is the same namespace where you defined the Pod.

Also, in case the Pod fails to start with the status `ImagePullBackOff`, view the Pod events:

```
kubectl describe pod private-reg
```

If you then see an event with the reason set to `FailedToRetrieveImagePullSecret`, Kubernetes can't find a Secret with name (`regcred`, in this example).

Make sure that the Secret you have specified exists, and that its name is spelled properly.

```
Events:
  ...  Reason                           ...  Message
       ------                                -------
  ...  FailedToRetrieveImagePullSecret  ...  Unable to retrieve some image pull secrets (<regcred>); attempting to pull the image may not succeed.
```

## Using images from multiple registries

A pod can have multiple containers, each container image can be from a different registry. You can use multiple `imagePullSecrets` with one pod, and each can contain multiple credentials.

The image pull will be attempted using each credential that matches the registry. If no credentials match the registry, the image pull will be attempted without authorization or using custom runtime specific configuration.
