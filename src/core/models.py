#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of state objects for the ZooKeeper relations, apps and units."""
import logging
from typing import Literal, MutableMapping

from ops.model import Application, Relation, Unit
from typing_extensions import override

from literals import CHARM_USERS, CLIENT_PORT, ELECTION_PORT, SERVER_PORT

logger = logging.getLogger(__name__)

SUBSTRATES = Literal["vm", "k8s"]


class StateBase:
    """Base state object."""

    def __init__(
        self, relation: Relation | None, component: Unit | Application, substrate: SUBSTRATES
    ):
        self.relation = relation
        self.component = component
        self.substrate = substrate

    @property
    def relation_data(self) -> MutableMapping[str, str]:
        """The raw relation data."""
        if not self.relation:
            return {}

        return self.relation.data[self.component]

    def update(self, items: dict[str, str]) -> None:
        """Writes to relation_data."""
        if not self.relation:
            return

        self.relation_data.update(items)


class ZKClient(StateBase):
    """State collection metadata for a single related client application."""

    def __init__(
        self,
        relation: Relation | None,
        component: Application,
        substrate: SUBSTRATES,
        local_app: Application | None = None,
        password: str = "",
        endpoints: str = "",
        tls: str = "",
        uris: str = "",
    ):
        super().__init__(relation, component, substrate)
        self.app = component
        self._password = password
        self._endpoints = endpoints
        self._tls = tls
        self._uris = uris
        self._local_app = local_app

    @override
    def update(self, items: dict[str, str]) -> None:
        """Overridden update to allow for same interface, but writing to local app bag."""
        if not self.relation or not self._local_app:
            return

        self.relation.data[self._local_app].update(items)

    @property
    def username(self) -> str:
        """The generated username for the client application."""
        return f"relation-{getattr(self.relation, 'id', '')}"

    @property
    def password(self) -> str:
        """The generated password for the client application."""
        return self._password

    @property
    def endpoints(self) -> str:
        """The ZooKeeper connection endpoints for the client application to connect with."""
        return self._endpoints

    @property
    def uris(self) -> str:
        """The ZooKeeper connection uris for the client application to connect with."""
        return self._uris + self.chroot if self._uris else ""

    @property
    def tls(self) -> str:
        """Flag to confirm whether or not ZooKeeper has TLS enabled.

        Returns:
            String of either 'enabled' or 'disabled'
        """
        return self._tls

    @property
    def chroot_acl(self) -> str:
        """The client defined ACLs for their requested ACL.

        Contains:
            - 'c' - create
            - 'd' - delete
            - 'r' - read
            - 'w' - write
            - 'a' - append
        """
        return self.relation_data.get("chroot-acl", "cdrwa")

    @property
    def chroot(self) -> str:
        """The client requested root zNode path value."""
        chroot = self.relation_data.get("chroot", "")
        if not chroot.startswith("/") and chroot:
            chroot = f"/{chroot}"

        return chroot


class ZKCluster(StateBase):
    """State collection metadata for the charm application."""

    def __init__(self, relation: Relation | None, component: Application, substrate: SUBSTRATES):
        super().__init__(relation, component, substrate)
        self.app = component

    @property
    def quorum_unit_ids(self) -> list[int]:
        """The unit ids that have been added/removed from the quorum.

        Returns:
            List of unit id integers
        """
        return [int(unit_id) for unit_id in self.relation_data if unit_id.isdigit()]

    @property
    def added_unit_ids(self) -> list[int]:
        """The unit ids that have been added to the current quorum.

        Returns:
            List of unit id integers
        """
        return [
            int(unit_id)
            for unit_id in self.quorum_unit_ids
            if self.relation_data.get(str(unit_id)) == "added"
        ]

    @property
    def internal_user_credentials(self) -> dict[str, str]:
        """The passwords for the internal quorum and super users.

        Returns:
            Dict of key username, value password
        """
        credentials = {
            user: password
            for user in CHARM_USERS
            if (password := self.relation_data.get(f"{user}-password"))
        }

        if not len(credentials) == len(CHARM_USERS):
            return {}

        return credentials

    @property
    def client_passwords(self) -> dict[str, str]:
        """The passwords for related client applications.

        Returns:
            Dict of key username, value password
        """
        return {key: value for key, value in self.relation_data.items() if "relation-" in key}

    @property
    def rotate_passwords(self) -> bool:
        """Flag to check if the cluster should rotate their internal passwords."""
        return bool(self.relation_data.get("rotate-passwords", ""))

    # -- TLS --

    @property
    def quorum(self) -> str:
        """The current quorum encryption for the cluster."""
        return self.relation_data.get("quorum", "")

    @property
    def switching_encryption(self) -> bool:
        """Flag to check if the cluster is switching quorum encryption."""
        return bool(self.relation_data.get("switching-encryption", ""))

    @property
    def tls(self) -> bool:
        """Flag to check if TLS is enabled for the cluster."""
        return self.relation_data.get("tls", "") == "enabled"


