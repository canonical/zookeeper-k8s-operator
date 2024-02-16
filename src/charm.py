#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed k8s Operator for Apache ZooKeeper."""

import logging
import time

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops.charm import (
    CharmBase,
    InstallEvent,
    LeaderElectedEvent,
    RelationDepartedEvent,
)
from ops.framework import EventBase
from ops.main import main
from ops.model import (
    ActiveStatus,
    ModelError,
    StatusBase,
    WaitingStatus,
)
from ops.pebble import Layer, LayerDict
from tenacity import RetryError

from core.cluster import ClusterState
from events.password_actions import PasswordActionEvents
from events.provider import ProviderEvents
from events.tls import TLSEvents
from events.upgrade import ZKUpgradeEvents, ZooKeeperDependencyModel
from literals import (
    CHARM_KEY,
    CHARM_USERS,
    CLIENT_PORT,
    CONTAINER,
    DEPENDENCIES,
    JMX_PORT,
    LOGS_RULES_DIR,
    METRICS_PROVIDER_PORT,
    METRICS_RULES_DIR,
    SUBSTRATE,
    DebugLevel,
    Status,
)
from managers.config import ConfigManager
from managers.quorum import QuorumManager
from managers.tls import TLSManager
from workload import ZKWorkload

logger = logging.getLogger(__name__)


