"""
Bash Tool - 继承自 Tool 基类的实现

本文件基于 /usr2/TaiyiAgent/ref/claude-code-reverse/results/tools/Bash.tool.yaml 中的input_schema字段
该schema定义了Bash工具的输入参数结构：
- command: 必需参数，要执行的命令字符串
- timeout: 可选参数，超时时间（毫秒，最大600000）
- description: 可选参数，命令的简洁描述（5-10个单词）

本实现包含：
1. 持久化shell会话管理
2. 超时控制和安全验证
3. 并发命令执行支持
4. 错误处理和输出管理
5. 命令验证和安全检查

BashTool使用说明 

在具有可选超时时间的持久 shell 会话中执行给定的 bash 命令，并确保妥善的处理逻辑和安全措施。

在执行命令之前，请遵循以下步骤：

**1. 目录验证：**
- 如果命令将创建新的目录或文件，请先使用 `LS` 工具验证父目录是否存在且位置正确。
- 例如：在运行 `mkdir foo/bar` 之前，先使用 `LS` 检查 `foo` 是否存在且确实是预期的父目录。

**2. 命令执行：**
- 对于包含空格的文件路径，务必使用双引号括起来（例如：`cd "path with spaces/file.txt"`）。
- 正确引用示例：
    - `cd "/Users/name/My Documents"`（正确）
    - `cd /Users/name/My Documents`（错误 - 将导致失败）
    - `python "/path/with spaces/script.py"`（正确）
    - `python /path/with spaces/script.py`（错误 - 将导致失败）
- 在确保引用正确后，执行命令并捕获输出。

**使用注意事项：**
- `command` 参数是必填项。
- 您可以指定可选的超时时间（单位：毫秒，最高 600,000ms / 10 分钟）。如果未指定，默认超时时间为 120,000ms（2 分钟）。
- 请用 5-10 个词简明扼要地描述该命令的作用，这将非常有帮助。
- 如果输出超过 30,000 个字符，返回给您的输出将被截断。
- **非常重要：** 您必须避免使用 `find` 和 `grep` 等搜索命令，而应使用 `Grep`、`Glob` 或 `Task` 工具进行搜索。您必须避免使用 `cat`、`head`、`tail` 和 `ls` 等读取工具，而应使用 `Read` 和 `LS` 工具来读取文件。
- 执行多条命令时，请使用 `;` 或 `&&` 运算符分隔。不要使用换行符（引号字符串内的换行符除外）。
- 尽量通过使用绝对路径和避免使用 `cd` 来保持当前的当前工作目录。除非用户明确要求，否则尽量不使用 `cd`。

# 使用 git 提交更改

当用户要求您创建新的 git 提交（commit）时，请严格遵循以下步骤：

1. **并行运行多个 bash 命令：**
   - 运行 `git status` 命令查看所有未跟踪的文件。
   - 运行 `git diff` 命令查看将要提交的已暂存（staged）和未暂存的更改。
   - 运行 `git log` 命令查看最近的提交记录，以便遵循该仓库的提交信息风格。
2. **分析所有暂存的更改（包括之前暂存的和新添加的）并起草提交信息：**
   - 总结更改的性质（例如：新功能、功能增强、修复 bug、重构、测试、文档等）。
   - 检查是否包含不应提交的敏感信息。
   - 起草一份简洁（1-2 句）的提交信息，侧重于“为什么”改动而非“改了什么”。
3. **并行运行以下命令：**
   - 将相关的未跟踪文件添加到暂存区。
   - 创建提交，提交信息必须以下列后缀结尾：`> Generated with [Claude Code](https://claude.ai/code) Co-Authored-By: Claude <noreply@anthropic.com>`
   - 运行 `git status` 确保提交成功。
4. **如果提交因 pre-commit 钩子修改了文件而失败，请重试提交一次，以包含这些自动生成的更改。**

**重要提示：**
- 严禁更新 git 配置（git config）。
- 除了 git bash 命令外，严禁运行额外的命令来读取或探索代码。
- 严禁使用 `TodoWrite` 或 `Task` 工具。
- 除非用户明确要求，否则不要推送到远程仓库。
- **重要：** 不要使用带有 `-i` 参数的 git 命令（如 `git rebase -i` 或 `git add -i`），因为它们需要交互式输入，而此处不支持交互。
- 如果没有任何更改（即：没有未跟踪文件，也没有修改），请不要创建空提交。
- **务必**通过 **HEREDOC** 传递提交信息，以确保格式正确。

# 创建拉取请求 (Pull Request)

涉及所有 GitHub 相关的任务（包括处理 issue、pull request、checks 和 release）时，请通过 Bash 工具使用 `gh` 命令。

**重要：** 当用户要求您创建拉取请求时，请严格遵循以下步骤：

1. **并行运行 bash 命令以了解分支状态：**
   - 运行 `git status` 查看所有未跟踪文件。
   - 运行 `git diff` 查看将要提交的已暂存和未暂存更改。
   - 检查当前分支是否跟踪了远程分支，以及是否与远程保持同步。
   - 运行 `git log` 和 `git diff [base-branch]...HEAD` 以了解当前分支的完整提交历史。
2. **分析拉取请求中包含的所有更改，查看所有相关提交，并起草拉取请求摘要。**
3. **并行运行以下命令：**
   - 如果需要，创建新分支。
   - 如果需要，使用 `-u` 参数推送到远程仓库。
   - 使用 `gh pr create` 创建 PR，并使用 **HEREDOC** 传递内容以确保格式正确。

**重要：**
- 严禁更新 git 配置。
- 不要使用 `TodoWrite` 或 `Task` 工具。
- 完成后返回 PR 的 URL。



"""

