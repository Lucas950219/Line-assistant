import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent


class CodexError(RuntimeError):
    pass


def find_codex(override: str | None) -> str:
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise CodexError("找不到 CODEX_BIN 指定的檔案。請填入 Codex CLI 執行檔的完整路徑。")

    if os.name == "nt":
        extensions = Path.home() / ".vscode" / "extensions"
        candidates = list(extensions.glob("openai.chatgpt-*/bin/windows-*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))

    executable = shutil.which("codex")
    if executable:
        return executable
    raise CodexError("找不到 Codex CLI。請安裝 VS Code 的 Codex 擴充套件，或設定 CODEX_BIN。")


def ask_codex(executable: str, model: str | None, message: str, *, output_schema: Path | None = None) -> str:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("LINE_")
        and name.upper() not in {"OPENAI_API_KEY", "CODEX_API_KEY"}
    }

    workdir = ROOT / ".runtime"
    instructions_path = json.dumps((ROOT / "assistant.txt").as_posix(), ensure_ascii=False)
    command = [
        executable, "exec", "--ignore-user-config", "--ephemeral",
        "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
        "-c", 'approval_policy="never"',
        "-c", 'web_search="disabled"',
        "-c", 'model_reasoning_effort="low"',
        "-c", "project_doc_max_bytes=0",
        "-c", "agents.enabled=false",
        "-c", f"model_instructions_file={instructions_path}",
        "--disable", "shell_tool", "--disable", "view_image",
        "--disable", "apps", "--disable", "plugins", "--disable", "hooks",
        "--disable", "memories", "--disable", "browser_use", "--disable", "computer_use",
    ]
    if model:
        command.extend(["--model", model])
    if output_schema is not None:
        command.extend(["--output-schema", str(output_schema.resolve())])
    command.append("-")

    try:
        workdir.mkdir(exist_ok=True)
        result = subprocess.run(
            command,
            cwd=workdir,
            input=message.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=40,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexError("Codex 超過 40 秒回覆期限。") from exc
    except OSError as exc:
        raise CodexError("無法建立 Codex 工作目錄或執行 Codex CLI。") from exc

    if result.returncode:
        diagnostics = result.stderr.lower()
        if any(term in diagnostics for term in (b"dns", b"failed to connect", b"stream disconnected")):
            raise CodexError("Codex 無法連線到 OpenAI。請確認電腦可以上網並重新測試。")
        raise CodexError(f"Codex 執行失敗（代碼 {result.returncode}）。請檢查 codex login status 與 CODEX_MODEL。")

    answer = result.stdout.decode("utf-8", errors="replace").strip()
    if not answer:
        raise CodexError("Codex 回傳空白內容。")
    return answer
