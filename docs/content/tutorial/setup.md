# Setup the environment

For this tutorial, we’ll set up a Multipass VM with two key components:

* MicroK8s that is a lightweight Kubernetes
* Juju that will help us to deploy and manage Apache ZooKeeper and related applications

## Multipass

Multipass is a quick and easy way to launch virtual machines running Ubuntu.
It uses the cloud-init standard to install and configure all the necessary parts automatically.

Let’s install Multipass from a snap and launch a new VM using the `charm-dev` cloud-init configuration:

```bash
sudo snap install multipass && \
multipass launch --cpus 4 --memory 8G --disk 50G --name zk-vm charm-dev
```

```{note}
See also: `multipass launch` command [reference](https://multipass.run/docs/launch-command).
```

Wait for the VM to start and open its shell:

```bash
multipass shell zk-vm
```

For the rest of the tutorial we will work inside this virtual environment.

## MicroK8s

MicroK8s is a low-ops, minimal production Kubernetes.
It provides the functionality of core Kubernetes components, in a small footprint, scalable from a single node to a high-availability production cluster.

Verify that MicroK8s is installed already:

```bash
microk8s status --wait-ready
```

This should produce an output similar to the following:

```text
microk8s is running
high-availability: no
  datastore master nodes: 127.0.0.1:19001
  datastore standby nodes: none
addons:
  enabled:
    dns                  # (core) CoreDNS
...
```

## Juju

[Juju](https://juju.is/) is an orchestration engine for clouds, bare metal, LXD or Kubernetes.
We will be using it to deploy and manage Apache ZooKeeper K8s charm.
We need to install it locally to be able to use CLI commands.

As with MicroK8s, Juju should be installed already, check:

```bash
juju clouds
```

Juju already has built-in knowledge of MicroK8s and how it works, so there is no additional setup or configuration needed.
A controller will be used to deploy and control Apache ZooKeeper K8s charm.
Make sure that you use the controller bound to the MicroK8s cluster:

```bash
juju switch microk8s
```

The controller can work with different models; models group applications such as Apache ZooKeeper K8s charm.
Set up a specific model for this tutorial named `tutorial`:

```shell
juju add-model tutorial
```

You can now view the model you created above by entering the command `juju status` into the command line. You should see the following:

```text
Model    Controller  Cloud/Region         Version  SLA          Timestamp
tutorial microk8s    microK8s/localhost   3.6.4    unsupported  23:20:53Z

Model "admin/tutorial" is empty.
```

## Next step

After finishing the setup, continue to the [deploy](deploy) page of the tutorial.
