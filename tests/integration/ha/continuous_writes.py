import logging
import subprocess
import sys
import time

from kazoo.client import KazooClient, SessionExpiredError
from kazoo.handlers.threading import KazooTimeoutError

logger = logging.getLogger(__name__)


def continous_writes(parent: str, hosts: str, username: str, password: str):
    count = 0
    while True:
        try:
            client = KazooClient(
                hosts=hosts,
                sasl_options={
                    "mechanism": "DIGEST-MD5",
                    "username": username,
                    "password": password,
                },
            )
            client.start()
            client.create(f"{parent}/{count}", acl=[], makepath=True)
            logger.info(f"Wrote {parent}/{count}...")
            count += 1
            time.sleep(1)
            client.stop()
            client.close()

        # kazoo.client randomly picks a single host out of the provided comma-delimeted string
        # if that unit's network is down, it will just fail to connect rather than re-discovering
        # as such, retry write rather than count as missed
        except (SessionExpiredError, KazooTimeoutError):
            time.sleep(1)
            continue


def start_continuous_writes(parent: str, hosts: str, username: str, password: str):
    subprocess.Popen(
        [
            "python3",
            "tests/integration/ha/continuous_writes.py",
            parent,
            hosts,
            username,
            password,
        ]
    )


def stop_continuous_writes():
    proc = subprocess.Popen(["pkill", "-9", "-f", "continuous_writes.py"])
    proc.communicate()


def get_last_znode(parent: str, hosts: str, username: str, password: str) -> int:
    client = KazooClient(
        hosts=hosts,
        sasl_options={"mechanism": "DIGEST-MD5", "username": username, "password": password},
    )
    client.start()

    znodes = client.get_children(parent)

    if not znodes:
        raise Exception(f"No child znodes found under {parent}")

    last_znode = sorted((int(x) for x in znodes))[-1]

    client.stop()
    client.close()

    return last_znode


def count_znodes(parent: str, hosts: str, username: str, password: str) -> int:
    client = KazooClient(
        hosts=hosts,
        sasl_options={"mechanism": "DIGEST-MD5", "username": username, "password": password},
    )
    client.start()

    znodes = client.get_children(parent)

    client.stop()
    client.close()

    if not znodes:
        raise Exception(f"No child znodes found under {parent}")

    return len(znodes) - 1  # to account for the parent node


def main():
    parent = sys.argv[1]
    hosts = sys.argv[2]
    username = sys.argv[3]
    password = sys.argv[4]

    continous_writes(parent, hosts, username, password)


if __name__ == "__main__":
    main()
