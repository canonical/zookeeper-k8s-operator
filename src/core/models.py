#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of state objects for the ZooKeeper relations, apps and units."""

import json
import logging
import socket
import warnings
from collections.abc import MutableMapping
from functools import cached_property
from typing import Literal

from charms.data_platform_libs.v0.data_interfaces import Data, DataPeerData, DataPeerUnitData
from ops.model import Application, Relation, Unit
from typing_extensions import deprecated, override

from core.stubs import RestoreStep, S3ConnectionInfo
from literals import CHARM_USERS, CLIENT_PORT, ELECTION_PORT, SECRETS_APP, SERVER_PORT
from managers.k8s import K8sManager

logger = logging.getLogger(__name__)

SUBSTRATES = Literal["vm", "k8s"]


class RelationState:
    """Relation state object."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: Data,
        component: Unit | Application | None,
        substrate: SUBSTRATES,
    ):
        self.relation = relation
        self.data_interface = data_interface
        self.component = component
        self.substrate = substrate
        self.relation_data = self.data_interface.as_dict(self.relation.id) if self.relation else {}

    def __bool__(self):
        """Boolean evaluation based on the existence of self.relation."""
        try:
            return bool(self.relation)
        except AttributeError:
            return False

    @property
    def data(self) -> MutableMapping:
        """Data representing the state."""
        return self.relation_data

    def update(self, items: dict[str, str]) -> None:
        """Writes to relation_data."""
        if not self.relation:
            logger.warning(
                f"Fields {list(items.keys())} were attempted to be written on the relation before it exists."
            )
            return

        delete_fields = [key for key in items if not items[key]]
        update_content = {k: items[k] for k in items if k not in delete_fields}

        self.relation_data.update(update_content)

        for field in delete_fields:
            del self.relation_data[field]


class ZKClient(RelationState):
    """State collection metadata for a single related client application."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: Data,
        component: Application,
        substrate: SUBSTRATES,
        local_app: Application | None = None,
        password: str = "",
        endpoints: str = "",
        tls: str = "",
        uris: str = "",
    ):
        super().__init__(relation, data_interface, None, substrate)
        self.app = component
        self._password = password
        self._endpoints = endpoints
        self._tls = tls
        self._uris = uris
        self._local_app = local_app

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
    @deprecated("Using 'uris' in the databag is deprecated, use 'endpoints' instead")
    def uris(self) -> str:
        """The ZooKeeper connection uris for the client application to connect with."""
        # TODO (zkclient): Remove this property
        return self._uris + self.database if self._uris else ""

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
        # TODO (zkclient): Remove this property and replace by "cdrwa" in self.extra_user_roles
        acl = self.relation_data.get("chroot-acl")
        if acl is not None:
            warnings.warn(
                "Using 'chroot-acl' in the databag is deprecated, use 'extra-user-roles' instead",
                DeprecationWarning,
            )

        else:
            acl = "cdrwa"

        return acl

    @property
    def extra_user_roles(self) -> str:
        """The client defined ACLs for their requested ACL.

        Contains:
            - 'c' - create
            - 'd' - delete
            - 'r' - read
            - 'w' - write
            - 'a' - append
        """
        return self.relation_data.get("extra-user-roles", self.chroot_acl)

    @property
    def chroot(self) -> str:
        """The client requested root zNode path value."""
        # TODO (zkclient): Remove this property and replace by "" in self.database
        chroot = self.relation_data.get("chroot", "")
        if chroot:
            warnings.warn(
                "Using 'chroot' in the databag is deprecated, use 'database' instead",
                DeprecationWarning,
            )
        if chroot and not chroot.startswith("/"):
            chroot = f"/{chroot}"

        return chroot

    @property
    def database(self) -> str:
        """The client requested root zNode path value."""
        chroot = self.relation_data.get("database", self.chroot)
        if chroot and not chroot.startswith("/"):
            chroot = f"/{chroot}"

        return chroot


