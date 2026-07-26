import hmac
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.advanced_operations import AdvancedOperationIntent, AdvancedOperationTarget
from app.models.auth import UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, Workload
from app.proxmox.client import ProxmoxClient
from app.schemas.advanced_operations import (
    AdvancedCapabilitiesResponse,
    AdvancedFeature,
    AdvancedFeatureCapability,
    AdvancedInspectionResponse,
    AdvancedOperationCreate,
    AdvancedOperationResponse,
    AdvancedPreviewRequest,
    AdvancedPreviewResponse,
    AdvancedTargetSnapshot,
)
from app.security.access import Principal, require_service_role
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.outbox import (
    ADVANCED_EVENT,
    add_operation_event,
    record_publish_failure,
    record_publish_success,
)

AdvancedPublisher = Callable[[UUID, str], None]
SNAPSHOT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
NODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
BRIDGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")
BOOT_ORDER = re.compile(r"^[A-Za-z0-9,;-]{1,120}$")
FEATURE_ACTIONS: dict[AdvancedFeature, tuple[str, ...]] = {
    AdvancedFeature.SNAPSHOT: ("CREATE", "DELETE", "ROLLBACK"),
    AdvancedFeature.MIGRATION: ("LIVE", "OFFLINE"),
    AdvancedFeature.HA: ("SET_STATE",),
    AdvancedFeature.NODE_MAINTENANCE: ("DRAIN", "ENTER", "EXIT"),
    AdvancedFeature.BULK: ("START", "SHUTDOWN", "STOP", "REBOOT"),
    AdvancedFeature.GUEST_CONFIG: ("APPLY",),
    AdvancedFeature.FIREWALL_SDN: ("INSPECT",),
}
FLAG_FIELDS: dict[AdvancedFeature, str] = {
    AdvancedFeature.SNAPSHOT: "advanced_snapshot_enabled",
    AdvancedFeature.MIGRATION: "advanced_migration_enabled",
    AdvancedFeature.HA: "advanced_ha_enabled",
    AdvancedFeature.NODE_MAINTENANCE: "advanced_node_maintenance_enabled",
    AdvancedFeature.BULK: "advanced_bulk_enabled",
    AdvancedFeature.GUEST_CONFIG: "advanced_guest_config_enabled",
    AdvancedFeature.FIREWALL_SDN: "advanced_firewall_sdn_enabled",
}


class AdvancedOperationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        principal: Principal,
        publisher: AdvancedPublisher,
        request_id: str,
        source_ip: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._principal = principal
        self._publisher = publisher
        self._request_id = request_id
        self._source_ip = source_ip
        self._transport = transport

    def capabilities(self) -> AdvancedCapabilitiesResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        return AdvancedCapabilitiesResponse(
            items=[
                AdvancedFeatureCapability(
                    feature=feature,
                    enabled=self._enabled(feature),
                    mode="READ_ONLY"
                    if feature is AdvancedFeature.FIREWALL_SDN
                    else "EXECUTE",
                    actions=list(actions),
                )
                for feature, actions in FEATURE_ACTIONS.items()
            ]
        )

    async def preview(self, payload: AdvancedPreviewRequest) -> AdvancedPreviewResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        if payload.action not in FEATURE_ACTIONS[payload.feature]:
            raise AppError(422, "ADVANCED_ACTION_UNSUPPORTED", "The action is not supported.")
        targets = await self._targets(payload.workload_ids)
        self._validate_target_count(payload.feature, targets)
        warnings: list[str] = []
        blockers: list[str] = []
        enabled = self._enabled(payload.feature)
        if not enabled:
            blockers.append("FEATURE_DISABLED")
        if payload.feature is AdvancedFeature.FIREWALL_SDN:
            blockers.append("READ_ONLY_FEATURE")
        requested_state = self._validate_options(payload, targets, warnings, blockers)
        confirmation = self._confirmation(payload, targets)
        step_up_action = self._step_up_action(payload, targets)
        return AdvancedPreviewResponse(
            feature=payload.feature,
            action=payload.action,
            enabled=enabled,
            executable=not blockers,
            targets=[self._target_response(item) for item in targets],
            warnings=warnings,
            blockers=blockers,
            required_confirmation=confirmation,
            step_up_action=step_up_action,
            requested_state=requested_state,
        )

    async def inspect(
        self, workload_id: UUID, feature: AdvancedFeature
    ) -> AdvancedInspectionResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        if feature not in {
            AdvancedFeature.SNAPSHOT,
            AdvancedFeature.HA,
            AdvancedFeature.FIREWALL_SDN,
        }:
            raise AppError(422, "ADVANCED_INSPECTION_UNSUPPORTED", "Inspection is unsupported.")
        if not self._enabled(feature):
            raise AppError(404, "FEATURE_DISABLED", "The feature is disabled.")
        workload = (await self._targets([workload_id]))[0]
        async with self._open_client(workload) as client:
            if feature is AdvancedFeature.SNAPSHOT:
                snapshots = await client.get_guest_snapshots(
                    kind=workload.kind, node=workload.node, vmid=workload.vmid
                )
                items = [
                    self._allow(item, {"name", "snaptime", "description", "vmstate", "parent"})
                    for item in snapshots
                    if item.get("name") != "current"
                ]
                return AdvancedInspectionResponse(
                    feature=feature,
                    scope="WORKLOAD",
                    workload_id=workload.id,
                    items=items,
                )
            if feature is AdvancedFeature.HA:
                resources = await client.get_ha_resources()
                resource_id = f"{'vm' if workload.kind == 'QEMU' else 'ct'}:{workload.vmid}"
                items = [
                    self._allow(item, {"sid", "state", "group", "max_restart", "max_relocate"})
                    for item in resources
                    if item.get("sid") == resource_id
                ]
                groups = [
                    self._allow(item, {"group", "nodes", "restricted", "nofailback"})
                    for item in await client.get_ha_groups()
                ]
                return AdvancedInspectionResponse(
                    feature=feature,
                    scope="CLUSTER",
                    workload_id=workload.id,
                    items=items,
                    related={"groups": groups},
                )
            rules = await client.get_guest_firewall_rules(
                kind=workload.kind, node=workload.node, vmid=workload.vmid
            )
            sdn = await client.get_sdn_resources()
            return AdvancedInspectionResponse(
                feature=feature,
                scope="WORKLOAD",
                workload_id=workload.id,
                items=[
                    self._allow(
                        item,
                        {
                            "pos",
                            "type",
                            "action",
                            "proto",
                            "source",
                            "dest",
                            "dport",
                            "enable",
                            "comment",
                        },
                    )
                    for item in rules
                ],
                related={
                    key: [
                        self._allow(item, {"zone", "vnet", "type", "tag", "alias"})
                        for item in value
                    ]
                    for key, value in sdn.items()
                },
            )

    async def create(
        self,
        payload: AdvancedOperationCreate,
        *,
        idempotency_key: str,
    ) -> tuple[AdvancedOperationResponse, bool]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        preview = await self.preview(payload.preview)
        if not preview.executable:
            raise AppError(
                409,
                "ADVANCED_OPERATION_BLOCKED",
                "The advanced operation preview contains blockers.",
                details={"blockers": preview.blockers},
            )
        if not hmac.compare_digest(payload.confirmation, preview.required_confirmation):
            raise AppError(
                422,
                "ADVANCED_CONFIRMATION_MISMATCH",
                "The typed confirmation does not match the preview.",
            )
        key_hash = sha256(idempotency_key.encode()).digest()
        fingerprint = sha256(
            json.dumps(payload.preview.model_dump(mode="json"), sort_keys=True).encode()
        ).digest()
        existing = await self._session.scalar(
            select(Operation).where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The idempotency key was already used for another request.",
                )
            return await self.get(existing.id), False
        workload_ids = [target.workload_id for target in preview.targets]
        conflict = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id.in_(workload_ids),
                Operation.status.in_(
                    [
                        OperationStatus.QUEUED.value,
                        OperationStatus.RUNNING.value,
                        OperationStatus.CANCEL_REQUESTED.value,
                    ]
                ),
            )
        )
        if conflict is not None:
            raise AppError(
                409,
                "OPERATION_CONFLICT",
                "A target already has an active operation.",
                details={"job_id": str(conflict)},
            )
        operation_id = uuid4()
        task_id = str(uuid4())
        first = preview.targets[0]
        operation = Operation(
            id=operation_id,
            operation_type=f"ADVANCED_{preview.feature.value}",
            action=preview.action.lower(),
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=None,
            cluster_id=(await self._targets([first.workload_id]))[0].cluster_id,
            workload_id=first.workload_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            celery_task_id=task_id,
            result={"target_count": len(preview.targets), "feature": preview.feature.value},
            attempt_count=0,
            version=1,
        )
        self._session.add(operation)
        try:
            await self._session.flush([operation])
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "OPERATION_CONFLICT",
                "A duplicate or conflicting operation already exists.",
            ) from exc
        intent = AdvancedOperationIntent(
            operation_id=operation.id,
            feature=preview.feature.value,
            action=preview.action,
            status="QUEUED",
            target_snapshot=[item.model_dump(mode="json") for item in preview.targets],
            options_snapshot=payload.preview.options,
            preview_snapshot={
                "warnings": preview.warnings,
                "blockers": preview.blockers,
                "required_confirmation": preview.required_confirmation,
                "step_up_action": preview.step_up_action,
            },
            requested_state=preview.requested_state,
            observed_state={},
            current_target_index=0,
        )
        self._session.add(intent)
        await self._session.flush([intent])
        self._session.add_all(
            [
                AdvancedOperationTarget(
                    operation_id=operation.id,
                    workload_id=target.workload_id,
                    ordinal=index,
                    active=True,
                )
                for index, target in enumerate(preview.targets)
            ]
        )
        outbox = add_operation_event(self._session, operation, ADVANCED_EVENT)
        add_audit_event(
            self._session,
            action=f"ADVANCED_{preview.feature.value}_{preview.action}_REQUESTED",
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            workload_id=operation.workload_id,
            operation_id=operation.id,
            source_ip=self._source_ip,
            target_type="advanced_operation",
            target_id=operation.id,
            details={"target_count": len(preview.targets)},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "OPERATION_CONFLICT",
                "A duplicate or conflicting operation already exists.",
            ) from exc
        try:
            self._publisher(operation.id, task_id)
        except Exception:
            await record_publish_failure(self._session, outbox, self._settings)
        else:
            await record_publish_success(self._session, outbox)
        return await self.get(operation.id), True

    async def get(self, operation_id: UUID) -> AdvancedOperationResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        row = (
            await self._session.execute(
                select(Operation, AdvancedOperationIntent)
                .join(
                    AdvancedOperationIntent,
                    AdvancedOperationIntent.operation_id == Operation.id,
                )
                .where(Operation.id == operation_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "ADVANCED_OPERATION_NOT_FOUND", "The operation was not found.")
        operation, intent = row
        return AdvancedOperationResponse(
            operation_id=operation.id,
            feature=AdvancedFeature(intent.feature),
            action=intent.action,
            status=operation.status,
            targets=[
                AdvancedTargetSnapshot.model_validate(item)
                for item in intent.target_snapshot
            ],
            requested_state=intent.requested_state,
            observed_state=intent.observed_state,
            error_code=operation.error_code,
        )

    async def _targets(self, workload_ids: list[UUID]) -> list[Workload]:
        items = (
            await self._session.scalars(
                select(Workload)
                .where(
                    Workload.id.in_(workload_ids),
                    Workload.is_present.is_(True),
                    Workload.is_template.is_(False),
                )
                .with_for_update(read=True)
            )
        ).all()
        by_id = {item.id: item for item in items}
        if len(by_id) != len(workload_ids):
            raise AppError(404, "WORKLOAD_NOT_FOUND", "A target workload was not found.")
        ordered = [by_id[item_id] for item_id in workload_ids]
        if len({item.cluster_id for item in ordered}) != 1:
            raise AppError(
                409,
                "CROSS_CLUSTER_OPERATION_UNSUPPORTED",
                "All targets must belong to one cluster.",
            )
        return ordered

    @staticmethod
    def _validate_target_count(
        feature: AdvancedFeature, targets: list[Workload]
    ) -> None:
        if feature not in {
            AdvancedFeature.BULK,
            AdvancedFeature.NODE_MAINTENANCE,
        } and len(targets) != 1:
            raise AppError(422, "ADVANCED_TARGET_COUNT_INVALID", "Select exactly one target.")

    def _validate_options(
        self,
        payload: AdvancedPreviewRequest,
        targets: list[Workload],
        warnings: list[str],
        blockers: list[str],
    ) -> dict[str, object]:
        options = payload.options
        feature = payload.feature
        action = payload.action
        if feature is AdvancedFeature.SNAPSHOT:
            self._only(options, {"snapshot_name", "include_memory"})
            name = self._text(options, "snapshot_name")
            if not SNAPSHOT_NAME.fullmatch(name):
                raise AppError(422, "SNAPSHOT_NAME_INVALID", "The snapshot name is invalid.")
            if action == "CREATE" and bool(options.get("include_memory")):
                warnings.append("MEMORY_SNAPSHOT_REQUIRES_COMPATIBLE_STORAGE")
            return {"snapshot_name": name, "include_memory": bool(options.get("include_memory"))}
        if feature is AdvancedFeature.MIGRATION:
            self._only(
                options,
                {
                    "target_node",
                    "target_storage",
                    "target_network",
                    "local_disks_compatible",
                    "passthrough_free",
                    "ha_compatible",
                    "replication_compatible",
                },
            )
            target_node = self._node(options)
            if target_node == targets[0].node:
                blockers.append("TARGET_NODE_EQUALS_SOURCE")
            for key, blocker in (
                ("local_disks_compatible", "LOCAL_DISK_COMPATIBILITY_UNCONFIRMED"),
                ("passthrough_free", "PASSTHROUGH_COMPATIBILITY_UNCONFIRMED"),
                ("ha_compatible", "HA_COMPATIBILITY_UNCONFIRMED"),
                ("replication_compatible", "REPLICATION_COMPATIBILITY_UNCONFIRMED"),
            ):
                if options.get(key) is not True:
                    blockers.append(blocker)
            if action == "LIVE" and targets[0].power_state.upper() != "RUNNING":
                blockers.append("LIVE_MIGRATION_REQUIRES_RUNNING_GUEST")
            if action == "OFFLINE" and targets[0].power_state.upper() != "STOPPED":
                warnings.append("OFFLINE_MIGRATION_CAUSES_DOWNTIME")
            return {
                "target_node": target_node,
                "target_storage": self._optional_text(options.get("target_storage")),
                "target_network": self._optional_text(options.get("target_network")),
                "online": action == "LIVE",
            }
        if feature is AdvancedFeature.HA:
            self._only(options, {"requested_state", "group"})
            state = self._text(options, "requested_state")
            if state not in {"started", "stopped", "ignored", "disabled"}:
                raise AppError(422, "HA_STATE_INVALID", "The HA state is invalid.")
            warnings.extend(["HA_FENCING_RISK", "HA_QUORUM_REQUIRED"])
            return {"state": state, "group": self._optional_text(options.get("group"))}
        if feature is AdvancedFeature.NODE_MAINTENANCE:
            self._only(
                options,
                {"target_node", "backup_confirmed", "customer_notification_confirmed"},
            )
            if action == "DRAIN":
                target_node = self._node(options)
                if any(item.node == target_node for item in targets):
                    blockers.append("DRAIN_TARGET_EQUALS_SOURCE")
                if len({item.node for item in targets}) != 1:
                    blockers.append("DRAIN_REQUIRES_SINGLE_SOURCE_NODE")
                if options.get("backup_confirmed") is not True:
                    blockers.append("BACKUP_CONFIRMATION_REQUIRED")
                if options.get("customer_notification_confirmed") is not True:
                    blockers.append("CUSTOMER_NOTIFICATION_REQUIRED")
                return {"target_node": target_node, "affected_count": len(targets)}
            if len({item.node for item in targets}) != 1:
                blockers.append("MAINTENANCE_REQUIRES_SINGLE_NODE")
            return {"node": targets[0].node, "maintenance": action == "ENTER"}
        if feature is AdvancedFeature.BULK:
            self._only(options, set())
            if len(targets) > 20:
                warnings.append("BULK_RATE_LIMIT_APPLIES")
            return {"power_action": action.lower(), "target_count": len(targets)}
        if feature is AdvancedFeature.GUEST_CONFIG:
            self._only(
                options,
                {"cores", "memory_mib", "bridge", "vlan_tag", "boot_order", "cloud_init"},
            )
            if not options:
                raise AppError(422, "GUEST_CONFIG_EMPTY", "No configuration was provided.")
            cores = options.get("cores")
            if cores is not None and (
                not isinstance(cores, int) or not 1 <= cores <= 512
            ):
                raise AppError(422, "GUEST_CONFIG_INVALID", "The CPU value is invalid.")
            memory = options.get("memory_mib")
            if memory is not None and (
                not isinstance(memory, int) or not 128 <= memory <= 1024 * 1024
            ):
                raise AppError(422, "GUEST_CONFIG_INVALID", "The memory value is invalid.")
            bridge = options.get("bridge")
            if bridge is not None and (
                not isinstance(bridge, str) or not BRIDGE_NAME.fullmatch(bridge)
            ):
                raise AppError(422, "GUEST_CONFIG_INVALID", "The bridge is invalid.")
            if targets[0].kind == "LXC" and "boot_order" in options:
                blockers.append("LXC_BOOT_ORDER_UNSUPPORTED")
            if "vlan_tag" in options:
                vlan = options["vlan_tag"]
                if not isinstance(vlan, int) or not 1 <= vlan <= 4094:
                    raise AppError(422, "VLAN_TAG_INVALID", "The VLAN tag is invalid.")
            boot_order = options.get("boot_order")
            if boot_order is not None and (
                not isinstance(boot_order, str)
                or not BOOT_ORDER.fullmatch(boot_order)
            ):
                raise AppError(422, "GUEST_CONFIG_INVALID", "The boot order is invalid.")
            cloud_init = options.get("cloud_init")
            if cloud_init is not None:
                if not isinstance(cloud_init, dict) or not set(cloud_init).issubset(
                    {"user", "nameserver", "ipconfig0"}
                ):
                    raise AppError(
                        422,
                        "GUEST_CONFIG_INVALID",
                        "The Cloud-Init configuration is invalid.",
                    )
                if any(
                    not isinstance(value, str) or not value or len(value) > 255
                    for value in cloud_init.values()
                ):
                    raise AppError(
                        422,
                        "GUEST_CONFIG_INVALID",
                        "A Cloud-Init value is invalid.",
                    )
            warnings.append("REBOOT_MAY_BE_REQUIRED")
            return dict(options)
        self._only(options, set())
        return {"scope": "WORKLOAD", "read_only": True}

    @staticmethod
    def _confirmation(
        payload: AdvancedPreviewRequest, targets: list[Workload]
    ) -> str:
        if payload.feature is AdvancedFeature.BULK:
            return f"{len(targets)} TARGETS"
        if payload.feature is AdvancedFeature.NODE_MAINTENANCE:
            return f"{targets[0].node} {payload.action}"
        return f"{targets[0].name or targets[0].vmid} {payload.action}"

    @staticmethod
    def _step_up_action(
        payload: AdvancedPreviewRequest, targets: list[Workload]
    ) -> str | None:
        high_risk = (
            payload.feature is not AdvancedFeature.SNAPSHOT
            or payload.action in {"DELETE", "ROLLBACK"}
            or len(targets) > 10
        )
        return (
            f"advanced:{payload.feature.value.lower()}:{payload.action.lower()}"
            if high_risk
            else None
        )

    @staticmethod
    def _target_response(item: Workload) -> AdvancedTargetSnapshot:
        return AdvancedTargetSnapshot(
            workload_id=item.id,
            name=item.name or str(item.vmid),
            kind=item.kind,
            node=item.node,
            power_state=item.power_state,
            version=item.version,
        )

    def _enabled(self, feature: AdvancedFeature) -> bool:
        return bool(getattr(self._settings, FLAG_FIELDS[feature]))

    @staticmethod
    def _allow(
        item: dict[str, object], allowed: set[str]
    ) -> dict[str, object]:
        return {key: value for key, value in item.items() if key in allowed}

    @asynccontextmanager
    async def _open_client(self, workload: Workload) -> AsyncIterator[ProxmoxClient]:
        cluster = await self._session.get(Cluster, workload.cluster_id)
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == workload.cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None or not cluster.is_active:
            raise AppError(409, "CLUSTER_UNAVAILABLE", "The cluster is unavailable.")
        try:
            token_secret = self._cipher.decrypt(
                EncryptedCredential(
                    ciphertext=credential.secret_ciphertext,
                    nonce=credential.secret_nonce,
                    key_version=credential.key_version,
                ),
                cluster_id=cluster.id,
                credential_id=credential.id,
            )
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise AppError(
                500,
                "CREDENTIAL_DECRYPTION_FAILED",
                "The stored Proxmox credential could not be decrypted.",
            ) from exc
        client = ProxmoxClient(
            api_base_url=cluster.api_base_url,
            token_identifier=credential.token_identifier,
            token_secret=token_secret,
            ca_bundle_pem=cluster.ca_bundle_pem,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
            transport=self._transport,
        )
        async with client:
            yield client

    @staticmethod
    def _only(options: dict[str, object], allowed: set[str]) -> None:
        if not set(options).issubset(allowed):
            raise AppError(
                422,
                "ADVANCED_OPTION_UNSUPPORTED",
                "An unsupported advanced operation option was provided.",
            )

    @staticmethod
    def _text(options: dict[str, object], key: str) -> str:
        value = options.get(key)
        if not isinstance(value, str) or not value:
            raise AppError(422, "ADVANCED_OPTION_INVALID", f"{key} is required.")
        return value

    @classmethod
    def _node(cls, options: dict[str, object]) -> str:
        value = cls._text(options, "target_node")
        if not NODE_NAME.fullmatch(value):
            raise AppError(422, "TARGET_NODE_INVALID", "The target node is invalid.")
        return value

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 255:
            raise AppError(422, "ADVANCED_OPTION_INVALID", "An option value is invalid.")
        return value
