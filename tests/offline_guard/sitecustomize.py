"""Prevent outbound Python connections in release tests, including job subprocesses."""
import ipaddress
import sys


def local_connections_only(event, args):
    if event != "socket.connect" or not isinstance(args[1], tuple):
        return
    host = args[1][0]
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("External network disabled during release acceptance")


sys.addaudithook(local_connections_only)
