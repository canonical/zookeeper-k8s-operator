#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling ZooKeeper Kubernetes resources."""
import logging
from typing import Literal

from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Node, Pod, Service

from literals import CLIENT_PORT, SECURE_CLIENT_PORT

logger = logging.getLogger(__name__)


class K8sManager:
    """Manager for handling ZooKeeper Kubernetes resources."""

    def __init__(self, pod_name: str, namespace: str) -> None:
        self.pod_name = pod_name
        self.app_name, _, _ = pod_name.rpartition("-")
        self.namespace = namespace
        self.exposer_service_name = f"{self.app_name}-exposer"

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
            self.client.delete(Service, name=service_name)
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
            ),
            ServicePort(
                protocol="TCP",
                port=SECURE_CLIENT_PORT,
                targetPort=SECURE_CLIENT_PORT,
                name=f"{self.exposer_service_name}-tls",
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

    def get_pod(self, pod_name: str) -> Pod:
        """Gets the Pod via the K8s API."""
        return self.client.get(
            res=Pod,
            name=pod_name,
        )

    def get_service(self, service_name: str) -> Service:
        """Gets the Service via the K8s API."""
        return self.client.get(
            res=Service,
            name=service_name,
        )

    def get_node(self, pod_name: str) -> Node:
        """Gets the Node the Pod is running on via the K8s API."""
        pod = self.get_pod(pod_name)
        if not pod.spec or not pod.spec.nodeName:
            raise Exception("Could not find podSpec or nodeName")

        return self.client.get(
            Node,
            name=pod.spec.nodeName,
        )

    def get_node_ip(self, pod_name: str) -> str:
        """Gets the IP Address of the Node of a given Pod via the K8s API."""
        node = self.get_node(pod_name)
        if not node.status or not node.status.addresses:
            raise Exception(f"No status found for {node}")

        for addresses in node.status.addresses:
            if addresses.type in ["ExternalIP", "InternalIP", "Hostname"]:
                return addresses.address

        return ""

    def get_nodeport(self, auth: Literal["plain", "tls"]) -> int:
        """Gets the NodePort number for the service via the K8s API."""
        if not (service := self.get_service(self.exposer_service_name)):
            raise Exception("Unable to find Service")

        if not service.spec or not service.spec.ports:
            raise Exception("Could not find Service spec or ports")

        for port in service.spec.ports:
            if str(port.name).endswith(auth):
                return port.nodePort

        raise Exception(f"Unable to find NodePort using {auth} for the {service} service")
