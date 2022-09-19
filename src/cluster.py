#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""ZooKeeperCluster class and methods."""


import logging
import re
from typing import Dict, List, Optional, Set, Tuple, Union

from charms.zookeeper.v0.client import (
    MemberNotReadyError,
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperManager,
)
from kazoo.exceptions import BadArgumentsError
from kazoo.handlers.threading import KazooTimeoutError
from ops.model import Relation, Unit

from literals import PEER

logger = logging.getLogger(__name__)


class UnitNotFoundError(Exception):
    """A desired unit isn't yet found in the relation data."""

    pass


class ZooKeeperCluster:
    """Handler for managing the ZK peer-relation.

    Mainly for managing scale-up/down orchestration
    """

    def __init__(
        self,
        charm,
        client_port: int = 2181,
        secure_client_port: int = 2182,
        server_port: int = 2888,
        election_port: int = 3888,
    ) -> None:
        self.charm = charm
        self.client_port = client_port
        self.secure_client_port = secure_client_port
        self.server_port = server_port
        self.election_port = election_port

    @property
    def relation(self) -> Relation:
        """Relation property to be used by both the instance and charm.

        Returns:
            The peer relation instance
        """
        return self.charm.model.get_relation(PEER)

    @property
    def peer_units(self) -> Set[Unit]:
        """Grabs all units in the current peer relation, including the running unit.

        Returns:
            Set of units in the current peer relation, including the running unit
        """
        return set([self.charm.unit] + list(self.relation.units))

    @property
    def all_units_related(self) -> bool:
        """Checks if currently related units make up all planned.

        Returns:
            True if all units are related. Otherwise False
        """
        if len(self.peer_units) != self.charm.app.planned_units():
            return False

        return True

    @property
    def lowest_unit_id(self) -> Optional[int]:
        """Grabs the first unit in the currently deployed application.

        Returns:
            Integer of lowest unit-id in the app.
            None if not all planned units are related to the currently running unit.
        """
        # in the case that not all units are related yet
        if not self.all_units_related:
            return None

        return min([self.get_unit_id(unit) for unit in self.peer_units])

    @property
    def started_units(self) -> Set[Unit]:
        """Checks peer relation units for whether they've started the ZK service.

        Such units are ready to join the ZK quorum if they haven't already.

        Returns:
            Set of units with unit data "state" == "started". Shows only those units
                currently found related to the current unit.
        """
        started_units = set()
        for unit in self.peer_units:
            if self.relation.data[unit].get("state", None) == "started":
                started_units.add(unit)

        return started_units

    @property
    def stale_quorum(self) -> bool:
        """Checks whether it's necessary to update the servers in quorum.

        Condition is dependent on all units being relating, and a unit not
            yet been added to the quorum.

        Returns:
            True if the quorum needs updating. Otherwise False
        """
        if not self.all_units_related:
            return False

        for unit in self.peer_units:
            unit_id = self.get_unit_id(unit)
            if self.relation.data[self.charm.app].get(str(unit_id), None) != "added":
                logger.debug(f"Unit {unit.name} needs adding")
                return True

        return False

    @property
    def active_hosts(self) -> List[str]:
        """Grabs all the hosts of the started units.

        Returns:
            List of hosts for started units
        """
        active_hosts = []
        # grabs all currently 'started' units from unit data
        # failed units will be absent
        for unit in self.started_units:
            config = self.unit_config(unit=unit)
            active_hosts.append(config["host"])

        return active_hosts

    @property
    def active_servers(self) -> Set[str]:
        """Grabs all the server strings of the started units.

        Returns:
            List of ZK server strings for started units
        """
        active_servers = set()
        # grabs all currently 'started' units from unit data
        # failed units will be absent
        for unit in self.started_units:
            config = self.unit_config(unit=unit)
            active_servers.add(config["server_string"])

        return active_servers

    @staticmethod
    def get_unit_id(unit: Unit) -> int:
        """Grabs the unit's ID as defined by Juju.

        Args:
            unit: The target `Unit`

        Returns:
            The Juju unit ID for the unit.
                e.g `zookeeper/0` -> `0`
        """
        return int(unit.name.split("/")[1])

    def get_unit_from_id(self, unit_id: int) -> Unit:
        """Grabs the corresponding Unit for a given Juju unit ID.

        Args:
            unit_id: The target unit id

        Returns:
            The target `Unit`

        Raises:
            UnitNotFoundError: The desired unit could not be found in the peer data
        """
        for unit in self.peer_units:
            if int(unit.name.split("/")[1]) == unit_id:
                return unit

        raise UnitNotFoundError

    def unit_config(
        self, unit: Union[Unit, int], state: str = "ready", role: str = "participant"
    ) -> Dict[str, str]:
        """Builds a collection of data useful for ZK for a given unit.

        Args:
            unit: The target `Unit`, either explicitly or from it's Juju unit ID
            state: The desired output state. "ready" or "started"
            role: The ZK role for the unit. Default = "participant"

        Returns:
            The generated config for the given unit.
                e.g for unit zookeeper-k8s/1:

                {
                    "host": 10.121.23.23,
                    "server_string":
                        "server.1=host:server_port:election_port:role;client_port",
                    "server_id": "2",
                    "unit_id": "1",
                    "unit_name": "zookeeper/1",
                    "state": "ready",
                }

        Raises:
            UnitNotFoundError: When the target unit can't be found in the unit relation data,
                and/or cannot extract the private-address
        """
        unit_id = None
        server_id = None
        if isinstance(unit, Unit):
            unit = unit
            unit_id = self.get_unit_id(unit=unit)
            server_id = unit_id + 1
        if isinstance(unit, int):
            unit_id = unit
            server_id = unit + 1
            unit = self.get_unit_from_id(unit)

        host = f"{self.charm.app.name}-{unit_id}.{self.charm.app.name}-endpoints"
        server_string = f"server.{server_id}={host}:{self.server_port}:{self.election_port}:{role};0.0.0.0:{self.client_port}"

        return {
            "host": host,
            "server_string": server_string,
            "server_id": str(server_id),
            "unit_id": str(unit_id),
            "unit_name": unit.name,
            "state": state,
        }

    def _get_updated_servers(
        self, added_servers: List[str], removed_servers: List[str]
    ) -> Dict[str, str]:
        """Simple wrapper for building `updated_servers` for passing to app data updates."""
        servers_to_update = added_servers + removed_servers

        updated_servers = {}
        for server in servers_to_update:
            unit_id = str(int(re.findall(r"server.([0-9]+)", server)[0]) - 1)
            if server in added_servers:
                updated_servers[unit_id] = "added"
            elif server in removed_servers:
                updated_servers[unit_id] = "removed"

        return updated_servers

    def update_cluster(self) -> Dict[str, str]:
        """Adds and removes members from the current ZK quorum.

        To be ran by the Juju leader.

        After grabbing all the "started" units that the leader can see in the peer relation
            unit data.
        Removes members not in the quorum anymore (i.e `relation_departed`/`leader_elected` event)
        Adds new members to the quorum (i.e `relation_joined` event).

        Returns:
            A mapping of Juju unit IDs and updated state for changed units
            To be used in updating the app data
                e.g {"0": "added", "1": "removed"}
        """
        super_password, _ = self.passwords

        # NOTE - BUG in Apache ZooKeeper - https://issues.apache.org/jira/browse/ZOOKEEPER-3577
        # This means that we cannot dynamically reconfigure without also having a PLAIN port open
        # Ideally, have a check here for `client_port=self.secure_client_port` if tls.enabled
        # Until then, we can just use the insecure port for convenience

        try:
            zk = ZooKeeperManager(
                hosts=self.active_hosts,
                client_port=self.client_port,
                username="super",
                password=super_password,
            )

            # remove units first, faster due to no startup/sync delay
            zk_members = zk.server_members
            servers_to_remove = list(zk_members - self.active_servers)
            zk.remove_members(members=servers_to_remove)

            # sorting units to ensure units are added in id order
            zk_members = zk.server_members
            servers_to_add = sorted(self.active_servers - zk_members)
            zk.add_members(members=servers_to_add)

            return self._get_updated_servers(
                added_servers=servers_to_add, removed_servers=servers_to_remove
            )

        # caught errors relate to a unit/zk_server not yet being ready to change
        except (
            MembersSyncingError,
            MemberNotReadyError,
            QuorumLeaderNotFoundError,
            KazooTimeoutError,
            UnitNotFoundError,
            BadArgumentsError,
        ) as e:
            logger.debug(str(e))
            return {}

    def is_unit_turn(self, unit: Optional[Unit] = None) -> bool:
        """Checks if all units with a lower id than the unit has updated in the ZK quorum.

        Returns:
            True if unit is cleared to start. Otherwise False.
        """
        turn_unit = unit or self.charm.unit
        unit_id = self.get_unit_id(turn_unit)

        if self.lowest_unit_id == None:  # noqa: E711
            # not all units have related yet
            return False

        # the init leader does not need servers to start, so good to go
        if self._is_init_leader(unit_id=unit_id):
            return True

        for peer_id in range(self.lowest_unit_id, unit_id):
            # missing relation data unit ids means that they are not yet added to quorum
            if not self.relation.data[self.charm.app].get(str(peer_id), None):
                return False

        return True

    def _is_init_leader(self, unit_id: int) -> bool:
        """Checks if the passed unit should be the first unit to start."""
        # if lowest_unit_id, and it exists in the relation data already, it's a restart, fail
        if int(unit_id) == self.lowest_unit_id and not self.relation.data[self.charm.app].get(
            str(self.lowest_unit_id), None
        ):
            return True

        return False

    def _generate_units(self, unit_string: str) -> str:
        """Gets valid start-up server strings for current ZK quorum units found in the app data."""
        servers = ""
        for unit_id, state in self.relation.data[self.charm.app].items():
            if state == "added":
                try:
                    server_string = self.unit_config(unit=int(unit_id))["server_string"]
                except UnitNotFoundError as e:
                    logger.debug(str(e))
                    continue

                servers = servers + "\n" + server_string

        servers = servers + "\n" + unit_string
        return servers

    def startup_servers(self, unit: Union[Unit, int]) -> str:
        """Decides whether a unit should start the ZK service, and with what configuration.

        Args:
            unit: the `Unit` or Juju unit ID to evaluate startability

        Returns:
            New-line delimited string of servers to add to a config file
        """
        servers = ""
        unit_config = self.unit_config(unit=unit, state="ready", role="observer")
        unit_string = unit_config["server_string"]
        unit_id = unit_config["unit_id"]

        # during cluster startup, we want the first unit to start as a solo participant
        # if the first unit fails and restarts after that, we want it to start as a normal unit
        # with all current members
        if self._is_init_leader(unit_id=int(unit_id)):
            unit_string = unit_string.replace("observer", "participant")
            return unit_string

        servers = self._generate_units(unit_string=unit_string)

        return servers

    def _all_rotated(self) -> bool:
        """Check if all units have rotated their passwords.

        All units need to have `password-rotated` for the process to be finished.

        Returns:
            result : bool
        """
        all_finished = True
        for unit in self.peer_units:
            if self.relation.data[unit].get("password-rotated") is None:
                all_finished = False
                break
        return all_finished

    @property
    def passwords(self) -> Tuple[str, str]:
        """Gets the current super+sync passwords from the app relation data.

        Returns:
            Tuple of super_password, sync_password
        """
        super_password = str(self.relation.data[self.charm.app].get("super-password", ""))
        sync_password = str(self.relation.data[self.charm.app].get("sync-password", ""))

        return super_password, sync_password

    @property
    def passwords_set(self) -> bool:
        """Checks that the two desired passwords are in the app relation data.

        Returns:
            True if both passwords have been set. Otherwise False
        """
        super_password, sync_password = self.passwords
        if not super_password or not sync_password:
            return False
        else:
            return True

    @property
    def started(self) -> bool:
        """Flag to check the whether the running unit has started.

        Returns:
            True if the unit has started. Otherwise False
        """
        return self.relation.data[self.charm.unit].get("state", None) == "started"

    @property
    def quorum(self) -> Optional[str]:
        """Gets state of current quorum encryption.

        Returns:
            String of either `ssl` or `non-ssl`. None if quorum not yet reached
        """
        return self.relation.data[self.charm.app].get("quorum", None)

    @property
    def manual_restart(self) -> bool:
        """Flag to ensure a rolling-restart will execute.

        To be used during manually triggered restarts, where config doesn't change.

        Returns:
            True if manual-restart flag is set. Otherwise False
        """
        return bool(self.relation.data[self.charm.app].get("manual-restart", None))