class ZKServer(StateBase):
    """State collection metadata for a charm unit."""

    def __init__(self, relation: Relation | None, component: Unit, substrate: SUBSTRATES):
        super().__init__(relation, component, substrate)
        self.unit = component

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name.

        e.g zookeeper/2 --> 2
        """
        return int(self.component.name.split("/")[1])

    # -- Cluster Init --

    @property
    def started(self) -> bool:
        """Flag to check if the unit has started the ZooKeeper service."""
        return self.relation_data.get("state", None) == "started"

    @property
    def password_rotated(self) -> bool:
        """Flag to check if the unit has rotated their internal passwords."""
        return bool(self.relation_data.get("password-rotated", None))

    @property
    def hostname(self) -> str:
        """The hostname for the unit."""
        return self.relation_data.get("hostname", "")

    @property
    def fqdn(self) -> str:
        """The Fully Qualified Domain Name for the unit."""
        return self.relation_data.get("fqdn", "")

    @property
    def ip(self) -> str:
        """The IP for the unit."""
        return self.relation_data.get("ip", "")

    @property
    def server_id(self) -> int:
        """The id of the server derived from the unit name.

        Server IDs are part of the server strings that ZooKeeper uses for
        intercommunication between quorum members. They should be positive integers.

        We default to (unit id + 1)

        e.g zookeeper/0 --> 1
        """
        return self.unit_id + 1

    @property
    def host(self) -> str:
        """The hostname for the unit."""
        host = ""
        if self.substrate == "vm":
            for key in ["hostname", "ip", "private-address"]:
                if host := self.relation_data.get(key, ""):
                    break

        if self.substrate == "k8s":
            host = f"{self.component.name.split('/')[0]}-{self.unit_id}.{self.component.name.split('/')[0]}-endpoints"

        return host

    @property
    def server_string(self) -> str:
        """The server string for the ZooKeeper server."""
        return f"server.{self.server_id}={self.host}:{SERVER_PORT}:{ELECTION_PORT}:participant;0.0.0.0:{CLIENT_PORT}"

    # -- TLS --

    @property
    def quorum(self) -> str:
        """The quorum encryption currently set on the unit."""
        return self.relation_data.get("quorum", "")

    @property
    def unified(self) -> bool:
        """Flag to check if server is running with portUnification.

        While switching between TLS + non-TLS server-server quorum encryption,
        it's necessary to unify the ports first so that members can still
        communicate during the switch.
        """
        return bool(self.relation_data.get("unified", ""))

    @property
    def private_key(self) -> str:
        """The private-key contents for the unit to use for TLS."""
        return self.relation_data.get("private-key", "")

    @property
    def keystore_password(self) -> str:
        """The Java Keystore password for the unit to use for TLS."""
        return self.relation_data.get("keystore-password", "")

    @property
    def truststore_password(self) -> str:
        """The Java Truststore password for the unit to use for TLS."""
        return self.relation_data.get("truststore-password", "")

    @property
    def csr(self) -> str:
        """The current certificate signing request contents for the unit."""
        return self.relation_data.get("csr", "")

    @property
    def certificate(self) -> str:
        """The certificate contents for the unit to use for TLS."""
        return self.relation_data.get("certificate", "")

    @property
    def ca(self) -> str:
        """The root CA contents for the unit to use for TLS."""
        return self.relation_data.get("ca", "")

    @property
    def sans(self) -> dict[str, list[str]]:
        """The Subject Alternative Name for the unit's TLS certificates."""
        if not all([self.ip, self.hostname, self.fqdn]):
            return {}

        return {
            "sans_ip": [self.ip],
            "sans_dns": [self.hostname, self.fqdn],
        }
