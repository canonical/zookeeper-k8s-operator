<!-- Copyright 2022 Canonical Ltd.
See LICENSE file for licensing details. -->

# ZooKeeper K8s Operator

[![code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black/tree/main)
[![Run-Tests](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/ci.yaml)

[![ZooKeeper K8s](https://charmhub.io/zookeeper-k8s/badge.svg)](https://charmhub.io/zookeeper-k8s)

## Description

Apache [ZooKeeper](https://zookeeper.apache.org) is an effort to develop and maintain an open-source server which enables highly reliable distributed coordination.

This repository contains a Charm Operator for deploying the ZooKeeper in a Kubernetes cluster.

<!-- ## Tutorials
-  -->

## How-to guides

### Deploy ZooKeeper

Deploy the ZooKeeper K8s Operator using the Juju command line:

```shell
$ juju add-model zookeeper-k8s
$ juju deploy zookeeper-k8s
```

### Scale ZooKeeper

Scale ZooKeeper by executing the following command

```shell
$ juju scale-application zookeeper-k8s 3
```

### How to integrate with ZooKeeper

If you are developing a charm that needs to integrate with ZooKeeper, please follow [the instructions](https://charmhub.io/zookeeper-k8s/libraries/zookeeper) to do so.

## Reference

- [ZooKeeper 3.6.3 documentation](https://zookeeper.apache.org/doc/r3.6.3/index.html)
- [OCI image](https://hub.docker.com/r/confluentinc/cp-zookeeper): currently using tag `7.0.1`.

## Explanation

- [What is ZooKeeper?](https://zookeeper.apache.org/doc/r3.6.3/zookeeperOver.html)

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
`CONTRIBUTING.md` for developer guidance.
