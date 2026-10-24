from __future__ import annotations

from dataclasses import dataclass

from opencollab.domain.vm import VMConnection, validate_vm_path


@dataclass(frozen=True)
class VMResource:
    resource_id: str
    connection: VMConnection
    workspace_root: str = "/workspace/opencollab"
    state_directory: str = "/tmp/opencollab-vm"
    reset_command: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.resource_id, str) or not self.resource_id.strip()
            or any(ord(character) < 32 for character in self.resource_id)
        ):
            raise ValueError("VM resource_id must be a stable nonempty identifier without control characters")
        if not isinstance(self.connection, VMConnection):
            raise TypeError("VM resource connection must be a VMConnection")
        validate_vm_path(self.workspace_root)
        validate_vm_path(self.state_directory)
        if self.state_directory == self.workspace_root or self.state_directory.startswith(self.workspace_root + "/"):
            raise ValueError("VM operation state must be outside its workspace root")
        if self.reset_command is not None and (
            not isinstance(self.reset_command, str) or not self.reset_command.strip() or "\0" in self.reset_command
        ):
            raise ValueError("VM reset_command must be a nonempty shell command")