import os
import json
import time
import subprocess
import shlex
import threading
import uuid
import queue
from typing import Optional, Dict, Any, List, Union
from pathlib import Path

from norma.core.tool_types import (

    Tool,
    ParametersSchema,
    ToolSchema,
    ToolRequest,
    ToolRequestResult
)


class BashSession:
    """管理持久化的bash会话"""

    def __init__(self, 
        cwd: str| Path ,
        session_id: str | None = None,
        ):
        self.cwd = str(Path(cwd).resolve())  # 规范化路径
        self.session_id = session_id or str(uuid.uuid4())
        
        self.process = None
        self.output_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.process_lock = threading.Lock()  # 添加线程锁
        self.last_access_time = time.time()  # 记录最后访问时间
        
        self._start_session()

    

    def _start_session(self):
        """启动新的bash会话"""
        with self.process_lock:
            try:
                # 终止旧进程（如果存在）
                if self.process and self.process.poll() is None:
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=2)
                    except:
                        self.process.kill()
                
                self.process = subprocess.Popen(
                    ['/bin/bash'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=0,
                    cwd=self.cwd,
                    preexec_fn=os.setsid  # 创建新进程组，便于终止
                )
                
                # 清空队列
                while not self.output_queue.empty():
                    self.output_queue.get()
                while not self.error_queue.empty():
                    self.error_queue.get()
                
                # 启动输出读取线程
                self._start_readers()
                time.sleep(0.1)
                
            except Exception as e:
                raise RuntimeError(f"Failed to start bash session: {e}")


    def _start_readers(self):
        """启动读取线程来捕获输出"""
        def read_stdout():
            try:
                while self.process and self.process.poll() is None:
                    line = self.process.stdout.readline()
                    if line:
                        self.output_queue.put(('stdout', line))
            except Exception as e:
                self.output_queue.put(('error', f"stdout reader error: {e}"))

        def read_stderr():
            try:
                while self.process and self.process.poll() is None:
                    line = self.process.stderr.readline()
                    if line:
                        self.error_queue.put(('stderr', line))
            except Exception as e:
                self.error_queue.put(('error', f"stderr reader error: {e}"))

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()



    def execute_command(self, command: str, timeout: int = 120000) -> Dict[str, Any]:
        """
        执行bash命令
        
        Args:
            command: 要执行的命令
            timeout: 超时时间（毫秒）
            
        Returns:
            包含执行结果的字典
        """
        self.last_access_time = time.time()  # 更新访问时间
    

        with self.process_lock:
            if not self.process or self.process.poll() is not None:
                try:
                    self._start_session()
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to restart session: {e}",
                        "output": "",
                        "stderr": "",
                        "session_id": self.session_id,
                        "exit_code": None
                    }

        # 清空队列
        while not self.output_queue.empty():
            self.output_queue.get()
        while not self.error_queue.empty():
            self.error_queue.get()

        # 使用唯一标记来判断命令执行完成，并获取exit code
        marker = f"__CMD_DONE_{uuid.uuid4().hex}__"
        exit_code_marker = f"__EXIT_CODE_{uuid.uuid4().hex}__"
        
        # 修改命令以捕获exit code
        full_command = (
            f"{command}\n"
            f"__EXIT_CODE=$?\n"
            f"echo {marker}\n"
            f"echo {marker} >&2\n"
            f"echo {exit_code_marker}$__EXIT_CODE{exit_code_marker}\n"
        )
        
        # 发送命令
        try:
            with self.process_lock:
                if self.process and self.process.poll() is None:
                    self.process.stdin.write(full_command + '\n')
                    self.process.stdin.flush()
                else:
                    return {
                        "success": False,
                        "error": "Process died unexpectedly",
                        "output": "",
                        "stderr": "",
                        "session_id": self.session_id,
                        "exit_code": None
                    }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to send command: {e}",
                "output": "",
                "stderr": "",
                "session_id": self.session_id,
                "exit_code": None
            }

        # 读取输出（使用标记判断完成）
        output, stderr, exit_code, timed_out = self._read_output_with_marker(
            marker, exit_code_marker, timeout / 1000.0
        )

        # 如果超时，终止进程
        if timed_out:
            with self.process_lock:
                if self.process and self.process.poll() is None:
                    try:
                        # 终止整个进程组
                        os.killpg(os.getpgid(self.process.pid), 9)
                    except:
                        try:
                            self.process.kill()
                        except:
                            pass
                    
            # 重启会话
            try:
                self._start_session()
            except:
                pass
            
            return {
                "success": False,
                "error": f"Command timed out after {timeout}ms",
                "output": output,
                "stderr": stderr,
                "session_id": self.session_id,
                "exit_code": None
            }

        # 检查进程状态
        with self.process_lock:
            if self.process and self.process.poll() is not None:
                # 进程已结束，重启会话
                try:
                    self._start_session()
                except:
                    pass

        # 限制输出长度
        max_output_len = 30000
        if len(output) > max_output_len:
            output = output[:max_output_len] + "\n... (output truncated)"
        if len(stderr) > max_output_len:
            stderr = stderr[:max_output_len] + "\n... (stderr truncated)"

        # 判断命令是否成功
        success = exit_code == 0 if exit_code is not None else False

        return {
            "success": success,
            "output": output,
            "stderr": stderr,
            "error": "" if success else f"Command exited with code {exit_code}",
            "session_id": self.session_id,
            "exit_code": exit_code
        }
    def _read_output_with_marker(self, marker: str, 
            exit_code_marker: str,

        timeout: float) -> tuple[str, str, Optional[int], bool]:
        """
        使用标记来读取命令输出，确保命令执行完成
        
        Args:
            marker: 用于标识命令结束的唯一标记
            timeout: 超时时间（秒）
            
        Returns:
            (stdout内容, stderr内容)
        """
        stdout_lines = []
        stderr_lines = []
        stdout_done = False
        stderr_done = False
        exit_code = None
        start_time = time.time()
        timed_out = False

        while time.time() - start_time < timeout:
            # 检查是否都完成
            if stdout_done and stderr_done and exit_code is not None:
                break

            # 读取标准输出
            try:
                while not self.output_queue.empty():
                    stream_type, line = self.output_queue.get_nowait()
                    
                    # 检查exit code标记
                    if exit_code_marker in line:
                        try:
                            code_str = line.split(exit_code_marker)[1]
                            exit_code = int(code_str.strip())
                        except (IndexError, ValueError):
                            pass
                    elif marker in line:
                        stdout_done = True
                    else:
                        stdout_lines.append(line)
            except queue.Empty:
                pass

            # 读取错误输出
            try:
                while not self.error_queue.empty():
                    stream_type, line = self.error_queue.get_nowait()
                    if marker in line:
                        stderr_done = True
                    else:
                        stderr_lines.append(line)
            except queue.Empty:
                pass

            # 短暂休眠避免CPU占用过高
            time.sleep(0.01)
        
        # 检查是否超时
        if time.time() - start_time >= timeout:
            timed_out = True

        return ''.join(stdout_lines), ''.join(stderr_lines), exit_code, timed_out


    
    def restart(self):
        """重启bash会话"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass
        
        self._start_session()
        return {
            "success": True,
            "message": f"Bash session {self.session_id} restarted",
            "session_id": self.session_id
        }

    def kill(self):
        """终止bash会话"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass
        
        return {
            "success": True,
            "message": f"Bash session {self.session_id} terminated",
            "session_id": self.session_id
        }

    def __del__(self):
        """析构函数，确保进程被清理"""
        self.kill()

    
