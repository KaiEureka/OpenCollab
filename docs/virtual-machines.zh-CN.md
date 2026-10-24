# 现有 Linux 虚拟机

`VMEnvironment` 通过系统 OpenSSH 接入现有 Linux VM。SSH 账号需要 Python 3.10 或更新版本、与控制端相同的 OpenCollab 安装包、Linux child-subreaper 与 pidfd 能力，以及能够完整查看操作后代进程的 `/proc`。连接前先在客户机安装控制端使用的 wheel。辅助程序从已安装的 `opencollab.adapters._vm_remote` 模块启动，命令和文件访问复用 `LocalEnvironment`。

准备已有工作区，以及由 SSH 账号拥有的私有状态目录。操作状态放在工作区之外。每个环境在状态目录内使用一个运行 ID。控制端重启后重建环境时继续使用原运行 ID。

```python
from opencollab.adapters.env import VMEnvironment
from opencollab.adapters.vm_config import VMConnection

connection = VMConnection(
    host="vm.example.net",
    user="worker",
    identity_file="/home/controller/.ssh/vm-key",
    known_hosts_file="/home/controller/.ssh/known_hosts",
)
env = VMEnvironment(
    connection,
    "/srv/work/repository",
    state_directory="/var/tmp/opencollab-operations",
    run_id="experiment-42",
)
await env.setup()
result = await env.exec_operation(
    "build-1",
    "python3 -c 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'",
    input_bytes=b"binary\x00input",
)
assert result.stdout_bytes == b"binary\x00input"
await env.write_file("notes.txt", "A remote edit\n")
page = await env.read_text_range("notes.txt", offset=1, limit=20, max_chars=4000)
await env.cleanup()
```

主机密钥始终采用严格验证。OpenSSH 使用指定的 known-hosts 文件，省略时使用其常规位置。`identity_file` 引用已有密钥。`python` 可以选择客户机虚拟环境中的 Python 可执行文件。连接参数与 VM 资源标识、机器分配分别配置。

命令沿用既有的标准输出与错误输出上限、截断计数、超时提示和二进制 `stdout_bytes`。`input_bytes` 提供二进制标准输入。命令退出码 255 保留为命令结果。SSH 故障抛出 `VMTransportError`，并携带 `transport_returncode` 与 `operation_id`。超时参数为 `None` 或 `math.inf` 时，远端命令可以持续运行。

每项操作都在私有运行目录保存完整请求、操作编号、监督进程身份与最终结果。重试提交时比较原始请求，并继续使用同一操作编号。独立运行的监督进程在命令执行与回收期间持有操作锁，因此 SSH 客户端断线后仍能重新连接原执行。`exec_cmd` 自动生成操作编号，`exec_operation` 接收调用者指定的编号。

恢复 SSH 后调用 `recover_operation(error.operation_id)`。命令操作返回原始 `ExecResult`，文件操作返回原始结果，临时文件恢复还会重建环境持有的文件身份记录。`pending_operations` 列出尚待处理的操作编号。状态不确定时，环境等待恢复结束再接受后续工作。已经开始执行的操作若失去监督进程，会保存为未知状态并保留现场。再次使用这台 VM 前，需要检查客户机和操作记录来处理该状态。

取消首先停止命令进程组，再结束监督进程拥有的后代。Linux subreaper 在子进程调用 `setsid` 或双重 fork 后继续持有其归属，pidfd 将终止信号绑定到已观察到的进程身份。回收失败时保留监督进程与后代记录，以便之后重试。清理还会核对临时文件记录的设备号与 inode，再删除环境拥有的临时文件。环境清理完成后，VM 继续运行。

文件操作复用既有的目录锚点、普通文件检查、工作区身份检查、读写上限和分页读取。`file_write_lock(path)` 在一次完整的读取、修改、写入期间持有客户机文件锁。同一物理工作区内，跨控制进程、跨运行 ID 的编辑会依次执行。文件操作状态不确定时，环境保留编辑锁，直到恢复和回收确定操作已结束。Git 工作区探测与 diff 工具使用环境的常规命令接口。

可选 SSH 回归测试从环境变量 `OPENCOLLAB_TEST_VM_CONFIG` 指定的 JSON 文件读取 `connection`、`workspace` 和 `state_directory`。`connection` 包含 `VMConnection` 的关键字参数。测试在指定工作区下创建任务目录，实际执行命令和文件操作、中断 SSH 客户端、停止守护化后代进程，并检查并发编辑结果。借用的 VM 在测试后继续运行。配置专用测试工作区后，运行 `pytest -q tests/runtime/test_vm_ssh.py`。

工作区适配器可以通过 `copy_to_host(source, target, directory=False)` 与 `copy_from_host(source, destination, directory=False)` 流式传输二进制文件和目录归档，传输大小独立于命令输出上限。客户机源路径和目标路径是工作区适配器明确指定的绝对路径，也可以指向暴露子目录之外的仓库元数据。目录复制将内容传入空目录或新目录，并保留已有工作区的 inode。常规权限、符号链接、空文件，以及目标文件系统支持的文件名会随传输保留。传输时移除 setuid 与 setgid 位。

归档先在目标文件系统内的自有临时目录解包，再通过目录描述符写入目标。父目录跳转、重复路径、硬链接、设备文件，以及经由符号链接父目录写入的情况会触发失败，并保留临时目录。上传分块带有明确结束帧，完整接收后才允许执行持久化导入。上传中断时保留操作编号和部分归档，供检查或清理继续处理。操作记录还保存失败导入的临时目录路径。向已有内容的目录复制时抛出 `FileExistsError`。
