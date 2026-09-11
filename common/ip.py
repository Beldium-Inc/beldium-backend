from rest_framework.settings import api_settings


def client_ip(request):
    """The caller's address, honouring only as many X-Forwarded-For hops as we actually run.

    X-Forwarded-For is appended to by every hop, so the *right-hand* entries are
    the ones our own proxies wrote and the left-hand ones are whatever the client
    sent. Trusting the leftmost entry — which is both DRF's default when
    NUM_PROXIES is unset and the obvious reading of the header — lets any caller
    name their own address, which forges the audit trail and hands every
    IP-keyed throttle an unlimited supply of fresh buckets.

    NUM_PROXIES is the number of proxies in front of this service, so it is the
    count of trailing entries we wrote ourselves. Absent that setting we trust
    the header not at all and use the peer address.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    remote_addr = request.META.get("REMOTE_ADDR")
    num_proxies = api_settings.NUM_PROXIES

    if not num_proxies or not forwarded:
        return remote_addr

    addresses = [address.strip() for address in forwarded.split(",") if address.strip()]
    if not addresses:
        return remote_addr
    return addresses[-min(num_proxies, len(addresses))]
