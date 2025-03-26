# High availability

Apache ZooKeeper is replicated over a set of hosts called an ensemble.

Ensemble
: the full set of peer servers in a Apache ZooKeeper cluster

Quorum
: the minimum number of available servers required for a cluster to serve requests. By default, it is a simple majority (greater than half) of all servers in the ensemble.

If an Apache ZooKeeper cluster looses quorum (has less than or equal to half of available servers), then it can't elect a new leader and effectively stops serving requests as it can't guarantee a consistent view of the system.

For Apache ZooKeeper implementation details, see the [official documentation](https://zookeeper.apache.org/doc/r3.9.2/zookeeperOver.html).

## Number of servers

For Apache ZooKeeper K8s charmed operator the number of servers in a cluster can be easily adjusted by [adding](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-units/#add-a-unit) or [removing](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-units/#remove-a-unit) Juju [units](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/unit/) from the application.

```{important}
Always use an odd number of servers in an Apache ZooKeeper cluster for optimal performance and high availability (HA).
```

Adding a unit to create an even number does not improve fault tolerance due to quorum rules. For example, both five- and six-node clusters can tolerate only two failures.

A minimum of three servers/units are required in a cluster for HA. A cluster with just two servers is inherently less stable than a single server, because there are two single points of failure. If one of them fails, there are not enough machines to form a majority quorum.

```{note}
We recommend deploying Apache ZooKeeper K8s charmed operator with either three or five units for high availability (HA) in production environments.
```

A three-unit ensemble offers optimal performance and resource efficiency while maintaining high availability (HA), tolerating one failed machine.

A five-unit ensemble enhances durability, tolerating two failures and enabling distribution across availability zones.

A seven-unit ensemble tolerates up to three failures. However, the larger quorum increases communication overhead, impacting performance.

The Apache ZooKeeper K8s charmed operator includes every active unit in the quorum. Other operational modes are not supported.

## Availability zones

For optimal high availability, deploy an Apache ZooKeeper cluster across at least three availability zones (AZ).

With only two AZ, if the zone hosting the majority of ZooKeeper nodes fails, the cluster will lose quorum and become unavailable.
