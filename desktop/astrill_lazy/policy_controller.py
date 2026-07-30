from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_package import PolicyLayerPlan, plan_balanced_policy
from .host_key import inspect_host_key
from .router import RouterClient
from .windows_controller import ControllerError, WindowsController
from .windows_ssh_setup import WindowsHostKey


@dataclass(frozen=True)
class BalancedDeploymentResult:
    status: dict[str, Any]
    plan: PolicyLayerPlan
    core_action: str
    overlay_action: str
    restore_enabled: bool


class PolicyController(WindowsController):
    """Platform-neutral hybrid policy controller.

    The policy transaction implementation remains shared with the native
    Windows frontend. Only host-key discovery differs on Unix-like systems.
    """

    def inspect_router_host_key(self) -> WindowsHostKey:
        if self.store.router_use_ssh_config:
            raise ControllerError(
                "hybrid deployment binding requires explicit host, user, port, "
                "identity, and known_hosts settings"
            )
        return inspect_host_key(
            self.store.router_host,
            self.store.router_port,
            known_hosts_path=self.known_hosts_path,
        )

    def bind_trusted_router(
        self,
        host_key: WindowsHostKey,
    ) -> RouterClient:
        if (
            host_key.host != self.store.router_host
            or host_key.port != self.store.router_port
            or host_key.known_hosts_path != self.known_hosts_path
            or host_key.trust_state != "trusted"
        ):
            raise ControllerError(
                "layered policy requires the trusted key for the configured "
                "router endpoint"
            )
        self.router = super()._router_client_from_store()
        return self.router

    def deploy_balanced_policy(
        self,
        *,
        source: str = "auto",
    ) -> BalancedDeploymentResult:
        """Deploy the conservative global core plus this computer's overlay."""

        self._require_companion_write("deploying the balanced policy")
        plan = plan_balanced_policy(self.store, self.catalog)
        if not plan.overlay_rule_ids:
            raise ControllerError(
                "balanced policy has no destination rules for a RAM overlay"
            )
        host_key = self.inspect_router_host_key()
        if host_key.trust_state != "trusted":
            raise ControllerError(
                "trust the inspected router SSH host key in this app before "
                "deploying hybrid policy storage"
            )
        self.bind_trusted_router(host_key)
        status = self.router.effective_status()
        self.configure_policy_deployment(
            core_rule_ids=plan.core_rule_ids,
            overlay_rule_ids=plan.overlay_rule_ids,
            source=source,
            restore_overlay_after_reboot=False,
            status=status,
            host_key=host_key,
        )
        comparison = self.hybrid_policy_status(status)
        if comparison.core_matches is True:
            core_action = "current"
            current = status
        else:
            current = self.apply_persistent_core()
            core_action = "applied"

        comparison = self.hybrid_policy_status(current)
        if comparison.overlay_matches is True:
            overlay_action = "current"
        else:
            current = self.load_ram_overlay(plan.overlay_rule_ids, source)
            overlay_action = "loaded"
        manifest = self.set_overlay_restore_enabled(
            True,
            source,
            status=current,
        )
        return BalancedDeploymentResult(
            status=current,
            plan=plan,
            core_action=core_action,
            overlay_action=overlay_action,
            restore_enabled=manifest.restore_overlay_after_reboot,
        )
