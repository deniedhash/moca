"""
MOCA CodeExecutor — Phase 4 Agentic Loop
Sandboxed Python execution, web search, integration registry.
All execution capability lives here — the model is purely a reasoning layer.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from logging.handlers import RotatingFileHandler


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Paths — auto-detect: MOCA_ROOT env var → or project root
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOCA_ROOT = os.getenv("MOCA_ROOT", _PROJECT_ROOT)

WORKSPACE = os.path.join(MOCA_ROOT, "workspace")
INTEGRATIONS = os.path.join(MOCA_ROOT, "integrations")
LOGS_DIR = os.path.join(MOCA_ROOT, "logs")
REGISTRY_FILE = os.path.join(INTEGRATIONS, "registry.json")

# Ensure directories exist
for d in [WORKSPACE, INTEGRATIONS, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

executor_logger = logging.getLogger("moca.executor")
executor_logger.setLevel(logging.DEBUG)

_handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, "executor.log"),
    maxBytes=50 * 1024 * 1024,  # 50MB
    backupCount=5,
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
executor_logger.addHandler(_handler)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Safety
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_env_path = os.path.join(MOCA_ROOT, ".env")
HARD_BLOCKS = [
    f"open('{_env_path}')",
    f'open("{_env_path}")',
    "os.environ",
    "shutil.rmtree",
    "/etc/passwd",
    "/etc/shadow",
]

IRREVERSIBLE_KEYWORDS = [
    "delete", "remove", "drop", "send",
    "post", "publish", "payment", "rmdir",
]

BLOCKED_PACKAGES = [
    "keylogger", "pynput", "pyautogui",
]


def safety_scan(code: str) -> dict:
    """
    Scan code for hard-blocked patterns.
    Returns {"passed": bool, "reason": str or None}
    """
    for pattern in HARD_BLOCKS:
        if pattern in code:
            return {"passed": False, "reason": f"Hard-blocked pattern: {pattern}"}
    return {"passed": True, "reason": None}


def reversibility_check(code: str) -> dict:
    """
    Check if code contains irreversible operations.
    Returns {"reversible": bool, "requires_confirm": bool}
    """
    code_lower = code.lower()
    for keyword in IRREVERSIBLE_KEYWORDS:
        if keyword in code_lower:
            return {"reversible": False, "requires_confirm": True}
    return {"reversible": True, "requires_confirm": False}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CodeExecutor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CodeExecutor:

    def __init__(self):
        self._ensure_registry()

    def _ensure_registry(self):
        """Create registry.json if it doesn't exist."""
        if not os.path.exists(REGISTRY_FILE):
            with open(REGISTRY_FILE, "w") as f:
                json.dump({"tools": []}, f, indent=2)

    def _load_registry(self) -> dict:
        try:
            with open(REGISTRY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"tools": []}

    def _save_registry(self, registry: dict):
        with open(REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Code Execution
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def execute(self, code: str, task_id: str = None) -> dict:
        """
        Execute Python code in a sandboxed subprocess.
        """
        task_id = task_id or f"exec_{int(time.time())}"

        # Safety scan
        scan = safety_scan(code)
        if not scan["passed"]:
            executor_logger.warning(
                f"BLOCKED | task_id={task_id} | reason={scan['reason']}"
            )
            return {
                "success": False,
                "output": "",
                "error": f"Safety block: {scan['reason']}",
                "files_created": [],
                "images": [],
                "execution_time": 0.0,
                "memory_mb": 0,
                "reversible": True,
                "requires_confirm": False,
            }

        # Reversibility check
        rev = reversibility_check(code)

        # Log the code being executed
        executor_logger.info(
            f"EXECUTE | task_id={task_id} | safety=passed | "
            f"reversible={rev['reversible']}"
        )
        executor_logger.debug(f"CODE | task_id={task_id} |\n{code}")

        # List workspace files before execution
        files_before = self._list_workspace_files()

        # Execute
        result = self._run_subprocess(code, task_id)

        # Check for auto-install
        if not result["success"] and "ModuleNotFoundError" in result["error"]:
            package = self._extract_missing_package(result["error"])
            if package and package.lower() not in BLOCKED_PACKAGES:
                executor_logger.info(
                    f"AUTO_INSTALL | task_id={task_id} | package={package}"
                )
                install_result = self._pip_install(package)
                if install_result:
                    executor_logger.info(
                        f"RETRY | task_id={task_id} | after installing {package}"
                    )
                    result = self._run_subprocess(code, task_id)

        # List workspace files after execution
        files_after = self._list_workspace_files()
        files_created = [f for f in files_after if f not in files_before]

        # Extract images from output (IMAGE: prefix)
        images = []
        clean_output_lines = []
        for line in result["output"].split("\n"):
            if line.startswith("IMAGE:"):
                images.append(line[6:].strip())
            else:
                clean_output_lines.append(line)

        final_result = {
            "success": result["success"],
            "output": "\n".join(clean_output_lines),
            "error": result["error"],
            "files_created": files_created,
            "images": images,
            "execution_time": result["execution_time"],
            "memory_mb": result.get("memory_mb", 0),
            "reversible": rev["reversible"],
            "requires_confirm": rev["requires_confirm"],
        }

        # Log the result
        executor_logger.info(
            f"RESULT | task_id={task_id} | success={final_result['success']} | "
            f"time={final_result['execution_time']:.2f}s | "
            f"files={len(files_created)} | images={len(images)} | "
            f"reversible={final_result['reversible']}"
        )
        if final_result["output"]:
            executor_logger.debug(
                f"STDOUT | task_id={task_id} | {final_result['output'][:1000]}"
            )
        if final_result["error"]:
            executor_logger.debug(
                f"STDERR | task_id={task_id} | {final_result['error'][:1000]}"
            )

        # journalctl-style summary to stdout
        print(
            f"[EXECUTOR] task_id={task_id} | "
            f"time={final_result['execution_time']:.1f}s | "
            f"files={len(files_created)} | images={len(images)} | "
            f"status={'success' if final_result['success'] else 'failed'}"
        )

        return final_result

    def _run_subprocess(self, code: str, task_id: str) -> dict:
        """Run Python code in an isolated subprocess via temp file."""
        safe_id = task_id.replace("/", "_").replace(" ", "_")
        code_file = os.path.join(WORKSPACE, f"_moca_exec_{safe_id}.py")

        # Build wrapper: resource limits + user code
        wrapper_lines = [
            "import sys, os",
            "",
            "# Resource limits — Linux only",
            "try:",
            "    import resource, signal",
            "    resource.setrlimit(resource.RLIMIT_AS, (1024*1024*1024, 1024*1024*1024))",
            "    def _timeout_handler(signum, frame):",
            "        print('TIMEOUT: Execution exceeded 60 seconds', file=sys.stderr)",
            "        sys.exit(124)",
            "    signal.signal(signal.SIGALRM, _timeout_handler)",
            "    signal.alarm(60)",
            "except (ImportError, AttributeError, ValueError):",
            "    pass",
            "",
            f"os.chdir({repr(WORKSPACE)})",
            "",
            "# ── User code below ──",
            code,
        ]

        try:
            with open(code_file, "w") as f:
                f.write("\n".join(wrapper_lines))
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Failed to write code file: {str(e)}",
                "execution_time": 0.0,
                "exit_code": 1,
            }

        # Clean environment
        clean_env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin:/usr/local/sbin",
            "HOME": WORKSPACE,
            "PYTHONPATH": "",
            "LANG": "en_US.UTF-8",
        }

        # Add venv site-packages if available
        python_path = sys.executable
        venv_dir = os.path.dirname(os.path.dirname(python_path))
        site_packages = os.path.join(
            venv_dir, "lib",
            f"python{sys.version_info.major}.{sys.version_info.minor}",
            "site-packages"
        )
        if os.path.isdir(site_packages):
            clean_env["PYTHONPATH"] = site_packages

        start_time = time.time()

        try:
            proc = subprocess.run(
                [python_path, code_file],
                capture_output=True,
                text=True,
                timeout=65,
                env=clean_env,
                cwd=WORKSPACE,
            )
            execution_time = time.time() - start_time
            return {
                "success": proc.returncode == 0,
                "output": proc.stdout.strip(),
                "error": proc.stderr.strip(),
                "execution_time": execution_time,
                "exit_code": proc.returncode,
            }

        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            executor_logger.warning(
                f"TIMEOUT | task_id={task_id} | time={execution_time:.2f}s"
            )
            return {
                "success": False,
                "output": "",
                "error": "Execution timed out after 60 seconds",
                "execution_time": execution_time,
                "exit_code": 124,
            }

        except Exception as e:
            execution_time = time.time() - start_time
            executor_logger.error(
                f"SUBPROCESS_ERROR | task_id={task_id} | error={str(e)}"
            )
            return {
                "success": False,
                "output": "",
                "error": f"Subprocess error: {str(e)}",
                "execution_time": execution_time,
                "exit_code": 1,
            }

        finally:
            try:
                if os.path.exists(code_file):
                    os.remove(code_file)
            except Exception:
                pass

    def _list_workspace_files(self) -> set:
        """List all files in the workspace directory."""
        files = set()
        try:
            for root, _, filenames in os.walk(WORKSPACE):
                for fn in filenames:
                    files.add(os.path.join(root, fn))
        except Exception:
            pass
        return files

    def _extract_missing_package(self, error: str) -> str:
        """Extract package name from ModuleNotFoundError."""
        match = re.search(r"No module named '([^']+)'", error)
        if match:
            return match.group(1).split(".")[0]
        return None

    def _pip_install(self, package: str) -> bool:
        """Install a Python package via pip."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True,
                text=True,
                timeout=120,
            )
            executor_logger.info(
                f"PIP_INSTALL | package={package} | "
                f"success={result.returncode == 0} | "
                f"output={result.stdout[:200]}"
            )
            return result.returncode == 0
        except Exception as e:
            executor_logger.error(
                f"PIP_INSTALL_FAILED | package={package} | error={str(e)}"
            )
            return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Web Search
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def web_search(self, query: str, num_results: int = 5) -> list:
        """
        Search the web using DuckDuckGo HTML (no API key).
        Returns list of {title, url, snippet}.
        """
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            executor_logger.error("WEB_SEARCH | Missing requests or bs4")
            return []

        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://duckduckgo.com/html/?q={encoded_query}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        executor_logger.info(f"WEB_SEARCH | query={query}")

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
        except Exception as e:
            executor_logger.error(f"WEB_SEARCH_FAILED | query={query} | error={str(e)}")
            return []

        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception:
            soup = BeautifulSoup(response.text, "html.parser")

        results = []
        for result_div in soup.select(".result"):
            if len(results) >= num_results:
                break

            title_el = result_div.select_one(".result__title a")
            snippet_el = result_div.select_one(".result__snippet")

            if title_el:
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if "uddg=" in href:
                    try:
                        parsed = urllib.parse.parse_qs(
                            urllib.parse.urlparse(href).query
                        )
                        href = parsed.get("uddg", [href])[0]
                    except Exception:
                        pass

                results.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                })

        executor_logger.info(
            f"WEB_SEARCH_RESULTS | query={query} | count={len(results)}"
        )

        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Integration Registry
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def save_integration(
        self,
        name: str,
        code: str,
        description: str,
        parameters: list,
    ):
        """Save a reusable integration to disk and update the registry."""
        filename = f"{name}.py"
        filepath = os.path.join(INTEGRATIONS, filename)
        with open(filepath, "w") as f:
            f.write(code)

        registry = self._load_registry()
        registry["tools"] = [
            t for t in registry["tools"] if t["name"] != name
        ]
        registry["tools"].append({
            "name": name,
            "description": description,
            "parameters": parameters,
            "created": datetime.now().strftime("%Y-%m-%d"),
            "last_used": datetime.now().strftime("%Y-%m-%d"),
            "use_count": 0,
            "file": f"integrations/{filename}",
        })
        self._save_registry(registry)

        executor_logger.info(
            f"INTEGRATION_SAVED | name={name} | "
            f"description={description} | params={parameters}"
        )

    def load_integration(self, name: str) -> str:
        """Load integration code by name. Updates last_used and use_count."""
        registry = self._load_registry()

        for tool in registry["tools"]:
            if tool["name"] == name:
                filepath = os.path.join(
                    os.path.dirname(INTEGRATIONS), tool["file"]
                )
                if not os.path.exists(filepath):
                    filepath = os.path.join(INTEGRATIONS, f"{name}.py")

                if os.path.exists(filepath):
                    with open(filepath, "r") as f:
                        code = f.read()

                    tool["last_used"] = datetime.now().strftime("%Y-%m-%d")
                    tool["use_count"] = tool.get("use_count", 0) + 1
                    self._save_registry(registry)

                    executor_logger.info(
                        f"INTEGRATION_LOADED | name={name} | "
                        f"use_count={tool['use_count']}"
                    )
                    return code

        executor_logger.warning(f"INTEGRATION_NOT_FOUND | name={name}")
        return None

    def list_integrations(self) -> list:
        """Return all registry entries."""
        registry = self._load_registry()
        return registry.get("tools", [])

    def search_integrations(self, query: str) -> list:
        """Simple keyword match on name + description."""
        query_lower = query.lower()
        results = []
        for tool in self.list_integrations():
            name = tool.get("name", "").lower()
            desc = tool.get("description", "").lower()
            if query_lower in name or query_lower in desc:
                results.append(tool)
        return results
