"""
MOCA Agent — Phase 4 Agentic Loop
ReAct loop: Think → Act → Observe → Think again → Act again
The model is purely a reasoning layer. All execution lives in executor.py.
"""

import json
import logging
import os
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
  "save_integration": null or {"name": "...", "description": "...", "parameters": ["..."]}
}

Rules:
- If moca_command is execute_python: write complete runnable Python.
  Print results explicitly — output is how you see results.
  To return an image: print('IMAGE:' + base64_string)
  To return structured data: print('JSON:' + json_string)
- If moca_command is duckduckgo_search: command_payload is the search query string.
- If moca_command is respond and is_final is true: the task is complete. command_payload is your final answer to Boss.
- If code solves a reusable problem: set save_integration with name, description, and parameters list.
- Always check available tools before writing new code — you may already have one.
- For irreversible actions (deleting, sending, posting, paying): tell Boss what you are about to do first. One sentence. Then wait for confirmation.
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
        self.max_steps = 10
        self.executor = CodeExecutor()

    def run(self, task: str, device: str, system_prompt: str = "",
            on_step=None) -> dict:
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
                        max_tokens=2048,
                    )
                except Exception:
                    # Fallback without response_format for models that don't support it
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=2048,
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
                    if result["images"]:
                        result_message += (
                            f"\n\n{len(result['images'])} image(s) generated. "
                            f"Use these base64 strings in your display HTML "
                            f"as <img src='data:image/png;base64,{{b64}}'>"
                        )

                    messages.append({
                        "role": "user",
                        "content": result_message,
                    })

            elif action == "duckduckgo_search":
                search_results = self.executor.web_search(action_input)
                formatted = format_search_results(search_results)

                output_history.append(formatted)
                consecutive_failures = 0  # Search doesn't count as failure

                agent_logger.info(
                    f"SEARCH_RESULT | id={task_id} | step={step} | "
                    f"query={action_input} | results={len(search_results)}"
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
                        # Find the last code that was executed successfully
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

                total_time = time.time() - start_time
                agent_logger.info(
                    f"COMPLETE | id={task_id} | steps={step} | "
                    f"time={total_time:.2f}s | "
                    f"integration_reused={integration_reused} | "
                    f"integration_created={integration_created}"
                )

                return self._build_response(
                    task_id, device, step, start_time,
                    action_input, parsed,
                )

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

        # Try to extract display from the action_input if it contains HTML
        display = None
        if parsed:
            # Check if the model included display info in the response
            display_data = parsed.get("display")
            if display_data:
                display = display_data

        # If reply contains HTML-like display content, try to build display
        if not display and reply_text and "<div" in reply_text:
            display = {
                "layout": "single",
                "windows": [{
                    "type": "html",
                    "title": "MOCA",
                    "content": reply_text,
                }],
            }
            # Clean reply for speech
            reply_text = parsed.get("narration", "Done Boss.") if parsed else "Done Boss."

        # Check for images in the execution history (from executor)
        # The model should have embedded them in the display HTML already

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
        total_time = time.time() - start_time

        return {
            "reply": reason_text,
            "display": None,
            "action": None,
            "device": device,
            "agent_used": True,
            "steps_taken": steps,
            "total_time": round(total_time, 2),
            "stopped_reason": stopped_reason,
        }

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
