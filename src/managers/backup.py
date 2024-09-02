#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for managing backups."""

import logging

import boto3
from botocore import loaders, regions
from botocore.exceptions import ClientError
from mypy_boto3_s3.service_resource import Bucket

from core.stubs import S3ConnectionInfo

logger = logging.getLogger(__name__)


class BackupManager:
    """Manager for all things backup-related."""

    def __init__(self, s3_parameters: S3ConnectionInfo) -> None:
        self.s3_parameters = s3_parameters

    @property
    def bucket(self) -> Bucket:
        """S3 bucket to read from and write to."""
        s3 = boto3.resource(
            "s3",
            aws_access_key_id=self.s3_parameters["access-key"],
            aws_secret_access_key=self.s3_parameters["secret-key"],
            region_name=self.s3_parameters["region"] if self.s3_parameters["region"] else None,
            endpoint_url=self._construct_endpoint(self.s3_parameters),
        )
        return s3.Bucket(self.s3_parameters["bucket"])

    def _construct_endpoint(self, s3_parameters: S3ConnectionInfo) -> str:
        """Construct the S3 service endpoint using the region.

        This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
        """
        endpoint = s3_parameters["endpoint"]

        loader = loaders.create_loader()
        data = loader.load_data("endpoints")

        resolver = regions.EndpointResolver(data)
        endpoint_data = resolver.construct_endpoint("s3", s3_parameters["region"])

        if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
            endpoint = f'{endpoint.split("://")[0]}://{endpoint_data["hostname"]}'

        return endpoint

    def create_bucket(self, s3_parameters: S3ConnectionInfo) -> bool:
        """Create bucket if it does not exists."""
        s3 = boto3.resource(
            "s3",
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            region_name=s3_parameters["region"] if s3_parameters["region"] else None,
            endpoint_url=s3_parameters["endpoint"],
        )
        bucket_name = s3_parameters["bucket"]
        bucket_exists = True

        bucket = s3.Bucket(bucket_name)  # pyright: ignore [reportAttributeAccessIssue]

        try:
            bucket.meta.client.head_bucket(Bucket=bucket_name)
        except ClientError as ex:
            if "(403)" in ex.args[0]:
                logger.error("Wrong credentials or access to bucket is forbidden")
                return False
            elif "(404)" in ex.args[0]:
                bucket_exists = False
        else:
            logger.info(f"Using existing bucket {bucket_name}")

        if not bucket_exists:
            bucket.create()
            bucket.wait_until_exists()
            logger.info(f"Created bucket {bucket_name}")

        return True

    def write_test_string(self) -> None:
        """Write content in the object storage."""
        self.bucket.put_object(Key="test_file.txt", Body=b"test string")
