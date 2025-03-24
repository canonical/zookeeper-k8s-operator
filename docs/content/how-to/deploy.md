# How to deploy Apache ZooKeeper K8s charmed operator

```{note}
For machine cloud environment (e.g., EC2), see the [Apache ZooKeeper charmed operator](https://canonical-zookeeper.readthedocs-hosted.com/) documentation instead.
```
<!-- TODO add a link to the K8s charm. -->

Ensure you have a Juju environment set up and running, including:

* Juju cloud
* Juju controller
* Juju client

For guidance on how to set up Juju environment, see [Juju tutorial](https://canonical-juju.readthedocs-hosted.com/en/latest/user/tutorial/) or a shorter [Give it a try](https://github.com/juju/juju?tab=readme-ov-file#give-it-a-try) section of the README file.

## Set up a model

To deploy a charm you need to use a [Juju model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/).
Create a new or use an existing Juju model on a Juju controller.

### Create a new model

To add a new Juju model, run:

```
juju add-model <model>
```

### Use an existing model

To see all available models, run:

```
juju models
```

Then, select one of the models to use:

```
juju switch <model-name>
```

## Deploy the charm

Deploy the Apache ZooKeeper K8s charmed operator:

```
juju deploy zookeeper-k8s -n <units>
```

Where <units> is the number of units to deploy (recommended values are `3` or `5`).

```{note}
See also: [High availability](../explanation/ha.md) and quorum explanation.
```

Check the [status](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/status/) of the deployment:

```
juju status
```

The deployment should be complete once all the units show `active` or `idle` status.

## Configure the charm

View existing configuration options:  

````  
juju config zookeeper  
```` 

Change or add configuration options:

````  
juju config zookeeper <key>=<value>  
````  

```{note}
See also: `juju config` command [reference](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/config/).
```

## Integrate with other charms

To utilise Apache ZooKeeper K8s charmed operator, use the `juju integrate` command (see [reference](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/integrate/)) to relate/integrate it with other charms. For example, to deploy Apache Kafka K8s charmed operator and integrate with it:

```
juju deploy kafka-k8s --channel 3/stable -n <kafka-units> --trust
juju integrate kafka zookeeper
```

Make sure to wait until the status of all units becomes `active` and `idle`.
