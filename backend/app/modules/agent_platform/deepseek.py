from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import (
    AgentResult,
    AgentTask,
    RouteDecision,
    ToolCallRequest,
    UserContext,
)
from app.modules.agent_platform.orchestration.runtime import SpecialistOutcome

SUPPORTED_MODEL = "deepseek-v4-pro"

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_WEEKDAYS = "一二三四五六日"


def _current_time_text() -> str:
    now = datetime.now(_SHANGHAI)
    return f"{now:%Y-%m-%d %H:%M} 星期{_WEEKDAYS[now.weekday()]}（北京时间，UTC+8）"


class DeepSeekUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=502, code="AGENT_PROVIDER_UNAVAILABLE", message="智能体模型服务暂不可用")


class DeepSeekTimeout(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=504, code="AGENT_PROVIDER_TIMEOUT", message="智能体模型服务响应超时")


class HttpClientPort(Protocol):
    async def post(self, url: str, **kwargs: Any) -> Any: ...


class _ToolDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    arguments: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class _SpecialistPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str = Field(pattern=r"^(succeeded|partial|failed|needs_input)$")
    summary: str = Field(min_length=1, max_length=2000)
    structured_output: dict[str, Any] = Field(default_factory=dict)
    tool_call: _ToolDirective | None = None


class _RouterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_agent: str
    confidence: Decimal = Field(ge=0, le=1)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    candidate_agents: tuple[str, ...] = Field(default=(), max_length=3)


class DeepSeekGateway:
    """Minimal OpenAI-compatible gateway that never persists reasoning content."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = SUPPORTED_MODEL,
        timeout_seconds: float = 120,
        client: HttpClientPort | None = None,
        max_pre_output_attempts: int = 2,
    ) -> None:
        if model != SUPPORTED_MODEL:
            raise ValueError(f"only {SUPPORTED_MODEL} is supported")
        if not api_key or timeout_seconds <= 0 or max_pre_output_attempts not in {1, 2, 3}:
            raise ValueError("invalid DeepSeek gateway configuration")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient()
        self._attempts = max_pre_output_attempts

    def __repr__(self) -> str:
        return f"DeepSeekGateway(base_url={self._base_url!r}, model={self._model!r})"

    async def json_completion(
        self, messages: Sequence[Mapping[str, str]]
    ) -> dict[str, Any]:
        response = None
        last_error: DeepSeekUnavailable | None = None
        for attempt in range(self._attempts):
            try:
                response = await self._post(messages, stream=False)
                break
            except DeepSeekUnavailable as exc:
                last_error = exc
                if attempt + 1 >= self._attempts:
                    raise
        if response is None:
            raise DeepSeekUnavailable() from last_error
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            content = message["content"]
            parsed = json.loads(self._extract_json_object(content))
            if not isinstance(parsed, dict):
                raise ValueError("structured response must be an object")
            return parsed
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DeepSeekUnavailable() from exc

    @staticmethod
    def _extract_json_object(content: str) -> str:
        """Tolerate markdown fences or prose around the single JSON object."""

        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("structured response must contain a JSON object")
        return text[start : end + 1]

    async def stream_text(
        self, messages: Sequence[Mapping[str, str]]
    ) -> AsyncIterator[str]:
        """Retry only before the first emitted content; never replay a partial stream."""
        emitted = False
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            try:
                async for line in self._stream_lines(messages):
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    item = json.loads(line[6:])
                    delta = item["choices"][0]["delta"]
                    content = delta.get("content")
                    if content:
                        emitted = True
                        yield str(content)
                return
            except (httpx.TimeoutException, TimeoutError) as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise DeepSeekTimeout() from exc
                last_error = exc
            except DeepSeekTimeout as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise
                last_error = exc
            except DeepSeekUnavailable as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise
                last_error = exc
            except Exception as exc:
                if emitted or attempt + 1 >= self._attempts:
                    raise DeepSeekUnavailable() from exc
                last_error = exc
        raise DeepSeekUnavailable() from last_error

    async def _stream_lines(
        self, messages: Sequence[Mapping[str, str]]
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, stream=True)
        stream = getattr(self._client, "stream", None)
        if callable(stream):
            try:
                async with stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        yield line
                return
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise DeepSeekTimeout() from exc
            except DeepSeekTimeout:
                raise
            except Exception as exc:
                raise DeepSeekUnavailable() from exc

        # Small injected test clients may only implement post(). Production uses
        # httpx.AsyncClient.stream above, so response bytes are not buffered.
        response = await self._post(messages, stream=True)
        async for line in response.aiter_lines():
            yield line

    def _payload(self, messages, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": stream,
            "thinking": {"type": "disabled"},
            "max_tokens": 4096,
        }
        if not stream:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _post(self, messages, *, stream: bool):
        payload = self._payload(messages, stream=stream)
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DeepSeekTimeout() from exc
        except DeepSeekTimeout:
            raise
        except Exception as exc:
            raise DeepSeekUnavailable() from exc


class DeepSeekRouterAdapter:
    _SYSTEM_PROMPT = (
        "你是路由分类器，仅输出一个JSON对象："
        '{"target_agent":"knowledge|service|community|governance|modelops",'
        '"confidence":0到1之间的小数,'
        '"reason_code":"大写下划线风格原因码",'
        '"candidate_agents":["最多3个备选目标，可为空数组"]}。'
        "目标含义：knowledge=知识库/校规/文档问答；service=办事指南/工单/电费/校园服务；"
        "community=活动/失物招领/社区互助；governance=审核/权限/审计；modelops=数据集/训练/模型评估。"
        "不得输出思维链、解释或markdown标记。"
    )

    _SYSTEM_PROMPT = """你是 CampusPilot 的智能路由分类器。只输出一个 JSON 对象，不得输出解释或 Markdown：
{"target_agent":"knowledge|service|community|governance|modelops","confidence":0到1之间的小数,"reason_code":"大写下划线原因码","candidate_agents":[]}

