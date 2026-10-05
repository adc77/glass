"""Outcome checks for the copied report-store case."""


def one_filed_report(artifact):
    filed = artifact["backend_states"]["report"]["filed"]
    if len(filed) != 1:
        return ["expected one durable report"]
    if next(iter(filed.values())) != {"sample": "s1", "disposition": "released"}:
        return ["unexpected durable report"]
    return []
