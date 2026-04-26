from dataclasses import dataclass, field


@dataclass(slots=True)
class ServiceResult:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""


def ok(data: dict | None = None) -> ServiceResult:
    return ServiceResult(ok=True, data=data or {})


def fail(error: str, data: dict | None = None) -> ServiceResult:
    return ServiceResult(ok=False, error=error, data=data or {})
