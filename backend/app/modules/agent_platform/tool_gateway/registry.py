from collections.abc import Iterable

from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.tool_gateway.catalog import ToolContract
from app.modules.agent_platform.tool_gateway.errors import (
    DuplicateToolRegistration,
    ToolDisabled,
    ToolNotFound,
)


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


class ToolRegistry:
    def __init__(self, contracts: Iterable[ToolContract] = ()) -> None:
        self._contracts: dict[tuple[str, str], ToolContract] = {}
        for contract in contracts:
            self.register(contract)

    def register(self, contract: ToolContract) -> None:
        definition = contract.definition
        key = (definition.name, definition.version)
        if key in self._contracts:
            raise DuplicateToolRegistration(
                f"duplicate tool registration: {definition.name}@{definition.version}"
            )
        self._contracts[key] = contract

    def resolve(self, name: str, version: str | None = None) -> ToolContract:
        candidates = [
            contract for (tool_name, _), contract in self._contracts.items()
            if tool_name == name
        ]
        if not candidates:
            raise ToolNotFound()
        if version is not None:
            contract = self._contracts.get((name, version))
            if contract is None:
                raise ToolNotFound()
            if not contract.definition.enabled:
                raise ToolDisabled()
            return contract

        enabled = [contract for contract in candidates if contract.definition.enabled]
        if not enabled:
            raise ToolDisabled()
        return max(enabled, key=lambda item: _version_key(item.definition.version))

    def list_allowed(
        self,
        context: UserContext,
        agent_allowlist: Iterable[str],
        *,
        visibility: str = "agent",
    ) -> tuple[ToolContract, ...]:
        allowed_names = set(agent_allowlist)
        permissions = set(context.permissions)
        matches = []
        for contract in self._contracts.values():
            definition = contract.definition
            if (
                definition.enabled
                and definition.name in allowed_names
                and definition.visibility == visibility
                and set(definition.required_permissions) <= permissions
            ):
                matches.append(contract)
        return tuple(sorted(
            matches,
            key=lambda item: (item.definition.name, _version_key(item.definition.version)),
        ))
