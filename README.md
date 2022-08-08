## ZooKeeper K8s Operator - a Charmed Operator for running ZooKeeper on Kubernetes from Canonical

This repository hosts the Machine Python Operator for [Apache ZooKeeper](https://zookeeper.apache.org/index.html).
The ZooKeeper K8s Operator is a Python script that uses the latest upstream ZooKeeper binaries released by the The Apache Software Foundation that comes with [Apache Kafka](https://github.com/apache/kafka), made available using the [Kafka Snap](https://snapcraft.io/kafka) distributed by Canonical.

### Usage

The ZooKeeper K8s Operator may be deployed using the Juju command line as follows:

```bash
$ juju deploy zookeeper-k8s -n 3
```

## A scalable, secure distributed coordinator for Apache Kafka, Apache Hadoop and more!

Manual, Day 2 operations for deploying and operating Apache ZooKeeper, scaling-up/retiring servers, updating users and distributing ACL permissions are handled automatically using the [Juju Operator Lifecycle Manager](https://juju.is/docs/olm).

### Key Features
- Horizontal scaling for high-availability out-of-the-box
- Server-Server and Client-Server authentication both enabled by default
- Access control management supported with user-provided ACL lists.

## Contributing

This charm is still in active development. If you would like to contribute, please refer to [CONTRIBUTING.md](https://github.com/canonical/zookeeper-k8s-operator/blob/main/CONTRIBUTING.md)
