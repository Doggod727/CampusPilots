import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import CommunityMatchConfigInvalid, LostFoundItemNotFound
from app.modules.community.lost_found import LostFoundItemData, LostFoundQueryService
from app.modules.community.models import LostFoundItem
from app.modules.community.repositories import LostFoundRepository
from app.modules.platform.auth import AuthenticatedUser

STOP_WORDS = frozenset({"的", "了", "在", "和", "是", "the", "a", "an", "of", "at"})
FACTORS = ("category", "location", "time", "keyword")


def tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    latin = set(re.findall(r"[a-z0-9]+", normalized))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese = {run[index:index + 2] for run in chinese_runs for index in range(max(len(run) - 1, 1))}
    return {value for value in latin | chinese if value and value not in STOP_WORDS}


def similarity(left: set[str], right: set[str]) -> Decimal:
    if not left or not right:
        return Decimal("0")
    return Decimal(len(left & right)) / Decimal(len(left | right))


@dataclass(frozen=True)
class MatchConfig:
    category: Decimal
    location: Decimal
    time: Decimal
    keyword: Decimal
    threshold: Decimal
    window_days: int


@dataclass(frozen=True)
class MatchData:
    id: UUID
    source_item_id: UUID
    candidate: LostFoundItemData
    score: Decimal
    reasons: tuple[dict[str, object], ...]
    algorithm_version: str
    created_at: object


@dataclass(frozen=True)
class MatchPageData:
    items: tuple[MatchData, ...]
    page: int
    page_size: int
    total: int


def parse_config(values: dict[str, object]) -> MatchConfig:
    try:
        weights = [Decimal(str(values[f"community.match.{name}_weight"]))
                   for name in FACTORS]
        threshold = Decimal(str(values["community.match.threshold"]))
        days = int(values["community.match.time_window_days"])
        if sum(weights) != Decimal("1") or any(weight < 0 or weight > 1 for weight in weights):
            raise ValueError
        if threshold < 0 or threshold > 1 or days < 1 or days > 365:
            raise ValueError
    except (KeyError, ValueError, TypeError, ArithmeticError):
        raise CommunityMatchConfigInvalid() from None
    return MatchConfig(*weights, threshold, days)


def score_match(source: LostFoundItem, candidate: LostFoundItem, config: MatchConfig) -> tuple[Decimal, list[object]]:
    category = Decimal("1") if source.category.casefold() == candidate.category.casefold() else Decimal("0")
    location = similarity(tokens(source.location), tokens(candidate.location))
    delta = abs((source.occurred_at - candidate.occurred_at).total_seconds()) / 86400
    time_score = max(Decimal("0"), Decimal("1") - Decimal(str(delta)) / Decimal(config.window_days))
    keyword = similarity(tokens(f"{source.title} {source.description}"),
                         tokens(f"{candidate.title} {candidate.description}"))
    factors = (category, location, time_score, keyword)
    total = sum((value * weight for value, weight in zip(factors,
        (config.category, config.location, config.time, config.keyword))), Decimal("0"))
    quantized = total.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    reasons = [{"factor": factor, "score": float(value.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)),
                "explanation": {"category": "类别一致度", "location": "地点相似度",
                                "time": "发生时间接近度", "keyword": "描述关键词相似度"}[factor]}
               for factor, value in zip(FACTORS, factors)]
    return quantized, reasons


class LostFoundMatcherService:
    def __init__(self, *, session: AsyncSession, repository: LostFoundRepository,
                 queries: LostFoundQueryService, algorithm_version: str = "rule-v1") -> None:
        self._session, self._repository, self._queries = session, repository, queries
        self._algorithm_version = algorithm_version

    async def recompute(self, source: LostFoundItem) -> None:
        config = parse_config(await self._repository.match_config())
        candidates = await self._repository.match_candidates(source,
            window_start=source.occurred_at - timedelta(days=config.window_days), limit=200)
        keep: set[UUID] = set()
        for candidate in candidates:
            score, reasons = score_match(source, candidate, config)
            if score >= config.threshold:
                await self._repository.upsert_match(source_id=source.id, candidate_id=candidate.id,
                    score=score, reasons=reasons, algorithm_version=self._algorithm_version)
                keep.add(candidate.id)
        await self._repository.delete_stale_matches(source_id=source.id,
            algorithm_version=self._algorithm_version, keep_ids=keep)

    async def list(self, *, actor: AuthenticatedUser, item_id: UUID,
                   page: int, page_size: int, manage_transaction: bool = True) -> MatchPageData:
        async with _transaction(self._session, manage_transaction):
            source = await self._repository.get_for_update(item_id)
            if source is None or source.owner_user_id != actor.user_id:
                raise LostFoundItemNotFound()
            await self.recompute(source)
            rows, total = await self._repository.list_matches(source_id=item_id,
                                                               page=page, page_size=page_size)
            candidates = await self._repository.items_by_ids({row.candidate_item_id for row in rows})
            hydrated = await self._queries._hydrate(actor, tuple(candidates.values()))
            data_by_id = {item.id: item for item in hydrated}
            items = tuple(MatchData(row.id, row.source_item_id, data_by_id[row.candidate_item_id],
                row.score, tuple(row.reasons), row.algorithm_version, row.created_at) for row in rows)
            return MatchPageData(items, page, page_size, total)


@asynccontextmanager
async def _transaction(session: AsyncSession, manage: bool):
    if manage:
        async with session.begin():
            yield
    else:
        yield
