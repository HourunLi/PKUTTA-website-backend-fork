"""Validate and persist competitions, events, stages, and scores as JSON.

All filesystem paths are derived from validated slugs below ``webpage/database``. Writes
replace one JSON file at a time so interrupted writes cannot truncate a file.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from . import logic


ROOT = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
RESERVED_FILENAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
DISCIPLINES = {"men": "男子", "women": "女子", "mixed": "混合"}
FORMATS = {"single": "单打", "double": "双打", "team": "团体"}
SCHEDULES = {
    "group-knockout": ("group", "knockout"),
    "knockout": ("knockout",),
    "round-robin": ("round_robin",),
}
class DataError(Exception):
    """A request cannot be applied to the tournament data."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{field}不能为空")
    return value.strip()


def _slug(value: Any) -> str:
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise DataError("英文文件名只能使用小写英文字母、数字和连字符")
    if value in RESERVED_FILENAMES:
        raise DataError("英文文件名不能使用系统保留名称")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise DataError(f"{field}必须是正整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{field}必须是正整数") from exc
    if result < 1 or str(value).strip() not in (str(result),):
        raise DataError(f"{field}必须是正整数")
    return result


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise DataError(f"{field}必须是非负整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{field}必须是非负整数") from exc
    if result < 0 or str(value).strip() != str(result):
        raise DataError(f"{field}必须是非负整数")
    return result


