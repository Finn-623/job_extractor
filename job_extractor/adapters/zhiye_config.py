from dataclasses import dataclass


@dataclass(frozen=True)
class ZhiyeTenantConfig:
    hostname: str
    company: str | None = None


@dataclass(frozen=True)
class ZhiyeScope:
    business_type: str | None
    categories: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.business_type or "unknown"


TENANT_CONFIGS: dict[str, ZhiyeTenantConfig] = {
    "leapmotor1.zhiye.com": ZhiyeTenantConfig(
        hostname="leapmotor1.zhiye.com",
        company="零跑汽车",
    ),
}


def get_tenant_config(hostname: str) -> ZhiyeTenantConfig:
    normalized = hostname.lower()
    return TENANT_CONFIGS.get(normalized, ZhiyeTenantConfig(hostname=normalized))