class BashTool(Tool):
    """Bash工具类 - 继承自Tool基类"""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return """
        
BashTool使用说明 

在具有可选超时时间的持久 shell 会话中执行给定的 bash 命令，并确保妥善的处理逻辑和安全措施。

在执行命令之前，请遵循以下步骤：

**1. 目录验证：**
- 如果命令将创建新的目录或文件，请先使用 `LS` 工具验证父目录是否存在且位置正确。
- 例如：在运行 `mkdir foo/bar` 之前，先使用 `LS` 检查 `foo` 是否存在且确实是预期的父目录。

**2. 命令执行：**
- 对于包含空格的文件路径，务必使用双引号括起来（例如：`cd "path with spaces/file.txt"`）。
- 正确引用示例：
    - `cd "/Users/name/My Documents"`（正确）
    - `cd /Users/name/My Documents`（错误 - 将导致失败）
    - `python "/path/with spaces/script.py"`（正确）
    - `python /path/with spaces/script.py`（错误 - 将导致失败）
- 在确保引用正确后，执行命令并捕获输出。

**使用注意事项：**
- `command` 参数是必填项。
- 您可以指定可选的超时时间（单位：毫秒，最高 600,000ms / 10 分钟）。如果未指定，默认超时时间为 120,000ms（2 分钟）。
- 请用 5-10 个词简明扼要地描述该命令的作用，这将非常有帮助。
- 如果输出超过 30,000 个字符，返回给您的输出将被截断。
- **非常重要：** 您必须避免使用 `find` 和 `grep` 等搜索命令，而应使用 `Grep`、`Glob` 或 `Task` 工具进行搜索。您必须避免使用 `cat`、`head`、`tail` 和 `ls` 等读取工具，而应使用 `Read` 和 `LS` 工具来读取文件。
- 执行多条命令时，请使用 `;` 或 `&&` 运算符分隔。不要使用换行符（引号字符串内的换行符除外）。
- 尽量通过使用绝对路径和避免使用 `cd` 来保持当前的当前工作目录。除非用户明确要求，否则尽量不使用 `cd`。

# 使用 git 提交更改

当用户要求您创建新的 git 提交（commit）时，请严格遵循以下步骤：

1. **并行运行多个 bash 命令：**
   - 运行 `git status` 命令查看所有未跟踪的文件。
   - 运行 `git diff` 命令查看将要提交的已暂存（staged）和未暂存的更改。
   - 运行 `git log` 命令查看最近的提交记录，以便遵循该仓库的提交信息风格。
2. **分析所有暂存的更改（包括之前暂存的和新添加的）并起草提交信息：**
   - 总结更改的性质（例如：新功能、功能增强、修复 bug、重构、测试、文档等）。
   - 检查是否包含不应提交的敏感信息。
   - 起草一份简洁（1-2 句）的提交信息，侧重于“为什么”改动而非“改了什么”。
3. **并行运行以下命令：**
   - 将相关的未跟踪文件添加到暂存区。
   - 创建提交，提交信息必须以下列后缀结尾：`> Generated with [Claude Code](https://claude.ai/code) Co-Authored-By: Claude <noreply@anthropic.com>`
   - 运行 `git status` 确保提交成功。
4. **如果提交因 pre-commit 钩子修改了文件而失败，请重试提交一次，以包含这些自动生成的更改。**

**重要提示：**
- 严禁更新 git 配置（git config）。
- 除了 git bash 命令外，严禁运行额外的命令来读取或探索代码。
- 严禁使用 `TodoWrite` 或 `Task` 工具。
- 除非用户明确要求，否则不要推送到远程仓库。
- **重要：** 不要使用带有 `-i` 参数的 git 命令（如 `git rebase -i` 或 `git add -i`），因为它们需要交互式输入，而此处不支持交互。
- 如果没有任何更改（即：没有未跟踪文件，也没有修改），请不要创建空提交。
- **务必**通过 **HEREDOC** 传递提交信息，以确保格式正确。

# 创建拉取请求 (Pull Request)

涉及所有 GitHub 相关的任务（包括处理 issue、pull request、checks 和 release）时，请通过 Bash 工具使用 `gh` 命令。

**重要：** 当用户要求您创建拉取请求时，请严格遵循以下步骤：

1. **并行运行 bash 命令以了解分支状态：**
   - 运行 `git status` 查看所有未跟踪文件。
   - 运行 `git diff` 查看将要提交的已暂存和未暂存更改。
   - 检查当前分支是否跟踪了远程分支，以及是否与远程保持同步。
   - 运行 `git log` 和 `git diff [base-branch]...HEAD` 以了解当前分支的完整提交历史。
2. **分析拉取请求中包含的所有更改，查看所有相关提交，并起草拉取请求摘要。**
3. **并行运行以下命令：**
   - 如果需要，创建新分支。
   - 如果需要，使用 `-u` 参数推送到远程仓库。
   - 使用 `gh pr create` 创建 PR，并使用 **HEREDOC** 传递内容以确保格式正确。

**重要：**
- 严禁更新 git 配置。
- 不要使用 `TodoWrite` 或 `Task` 工具。
- 完成后返回 PR 的 URL。
        
        """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in milliseconds (max 600000)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of the command (5-10 words)"
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session ID for persistent bash session"
                    }
                },
                required=["command"]
            ),
            strict=False
        )

    def __init__(self, cwd: str| Path):
        """
        初始化 BashTool
        
        Args:
            default_cwd: 默认工作目录，如果不提供则使用当前目录
        """
        self.sessions: Dict[str, BashSession] = {}
        self.cwd = cwd
        self.sessions_lock = threading.Lock()  # 保护sessions字典
        
        # 启动会话清理线程
    



    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Bash工具
        
        ToolRequestResult: 
            request: ToolRequest
            result: Any     # 工具的执行结果
            content: str    # 工具返回结果中最终使用的内容, 默认为 result 的string 格式
            is_error: bool = False
            execution_times: float = 0.0
        """
        start_time = time.time()
        
        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            try:
                call_args = json.loads(tool_request.tool_call_arguments)
            except json.JSONDecodeError as e:
                duration = time.time() - start_time
                return ToolRequestResult(
                    request=tool_request,
                    result={"error": f"Invalid JSON arguments: {str(e)}"},
                    content=json.dumps({"error": f"Invalid JSON arguments: {str(e)}"}),
                    is_error=True,
                    execution_times=duration
                )
        else:
            call_args = tool_request.tool_call_arguments

        # 验证必需参数
        if not call_args.get("command"):
            duration = time.time() - start_time
            return ToolRequestResult(
                request=tool_request,
                result={"error": "command is required"},
                content=json.dumps({"error": "command is required"}),
                is_error=True,
                execution_times=duration
            )

        # 提取参数
        command = call_args.get("command")
        timeout = call_args.get("timeout")
        description = call_args.get("description")
        session_id = call_args.get("session_id")

        # 执行命令
        return self._execute_command(
            command=command,
            timeout=timeout,
            description=description,
            session_id=session_id,
            tool_request=tool_request,
            start_time=start_time
        )


    def _execute_command(
        self, 
        command: str, 
        timeout: Optional[int] = None,
        description: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_request: Optional[ToolRequest] = None,
        start_time: Optional[float] = None
    ) -> ToolRequestResult:
        """执行bash命令的核心逻辑"""
        
        if start_time is None:
            start_time = time.time()
        
        # 设置默认超时
        if timeout is None:
            timeout = 120000
        elif timeout > 600000:
            timeout = 600000

        # 获取或创建会话
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
        else:
            session = BashSession(cwd=self.cwd, session_id=session_id)
            self.sessions[session.session_id] = session

        # 执行命令
        result = session.execute_command(command, timeout)
        
        # 计算执行时间
        duration = time.time() - start_time
        
        # 构造返回结果
        is_error = not result.get("success", False)
        
        # 生成内容字符串
        if is_error:
            content = json.dumps({
                "error": result.get("error", "Unknown error"),
                "session_id": result.get("session_id")
            })
        else:
            content_dict = {
                "output": result.get("output", ""),
                "stderr": result.get("stderr", ""),
                "session_id": result.get("session_id"),
                "exit_code": result.get("exit_code")
            }
            content = json.dumps(content_dict, ensure_ascii=False)
        
        return ToolRequestResult(
            request=tool_request,
            result=result,
            content=content,
            is_error=is_error,
            execution_times=duration
        )


    def cleanup_session(self, session_id: str) -> Dict[str, Any]:
        """手动清理指定会话"""
        with self.sessions_lock:
            if session_id in self.sessions:
                result = self.sessions[session_id].kill()
                del self.sessions[session_id]
                return result
            else:
                return {
                    "success": False,
                    "error": f"Session {session_id} not found"
                }

    def cleanup_all_sessions(self) -> Dict[str, Any]:
        """清理所有会话"""
        with self.sessions_lock:
            count = len(self.sessions)
            for session in self.sessions.values():
                session.kill()
            self.sessions.clear()
            return {
                "success": True,
                "message": f"Cleaned up {count} sessions"
            }