class ZKCluster(RelationState):
    """State collection metadata for the charm application."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerData,
        component: Application,
        substrate: SUBSTRATES,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.data_interface = data_interface
        self.app = component

    @override
    def update(self, items: dict[str, str]) -> None:
        """Overridden update to allow for same interface, but writing to local app bag."""
        if not self.relation:
            return

        for key, value in items.items():
            if key in SECRETS_APP or key.startswith("relation-"):
                if value:
                    self.data_interface.set_secret(self.relation.id, key, value)
                else:
                    self.data_interface.delete_secret(self.relation.id, key)
            else:
                self.data_interface.update_relation_data(self.relation.id, {key: value})

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

    @property
    def s3_credentials(self) -> S3ConnectionInfo:
        """The current credentials and parameters to access object storage."""
        # Using "{}" would lead to an incorrect runtime object according to the type above.
        # This is checked in events.backup actions
        return json.loads(self.relation_data.get("s3-credentials", "{}"))

    @property
    def id_to_restore(self) -> str:
        """Backup id to restore."""
        return self.relation_data.get("id-to-restore", "")

    @property
    def restore_instruction(self) -> RestoreStep:
        """Current restore flow step to go through."""
        return RestoreStep(self.relation_data.get("restore-instruction", ""))

    @property
    def is_restore_in_progress(self) -> bool:
        """Is the cluster undergoing a restore?"""
        return bool(self.id_to_restore)


class ZKServer(RelationState):
    """State collection metadata for a charm unit."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerUnitData,
        component: Unit,
        substrate: SUBSTRATES,
        dns: bool = False,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.unit = component
        self.k8s = K8sManager(
            pod_name=self.pod_name,
            namespace=self.unit._backend.model_name,
        )
        self.dns = dns

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name.

        e.g zookeeper/2 --> 2
        """
        return int(self.unit.name.split("/")[1])

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
    def internal_dns_address(self) -> str:
        """The DNS address for the unit, for internal communication."""
        return socket.getfqdn()

    @property
    def internal_address(self) -> str:
        """The hostname for the unit, for internal communication."""
        host = ""
        if self.substrate == "vm":
            if self.dns:
                return self.relation_data.get("hostname", "")
            for key in ["ip", "hostname", "private-address"]:
                if host := self.relation_data.get(key, ""):
                    break

        if self.substrate == "k8s":
            host = f"{self.unit.name.replace('/', '-')}.{self.unit.app.name}-endpoints"

        return host

    @property
    def server_string(self) -> str:
        """The server string for the ZooKeeper server."""
        return f"server.{self.server_id}={self.internal_address}:{SERVER_PORT}:{ELECTION_PORT}:participant;0.0.0.0:{CLIENT_PORT}"

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
        # Backwards compatibility
        # TODO (zkclient): Remove this property and replace by "" in self.ca_cert
        ca = self.relation_data.get("ca", "")
        if ca:
            warnings.warn(
                "Using 'ca' in the databag is deprecated, use 'ca_cert' instead",
                DeprecationWarning,
            )
        return ca

    @property
    def ca_cert(self) -> str:
        """The root CA contents for the unit to use for TLS."""
        return self.relation_data.get("ca-cert", self.ca)

    @property
    def chain(self) -> list[str]:
        """The chain used to sign unit cert."""
        return json.loads(self.relation_data.get("chain", "null")) or []

    @property
    def bundle(self) -> list[str]:
        """The cert bundle used for TLS identity."""
        if not all([self.certificate, self.ca_cert, self.chain]):
            return []

        # manual-tls-certificates is loaded with the signed cert, the intermediate CA that signed it
        # and then the missing chain for that CA
        # ZK needs to present the full bundle - aka Keystore
        # ZK needs to trust each item in the bundle - aka Truststore
        bundle = [self.certificate, self.ca_cert] + self.chain
        return sorted(set(bundle), key=bundle.index)  # ordering might matter

    @property
    def restore_progress(self) -> RestoreStep:
        """Latest restore flow step the unit went through."""
        return RestoreStep(self.relation_data.get("restore-progress", ""))

    @property
    def pod_name(self) -> str:
        """The name of the K8s Pod for the unit.

        K8s-only.
        """
        return self.unit.name.replace("/", "-")

    @cached_property
    def node_ip(self) -> str:
        """The IPV4/IPV6 IP address of the Node the unit is on.

        K8s-only.
        """
        return self.k8s.get_node_ip(self.pod_name)

    @cached_property
    def loadbalancer_ip(self) -> str:
        """The IPV4/IPV6 IP address of the LoadBalancer exposing the unit.

        K8s-only.
        """
        return self.k8s.get_loadbalancer()

    def get_relation_ip(self, relation: Relation) -> str:
        """Returns the IP address for a specific relation taking network binds into account."""
        return self.relation_data.get(f"ip-{relation.id}") or self.internal_address
