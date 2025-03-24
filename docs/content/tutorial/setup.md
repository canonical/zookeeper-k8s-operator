# Setup the environment

For this tutorial, we will need to set up the environment with two main components:

* LXD that is a simple and lightweight virtual machine provisioner
* Juju that will help us to deploy and manage Apache ZooKeeper and related applications

## MicroK8s

The fastest, simplest way to get started with Charmed Apache ZooKeeper K8s is to set up a local [microk8s](https://microk8s.io/) cloud. MicroK8s is the easiest and fastest way to get Kubernetes up and running. Apache ZooKeeper will be run on MicroK8s and managed by Juju. While this tutorial covers the basics of MicroK8s, you can [explore more about LXD here](https://linuxcontainers.org/lxd/getting-started-cli/). 

[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses “[cloud-init](https://cloud-init.io/)” standard to install and configure all the necessary parts automatically.

Let’s install Multipass from [Snap](https://snapcraft.io/multipass) and launch a new VM using “[charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml)” cloud-init config:

```bash
sudo snap install multipass && \
multipass launch --cpus 4 --memory 8G --disk 50G --name my-vm charm-dev
```

```{note}
See also: [launch command reference](https://multipass.run/docs/launch-command).
```

As soon as the new VM starts, enter:

```bash
multipass shell my-vm
```

Verify that MicroK8s is installed:

```bash
microk8s status --wait-ready
```

## Juju

[Juju](https://juju.is/) is an orchestration engine for clouds, bare metal, LXD or Kubernetes. We will be using it to deploy and manage Apache ZooKeeper K8s charmed operator. We need to install it locally to be able to use CLI commands. As with MicroK8s, Juju is installed from a snap package:

```bash
sudo snap install juju
```

Juju already has built-in knowledge of MicroK8s and how it works, so there is no additional setup or configuration needed. A controller will be used to deploy and control Charmed Apache ZooKeeper K8s. Make sure that you use the controller bound to the MicroK8s cluster:

```bash
juju switch microk8s
```

The controller can work with different models; models group applications such as Charmed Apache ZooKeeper K8s. Set up a specific model for this tutorial named `tutorial`:

```shell
juju add-model tutorial
```

You can now view the model you created above by entering the command `juju status` into the command line. You should see the following:

```
Model    Controller  Cloud/Region         Version  SLA          Timestamp
tutorial overlord    microK8s/localhost  3.1.6    unsupported  23:20:53Z

Model "admin/tutorial" is empty.
```

## Next step

After finishing the setup, continue to the [deploy](deploy) page of the tutorial.
