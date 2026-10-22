import truststore


def use_os_trust_store() -> None:
    # This network (NetFree) re-signs HTTPS with a CA that only the Windows store
    # trusts, and Python 3.13's strict checks reject it. Verifying through the OS
    # keeps TLS checks on and behaves normally on networks without filtering.
    truststore.inject_into_ssl()