class ZooKeeperCharm(CharmBase):
    """Charmed Operator for ZooKeeper K8s."""

    def __init__(self, *args):
        super().__init__(*args)
        self.name = CHARM_KEY
        self.state = ClusterState(self, substrate=SUBSTRATE)
        self.workload = ZKWorkload(container=self.unit.get_container(CONTAINER))

        # --- CHARM EVENT HANDLERS ---

        self.password_action_events = PasswordActionEvents(self)
        self.tls_events = TLSEvents(self)
        self.provider_events = ProviderEvents(self)
        self.upgrade_events = ZKUpgradeEvents(
            self,
            dependency_model=ZooKeeperDependencyModel(
                **DEPENDENCIES  # pyright: ignore[reportGeneralTypeIssues]
            ),
        )

        # --- MANAGERS ---

        self.quorum_manager = QuorumManager(state=self.state)
        self.tls_manager = TLSManager(
            state=self.state, workload=self.workload, substrate=SUBSTRATE
        )
        self.config_manager = ConfigManager(
            state=self.state, workload=self.workload, substrate=SUBSTRATE, config=self.config
        )

        # --- LIB EVENT HANDLERS ---

        self.restart = RollingOpsManager(self, relation="restart", callback=self._restart)
        self.grafana_dashboards = GrafanaDashboardProvider(self)
        self.metrics_endpoint = MetricsEndpointProvider(
            self,
            refresh_event=self.on.start,
            alert_rules_path=METRICS_RULES_DIR,
            jobs=[
                {"static_configs": [{"targets": [f"*:{JMX_PORT}", f"*:{METRICS_PROVIDER_PORT}"]}]}
            ],
        )
        self.loki_push = LogProxyConsumer(
            self,
            log_files=["/var/log/zookeeper/zookeeper.log"],  # FIXME: update when rebased on merged
            alert_rules_path=LOGS_RULES_DIR,
            relation_name="logging",
            container_name=CONTAINER,
        )
        # --- CORE EVENTS ---

        self.framework.observe(getattr(self.on, "install"), self._on_install)
        self.framework.observe(
            getattr(self.on, "update_status"), self._on_cluster_relation_changed
        )
        self.framework.observe(getattr(self.on, "upgrade_charm"), self._on_zookeeper_pebble_ready)
        self.framework.observe(getattr(self.on, "start"), self._on_zookeeper_pebble_ready)
        self.framework.observe(
            getattr(self.on, "zookeeper_pebble_ready"), self._on_zookeeper_pebble_ready
        )
        self.framework.observe(
            getattr(self.on, "leader_elected"), self._on_cluster_relation_changed
        )
        self.framework.observe(
            getattr(self.on, "config_changed"), self._on_cluster_relation_changed
        )

        self.framework.observe(
            getattr(self.on, "cluster_relation_changed"), self._on_cluster_relation_changed
        )
        self.framework.observe(
            getattr(self.on, "cluster_relation_joined"), self._on_cluster_relation_changed
        )
        self.framework.observe(
            getattr(self.on, "cluster_relation_departed"), self._on_cluster_relation_changed
        )

    @property
    def _layer(self) -> Layer:
        """Returns a Pebble configuration layer for ZooKeeper on K8s."""
        layer_config: "LayerDict" = {
            "summary": "zookeeper layer",
            "description": "Pebble config layer for zookeeper",
            "services": {
                CONTAINER: {
                    "override": "replace",
                    "summary": "zookeeper",
                    "command": f"{self.workload.paths.binaries_path}/bin/zkServer.sh --config {self.workload.paths.conf_path} start-foreground",
                    "startup": "enabled",
                    "environment": {
                        "SERVER_JVMFLAGS": " ".join(
                            self.config_manager.server_jvmflags + self.config_manager.jmx_jvmflags
                        )
                    },
                },
            },
            "checks": {
                CONTAINER: {
                    "override": "replace",
                    "level": "alive",
                    "exec": {
                        "command": f"echo ruok | nc {self.state.unit_server.host} {CLIENT_PORT}"
                    },
                }
            },
        }
        return Layer(layer_config)

    # --- CORE EVENT HANDLERS ---

    def _on_install(self, event: InstallEvent) -> None:
        """Handler for the `on_install` event."""
        # don't complete install until passwords set
        if not self.state.peer_relation:
            self.unit.status = WaitingStatus("waiting for peer relation")
            event.defer()
            return

        if self.unit.is_leader() and not self.state.cluster.internal_user_credentials:
            for user in CHARM_USERS:
                self.state.cluster.update({f"{user}-password": self.workload.generate_password()})

        # give the leader a default quorum during cluster initialisation
        if self.unit.is_leader():
            self.state.cluster.update({"quorum": "default - non-ssl"})

    def _on_cluster_relation_changed(self, event: EventBase) -> None:
        """Generic handler for all 'something changed, update' events across all relations."""
        if not self.workload.alive:
            self._set_status(Status.CONTAINER_NOT_CONNECTED)
            event.defer()
            return

        # not all methods called
        if not self.state.peer_relation:
            self._set_status(Status.NO_PEER_RELATION)
            return

        # don't want to prematurely set config using outdated/missing relation data
        # also skip update-status overriding statues during upgrades
        if not self.upgrade_events.idle:
            event.defer()
            return

        # attempt startup of server
        if not self.state.unit_server.started:
            self.init_server()

        # even if leader has not started, attempt update quorum
        self.update_quorum(event=event)

        # don't delay scale-down leader ops by restarting dying unit
        if getattr(event, "departing_unit", None) == self.unit:
            return

        # check whether restart is needed for all `*_changed` events
        # only restart where necessary to avoid slowdowns
        # config_changed call here implicitly updates jaas + zoo.cfg
        if (
            (self.config_manager.config_changed() or self.state.cluster.switching_encryption)
            and self.state.unit_server.started
            and self.upgrade_events.idle
        ):
            self.on[f"{self.restart.name}"].acquire_lock.emit()

        # ensures events aren't lost during an upgrade on single units
        if self.state.cluster.switching_encryption and len(self.state.servers) == 1:
            event.defer()

        # service can stop serving requests if the quorum is lost
        if self.state.unit_server.started and not self.workload.healthy:
            self._set_status(Status.SERVICE_UNHEALTHY)
            return

    def _on_zookeeper_pebble_ready(self, event: EventBase) -> None:
        """Handler for the `upgrade-charm`, `zookeeper-pebble-ready` and `start` events.

        Handles case where workload has shut down due to failing `ruok` 4lw command and
        needs to be restarted.
        """
        # don't want to run default pebble ready during upgrades
        if not self.upgrade_events.idle:
            return

        # ensure pebble-ready only fires after normal peer-relation-driven server init
        if not self.workload.alive or not self.state.unit_server.started:
            self._set_status(Status.CONTAINER_NOT_CONNECTED)
            event.defer()
            return

        try:
            if self.workload.healthy:
                return  # nothing to do, service is up and running, don't replan
        except (ModelError, RetryError):
            logger.info(f"{CONTAINER} workload service not running, re-initialising...")

        # re-initialise + replan pebble layer if no service, or service not running
        self.init_server()

    def _restart(self, event: EventBase) -> None:
        """Handler for emitted restart events."""
        self._set_status(self.state.stable)
        if not isinstance(self.unit.status, ActiveStatus):
            event.defer()
            return

        logger.info(f"{self.unit.name} restarting...")
        self.workload.restart()

        # gives time for server to rejoin quorum, as command exits too fast
        # without, other units might restart before this unit rejoins, losing quorum
        time.sleep(5)

        if not self.workload.alive:
            self._set_status(Status.CONTAINER_NOT_CONNECTED)
            return

        if not self.workload.healthy:
            self._set_status(Status.SERVICE_UNHEALTHY)
            return

        self._set_status(Status.ACTIVE)

        self.state.unit_server.update(
            {
                # flag to declare unit running `portUnification` during ssl<->no-ssl upgrade
                "unified": "true" if self.state.cluster.switching_encryption else "",
                # flag to declare unit restarted with new quorum encryption
                "quorum": self.state.cluster.quorum,
                # indicate that unit has completed restart on password rotation
                "password-rotated": "true" if self.state.cluster.rotate_passwords else "",
            }
        )

    # --- CONVENIENCE METHODS ---

    def init_server(self):
        """Calls startup functions for server start.

        Sets myid, server_jvmflgas env_var, initial servers in dynamic properties,
            default properties and jaas_config
        """
        # don't run if leader has not yet created passwords
        if not self.state.cluster.internal_user_credentials:
            self._set_status(Status.NO_PASSWORDS)
            return

        # don't run (and restart) if some units are still joining
        # instead, wait for relation-changed from it's setting of 'started'
        if not self.state.all_units_related:
            self._set_status(Status.NOT_ALL_RELATED)
            return

        # start units in order
        if (
            self.state.next_server
            and self.state.next_server.component.name != self.state.unit_server.component.name
        ):
            self._set_status(Status.NOT_UNIT_TURN)
            return

        logger.info(f"{self.unit.name} initializing...")

        # setting default properties
        self.config_manager.set_zookeeper_myid()
        self.config_manager.set_server_jvmflags()

        # servers properties needs to be written to dynamic config
        self.config_manager.set_zookeeper_dynamic_properties(servers=self.state.startup_servers)

        logger.debug("setting properties and jaas")
        self.config_manager.set_zookeeper_properties()
        self.config_manager.set_jaas_config()

        logger.debug("starting ZooKeeper service")
        self.workload.start(layer=self._layer)

        if not self.workload.alive:
            self._set_status(Status.CONTAINER_NOT_CONNECTED)
            return

        if not self.workload.healthy:
            self._set_status(Status.SERVICE_UNHEALTHY)
            return

        self._set_status(Status.ACTIVE)

        # unit flags itself as 'started' so it can be retrieved by the leader
        logger.info(f"{self.unit.name} started")

        # added here in case a `restart` was missed
        self.state.unit_server.update(
            {
                "state": "started",
                "unified": "true" if self.state.cluster.switching_encryption else "",
                "quorum": self.state.cluster.quorum,
            }
        )

    def update_quorum(self, event: EventBase) -> None:
        """Updates the server quorum members for all currently started units in the relation.

        Also sets app-data pertaining to quorum encryption state during upgrades.
        """
        if not self.unit.is_leader() or getattr(event, "departing_unit", None) == self.unit:
            return

        # set first unit to "added" asap to get the units starting sooner
        # sets to "added" for init quorum leader, if not already exists
        # may already exist if during the case of a failover of the first unit
        if (init_leader := self.state.init_leader) and init_leader.started:
            self.state.cluster.update({str(init_leader.unit_id): "added"})

        if (
            self.state.stale_quorum  # in the case of scale-up
            or isinstance(  # to run without delay to maintain quorum on scale down
                event,
                (RelationDepartedEvent, LeaderElectedEvent),
            )
            or self.state.healthy  # to ensure run on update-status
        ):
            updated_servers = self.quorum_manager.update_cluster()
            logger.debug(f"{updated_servers=}")

            # triggers a `cluster_relation_changed` to wake up following units
            self.state.cluster.update(updated_servers)

        # default startup without ssl relation
        logger.debug("updating quorum - checking cluster stability")
        self._set_status(self.state.stable)
        if not isinstance(self.unit.status, ActiveStatus):
            return

        # declare upgrade complete only when all peer units have started
        # triggers `cluster_relation_changed` to rolling-restart without `portUnification`
        if self.state.all_units_unified:
            logger.debug("all units unified")
            if self.state.cluster.tls:
                logger.debug("tls enabled - switching to ssl")
                self.state.cluster.update({"quorum": "ssl"})
            else:
                logger.debug("tls disabled - switching to non-ssl")
                self.state.cluster.update({"quorum": "non-ssl"})

            if self.state.all_units_quorum:
                logger.debug(
                    "all units running desired encryption - removing switching-encryption"
                )
                self.state.cluster.update({"switching-encryption": ""})
                logger.info(f"ZooKeeper cluster switching to {self.state.cluster.quorum} quorum")

        self.update_client_data()

    def update_client_data(self) -> None:
        """Writes necessary relation data to all related applications."""
        if not self.unit.is_leader():
            return

        self._set_status(self.state.ready)
        if not isinstance(self.unit.status, ActiveStatus):
            return

        for client in self.state.clients:
            if (
                not client.password  # password not set to peer data, i.e ACLs created
                or client.password
                not in "".join(
                    self.config_manager.current_jaas
                )  # if password in jaas file, unit has probably restarted
            ):
                logger.debug(f"Skipping update of {client.component.name}, ACLs not yet set...")
                continue

            client.update(
                {
                    "uris": client.uris,
                    "endpoints": client.endpoints,
                    "tls": client.tls,
                    "username": client.username,
                    "password": client.password,
                    "chroot": client.chroot,
                }
            )

    def _set_status(self, key: Status) -> None:
        """Sets charm status."""
        status: StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.unit.status = status


if __name__ == "__main__":
    main(ZooKeeperCharm)
