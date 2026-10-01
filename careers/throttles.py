from accounts.throttles import IPRateThrottle


class CareersSubmitThrottle(IPRateThrottle):
    scope = "careers_submit"


class CareersUploadThrottle(IPRateThrottle):
    scope = "careers_upload"


class CareersStatusThrottle(IPRateThrottle):
    scope = "careers_status"
