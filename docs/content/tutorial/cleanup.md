# Clean up

This is the last part of the Apache ZooKeeper K8s charm tutorial about clean up of used resources. Make sure to complete instruction from the [Integrate](integrate) page before reading further.

## Destroy the VM

Destroy the entire Multipass VM created for this tutorial:

```bash
multipass stop zk-vm
multipass delete --purge zk-vm
```

This concludes the clean up process and the Apache ZooKeeper charm tutorial.
