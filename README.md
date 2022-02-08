<!-- Copyright 2022 Canonical Ltd.
See LICENSE file for licensing details. -->

# Zookeeper K8s Operator

[![code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black/tree/main)
[![Run-Tests](https://github.com/charmed-osm/zookeeper-k8s-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/charmed-osm/zookeeper-k8s-operator/actions/workflows/ci.yaml)


[![Zookeeper K8s](https://charmhub.io/zookeeper-k8s/badge.svg)](https://charmhub.io/zookeeper-k8s)

## Description

TODO: Describe your charm in a few paragraphs of Markdown


## Usage

The Zookeeper K8s Operator may be deployed using the Juju command line as in

```shell
$ juju add-model zookeeper-k8s
$ juju deploy zookeeper-k8s
```

### Scale

```shell
$ juju scale-application zookeeper-k8s 3
```

## OCI Images

- [zookeeper-k8s](https://hub.docker.com/layers/confluentinc/cp-zookeeper/7.0.1/images)

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
`CONTRIBUTING.md` for developer guidance.
