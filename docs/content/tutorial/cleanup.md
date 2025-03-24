# Clean up

This is the last part of the Apache ZooKeeper K8s charmed operator tutorial about clean up of used resources. Make sure to complete instruction from the [Integrate](integrate) page before reading further.

## Destroy the Apache ZooKeeper K8s application

Destroy the entire Apache ZooKeeper K8s application:

```
juju remove-application zookeeper
```

This will remove Apache ZooKeeper K8s from your Juju model.

## Destroy other applications

Let's say we are not sure what other applications we have in our model. 
Use `juju status` command:

```
juju status
```

Now, the applications mentioned in the output:

```
juju remove-application kafka
```

Finally, make sure that deletion is complete with `juju status` and remove the Juju model.

```
juju destroy-model tutorial
```

That concludes the clean up process and the Apache ZooKeeper K8s charmed operator tutorial.
