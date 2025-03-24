# Clean up

This is the last part of the Apache ZooKeeper charmed operator tutorial about clean up of used resources. Make sure to complete instruction from the [Integrate](integrate) page before reading further.

## Destroy the Apache ZooKeeper application

Destroy the entire Apache ZooKeeper application:

```
juju remove-application zookeeper
```

This will remove Apache ZooKeeper from your Juju model.

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

That concludes the clean up process and the Apache ZooKeeper charmed operator tutorial.
