"""
MOCA Agent — Phase 4 Agentic Loop
ReAct loop: Think → Act → Observe → Think again → Act again
The model is purely a reasoning layer. All execution lives in executor.py.
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler

from executor import CodeExecutor


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOCA_ROOT = os.getenv("MOCA_ROOT", _PROJECT_ROOT)
LOGS_DIR = os.path.join(MOCA_ROOT, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

agent_logger = logging.getLogger("moca.agent")
agent_logger.setLevel(logging.DEBUG)

_handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, "agent.log"),
    maxBytes=50 * 1024 * 1024,  # 50MB
    backupCount=5,
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
agent_logger.addHandler(_handler)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Complexity Assessment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SIMPLE_PATTERNS = [
    "what time", "reminder", "set a", "what's my",
    "who am i", "what do you know", "hello", "hi",
    "thanks", "thank you", "close", "open",
    "how are you", "what are you",
]


def is_simple_task(task: str) -> bool:
    """Check if a task is simple enough to skip the agent loop."""
    task_lower = task.lower()
    return (
        any(p in task_lower for p in SIMPLE_PATTERNS)
        or len(task.split()) <= 5
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Task Completion Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VISUAL_KEYWORDS = [
    "show", "display", "chart", "graph",
    "image", "picture", "photo", "visualize",
]

INCOMPLETE_SIGNALS = [
    "you can find", "visit", "check out",
    "available at", "go to", "here are some sources",
]


def is_task_complete(task: str, response: dict) -> tuple[bool, str | None]:
    """Validate that a response actually completes the requested task."""
    task_lower = task.lower()

    # Check 1: Visual intent requires display field (url or windows format)
    visual_intent = any(w in task_lower for w in VISUAL_KEYWORDS)
    display = response.get("display")
    has_display = display and (display.get("url") or display.get("windows"))
    if visual_intent and not has_display:
        return False, (
            "Your response does not include a display window. "
            "The user asked to SEE something. Complete the task by "
            "including actual content in a display field. "
            "For images: find real image URLs and embed them in HTML "
            "img tags. Do not describe where to find them — show them directly."
        )

    # Check 2: Response describes where to find content instead of showing it
    reply = response.get("reply", "").lower()
    if any(s in reply for s in INCOMPLETE_SIGNALS):
        return False, (
            "Response describes where to find content instead of showing it. "
            "Complete the task by actually fetching/showing the content directly. "
            "Do not tell the user where to go — do it for them."
        )

    return True, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent Instructions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AGENT_INSTRUCTIONS = """
You are in agentic mode. Reason step by step.
You have tools available. Use them to complete the task.

Respond ONLY in this exact JSON format — nothing else. DO NOT use native tool calling syntax:
{
  "thought": "what you are thinking right now",
  "moca_command": "execute_python" or "duckduckgo_search" or "respond",
  "command_payload": "python code, search query, or final answer",
  "narration": "one sentence describing this step for Boss",
  "is_final": true or false,
  "display_file": null or "<!DOCTYPE html>...",
  "save_integration": null or {"name": "...", "description": "...", "parameters": ["..."]}
}

Rules:
- If moca_command is duckduckgo_search: command_payload is the search query string. Use this for ALL web searches.
- If moca_command is execute_python: write complete runnable Python.
  Print results explicitly — output is how you see results.
  To save a chart or image: use matplotlib plt.savefig('chart.png') then print('IMAGE_FILE:chart.png')
  To return structured data: print('JSON:' + json_string)
  When your code generates a chart image, reference it as /files/FILENAME.png in display_file HTML.
- If moca_command is respond and is_final is true: the task is complete. command_payload is your final answer to Boss.
- If code solves a reusable problem: set save_integration with name, description, and parameters list.
- Always check available tools before writing new code — you may already have one.
- For irreversible actions (deleting, sending, posting, paying): tell Boss what you are about to do first. One sentence. Then wait for confirmation.

