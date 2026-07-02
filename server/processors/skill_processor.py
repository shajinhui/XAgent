"""WebSocket Skills 控制请求处理器。"""

from __future__ import annotations

from typing import Any, Dict

from server.protocol.events import build_event
from server.runtime.websocket_context import WebSocketRuntimeContext
from skills.dependencies import dependency_status
from skills.management import SkillManagementError


class SkillRequestProcessor:
    """处理不会进入模型回合的 skill 管理请求。"""

    HANDLED_TYPES = {
        "list_skills",
        "list_installable_skills",
        "get_skill",
        "create_skill",
        "import_skill",
        "install_skill",
        "install_registry_skill",
        "reinstall_skill",
        "update_skill",
        "delete_skill",
        "list_skill_resources",
        "get_skill_resource",
        "save_skill_resource",
        "delete_skill_resource",
    }

    def __init__(self, ws: Any, context: WebSocketRuntimeContext) -> None:
        self.ws = ws
        self.context = context

    def can_handle(self, packet_type: Any) -> bool:
        return isinstance(packet_type, str) and packet_type in self.HANDLED_TYPES

    async def handle(self, packet: Dict[str, Any]) -> None:
        packet_type = packet.get("type")
        if packet_type == "list_skills":
            await self._handle_list_skills(packet)
        elif packet_type == "list_installable_skills":
            await self._handle_list_installable_skills(packet)
        elif packet_type == "get_skill":
            await self._handle_get_skill(packet)
        elif packet_type == "create_skill":
            await self._handle_create_skill(packet)
        elif packet_type == "import_skill":
            await self._handle_import_skill(packet)
        elif packet_type == "install_skill":
            await self._handle_install_skill(packet)
        elif packet_type == "install_registry_skill":
            await self._handle_install_registry_skill(packet)
        elif packet_type == "reinstall_skill":
            await self._handle_reinstall_skill(packet)
        elif packet_type == "update_skill":
            await self._handle_update_skill(packet)
        elif packet_type == "delete_skill":
            await self._handle_delete_skill(packet)
        elif packet_type == "list_skill_resources":
            await self._handle_list_skill_resources(packet)
        elif packet_type == "get_skill_resource":
            await self._handle_get_skill_resource(packet)
        elif packet_type == "save_skill_resource":
            await self._handle_save_skill_resource(packet)
        elif packet_type == "delete_skill_resource":
            await self._handle_delete_skill_resource(packet)

    async def _handle_list_skills(self, packet: Dict[str, Any]) -> None:
        """返回当前 workspace/current_dir 可见的 skills catalog。"""

        payload = self.context.list_skills(force_reload=bool(packet.get("force_reload")))
        await self.ws.send_json(
            build_event(
                "skills_listed",
                self.context.session_id,
                _turn_id(packet),
                request_id=_request_id(packet),
                skills=self._annotate_skill_payloads(payload["skills"]),
                errors=payload["errors"],
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_list_installable_skills(self, packet: Dict[str, Any]) -> None:
        """返回 curated registry 中可安装的 skills。"""

        request_id = _request_id(packet)
        installable_skills, outcome = self.context.skill_manager.list_installable_skill_payloads(
            self.context.workspace,
            force_reload=bool(packet.get("force_reload"))
        )
        await self.ws.send_json(
            build_event(
                "installable_skills_listed",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                installable_skills=self._annotate_skill_payloads(installable_skills),
                errors=[error.as_dict() for error in outcome.errors],
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_get_skill(self, packet: Dict[str, Any]) -> None:
        """读取一个 skill 的可编辑正文。"""

        request_id = _request_id(packet)
        try:
            skill, content = self.context.skill_manager.read_skill_for_management(
                self.context.workspace,
                path=str(packet.get("path") or ""),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self.ws.send_json(
            build_event(
                "skill_loaded",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                skill=self._annotate_skill_payload(skill),
                content=content,
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_create_skill(self, packet: Dict[str, Any]) -> None:
        """创建 repo/user skill，并返回刷新后的 catalog。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.create_skill(
                self.context.workspace,
                scope=str(packet.get("scope") or "repo"),
                name=packet.get("name"),
                description=packet.get("description"),
                short_description=packet.get("short_description"),
                icon=packet.get("icon"),
                allow_implicit_invocation=packet.get("allow_implicit_invocation", True),
                content=packet.get("content"),
                package_template=packet.get("package_template"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="create",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_import_skill(self, packet: Dict[str, Any]) -> None:
        """从已授权源路径导入 repo/user skill。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.import_skill(
                self.context.workspace,
                scope=str(packet.get("scope") or "repo"),
                source_path=packet.get("source_path"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="import",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_install_skill(self, packet: Dict[str, Any]) -> None:
        """从支持的安装源安装 repo/user skill。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.install_skill(
                self.context.workspace,
                scope=str(packet.get("scope") or "user"),
                source_type=packet.get("source_type") or "github",
                source=packet.get("source"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="install",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_install_registry_skill(self, packet: Dict[str, Any]) -> None:
        """从 curated registry 安装 repo/user skill。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.install_registry_skill(
                self.context.workspace,
                scope=str(packet.get("scope") or "user"),
                registry_id=packet.get("id") or packet.get("name"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="install_registry",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_reinstall_skill(self, packet: Dict[str, Any]) -> None:
        """按安装来源重新安装 repo/user skill。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.reinstall_skill(
                self.context.workspace,
                path=str(packet.get("path") or ""),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="reinstall",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_update_skill(self, packet: Dict[str, Any]) -> None:
        """更新 repo/user skill，并返回刷新后的 catalog。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.update_skill(
                self.context.workspace,
                path=str(packet.get("path") or ""),
                name=packet.get("name"),
                description=packet.get("description"),
                short_description=packet.get("short_description"),
                icon=packet.get("icon"),
                allow_implicit_invocation=packet.get("allow_implicit_invocation", True),
                content=packet.get("content"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_saved",
            action="update",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_delete_skill(self, packet: Dict[str, Any]) -> None:
        """删除 repo/user skill，并返回刷新后的 catalog。"""

        request_id = _request_id(packet)
        try:
            skill, outcome = self.context.skill_manager.delete_skill(
                self.context.workspace,
                path=str(packet.get("path") or ""),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_catalog_mutation(
            packet,
            request_id=request_id,
            event_type="skill_deleted",
            action="delete",
            skill=skill,
            outcome=outcome,
        )

    async def _handle_list_skill_resources(self, packet: Dict[str, Any]) -> None:
        """列出 repo/user skill 的标准资源文件。"""

        request_id = _request_id(packet)
        try:
            skill, resources = self.context.skill_manager.list_skill_resources(
                self.context.workspace,
                path=str(packet.get("path") or ""),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_resource_event(
            packet,
            request_id=request_id,
            event_type="skill_resources_listed",
            skill=skill,
            resources=resources,
        )

    async def _handle_get_skill_resource(self, packet: Dict[str, Any]) -> None:
        """读取 repo/user skill 的一个标准资源文件。"""

        request_id = _request_id(packet)
        try:
            skill, resource, content = self.context.skill_manager.read_skill_resource_for_management(
                self.context.workspace,
                path=str(packet.get("path") or ""),
                resource=packet.get("resource"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_resource_event(
            packet,
            request_id=request_id,
            event_type="skill_resource_loaded",
            skill=skill,
            resource=resource,
            content=content,
        )

    async def _handle_save_skill_resource(self, packet: Dict[str, Any]) -> None:
        """写入 repo/user skill 的一个标准资源文件。"""

        request_id = _request_id(packet)
        try:
            skill, resource, resources = self.context.skill_manager.write_skill_resource(
                self.context.workspace,
                path=str(packet.get("path") or ""),
                resource=packet.get("resource"),
                content=packet.get("content"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_resource_event(
            packet,
            request_id=request_id,
            event_type="skill_resource_saved",
            skill=skill,
            resource=resource,
            resources=resources,
        )

    async def _handle_delete_skill_resource(self, packet: Dict[str, Any]) -> None:
        """删除 repo/user skill 的一个标准资源文件。"""

        request_id = _request_id(packet)
        try:
            skill, resource, resources = self.context.skill_manager.delete_skill_resource(
                self.context.workspace,
                path=str(packet.get("path") or ""),
                resource=packet.get("resource"),
            )
        except SkillManagementError as exc:
            await self._send_skill_error(packet, request_id=request_id, message=str(exc))
            return

        await self._send_skill_resource_event(
            packet,
            request_id=request_id,
            event_type="skill_resource_deleted",
            skill=skill,
            resource=resource,
            resources=resources,
        )

    async def _send_skill_catalog_mutation(
        self,
        packet: Dict[str, Any],
        *,
        request_id: str | None,
        event_type: str,
        action: str,
        skill: Dict[str, Any],
        outcome: Any,
    ) -> None:
        """发送 skill 管理后的 catalog 快照。"""

        await self.ws.send_json(
            build_event(
                event_type,
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                action=action,
                skill=self._annotate_skill_payload(skill),
                skills=self._annotate_skill_payloads([item.as_dict() for item in outcome.skills]),
                errors=[error.as_dict() for error in outcome.errors],
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _send_skill_error(
        self,
        packet: Dict[str, Any],
        *,
        request_id: str | None,
        message: str,
    ) -> None:
        """发送 skill 管理错误。"""

        await self.ws.send_json(
            build_event(
                "skill_error",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                message=message,
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _send_skill_resource_event(
        self,
        packet: Dict[str, Any],
        *,
        request_id: str | None,
        event_type: str,
        skill: Dict[str, Any],
        resource: Dict[str, Any] | None = None,
        resources: list[Dict[str, Any]] | None = None,
        content: str | None = None,
    ) -> None:
        """发送 skill 资源管理事件。"""

        payload: Dict[str, Any] = {
            "request_id": request_id,
            "skill": self._annotate_skill_payload(skill),
            "workspace": self.context.workspace.as_dict(),
        }
        if resource is not None:
            payload["resource"] = resource
        if resources is not None:
            payload["resources"] = resources
        if content is not None:
            payload["content"] = content
        await self.ws.send_json(
            build_event(
                event_type,
                self.context.session_id,
                _turn_id(packet),
                **payload,
            )
        )

    def _annotate_skill_payloads(self, skills: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        """给 skill payload 增加只展示的依赖状态。"""

        return [self._annotate_skill_payload(skill) for skill in skills]

    def _annotate_skill_payload(self, skill: Dict[str, Any]) -> Dict[str, Any]:
        """按当前工具目录检查 dependencies.tools。"""

        status = dependency_status(skill.get("dependencies"), set(self.context.registry.metadata().keys()))
        if status is None:
            return skill
        annotated = dict(skill)
        annotated["dependency_status"] = status
        return annotated


def _request_id(packet: Dict[str, Any]) -> str | None:
    request_id = packet.get("request_id")
    if request_id is None:
        return None
    return str(request_id)


def _turn_id(packet: Dict[str, Any], fallback: str = "system") -> str:
    return str(packet.get("turn_id") or fallback)
