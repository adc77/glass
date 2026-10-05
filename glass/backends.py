"""A simulated report store with idempotent writes and an optional lost acknowledgment."""

from seam import Backend, Fault, PortError


def report_store(data, config):
    if type(data) is not dict or type(data.get("filed")) is not dict:
        raise Fault("bad_value")
    filed = data["filed"]
    lose_ack = config.get("lose_first_report_ack", False)
    if type(lose_ack) is not bool:
        raise Fault("bad_value")

    def handle(request):
        nonlocal lose_ack
        key = request["idempotency_key"]
        report = {"sample": request["sample"], "disposition": request["disposition"]}
        if key in filed and filed[key] != report:
            raise Fault("bad_value")
        filed[key] = report
        if lose_ack:
            lose_ack = False
            raise PortError("timeout")
        return {"status": "filed"}

    return Backend(handle, lambda: {"filed": filed})
