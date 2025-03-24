# Apache ZooKeeper K8s charmed operator

[![CharmHub Badge](https://charmhub.io/zookeeper-k8s/badge.svg)](https://charmhub.io/zookeeper-k8s)
[![Release](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/canonical/zookeeper-k8s-operator/actions/workflows/ci.yaml?query=branch%3Amain)

## Overview

The Apache ZooKeeper K8s charmed operator delivers automated operations management from day 0 to day 2 on the [Apache ZooKeeper](https://zookeeper.apache.org/) server which enables highly reliable distributed coordination, deployed on top of a [Kubernetes cluster](https://kubernetes.io/). It is an open source, end-to-end, production ready data platform on top of cloud native technologies.

The Apache ZooKeeper K8s charmed operator can be found on [Charmhub](https://charmhub.io/zookeeper-k8s) and it comes with features such as:
- Horizontal scaling for high-availability out-of-the-box
- Server-Server and Client-Server authentication both enabled by default
- Access control management supported with user-provided ACL lists.

Apache ZooKeeper is a free, open source software project by the Apache Software Foundation. Users can find out more at the [ZooKeeper project page](https://zookeeper.apache.org/).

## Requirements

For production environments, it is recommended to deploy at least 5 nodes for Apache Zookeeper.
While the following requirements are meant to be for production, the charm can be deployed in smaller environments.

- 4-8GB of RAM
- 2-4 cores
- 1 storage device, 64GB

## Config options

To get a description of all config options available, please refer to the [`config.yaml`](https://github.com/canonical/zookeeper-k8s-operator/blob/main/config.yaml) file.

Options can be changed by using the `juju config` command:

```shell
juju config zookeeper-k8s <config_option_1>=<value> [<config_option_2>=<value>]
```

## Usage

The Apache ZooKeeper charmed operator may be deployed using the Juju command line as follows:

```bash
$ juju deploy zookeeper-k8s -n 5
```

To watch the process, `juju status` can be used. Once all the units show as `active|idle` the credentials to access the admin user can be queried with:

```shell
juju run-action zookeeper-k8s/leader get-super-password --wait 
```

#### Scaling application

The charm can be scaled using `juju scale-application` command.

```shell
juju scale-application zookeeper-k8s <num_of_servers_to_scale_to>
```

This will add or remove servers to match the required number. To scale a deployment with 3 Apache ZooKeeper units to 5, run:

```shell
juju scale-application zookeeper-k8s 5
```

### Password rotation

The Charmed Apache ZooKeeper K8s Operator has two internal users:

- `super`: admin user for the cluster. Used mainly with the Kafka operator.
- `sync`: specific to the internal quorum handling. 

The `set-password` action can be used to rotate the password of one of them. If no username is passed, it will default to the `super` user.
```shell
# to set a specific password for the sync user
juju run-action zookeeper-k8s/leader set-password username=sync password=<password> --wait

# to randomly generate a password for the super user
juju run-action zookeeper-k8s/leader set-password --wait
```

## Relations

Supported [relations](https://juju.is/docs/olm/relations):

#### The `tls-certificates` interface

The `tls-certificates` interface is used with the `tls-certificates-operator` charm.

To enable TLS:

```shell
# deploy the TLS charm 
juju deploy tls-certificates-operator --channel=edge
# add the necessary configurations for TLS
juju config tls-certificates-operator generate-self-signed-certificates="true" ca-common-name="Test CA" 
# to enable TLS relate the application 
juju relate tls-certificates-operator zookeeper-k8s
```

Updates to private keys for certificate signing requests (CSR) can be made via the `set-tls-private-key` action.

```shell
# Updates can be done with auto-generated keys with
juju run-action zookeeper-k8s/0 set-tls-private-key --wait
juju run-action zookeeper-k8s/1 set-tls-private-key --wait
juju run-action zookeeper-k8s/2 set-tls-private-key --wait
```

Passing keys to internal keys should *only be done with* `base64 -w0` *not* `cat`. With three servers this schema should be followed:

```shell
# generate shared internal key
openssl genrsa -out internal-key.pem 3072
# apply keys on each unit
juju run-action zookeeper-k8s/0 set-tls-private-key "internal-key=$(base64 -w0 internal-key.pem)"  --wait
juju run-action zookeeper-k8s/1 set-tls-private-key "internal-key=$(base64 -w0 internal-key.pem)"  --wait
juju run-action zookeeper-k8s/2 set-tls-private-key "internal-key=$(base64 -w0 internal-key.pem)"  --wait
```

To disable TLS remove the relation

```shell
juju remove-relation zookeeper-k8s tls-certificates-operator
```

Note: The TLS settings here are for self-signed-certificates which are not recommended for production clusters, the `tls-certificates-operator` charm offers a variety of configurations, read more on the TLS charm [here](https://charmhub.io/tls-certificates-operator)

## Monitoring

The Charmed Apache ZooKeeper K8s Operator comes with several exporters by default. The metrics can be queried by accessing the following endpoints:

- JMX exporter: `http://<pod-ip>:9998/metrics`
- Apache ZooKeeper metrics: `http://<pod-ip>:7000/metrics`

Additionally, the charm provides integration with the [Canonical Observability Stack](https://charmhub.io/topics/canonical-observability-stack).

Deploy `cos-lite` bundle in a Kubernetes environment. This can be done by following the [deployment tutorial](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s). It is needed to offer the endpoints of the COS relations. The [offers-overlay](https://github.com/canonical/cos-lite-bundle/blob/main/overlays/offers-overlay.yaml) can be used, and this step is shown on the COS tutorial.

Once COS is deployed, we can find the offers from the Apache ZooKeeper model:

```shell
# We are on the `cos` model. Switch to `zookeeper` model
juju switch <zookeeper_model_name>
juju find-offers <k8s_controller_name>:
```

A similar output should appear, if `micro` is the k8s controller name and `cos` the model where `cos-lite` has been deployed:

```
Store  URL                   Access  Interfaces                         
micro  admin/cos.grafana     admin   grafana_dashboard:grafana-dashboard
micro  admin/cos.prometheus  admin   prometheus_scrape:metrics-endpoint
. . .
```

Now, integrate zookeeper with the `metrics-endpoint`, `grafana-dashboard` and `logging` relations:

```shell
juju relate micro:admin/cos.prometheus zookeeper-k8s
juju relate micro:admin/cos.grafana zookeeper-k8s
juju relate micro:admin/cos.loki zookeeper-k8s
```

After this is complete, Grafana will show a new dashboard: `ZooKeeper Metrics`

## Security

Security issues in the Charmed Apache ZooKeeper K8s Operator can be reported through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). Please do not file GitHub issues about security issues.


## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/zookeeper-k8s-operator/blob/main/CONTRIBUTING.md) for developer guidance.


## License

The Charmed Apache ZooKeeper K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](https://github.com/canonical/zookeeper-k8s-operator/blob/main/LICENSE) for more information.