def _date(value: Any, field: str) -> str:
    value = _text(value, field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{field}必须是 YYYY-MM-DD 格式") from exc
    if parsed.isoformat() != value:
        raise DataError(f"{field}必须是 YYYY-MM-DD 格式")
    return value


def _placeholder() -> dict[str, Any]:
    return {"kind": "undecided", "members": [], "team": ""}


def _empty_knockout(rounds: int) -> dict[str, Any]:
    return logic.refresh_knockout_bracket({
        "drawn": False,
        "rounds": rounds,
        "slots": [_placeholder() for _ in range(2 ** rounds)],
        "matches": [],
    })


def _atomic_json(path: Path, payload: Any) -> None:
    # Unchanged records keep their modification time, so directory syncs only
    # need to upload files affected by the administrator's latest change.
    if path.is_file() and _read_json(path) == payload:
        _document_json(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _document_json(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as exc:
        raise DataError("数据不存在", 404) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"数据库文件无法读取：{path}", 500) from exc


def _remove_tree(path: Path, parent: Path) -> None:
    """Remove only a verified direct child of the intended directory."""
    if path.resolve().parent != parent.resolve():
        raise DataError(f"目录路径无效：{path}", 500)
    if path.exists():
        shutil.rmtree(path)


def _document_json(path: Path) -> None:
    """Maintain the matching data-file description without rebuilding a site."""
    database = next((parent for parent in path.parents
                     if parent.name == "database" and parent.parent.name == "webpage"), None)
    if database is None:
        return
    relative = path.relative_to(database)
    root = database.parent.parent
    document = root / "docs" / "webpage" / "database" / f"{relative}.md"
    roles = {
        "index.json": "赛事索引，保存赛事英文标识列表。",
        "meta.json": "赛事或项目元数据，记录名称、日期、地点或项目属性。",
        "metadata.json": "阶段元数据，记录阶段类型、选手与种子级别。",
        "data.json": "阶段数据，记录分组、预定时间、桌号和裁判、对阵、场次信息与比分、小组与循环赛排名及其固定抽签顺序。",
    }
    content = (f"# `webpage/database/{relative.as_posix()}`\n\n"
               f"{roles.get(path.name, '比赛 JSON 数据。')}\n\n"
               "这是后台与公开页面共用的唯一数据文件，采用 UTF-8 JSON。"
               "后台直接增量保存；发布时同步 `webpage/` 即可，无需生成或构建。\n")
    if document.is_file() and document.read_text(encoding="utf-8") == content:
        return
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(content, encoding="utf-8")


def _is_header(row: list[str], kind: str) -> bool:
    normal = [re.sub(r"[\s_（）()\-]", "", cell).lower() for cell in row]
    if not normal:
        return False
    first_labels = {
        "single": {"选手", "选手姓名", "姓名", "name", "player"},
        "double": {"选手1", "选手一", "player1", "name1"},
        "team": {"队伍", "队名", "team"},
    }
    if normal[0] not in first_labels[kind]:
        return False
    seed_at = 1 if kind == "team" else (3 if kind == "double" else 2)
    return len(normal) > seed_at and normal[seed_at] in {"种子", "种子级别", "seed", "seeding"}


def parse_entrants(
    csv_text: str, format_name: str, *, allow_negative_one: bool = False
) -> list[dict[str, Any]]:
    """Read CSV with optional header; standalone knockout may use seed -1."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise DataError("请上传选手 CSV 名单")
    if "\ufffd" in csv_text:
        raise DataError("CSV 包含乱码字符（�）。请将原文件以 UTF-8 或 GB18030 编码保存后重新上传。")
    if format_name not in FORMATS:
        raise DataError("项目形式无效")
    try:
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)]
    except csv.Error as exc:
        raise DataError(f"CSV 格式错误：{exc}") from exc
    rows = [row for row in rows if any(row)]
    if rows and _is_header(rows[0], format_name):
        rows.pop(0)
    if not rows:
        raise DataError("CSV 中没有选手")
    entrants: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for number, row in enumerate(rows, 1):
        if format_name == "single":
            if len(row) != 3:
                raise DataError(f"CSV 第 {number} 行必须有 3 列：选手姓名、队伍、种子级别")
            name, team, seed_text = row
            if not name or not team:
                raise DataError(f"CSV 第 {number} 行选手姓名和队伍不能为空")
            participant = {"kind": "player", "members": [name], "team": team}
        elif format_name == "double":
            if len(row) != 4:
                raise DataError(f"CSV 第 {number} 行必须有 4 列：选手1、选手2、队伍、种子级别")
            first, second, team, seed_text = row
            if not first or not second or not team or first == second:
                raise DataError(f"CSV 第 {number} 行须有不同的两名选手和队伍")
            participant = {"kind": "pair", "members": [first, second], "team": team}
        else:
            if len(row) < 3:
                raise DataError(f"CSV 第 {number} 行至少需要队伍、种子级别和一名成员")
            team, seed_text, *members = row
            if not team or not members or any(not member for member in members):
                raise DataError(f"CSV 第 {number} 行队伍和所有成员不能为空")
            participant = {"kind": "team", "members": members, "team": team}
        seed = -1 if allow_negative_one and seed_text == "-1" else _nonnegative_int(seed_text, f"CSV 第 {number} 行种子级别")
        key = (participant["kind"], tuple(participant["members"]), participant["team"])
        if key in seen:
            raise DataError(f"CSV 第 {number} 行有重复选手或队伍")
        seen.add(key)
        entrants.append({"participant": participant, "seed": seed})
    return entrants


class Database:
    """Local JSON repository. The HTTP layer serializes mutations with a lock."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root).resolve()
        webpage = self.root / "webpage"
        if webpage.resolve().parent != self.root:
            raise DataError("网页目录路径无效", 500)
        webpage.mkdir(parents=True, exist_ok=True)
        self.base = webpage / "database"
        if self.base.resolve().parent != webpage.resolve():
            raise DataError("数据库目录路径无效", 500)
        self._migrate_database()
        self.base.mkdir(parents=True, exist_ok=True)
        if not (self.base / "index.json").exists():
            _atomic_json(self.base / "index.json", {"competitions": []})
        self._normalize_database()

    def _migrate_database(self) -> None:
        """Move the legacy authoritative tree, retaining temporary admin scores."""
        legacy = self.root / "database"
        if not legacy.exists():
            return
        if legacy.resolve().parent != self.root:
            raise DataError("旧数据库目录路径无效", 500)
        if not self.base.exists():
            legacy.replace(self.base)
            return
        current_index = _read_json(self.base / "index.json")
        entries = current_index.get("competitions", [])
        if entries and all(isinstance(item, str) for item in entries):
            # A normalized live tree is not a disposable generated snapshot.
            current = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*.json")}
            previous = {p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*.json")}
            if current != previous:
                raise DataError("存在两份不同的实时数据库，无法自动合并", 500)
            _remove_tree(legacy, self.root)
            return
        displaced = self.base.parent / f".database-migration-{secrets.token_hex(6)}"
        self.base.replace(displaced)
        try:
            legacy.replace(self.base)
        except OSError:
            displaced.replace(self.base)
            raise
        _remove_tree(displaced, self.base.parent)

    def _normalize_database(self) -> None:
        """Upgrade old layouts once; public readers use these raw files directly."""
        index = _read_json(self.base / "index.json")
        entries = index.get("competitions") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise DataError("赛事索引格式错误", 500)
        if any(isinstance(item, dict) for item in entries):
            index["competitions"] = [item.get("slug") if isinstance(item, dict) else item for item in entries]
            _atomic_json(self.base / "index.json", index)
        self._rename_legacy_events(index["competitions"])
        for slug in index["competitions"]:
            competition = self._competition_path(slug)
            meta = _read_json(competition / "meta.json")
            for event in meta["events"]:
                event_path = self._event_path(slug, event)
                event_meta = _read_json(event_path / "meta.json")
                for stage in event_meta["stages"]:
                    path = self._stage_path(slug, event, stage) / "data.json"
                    data = _read_json(path)
                    if stage == "group":
                        self._refresh_group_standings(data)
                    elif stage == "knockout":
                        try:
                            data = logic.refresh_knockout_bracket(data)
                        except ValueError as exc:
                            raise DataError(f"淘汰赛数据无效：{exc}", 500) from exc
                    elif stage == "round_robin":
                        self._refresh_round_robin_standings(data)
                    _atomic_json(path, data)
                if "group" in event_meta["stages"] and "knockout" in event_meta["stages"]:
                    group_path = self._stage_path(slug, event, "group")
                    self._sync_knockout_after_group_change(
                        group_path,
                        _read_json(group_path / "metadata.json"),
                        _read_json(group_path / "data.json"),
                    )
                self._remove_aggregate_index(event_path)
            self._remove_aggregate_index(competition)
        for path in self.base.rglob("*.json"):
            _document_json(path)
        docs = self.root / "docs" / "webpage" / "database"
        for document in docs.rglob("*.json.md"):
            data_path = self.base / str(document.relative_to(docs))[:-3]
            if not data_path.exists():
                if not document.resolve().is_relative_to(docs.resolve()):
                    raise DataError("数据文档路径无效", 500)
                document.unlink()
        # Renamed events leave empty documentation folders behind after their
        # obsolete .md files are removed. Remove only empty children of this
        # documentation root, deepest first.
        for directory in sorted(docs.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not directory.resolve().is_relative_to(docs.resolve()):
                raise DataError("数据文档目录路径无效", 500)
            if not any(directory.iterdir()):
                directory.rmdir()
        old_docs = self.root / "docs" / "database"
        if old_docs.exists():
            _remove_tree(old_docs, self.root / "docs")

    def _rename_legacy_events(self, competitions: list[str]) -> None:
        """Shorten old event slugs, including an interrupted previous rename.

        Check every destination before moving any directory so a conflicting
        short slug cannot silently replace an existing event.
        """
        changes = []
        for slug in competitions:
            competition = self._competition_path(slug)
            meta_path = competition / "meta.json"
            meta = _read_json(meta_path)
            events = meta.get("events")
            if not isinstance(events, list):
                raise DataError("赛事项目列表格式错误", 500)
            new_events = []
            for event in events:
                event = _slug(event)
                short = next(
                    (f"{discipline}-{format_name}"
                     for discipline in DISCIPLINES for format_name in FORMATS
                     if event == f"{slug}-{discipline}-{format_name}"),
                    event,
                )
                source = self._event_path(slug, event)
                target = self._event_path(slug, short)
                if short != event and source.exists() and target.exists():
                    raise DataError(f"项目目录冲突：{target}", 500)
                # Source may already have moved when a prior start stopped
                # before updating the competition's event list.
                current = source if source.exists() else target
                event_meta = _read_json(current / "meta.json")
                if short != event:
                    discipline, format_name = short.split("-", 1)
                    if (event_meta.get("discipline") != discipline or
                        event_meta.get("format") != format_name or
                        event_meta.get("slug") not in (event, short)):
                        raise DataError(f"项目元数据与目录不匹配：{current}", 500)
                if short in new_events:
                    raise DataError(f"项目英文文件名重复：{short}", 500)
                new_events.append(short)
                if short != event:
                    changes.append((source, target, current / "meta.json", event_meta))
            if new_events != events:
                updated = {**meta, "events": new_events}
                changes.append((meta_path, updated))
        for change in changes:
            if len(change) == 4:
                source, target, _, event_meta = change
                if source.exists():
                    source.rename(target)
                event_meta["slug"] = target.name
                _atomic_json(target / "meta.json", event_meta)
            else:
                meta_path, updated = change
                _atomic_json(meta_path, updated)

    def _remove_aggregate_index(self, directory: Path) -> None:
        """Discard former snapshots that duplicated the canonical stage data."""
        path = directory / "index.json"
        if path.exists():
            if not path.resolve().is_relative_to(self.base.resolve()):
                raise DataError("汇总索引路径无效", 500)
            path.unlink()

    def _competition_path(self, slug: str) -> Path:
        path = self.base / _slug(slug)
        if path.resolve().parent != self.base.resolve():
            raise DataError("赛事路径无效", 403)
        return path

    def _event_path(self, competition: str, event: str) -> Path:
        competition_path = self._competition_path(competition)
        event = _slug(event)
        path = competition_path / event
        if path.resolve().parent != competition_path.resolve():
            raise DataError("项目路径无效", 403)
        return path

    def _stage_path(self, competition: str, event: str, stage: str) -> Path:
        if stage not in ("group", "knockout", "round_robin"):
            raise DataError("阶段不存在", 404)
        event_path = self._event_path(competition, event)
        path = event_path / stage
        if path.resolve().parent != event_path.resolve():
            raise DataError("阶段路径无效", 403)
        return path

    def _index(self) -> dict[str, Any]:
        index = _read_json(self.base / "index.json")
        if not isinstance(index, dict) or not isinstance(index.get("competitions"), list):
            raise DataError("赛事索引格式错误", 500)
        return index

    def list_competitions(self) -> dict[str, Any]:
        competitions = [self.get_competition(slug)["competition"] for slug in self._index()["competitions"]]
        competitions.sort(key=lambda item: (item["start_date"], item["end_date"], item["slug"]), reverse=True)
        return {"competitions": competitions}

    def get_competition(self, slug: str) -> dict[str, Any]:
        path = self._competition_path(slug)
        meta = _read_json(path / "meta.json")
        events = [_read_json(self._event_path(slug, event) / "meta.json") for event in meta["events"]]
        return {"competition": meta, "events": events}

    def get_event(self, slug: str, event: str) -> dict[str, Any]:
        comp = self.get_competition(slug)["competition"]
        path = self._event_path(slug, event)
        meta = _read_json(path / "meta.json")
        stages = {
            name: {
                "metadata": _read_json(path / name / "metadata.json"),
                "data": _read_json(path / name / "data.json"),
            }
            for name in meta["stages"]
        }
        if "knockout" in stages:
            # Older JSON files do not contain a bronze match. Derive it for
            # every reader without rewriting an existing draw or its scores.
            try:
                stages["knockout"]["data"] = logic.refresh_knockout_bracket(stages["knockout"]["data"])
            except ValueError as exc:
                raise DataError(f"淘汰赛数据无效：{exc}", 500) from exc
        return {"competition": comp, "event": meta, "stages": stages}

    def snapshot(self) -> dict[str, Any]:
        return {
            "competitions": [
                {
                    **self.get_competition(slug),
                    "event_details": [self.get_event(slug, event) for event in self.get_competition(slug)["competition"]["events"]],
                }
                for slug in self._index()["competitions"]
            ]
        }

    def create_competition(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = _text(payload.get("name"), "赛事名称")
        start = _date(payload.get("start_date"), "开始日期")
        end = _date(payload.get("end_date"), "结束日期")
        if end < start:
            raise DataError("结束日期不能早于开始日期")
        venue = _text(payload.get("venue"), "比赛地点")
        slug = _slug(payload["slug"] if "slug" in payload else f"competition-{start.replace('-', '')}-{secrets.token_hex(3)}")
        path = self._competition_path(slug)
        if path.exists() or slug in self._index()["competitions"]:
            raise DataError("赛事英文文件名已存在", 409)
        meta = {"slug": slug, "name": name, "start_date": start, "end_date": end, "venue": venue, "events": []}
        path.mkdir()
        _atomic_json(path / "meta.json", meta)
        index = self._index()
        index["competitions"].append(slug)
        _atomic_json(self.base / "index.json", index)
        return self.get_competition(slug)

    def update_competition(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._competition_path(slug)
        meta = _read_json(path / "meta.json")
        if "slug" in payload and payload["slug"] != slug:
            raise DataError("赛事英文文件名创建后不可修改")
        for field, label in (("name", "赛事名称"), ("venue", "比赛地点")):
            if field in payload:
                meta[field] = _text(payload[field], label)
        for field, label in (("start_date", "开始日期"), ("end_date", "结束日期")):
            if field in payload:
                meta[field] = _date(payload[field], label)
        if meta["end_date"] < meta["start_date"]:
            raise DataError("结束日期不能早于开始日期")
        _atomic_json(path / "meta.json", meta)
        return self.get_competition(slug)

    def delete_competition(self, slug: str) -> dict[str, Any]:
        path = self._competition_path(slug)
        if not (path / "meta.json").is_file():
            raise DataError("赛事不存在", 404)
        index = self._index()
        if slug not in index["competitions"]:
            raise DataError("赛事索引不包含此赛事", 500)
        # A validated direct child of database is the only directory removed.
        if path.resolve().parent != self.base.resolve():
            raise DataError("赛事路径无效", 500)
        shutil.rmtree(path)
        docs = self.root / "docs" / "webpage" / "database"
        _remove_tree(docs / slug, docs)
        index["competitions"].remove(slug)
        _atomic_json(self.base / "index.json", index)
        return self.list_competitions()

    def create_event(self, competition: str, payload: dict[str, Any]) -> dict[str, Any]:
        comp_path = self._competition_path(competition)
        comp_meta = _read_json(comp_path / "meta.json")
        discipline = payload.get("discipline")
        format_name = payload.get("format")
        schedule = payload.get("schedule")
        if discipline not in DISCIPLINES or format_name not in FORMATS or schedule not in SCHEDULES:
            raise DataError("项目性质或赛程无效")
        entrants = parse_entrants(payload.get("csv"), format_name, allow_negative_one=schedule == "knockout")
        stages = list(SCHEDULES[schedule])
        specifications: list[dict[str, int]] = []
        if schedule == "group-knockout":
            try:
                specifications = logic.parse_group_spec(_text(payload.get("group_spec"), "分组表达式"))
            except ValueError as exc:
                raise DataError(str(exc)) from exc
            if sum(item["count"] * item["size"] for item in specifications) != len(entrants):
                raise DataError("分组总人数必须等于 CSV 选手人数")
            bracket_count = sum(item["count"] * item["qualifiers"] for item in specifications)
        else:
            bracket_count = len(entrants)
        rounds = None
        if "knockout" in stages:
            rounds = _positive_int(payload.get("knockout_rounds"), "淘汰赛轮数")
            if not (2 ** (rounds - 1) < bracket_count <= 2 ** rounds):
                raise DataError(f"{rounds} 轮淘汰赛需要超过 {2 ** (rounds - 1)} 且至多 {2 ** rounds} 人")
        event_slug = f"{discipline}-{format_name}"
        event_path = self._event_path(competition, event_slug)
        if event_path.exists() or event_slug in comp_meta["events"]:
            raise DataError("此赛事已有相同项目", 409)
        event_meta = {
            "slug": event_slug,
            "discipline": discipline,
            "format": format_name,
            "name": DISCIPLINES[discipline] + FORMATS[format_name],
            "schedule": schedule,
            "stages": stages,
        }
        stage_files: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        if "group" in stages:
            groups = []
            number = 1
            for spec in specifications:
                for _ in range(spec["count"]):
                    participants = [_placeholder() for _ in range(spec["size"])]
                    groups.append({
                        "id": number,
                        "size": spec["size"],
                        "qualifiers": spec["qualifiers"],
                        "scheduled_time": "",
                        "scheduled_table": "",
                        "scheduled_referee": "",
                        "standings": None,
                        "ranking_draw_order": None,
                        "participants": participants,
                        "matches": logic.round_robin_matches(participants, id_prefix=f"g{number}-m"),
                    })
                    number += 1
            stage_files["group"] = ({"type": "group", "entrants": entrants}, {"drawn": False, "groups": groups})
        if "knockout" in stages:
            knockout_entrants = entrants if schedule == "knockout" else []
            stage_files["knockout"] = (
                {"type": "knockout", "entrants": knockout_entrants},
                _empty_knockout(rounds),
            )
        if "round_robin" in stages:
            participants = [entrant["participant"] for entrant in entrants]
            stage_files["round_robin"] = (
                {"type": "round_robin", "entrants": entrants},
                {"count": len(participants), "participants": participants,
                 "scheduled_time": "", "scheduled_table": "", "scheduled_referee": "",
                 "matches": logic.round_robin_matches(participants),
                 "standings": None, "ranking_draw_order": None},
            )
            self._refresh_round_robin_standings(stage_files["round_robin"][1])
        event_path.mkdir()
        _atomic_json(event_path / "meta.json", event_meta)
        for name, (metadata, data) in stage_files.items():
            _atomic_json(event_path / name / "metadata.json", metadata)
            _atomic_json(event_path / name / "data.json", data)
        comp_meta["events"].append(event_slug)
        _atomic_json(comp_path / "meta.json", comp_meta)
        return self.get_event(competition, event_slug)

    def delete_event(self, competition: str, event: str) -> dict[str, Any]:
        comp_path = self._competition_path(competition)
        meta = _read_json(comp_path / "meta.json")
        path = self._event_path(competition, event)
        if event not in meta["events"] or not (path / "meta.json").is_file():
            raise DataError("项目不存在", 404)
        if path.resolve().parent != comp_path.resolve():
            raise DataError("项目路径无效", 500)
        shutil.rmtree(path)
        docs = self.root / "docs" / "webpage" / "database" / competition
        _remove_tree(docs / event, docs)
        meta["events"].remove(event)
        _atomic_json(comp_path / "meta.json", meta)
        return self.get_competition(competition)

    def _stage(self, competition: str, event: str, stage: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        path = self._stage_path(competition, event, stage)
        metadata = _read_json(path / "metadata.json")
        data = _read_json(path / "data.json")
        if stage == "knockout":
            try:
                data = logic.refresh_knockout_bracket(data)
            except ValueError as exc:
                raise DataError(f"淘汰赛数据无效：{exc}", 500) from exc
        return path, metadata, data

    def draw_stage(self, competition: str, event: str, stage: str) -> dict[str, Any]:
        path, metadata, data = self._stage(competition, event, stage)
        if stage == "round_robin":
            raise DataError("循环赛不需要抽签")
        if stage == "group":
            specs = [{"count": 1, "size": group["size"], "qualifiers": group["qualifiers"]} for group in data["groups"]]
            schedules = [(group.get("scheduled_time", ""), group.get("scheduled_table", ""), group.get("scheduled_referee", ""))
                         for group in data["groups"]]
            try:
                data = {"drawn": True, "groups": logic.draw_groups(metadata["entrants"], specs)}
            except ValueError as exc:
                raise DataError(str(exc)) from exc
            for group, (time, table, referee) in zip(data["groups"], schedules):
                group["scheduled_time"] = time
                group["scheduled_table"] = table
                group["scheduled_referee"] = referee
                for match in group["matches"]:
                    match["time"] = time
                    match["table"] = table
                    match["referee"] = referee
            self._refresh_group_standings(data)
            # Re-drawing groups invalidates the derived knockout entrants and bracket.
            knockout_path = path.parent / "knockout"
            if knockout_path.exists():
                knockout_metadata = _read_json(knockout_path / "metadata.json")
                knockout_data = _read_json(knockout_path / "data.json")
                knockout_metadata["entrants"] = []
                knockout_data = _empty_knockout(knockout_data["rounds"])
                _atomic_json(knockout_path / "metadata.json", knockout_metadata)
                _atomic_json(knockout_path / "data.json", knockout_data)
        else:
            if (path.parent / "group").exists():
                group_metadata = _read_json(path.parent / "group" / "metadata.json")
                group_data = _read_json(path.parent / "group" / "data.json")
                if not group_data["drawn"]:
                    raise DataError("请先完成小组赛抽签与比赛")
                selected = self._qualified_entrants(group_metadata, group_data)
                if selected is None:
                    raise DataError("全部小组场次结束后才能抽淘汰赛")
                metadata["entrants"] = selected
                _atomic_json(path / "metadata.json", metadata)
            try:
                data = logic.draw_knockout(metadata["entrants"], data["rounds"])
            except ValueError as exc:
                raise DataError(str(exc)) from exc
        _atomic_json(path / "data.json", data)
        return self.get_event(competition, event)

    def update_group(
        self, competition: str, event: str, group_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Save a group's plan and copy submitted fields to all its matches."""
        fields = {"scheduled_time": "time", "scheduled_table": "table", "scheduled_referee": "referee"}
        if not isinstance(payload, dict) or not payload or set(payload) - fields.keys():
            raise DataError("仅可设置小组预定时间、预定桌号和裁判")
        if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id < 1:
            raise DataError("小组不存在", 404)
        values = {}
        for field, value in payload.items():
            if not isinstance(value, str):
                raise DataError("小组预定时间、预定桌号和裁判必须是文字")
            values[field] = value.strip()
        path, _, data = self._stage(competition, event, "group")
        group = next((item for item in data["groups"] if item["id"] == group_id), None)
        if group is None:
            raise DataError("小组不存在", 404)
        for field, value in values.items():
            group[field] = value
            for match in group["matches"]:
                match[fields[field]] = value
        _atomic_json(path / "data.json", data)
        return self.get_event(competition, event)

    def update_round_robin_schedule(
        self, competition: str, event: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Save a round-robin plan and apply the supplied fields to its matches."""
        fields = {"scheduled_time": "time", "scheduled_table": "table", "scheduled_referee": "referee"}
        if not isinstance(payload, dict) or not payload or set(payload) - fields.keys():
            raise DataError("仅可设置循环赛预定时间、预定桌号和裁判")
        values = {}
        for field, value in payload.items():
            if not isinstance(value, str):
                raise DataError("循环赛预定时间、预定桌号和裁判必须是文字")
            values[field] = value.strip()
        path, _, data = self._stage(competition, event, "round_robin")
        for field, value in values.items():
            data[field] = value
            for match in data["matches"]:
                match[fields[field]] = value
        _atomic_json(path / "data.json", data)
        return self.get_event(competition, event)

    @staticmethod
    def _refresh_group_standings(data: dict[str, Any]) -> None:
        """Persist ranking evidence and a stable order for any required draw."""
        for group in data["groups"]:
            for field in ("scheduled_time", "scheduled_table", "scheduled_referee"):
                group.setdefault(field, "")
            for match in group["matches"]:
                match.setdefault("referee", "")
            group.setdefault("ranking_draw_order", None)
            if not data.get("drawn") or any(not match.get("finished") for match in group["matches"]):
                group["standings"] = None
                continue
            count = len(group["participants"])
            draw_order = group["ranking_draw_order"]
            if (not isinstance(draw_order, list) or
                any(not isinstance(index, int) or isinstance(index, bool) for index in draw_order) or
                sorted(draw_order) != list(range(count))):
                draw_order = list(range(count))
                secrets.SystemRandom().shuffle(draw_order)
                group["ranking_draw_order"] = draw_order
            try:
                group["standings"] = logic.group_standings(
                    group["participants"], group["matches"], draw_order=draw_order
                )
            except ValueError as exc:
                raise DataError(f"小组 {group['id']} 比赛结果无效：{exc}") from exc

    @staticmethod
    def _refresh_round_robin_standings(data: dict[str, Any]) -> None:
        """Use the group ranking rules after every distinct pairing finishes."""
        for field in ("scheduled_time", "scheduled_table", "scheduled_referee"):
            data.setdefault(field, "")
        participants = data["participants"]
        matches = data["matches"]
        count = len(participants)
        for match in matches:
            match.setdefault("referee", "")
        data.setdefault("ranking_draw_order", None)
        expected_pairs = {(a, b) for a in range(count) for b in range(a + 1, count)}
        actual_pairs = []
        for match in matches:
            a, b = match.get("a"), match.get("b")
            if (isinstance(a, bool) or isinstance(b, bool) or
                not isinstance(a, int) or not isinstance(b, int) or
                not 0 <= a < count or not 0 <= b < count or a == b):
                data["standings"] = None
                return
            actual_pairs.append((min(a, b), max(a, b)))
        if (len(actual_pairs) != count * (count - 1) // 2 or
            set(actual_pairs) != expected_pairs or
            any(not match.get("finished") for match in matches)):
            data["standings"] = None
            return
        draw_order = data["ranking_draw_order"]
        if (not isinstance(draw_order, list) or
            any(not isinstance(index, int) or isinstance(index, bool) for index in draw_order) or
            sorted(draw_order) != list(range(count))):
            draw_order = list(range(count))
            secrets.SystemRandom().shuffle(draw_order)
            data["ranking_draw_order"] = draw_order
        try:
            data["standings"] = logic.group_standings(
                participants, matches, draw_order=draw_order
            )
        except ValueError as exc:
            raise DataError(f"循环赛比赛结果无效：{exc}") from exc

    @staticmethod
    def _qualified_entrants(group_metadata: dict[str, Any], group_data: dict[str, Any]) -> list[dict[str, Any]] | None:
        groups = group_data["groups"]
        seed_count = min(sum(item.get("seed", 0) > 0 for item in group_metadata["entrants"]), len(groups))
        selected: list[dict[str, Any]] = []
        for group_index, group in enumerate(groups):
            try:
                standings = logic.group_standings(
                    group["participants"], group["matches"], draw_order=group.get("ranking_draw_order")
                )
            except ValueError as exc:
                raise DataError(f"小组 {group['id']} 比赛结果无效：{exc}") from exc
            if standings is None:
                return None
            ranks = standings["order"]
            for rank_index, index in enumerate(ranks[:group["qualifiers"]]):
                participant = group["participants"][index]
                virtual_seed = (seed_count - group_index if group_index < seed_count else 0) if rank_index == 0 else -1
                selected.append({
                    "participant": participant,
                    "seed": virtual_seed,
                    "group": group["id"],
                    "group_rank": rank_index,
                })
        return selected

    def _sync_knockout_after_group_change(self, group_path: Path, group_metadata: dict[str, Any], group_data: dict[str, Any]) -> None:
        knockout_path = group_path.parent / "knockout"
        if not knockout_path.exists():
            return
        knockout_metadata = _read_json(knockout_path / "metadata.json")
        knockout_data = _read_json(knockout_path / "data.json")
        selected = self._qualified_entrants(group_metadata, group_data) if group_data["drawn"] else None
        selected = selected or []
        # Existing draws and their scores stay fixed when only the ranking of
        # the same qualifiers changes. Store current virtual seeds so the next
        # explicit draw uses the latest group standings.
        identity = lambda entrants: {
            json.dumps(item["participant"], ensure_ascii=False, sort_keys=True) for item in entrants
        }
        if identity(knockout_metadata["entrants"]) == identity(selected):
            if knockout_metadata["entrants"] != selected:
                knockout_metadata["entrants"] = selected
                _atomic_json(knockout_path / "metadata.json", knockout_metadata)
            return
        knockout_metadata["entrants"] = selected
        knockout_data = _empty_knockout(knockout_data["rounds"])
        _atomic_json(knockout_path / "metadata.json", knockout_metadata)
        _atomic_json(knockout_path / "data.json", knockout_data)

    @staticmethod
    def _find_match(data: dict[str, Any], stage: str, match_id: str) -> dict[str, Any]:
        if stage == "knockout" and match_id == "bronze":
            bronze = data.get("bronze_match")
            if bronze is not None:
                return bronze
            raise DataError("场次不存在", 404)
        matches = data["matches"] if stage != "group" else [match for group in data["groups"] for match in group["matches"]]
        for match in matches:
            if match["id"] == match_id:
                return match
        raise DataError("场次不存在", 404)

    @staticmethod
    def _validate_game(payload: dict[str, Any]) -> list[int | str | None]:
        points: list[int | str | None] = []
        for side in ("a", "b"):
            if side not in payload:
                raise DataError("局比分须包含双方分数")
            value = payload[side]
            if isinstance(value, str) and value.strip().lower() == "wo":
                value = "wo"
            elif value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise DataError("局比分必须是非负整数、wo 或空值")
            points.append(value)
        if points[0] == "wo" and points[1] is None:
            points[1] = 0
        elif points[1] == "wo" and points[0] is None:
            points[0] = 0
        if points[0] is not None and points[1] is not None and points[0] == points[1] and points[0] != "wo":
            raise DataError("一局不能平分")
        return points

    @staticmethod
    def _apply_completion(match: dict[str, Any], explicit: bool | None = None) -> None:
        games = match.get("games", [])
        completed = [(a, b) for a, b in games if a is not None and b is not None]
        try:
            score_a, score_b = logic.big_score(match)
        except ValueError as exc:
            raise DataError(str(exc)) from exc
        decided = bool(completed) and ("wo" in (score_a, score_b) or score_a != score_b)
        if explicit is True and not decided:
            raise DataError("须有明确胜者才能结束场次")
        if explicit is None:
            match["finished"] = bool(match.get("finished")) and decided
        else:
            match["finished"] = explicit

    def mutate_match(
        self,
        competition: str,
        event: str,
        stage: str,
        match_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        game_index: int | None = None,
    ) -> dict[str, Any]:
        path, metadata, data = self._stage(competition, event, stage)
        if stage in ("group", "knockout") and not data.get("drawn"):
            raise DataError("请先抽签，再记录场次")
        match = self._find_match(data, stage, match_id)
        if stage == "knockout" and any(
            match[side].get("kind") not in ("player", "pair", "team") for side in ("a", "b")
        ):
            raise DataError("轮空或待定场次无需录入比分")
        payload = payload or {}
        if action == "add_game":
            match["games"].append(self._validate_game(payload))
            self._apply_completion(match)
        elif action == "edit_game":
            if game_index is None or not 0 <= game_index < len(match["games"]):
                raise DataError("局不存在", 404)
            match["games"][game_index] = self._validate_game(payload)
            self._apply_completion(match)
        elif action == "delete_game":
            if game_index is None or not 0 <= game_index < len(match["games"]):
                raise DataError("局不存在", 404)
            match["games"].pop(game_index)
            self._apply_completion(match)
        elif action == "edit_match":
            if not any(key in payload for key in ("finished", "table", "time", "referee")):
                raise DataError("没有可修改的场次字段")
            for field in ("table", "time", "referee"):
                if field in payload:
                    value = payload[field]
                    if value is not None and not isinstance(value, str):
                        raise DataError(f"{field}必须是文字或空值")
                    match[field] = value.strip() if isinstance(value, str) else None
            # Unfilled rows are temporary while the administrator edits scores.
            # Saving match details commits only complete games.
            match["games"] = [game for game in match["games"] if all(score is not None for score in game)]
            if "finished" in payload:
                if not isinstance(payload["finished"], bool):
                    raise DataError("finished 必须是布尔值")
                self._apply_completion(match, payload["finished"])
            else:
                self._apply_completion(match)
        else:
            raise DataError("不支持的场次操作", 500)
        if stage == "knockout":
            try:
                data = logic.refresh_knockout_bracket(data)
            except ValueError as exc:
                raise DataError(str(exc)) from exc
        elif stage == "group":
            self._refresh_group_standings(data)
            self._sync_knockout_after_group_change(path, metadata, data)
        elif stage == "round_robin":
            self._refresh_round_robin_standings(data)
        _atomic_json(path / "data.json", data)
        return self.get_event(competition, event)
