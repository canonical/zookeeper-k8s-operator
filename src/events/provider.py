#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `zookeeper` relation interface."""
import logging
from typing import TYPE_CHECKING

from charms.zookeeper.v0.client import (
    MemberNotReadyError,
    MembersSyncingError,
    QuorumLeaderNotFoundError,
)
from kazoo.handlers.threading import KazooTimeoutError
from ops.charm import RelationBrokenEvent, RelationEvent
from ops.framework import Object

from literals import REL_NAME

if TYPE_CHECKING:
    from charm import ZooKeeperCharm

logger = logging.getLogger(__name__)


class ProviderEvents(Object):
    """Event handlers for related applications on the `zookeeper` relation interface."""

    def __init__(self, charm):
        super().__init__(charm, "provider")
        self.charm: "ZooKeeperCharm" = charm

        self.framework.observe(
            self.charm.on[REL_NAME].relation_changed, self._on_client_relation_updated
        )
        self.framework.observe(
            self.charm.on[REL_NAME].relation_broken, self._on_client_relation_broken
        )

    def _on_client_relation_updated(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_joined` events.

        Once credentals and ACLs are added for the event username, sets them to relation data.
        Future `client_relation_changed` events called on non-leader units checks passwords before
            restarting.
        """
        if not self.charm.unit.is_leader() or not self.charm.state.stable:
            event.defer()
            return

        # ACLs created before passwords set to avoid restarting before successful adding
        try:
            self.charm.quorum_manager.update_acls()
        except (
            MembersSyncingError,
            MemberNotReadyError,
            QuorumLeaderNotFoundError,
            KazooTimeoutError,
        ) as e:
            logger.warning(str(e))
            event.defer()
            return

        # FIXME: should be data-platform-libs
        # setting password for new users to relation data
        for client in self.charm.state.clients:
            if not client.chroot:
                continue

            if (
                isinstance(event, RelationBrokenEvent)
                and client.relation
                and client.relation.id == event.relation.id
            ):
                continue  # don't re-add passwords for broken events

            self.charm.state.cluster.update(
                {client.username: client.password or self.charm.workload.generate_password()}
            )

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Removes user from ZK app data on `client_relation_broken`.

        Args:
            event: used for passing `RelationBrokenEvent` to subsequent methods
        """
        # Don't remove anything if ZooKeeper is going down
        if self.charm.app.planned_units == 0 or not self.charm.unit.is_leader():
            return

        # clearing up broken application passwords
        self.charm.state.cluster.update({f"relation-{event.relation.id}": ""})

        # call normal updated handler
        self._on_client_relation_updated(event=event)
