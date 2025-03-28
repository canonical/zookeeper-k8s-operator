# Contributing

## Overview

This documents explains the processes and practices recommended for contributing enhancements to this operator.

- Generally, before developing enhancements to this charm, you should consider [opening an issue](https://github.com/canonical/zookeeper-operator/issues) explaining your problem with examples, and your desired use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach us at [Canonical Mattermost public channel](https://chat.charmhub.io/charmhub/channels/charm-dev) or [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Requirements

To build the charm locally, you will need to install [Charmcraft](https://juju.is/docs/sdk/install-charmcraft).

To run the charm locally with Juju, it is recommended to use [LXD](https://linuxcontainers.org/lxd/introduction/) as your virtual machine manager. Instructions for running Juju on LXD can be found [here](https://juju.is/docs/olm/lxd).

## Developing

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

### Testing

```shell
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'lint' and 'unit' environments
```

## Build and Deploy

To download, build and deploy the charm you can run:

```bash
# Clone and enter the repository
git clone https://github.com/canonical/zookeeper-k8s-operator.git
cd zookeeper-k8s-operator/

# Create a working model
juju add-model zookeeper-k8s

# Enable DEBUG logging for the model
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Build the charm locally
charmcraft pack

# Deploy the charm
juju deploy ./*.charm -n 3 --trust
```

If you already have a `zookeeper-k8s` Juju model in place and would like to deploy a fresh build of the charm, as well as a toy client for testing, you can run:

```bash
tox -e refresh
```