CRITICAL — web search vs Python:
You already have a working duckduckgo_search tool. USE IT. Do NOT write Python code to scrape websites or call search engines directly — sites block automated requests and it will always fail.

For finding images:
  moca_command: "duckduckgo_search"
  command_payload: "Qutub Minar high resolution photo"
Search results include an image_urls field with direct .jpg/.png URLs you can use immediately in img tags. Prefer upload.wikimedia.org URLs — always publicly accessible and high quality. Do NOT use Unsplash, Getty, Shutterstock or any service requiring an API key.

Only use execute_python for:
- Data processing and calculations
- Chart generation with matplotlib
- File manipulation
- API calls where you have a valid API key

NEVER write Python code using requests, urllib, BeautifulSoup, or selenium to scrape Google, Bing, DuckDuckGo, or any website for search results or images. Use duckduckgo_search instead.

Visual display — display_file:
When the user asks to SEE something (show, display, chart, graph, image, picture, photo, visualize), you MUST include a display_file field with a COMPLETE self-contained HTML webpage. Not a div snippet — a full <!DOCTYPE html> page with head and body.

display_file rules:
- All CSS in <style> tags in the head. Inline CSS also fine.
- Dark theme: background #0d0d14, cards #16161f, accent #00d4ff, text #e0e0e0
- For images: use real URLs you found via duckduckgo_search in <img> tags. NEVER use placeholder URLs.
- For charts: generate with matplotlib, save PNG to workspace, reference as /files/chart_xxx.png
- Can include JavaScript for interactivity and real-time updates
- Can embed YouTube, maps, live data via JavaScript
- Make it visually impressive — this is a full webpage with complete browser capability