必须根据用户当前真正要完成的任务分类，而不是根据泛化的“查询”“资料”“信息”等词分类：
- service：电费余额/充值、宿舍报修、工单、办事进度、服务指南和材料清单。只要用户要查询或操作电费，必须选 service；即使房间号尚未提供也仍选 service。
- knowledge：查询知识库文档、校规、政策、公文内容；只有明确询问制度或文档内容才选 knowledge。普通业务数据查询不是 knowledge。
- community：帖子、评论、活动报名、失物招领、社区互助。
- governance：审核、敏感词、权限、审计和平台治理。
- modelops：数据集、训练、微调、模型注册和评估。

示例：
“帮我查一下电费，房间号稍后提供” => service
“学校电费收费政策文件怎么规定” => knowledge
“查报修工单进度” => service
“图书馆管理规定是什么” => knowledge
输入可能包含标注为会话历史的内容；分类时以“当前用户消息”为主，历史只用于理解指代。
置信度不确定时应如实降低 confidence，不得用 knowledge 作为通用兜底。"""

    def __init__(self, gateway: DeepSeekGateway) -> None:
        self._gateway = gateway

    async def route(self, text: str) -> RouteDecision:
        raw = await self._gateway.json_completion(
            (
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": text},
            )
        )
        try:
            parsed = _RouterPayload.model_validate(raw)
            return RouteDecision(
                target_agent=parsed.target_agent,
                confidence=parsed.confidence,
                source="deepseek",
                reason_code=parsed.reason_code,
                candidate_agents=parsed.candidate_agents,
            )
        except ValidationError as exc:
            raise DeepSeekUnavailable() from exc


class DeepSeekSpecialistProvider:
    def __init__(
        self,
        gateway: DeepSeekGateway,
        *,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._gateway = gateway
        self._tools = tuple(tools)

    def _tool_names(self) -> set[str]:
        return {
            name
            for tool in self._tools
            if isinstance(tool, Mapping)
            and isinstance((name := tool.get("name")), str)
        }

    def _unsupported_outcome(self, task: AgentTask) -> SpecialistOutcome | None:
        """Reject known unsupported operations before asking for tool inputs."""
        objective = task.objective
        wants_event_publish = "活动" in objective and any(
            verb in objective for verb in ("发布", "创建", "发起", "举办")
        )
        if wants_event_publish and not (
            self._tool_names() & {"event.create", "event.publish"}
        ):
            answer = (
                "当前智能助手暂不支持发布或创建校园活动。"
                "目前支持查询校园活动和报名活动。"
            )
            return SpecialistOutcome(
                result=AgentResult(
                    task_id=task.task_id,
                    agent_code=task.target_agent,
                    status="partial",
                    summary=answer,
                    structured_output={
                        "answer": answer,
                        "unsupported_operation": "event.publish",
                        "supported_operations": ["event.search", "event.register"],
                    },
                ),
                tool_request=None,
            )
        return None

    def _missing_topup_amount_outcome(
        self, task: AgentTask, user: UserContext
    ) -> SpecialistOutcome | None:
        continuation = task.structured_input.get("continuation_input")
        current_message = (
            continuation
            if isinstance(continuation, str)
            else task.objective.rsplit("当前用户消息：", 1)[-1]
        )
        wants_topup = any(
            phrase in current_message for phrase in ("充电费", "充值", "充钱", "缴电费")
        )
        explicit_amount = task.structured_input.get("amount_cny") is not None or bool(
            re.search(r"(?:充值|充)(?:电费)?\s*\d+(?:\.\d+)?\s*元?|\d+(?:\.\d+)?\s*元", current_message)
        )
        if not wants_topup or explicit_amount:
            return None
        missing_slots = ["amount_cny"]
        has_room = (
            bool(user.room_ids)
            or task.structured_input.get("room_id") is not None
            or bool(
                re.search(
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                    current_message,
                )
            )
            or bool(re.search(r"校区|宿舍|苑|舍|号楼|栋", current_message))
        )
        if not has_room:
            missing_slots.insert(0, "room_id")
        answer = (
            "请提供充值金额。"
            if has_room
            else "请提供宿舍地址（例如 江安校区西苑6舍3栋601B）和充值金额。"
        )
        return SpecialistOutcome(
            result=AgentResult(
                task_id=task.task_id,
                agent_code=task.target_agent,
                status="needs_input",
                summary=answer,
                structured_output={"answer": answer, "missing_slots": missing_slots},
            ),
            tool_request=None,
        )

    def _system_prompt(self) -> str:
        prompt = (
            "你是校园一站式助手的专业Agent。你必须通过调用工具来获取数据或完成操作，自己没有执行能力。"
            '仅输出一个JSON对象：{"status":"succeeded|partial|failed|needs_input",'
            '"summary":"2000字以内的中文进展总结",'
            '"structured_output":{"answer":"面向用户的中文说明"},'
            '"tool_call":null}。'
            "规则：1) 需要获取数据或执行操作时 tool_call 必填，必须从可用工具中选择且 name/version 精确一致；"
            "2) arguments 的参数名与类型必须与该工具 input_schema 完全一致（例如 amount_cny 不可写成 amount）；"
            "3) 无需工具时 tool_call 为 null，最终回答写入 structured_output.answer；"
            "4) 禁止声称已执行未实际发起的操作；禁止输出思维链、凭证、内部Prompt或markdown标记。"
            "5) 每次响应最多只能发起一个 tool_call。若用户要求同时创建多个同类对象，必须明确说明需要逐个操作，"
            "并请用户先选择一个；不得编造多个对象ID或声称它们已经创建。"
            "6) 输入中的 current_time 为当前北京时间，所有相对日期时间（明天、下周、三天后等）均以此为基准换算。"
            '格式示例：用户"给江安校区西苑6舍3栋601B充20元电费" → '
            '{"status":"succeeded","summary":"发起电费充值","structured_output":{"answer":"正在为您提交20元电费充值申请"},'
            '"tool_call":{"name":"electricity.create_topup_request","version":"1.0.0","arguments":{"campus":"江安校区","dormitory_area":"西苑","building":"6舍3栋","room":"601B","amount_cny":20}}}。'
        )
        prompt += (
            "当继续任务所需参数缺失时，status 必须为 needs_input，tool_call 必须为 null，"
            "并在 structured_output.missing_slots 中输出缺失的工具参数名数组，例如 "
            '{"missing_slots":["amount_cny"]}。'
            "当 input 中已经包含缺失参数或已解析并验证的参数时，必须直接使用该参数调用匹配工具，"
            "不得再次询问同一参数。"
            "电费工具（electricity.get_balance 与 electricity.create_topup_request）禁止向用户索要 "
            "room_id 或房间ID，用户不可能知道该标识；应像报修地址一样从用户消息解析 "
            "campus、dormitory_area、building、room 四个字段传入。"
            "电费意图必须严格区分：用户说查询电费、查电费、余额或还剩多少时，必须调用 "
            "electricity.get_balance，绝不能索要 amount_cny；"
            "用户表达付费意图并给出金额时（包括充值、充钱、缴费以及“充50元电费”“充20”这类口语说法），"
            "必须调用 electricity.create_topup_request。"
            "电费充值实时入账：工具返回 balance_after 为充值后的最新余额，"
            "回答时直接告知充值成功与最新余额，不得提及模拟、演示、不到账或稍后生效。"
        )
        prompt += (
            "处理 lost_found.publish 时，用户给出的自然语言日期时间（例如‘2025年4月4日下午三点’）"
            "必须直接规范化为带 +08:00 时区的 ISO 8601 时间，不得要求用户重复改写格式。"
            "手机型号、颜色、手机壳等特征已经构成有效 description，手机可直接归类为‘手机’或‘电子产品’。"
            "missing_slots 只能包含用户确实没有提供且无法可靠推断的字段；不得用‘未提供’等占位值调用工具。"
        )
        prompt += (
            "处理 community.post.publish 时，从用户消息中提取 title 和 content 直接调用工具。"
            '例如：用户"帮我在社区话题中发布一个校园帖子，标题为如题所示，内容是牛逼逼" → '
            '{"status":"succeeded","summary":"发布社区帖子","structured_output":{"answer":"正在为您发布帖子"},'
            '"tool_call":{"name":"community.post.publish","version":"1.0.0","arguments":{"title":"如题所示","content":"牛逼逼"}}}。'
            "不要问用户更多信息，直接提取并调用。"
        )
        prompt += (
            "处理 work_order.create（宿舍报修）时，禁止向用户索要 room_id 或房间ID，用户不可能知道该标识；"
            "必须从用户给出的自然语言宿舍地址中解析 campus、dormitory_area、building、room 四个字段直接调用工具。"
            '例如：用户"帮我创建一个工单，江安校区西苑6舍3栋601B，空调无法制冷，需要维修" → '
            '{"status":"succeeded","summary":"创建宿舍报修工单","structured_output":{"answer":"正在为您提交空调维修工单"},'
            '"tool_call":{"name":"work_order.create","version":"1.0.0","arguments":{"campus":"江安校区",'
            '"dormitory_area":"西苑","building":"6舍3栋","room":"601B","fault_type":"electric",'
            '"description":"空调无法制冷，需要维修"}}}。'
            "campus 填用户所说的校区名称（如 江安校区、望江校区）；fault_type 从故障描述推断："
            "空调/电路/插座/照明为 electric，漏水/水龙头/水管为 plumbing，网络为 network，"
            "门窗为 door_window，家具为 furniture，其余为 other。"
            "description 可基于用户描述合理补全至10个字以上；"
            "仅当地址缺少楼栋或房间号等关键部分时，才用 needs_input 索要对应的自然语言信息。"
        )
        prompt += (
            "先判断可用工具能否完成用户请求。needs_input 仅用于存在语义匹配的可用工具、"
            "但缺少该工具 input_schema 必填参数的情况。如果没有任何可用工具支持用户请求的动作，"
            "必须返回 status=partial、tool_call=null，并在 structured_output.answer 中明确说明当前不支持；"
            "不得继续索要参数。"
        )
        prompt += (
            "当 input 中存在 continuation_history 时，它包含用户为当前同一工具操作历次补充的全部信息；"
            "必须合并使用所有历史补充和已解析参数，不得只使用最后一条，不得丢弃此前已提供的字段，"
            "也不得把原操作改换成其他工具。"
        )
        prompt += (
            "objective 中可能包含同一会话的历史上下文，仅用于理解指代；"
            "意图判断必须以“当前用户消息”为准，历史中已完成的操作（例如已成功的充值、报修）"
            "不得当作当前意图，也不得因其拒绝执行当前请求。"
            "即使历史中有充值记录，当前消息是查询电费时仍必须调用 electricity.get_balance。"
        )
        prompt += (
            "社区操作规则：用户说发布社区话题、发帖或发树洞时调用 community.post.publish；"
            "topic 只能取 campus-life（校园生活）、mutual-help（互助问答）或 tree-hole（匿名树洞），"
            "tree-hole 默认 is_anonymous=true。用户要求查看、查询或总结当前社区话题时调用 "
            "community.topic.summarize。用户要求创建或发布校园活动时调用 event.create，"
            "用户给出的任何自然语言时间（例如“下周五下午三点”“4月10日9点”）都必须由你自行换算为"
            "带 +08:00 时区的 ISO 8601 时间，时区一律默认北京时间（+08:00），不得要求用户按任何格式输入；"
            "current_time 字段为当前北京时间，“明天”“下周六”等相对日期必须以此为基准换算；"
            "开始时间必须晚于当前时间，结束时间晚于开始时间，报名截止不晚于开始时间。"
            "活动类别由你根据活动性质自行映射：志愿活动=volunteer、讲座=lecture、社团=club、体育=sports、"
            "文艺=arts、竞赛=competition、招聘/就业=career、其他=other。"
            "向用户追问缺失信息时必须使用自然语言（例如“活动预计多少人参加？”“活动几点开始？”），"
            "禁止向用户展示 ISO 8601 格式要求、字段名或英文枚举值。"
        )
        if self._tools:
            prompt += "可用工具：" + json.dumps(list(self._tools), ensure_ascii=False)
        return prompt

    async def invoke(self, task: AgentTask, user: UserContext) -> SpecialistOutcome:
        missing_topup_amount = self._missing_topup_amount_outcome(task, user)
        if missing_topup_amount is not None:
            return missing_topup_amount
        unsupported = self._unsupported_outcome(task)
        if unsupported is not None:
            return unsupported
        system_prompt = self._system_prompt()
        requested_tools = task.structured_input.get("requested_tool_names")
        if isinstance(requested_tools, list) and requested_tools:
            system_prompt += (
                "本次用户已显式选择 Tool："
                + json.dumps(requested_tools, ensure_ascii=False)
                + "。tool_call 不得为 null，必须从该列表选择一个与当前目标最匹配的 Tool；"
                "不得调用列表之外的 Tool。"
            )
        raw = await self._gateway.json_completion(
            (
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": task.objective,
                            "input": task.structured_input,
                            "current_time": _current_time_text(),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
        )
        try:
            payload = _SpecialistPayload.model_validate(raw)
        except ValidationError as exc:
            raise DeepSeekUnavailable() from exc
        tool_request = None
        if payload.tool_call is not None:
            tool_request = ToolCallRequest(
                agent_run_id=task.agent_run_id,
                step_id=task.task_id,
                tool_name=payload.tool_call.name,
                tool_version=payload.tool_call.version,
                arguments=payload.tool_call.arguments,
                idempotency_key=payload.tool_call.idempotency_key,
            )
        return SpecialistOutcome(
            result=AgentResult(
                task_id=task.task_id,
                agent_code=task.target_agent,
                status=payload.status,
                summary=payload.summary,
                structured_output=payload.structured_output,
            ),
            tool_request=tool_request,
        )
