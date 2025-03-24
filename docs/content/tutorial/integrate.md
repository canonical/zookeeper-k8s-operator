# Integrate

This is the part of the Apache ZooKeeper K8s charmed operator tutorial about using Apache ZooKeeper with other charms. Make sure to complete instruction from the [Deploy](deploy) page before reading further.

The main way to use the Apache ZooKeeper K8s charmed operator is to integrate it with another charm via [Juju relations](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/relation/). For that reason we will deploy Apache Kafka K8s charmed operator and integrate them via the [zookeeper interface](https://charmhub.io/integrations/zookeeper/).

## Deploy Apache Kafka

Deploy Apache Kafka charm to the same model:

```
juju deploy kafka-k8s --channel 3/stable -n 3 --trust
```

Make sure Apache Kafka cluster is deployed by checking its status:

```
juju status --watch 1s
```

Wait until all units have `active` and `idle` status.

## Integrate

Integrate Apache ZooKeeper and Apache Kafka applications for them to be able to work together:

```
juju integrate kafka zookeeper
```

Juju finds a common interface to integrate the charms together. In this case, it's the `zookeeper` interface. See the interface's [documentation](https://charmhub.io/integrations/zookeeper/) for more information.

Check the Juju applications status, including the information on existing relations:

```
juju status --relations --watch 1s
```

<!-- Add something here to show the ZooKeeper's role and/or process. -->

## Next step

Continue to the [Cleanup](cleanup) page of the tutorial to finish this Tutorial and free the resources.
