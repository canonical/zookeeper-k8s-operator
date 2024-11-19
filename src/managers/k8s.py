#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling ZooKeeper Kubernetes resources."""
import logging

from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Pod, Service

from literals import CLIENT_PORT, SECURE_CLIENT_PORT

logger = logging.getLogger(__name__)

NODEPORT_OFFSET = 30_000


class K8sManager:
    """Manager for handling ZooKeeper Kubernetes ressources."""

    def __init__(self, app_name: str, namespace: str) -> None:
        self.app_name = app_name
        self.namespace = namespace
        self.exposer_service_name = f"{app_name}-exposer"

    @property
    def client(self) -> Client:
        """The Lightkube client."""
        return Client(
            field_manager=self.app_name,
            namespace=self.namespace,
        )

    def apply_service(self, service: Service) -> None:
        """Apply a given Service."""
        try:
            self.client.apply(service)
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Could not apply service, application needs `juju trust`")
                return
            if e.status.code == 422 and "port is already allocated" in e.status.message:
                logger.error(e.status.message)
                return
            else:
                raise

    def remove_service(self, service_name: str) -> None:
        """Remove the exposer service."""
        try:
            self.client.delete(Service, name=self.exposer_service_name)
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Could not apply service, application needs `juju trust`")
                return
            if e.status.code == 404:
                return
            else:
                raise

    def build_nodeport_service(self) -> Service:
        """Build the exposer service for 'nodeport' configuration option."""
        # Pods are incrementally added to the StatefulSet, so we will always have a "0".
        # Even if the "0" unit is not the leader, we just want a reference to the StatefulSet
        # which owns the "0" pod.
        pod = self.get_pod(f"{self.app_name}-0")
        if not pod.metadata:
            raise Exception(f"Could not find metadata for {pod}")

        ports = [
            ServicePort(
                protocol="TCP",
                port=CLIENT_PORT,
                targetPort=CLIENT_PORT,
                name=f"{self.exposer_service_name}-plain",
                nodePort=CLIENT_PORT + NODEPORT_OFFSET,
            ),
            ServicePort(
                protocol="TCP",
                port=SECURE_CLIENT_PORT,
                targetPort=SECURE_CLIENT_PORT,
                name=f"{self.exposer_service_name}-tls",
                nodePort=SECURE_CLIENT_PORT + NODEPORT_OFFSET,
            ),
        ]

        return Service(
            metadata=ObjectMeta(
                name=self.exposer_service_name,
                namespace=self.namespace,
                # owned by the StatefulSet
                ownerReferences=pod.metadata.ownerReferences,
            ),
            spec=ServiceSpec(
                externalTrafficPolicy="Local",
                type="NodePort",
                selector={"app.kubernetes.io/name": self.app_name},
                ports=ports,
            ),
        )

    def get_pod(self, pod_name):
        """Gets the Pod via the K8s API."""
        return self.client.get(
            res=Pod,
            name=pod_name,
        )