Set display_file to null when no visual output is needed.
Do NOT describe where to find images or data — actually fetch and show them directly.
"""


def build_tools_prompt(executor: CodeExecutor) -> str:
    """Build the available tools description from the registry."""
    integrations = executor.list_integrations()

    lines = [
        "\nAvailable tools:",
        "- execute_python(code): execute Python on the server",
        "- duckduckgo_search(query): search the web",
        "",
    ]

    if integrations:
        lines.append("Your saved integrations:")
        for tool in integrations:
            params = ", ".join(tool.get("parameters", []))
            lines.append(
                f"- {tool['name']} — {tool['description']} — "
                f"parameters: [{params}]"
            )
        lines.append("")
        lines.append("Check these before writing new code.")
    else:
        lines.append("You have no saved integrations yet. Build new ones as needed.")

    return "\n".join(lines)


def format_search_results(results: list) -> str:
    """Format web search results for the model."""
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['snippet']}")
        lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOCAAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MOCAAgent:

    def __init__(self, client, model: str, memory, user_id: str):
        self.client = client
        self.model = model
        self.memory = memory
        self.user_id = user_id
        self.executor = CodeExecutor()
        self.max_steps = 10
        self.last_images = []  # Track base64 images across steps
        self.last_image_files = []  # Track saved image filenames

    def run(
        self,
        task: str,
        device: str = "unknown",
        system_prompt: str = "",
        on_step: callable = None,
    ) -> dict:
        """
        Execute a task autonomously using the ReAct loop.

        on_step({"step": N, "narration": "..."}) fires after each step
        for streaming to client.

        Returns final response dict:
        {
            "reply": "...",
            "display": {...} or None,
            "action": None,
            "device": "...",
            "agent_used": True,
            "steps_taken": N,
        }
        """
        task_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.last_images = []
        self.last_image_files = []
        self._failure_sent = False
        self._cached_failure = None

        # Complexity assessment
        if is_simple_task(task):
            agent_logger.info(
                f"TASK | id={task_id} | complexity=simple | "
                f"task={task[:100]} | agent=false"
            )
            return None  # Signal to caller: use direct response

        agent_logger.info(
            f"TASK | id={task_id} | complexity=complex | "
            f"task={task[:100]} | agent=true"
        )

        # Retrieve memory context
        memory_context = ""
        try:
            results = self.memory.search(task)
            if results:
                memory_context = (
                    "\n\nWhat you know about Boss:\n" +
                    "\n".join(f"- {r}" for r in results)
                )
        except Exception as e:
            agent_logger.warning(f"MEMORY_ERROR | id={task_id} | error={str(e)}")

        # Build tools prompt
        tools_prompt = build_tools_prompt(self.executor)

        # Check if any existing integration is relevant
        integrations_checked = []
        existing_integrations = self.executor.list_integrations()
        for tool in existing_integrations:
            integrations_checked.append(tool["name"])

        agent_logger.info(
            f"INTEGRATIONS_CHECKED | id={task_id} | "
            f"available={integrations_checked}"
        )

        # Build initial messages
        full_system = (
            system_prompt + memory_context +
            "\n\n" + AGENT_INSTRUCTIONS +
            "\n" + tools_prompt
        )

        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": task},
        ]

        # Tracking for stopping conditions
        step = 1
        error_history = []        # list of (code_hash, error_text)
        consecutive_failures = 0
        output_history = []       # list of output strings per step
        integration_reused = None
        integration_created = None

        while step <= self.max_steps:
            step_start = time.time()

            agent_logger.info(
                f"STEP | id={task_id} | step={step}/{self.max_steps}"
            )

            try:
                # Try with JSON mode first — not all models support it
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        response_format={"type": "json_object"},
                        temperature=0.7,
                        max_tokens=4096,
                    )
                except Exception:
                    # Fallback without response_format for models that don't support it
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=4096,
                    )

                raw_content = response.choices[0].message.content or "{}"

            except Exception as e:
                agent_logger.error(
                    f"MODEL_ERROR | id={task_id} | step={step} | error={str(e)}"
                )
                return self._build_failure_response(
                    task_id, device, step, start_time,
                    f"Model call failed: {str(e)}",
                    "model_error",
                )

            # Strip <think>...</think> reasoning blocks and markdown wrappers
            raw_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            raw_content = re.sub(r'<think>.*$', '', raw_content, flags=re.DOTALL).strip()
            raw_content = re.sub(r'<reasoning>.*?</reasoning>', '', raw_content, flags=re.DOTALL).strip()
            raw_content = re.sub(r'<reasoning>.*$', '', raw_content, flags=re.DOTALL).strip()
            raw_content = re.sub(r'^```json\s*', '', raw_content, flags=re.MULTILINE)
            raw_content = re.sub(r'\s*```$', '', raw_content, flags=re.MULTILINE)
            raw_content = raw_content.strip() or "{}"

            # Parse JSON response
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError:
                agent_logger.warning(
                    f"JSON_PARSE_ERROR | id={task_id} | step={step} | "
                    f"raw={raw_content[:200]}"
                )
                # Try to salvage — treat as final response
                return self._build_response(
                    task_id, device, step, start_time,
                    raw_content, None,
                )

            # Extract fields with safe defaults
            thought = parsed.get("thought", "")
            action = parsed.get("moca_command", "respond")
            action_input = parsed.get("command_payload", "")
            narration = parsed.get("narration", "Working on it...")
            is_final = parsed.get("is_final", False)
            save_integration = parsed.get("save_integration")

            step_time = time.time() - step_start

            agent_logger.info(
                f"PARSED | id={task_id} | step={step} | "
                f"action={action} | is_final={is_final} | "
                f"time={step_time:.2f}s"
            )
            agent_logger.debug(
                f"THOUGHT | id={task_id} | step={step} | {thought}"
            )
            agent_logger.debug(
                f"ACTION_INPUT | id={task_id} | step={step} | "
                f"{str(action_input)[:500]}"
            )

            # Fire narration callback
            if on_step:
                on_step({
                    "step": step,
                    "narration": narration,
                    "total_steps": self.max_steps,
                })

            # ── Handle actions ──────────────────────────────

            if action == "execute_python":
                result = self.executor.execute(
                    action_input,
                    task_id=f"{task_id}_step{step}",
                )

                # Track for stopping conditions
                output_text = result["output"] or result["error"]
                output_history.append(output_text)

                if result.get("images"):
                    self.last_images = result["images"]
                if result.get("image_files"):
                    self.last_image_files = result["image_files"]

                if not result["success"]:
                    consecutive_failures += 1
                    code_hash = hash(action_input)
                    error_history.append((code_hash, result["error"]))
                else:
                    consecutive_failures = 0

                agent_logger.info(
                    f"CODE_RESULT | id={task_id} | step={step} | "
                    f"success={result['success']} | "
                    f"output={output_text[:500]}"
                )

                # Check if this requires confirmation (irreversible)
                if result["requires_confirm"] and not result["success"]:
                    # Code wasn't run — it was blocked for confirmation
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps(parsed),
                    })
                    messages.append({
                        "role": "user",
                        "content": (
                            "This action was flagged as potentially irreversible. "
                            "Tell Boss what you are about to do and ask for "
                            "confirmation before proceeding."
                        ),
                    })
                else:
                    # Feed result back for next iteration
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps(parsed),
                    })

                    result_message = f"Code result:\n{result['output']}"
                    if result["error"]:
                        result_message += f"\nErrors:\n{result['error']}"
                    if result.get("image_files"):
                        files_list = ", ".join(result["image_files"])
                        result_message += (
                            f"\n\n{len(result['image_files'])} image(s) saved: {files_list}. "
                            f"Reference them in display_file HTML as /files/FILENAME.png"
                        )

                    messages.append({
                        "role": "user",
                        "content": result_message,
                    })

            elif action == "duckduckgo_search":
                search_data = self.executor.web_search(action_input)
                search_results = search_data.get("results", [])
                image_urls = search_data.get("image_urls", [])
                formatted = format_search_results(search_results)

                # Append image URLs section if found
                if image_urls:
                    formatted += "\n\nDirect image URLs found (use these in img tags):\n"
                    for img_url in image_urls:
                        formatted += f"  {img_url}\n"

                output_history.append(formatted)
                consecutive_failures = 0  # Search doesn't count as failure

                agent_logger.info(
                    f"SEARCH_RESULT | id={task_id} | step={step} | "
                    f"query={action_input} | results={len(search_results)} | "
                    f"image_urls={len(image_urls)}"
                )

                messages.append({
                    "role": "assistant",
                    "content": json.dumps(parsed),
                })
                messages.append({
                    "role": "user",
                    "content": f"Search results:\n{formatted}",
                })

            elif action == "respond" and is_final:
                # Save integration if requested
                if save_integration and isinstance(save_integration, dict):
                    try:
                        integration_code = self._find_last_successful_code(
                            messages
                        )
                        if integration_code:
                            self.executor.save_integration(
                                name=save_integration["name"],
                                code=integration_code,
                                description=save_integration.get(
                                    "description", ""
                                ),
                                parameters=save_integration.get(
                                    "parameters", []
                                ),
                            )
                            integration_created = save_integration["name"]
                            agent_logger.info(
                                f"INTEGRATION_SAVED | id={task_id} | "
                                f"name={save_integration['name']}"
                            )
                    except Exception as e:
                        agent_logger.warning(
                            f"INTEGRATION_SAVE_FAILED | id={task_id} | "
                            f"error={str(e)}"
                        )

                # ── Task completion validation ──────────────
                candidate = self._build_response(
                    task_id, device, step, start_time,
                    action_input, parsed,
                )

                complete, reason = is_task_complete(task, candidate)

                if not complete and not hasattr(self, '_completion_retries'):
                    self._completion_retries = 0

                if not complete and getattr(self, '_completion_retries', 0) < 2:
                    self._completion_retries = getattr(self, '_completion_retries', 0) + 1
                    agent_logger.warning(
                        f"INCOMPLETE | id={task_id} | step={step} | "
                        f"retry={self._completion_retries} | reason={reason}"
                    )

                    # Feed reason back and loop again
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps(parsed),
                    })
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Task NOT complete: {reason}\n\n"
                            "Try again. Use moca_command 'respond' with "
                            "is_final: true only when the task is truly done. "
                            "Include display field with HTML content when "
                            "visual output is needed."
                        ),
                    })

                    if on_step:
                        on_step({
                            "step": step,
                            "narration": "Refining response...",
                            "total_steps": self.max_steps,
                        })

                    step += 1
                    continue  # Re-enter while loop

                # Clean up retry counter
                if hasattr(self, '_completion_retries'):
                    del self._completion_retries

                total_time = time.time() - start_time
                agent_logger.info(
                    f"COMPLETE | id={task_id} | steps={step} | "
                    f"time={total_time:.2f}s | "
                    f"integration_reused={integration_reused} | "
                    f"integration_created={integration_created}"
                )

                return candidate

            else:
                # Unknown action or respond without is_final — continue
                output_history.append("")
                messages.append({
                    "role": "assistant",
                    "content": json.dumps(parsed),
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "Continue with the task. If you're done, "
                        "use moca_command 'respond' with is_final: true."
                    ),
                })

            # ── Check stopping conditions ────────────────────

            # 1. Same code attempted 3 times with identical error
            if len(error_history) >= 3:
                last_three = error_history[-3:]
                if (
                    all(h == last_three[0][0] for h, _ in last_three) and
                    all(e == last_three[0][1] for _, e in last_three)
                ):
                    agent_logger.warning(
                        f"STOPPED | id={task_id} | reason=repeated_failure | "
                        f"step={step}"
                    )
                    return self._build_failure_response(
                        task_id, device, step, start_time,
                        (
                            f"Hit a wall Boss. Tried the same approach three "
                            f"times and got the same error: "
                            f"{last_three[0][1][:200]}. "
                            f"Want me to try a different approach?"
                        ),
                        "repeated_failure",
                    )

            # 2. Code execution fails 3 times consecutively
            if consecutive_failures >= 3:
                agent_logger.warning(
                    f"STOPPED | id={task_id} | "
                    f"reason=consecutive_failures | step={step}"
                )
                last_error = (
                    error_history[-1][1][:200] if error_history else "unknown"
                )
                return self._build_failure_response(
                    task_id, device, step, start_time,
                    (
                        f"Hit a wall Boss. Code failed three times in a row. "
                        f"Last error: {last_error}. "
                        f"Want me to try a different approach?"
                    ),
                    "consecutive_failures",
                )

            # 3. Output of step N identical to step N-2 (looping)
            if len(output_history) >= 3:
                if (
                    output_history[-1] == output_history[-3] and
                    output_history[-1]  # Not empty
                ):
                    agent_logger.warning(
                        f"STOPPED | id={task_id} | reason=no_progress | "
                        f"step={step}"
                    )
                    return self._build_failure_response(
                        task_id, device, step, start_time,
                        (
                            "Hit a wall Boss. I'm going in circles and not "
                            "making progress. Want me to try a different "
                            "approach?"
                        ),
                        "no_progress",
                    )

            step += 1

        # Max steps reached
        agent_logger.warning(
            f"STOPPED | id={task_id} | reason=max_steps | step={step - 1}"
        )
        return self._build_failure_response(
            task_id, device, step - 1, start_time,
            (
                f"Hit a wall Boss. Reached the maximum of {self.max_steps} "
                f"steps without completing the task. Here is what I have so "
                f"far. Want me to continue?"
            ),
            "max_steps",
        )

    def _build_response(
        self, task_id: str, device: str, steps: int,
        start_time: float, reply_text: str, parsed: dict,
    ) -> dict:
        """Build a successful final response."""
        total_time = time.time() - start_time

        display = None

        # ── Handle display_file: full HTML page → save to workspace ──
        display_file_html = parsed.get("display_file") if parsed else None
        if display_file_html and isinstance(display_file_html, str) and display_file_html.strip():
            html = display_file_html
            filename = self.executor.save_display_file(html)
            display = {"url": f"{SERVER_URL}/files/{filename}"}
            agent_logger.info(
                f"DISPLAY_FILE | id={task_id} | file={filename} | "
                f"size={len(html)} bytes"
            )

        # ── Fallback: model returned command_payload as nested JSON ──
        if not display and reply_text and reply_text.strip().startswith("{"):
            try:
                nested = json.loads(reply_text)
                if isinstance(nested, dict) and "reply" in nested:
                    reply_text = nested["reply"]
                    if nested.get("display_file"):
                        html = nested["display_file"]
                        filename = self.executor.save_display_file(html)
                        display = {"url": f"{SERVER_URL}/files/{filename}"}
                    elif nested.get("display"):
                        display = nested["display"]
                    agent_logger.info(
                        f"PARSED_NESTED_REPLY | id={task_id} | "
                        f"has_display={display is not None}"
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        # ── Fallback: old-style display dict in parsed response ──
        if not display and parsed:
            display_data = parsed.get("display")
            if display_data:
                display = display_data

        # ── Fallback: reply contains raw HTML ──
        if not display and reply_text and "<div" in reply_text:
            # Wrap in full page and save
            full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0d0d14; color: #e0e0e0; font-family: system-ui, sans-serif; padding: 20px; }}
img {{ max-width: 100%; border-radius: 6px; }}
</style></head>
<body>{reply_text}</body></html>"""
            filename = self.executor.save_display_file(full_html)
            display = {"url": f"{SERVER_URL}/files/{filename}"}
            reply_text = parsed.get("narration", "Done Boss.") if parsed else "Done Boss."

        # ── Normalize old-style windows display ──
        if display and "windows" in display:
            img_url = None
            if self.last_image_files:
                img_url = f"{SERVER_URL}/files/{self.last_image_files[0]}"
            for window in display.get("windows", []):
                content = window.get("content", "")
                if content and img_url:
                    content = content.replace("{{IMAGE_URL}}", img_url)
                    window["content"] = content

        # Clean placeholders from reply text
        if reply_text:
            reply_text = reply_text.replace("{{IMAGE_BASE64}}", "")
            reply_text = reply_text.replace("{{IMAGE_URL}}", "")

        result = {
            "reply": reply_text or "Done Boss.",
            "display": display,
            "action": None,
            "device": device,
            "agent_used": True,
            "steps_taken": steps,
            "total_time": round(total_time, 2),
        }

        return result

    def _build_failure_response(
        self, task_id: str, device: str, steps: int,
        start_time: float, reason_text: str, stopped_reason: str,
    ) -> dict:
        """Build a failure response with honest explanation."""
        # Guard against double failure — return cached first response
        if self._failure_sent:
            agent_logger.warning(
                f"DUPLICATE_FAILURE_BLOCKED | id={task_id} | reason={stopped_reason}"
            )
            return self._cached_failure
        self._failure_sent = True

        total_time = time.time() - start_time

        self._cached_failure = {
            "reply": reason_text,
            "display": None,
            "action": None,
            "device": device,
            "agent_used": True,
            "steps_taken": steps,
            "total_time": round(total_time, 2),
            "stopped_reason": stopped_reason,
        }
        return self._cached_failure

    def _find_last_successful_code(self, messages: list) -> str:
        """Find the last successfully executed code from message history."""
        # Walk backwards through messages to find assistant messages
        # with run_code actions that were followed by successful results
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg["role"] == "assistant":
                try:
                    parsed = json.loads(msg["content"])
                    if parsed.get("moca_command") == "execute_python":
                        # Check if next message shows success
                        if i + 1 < len(messages):
                            next_msg = messages[i + 1]
                            if (
                                next_msg["role"] == "user" and
                                "Errors:\n\n" in next_msg["content"] or
                                "Errors:" not in next_msg["content"]
                            ):
                                return parsed["action_input"]
                except (json.JSONDecodeError, KeyError):
                    continue
        return None
