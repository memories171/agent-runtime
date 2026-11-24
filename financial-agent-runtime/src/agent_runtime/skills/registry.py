from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SkillRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_skills(self) -> list[dict[str, Any]]:
        results = []
        if not self.root.exists():
            return results
        root_resolved = self.root.resolve()
        for path in sorted(self.root.glob("*/SKILL.md")):
            if root_resolved not in path.resolve().parents:
                continue
            metadata, _ = self._parse(path.read_text(encoding="utf-8"))
            tools = [item.strip() for item in metadata.get("tools", "").split(",") if item.strip()]
            scopes = [item.strip() for item in metadata.get("required_scopes", "").split(",") if item.strip()]
            results.append({
                "skill_id": metadata.get("name", path.parent.name),
                "name": metadata.get("name", path.parent.name),
                "display_name": metadata.get("display_name", path.parent.name),
                "description": metadata.get("description", ""),
                "path": str(path),
                "version": metadata.get("version", "1.0.0"),
                "release_status": metadata.get("release_status", "STABLE"),
                "tools": tools,
                "required_scopes": scopes,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        return results

    def load_skill(self, skill_id: str) -> dict[str, str] | None:
        path = self.root / skill_id / "SKILL.md"
        if not path.is_file() or self.root.resolve() not in path.resolve().parents:
            return None
        metadata, body = self._parse(path.read_text(encoding="utf-8"))
        return {"skill_id": metadata.get("name", skill_id), "description": metadata.get("description", ""), "instructions": body}

    @staticmethod
    def _parse(content: str) -> tuple[dict[str, str], str]:
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", content, re.S)
        if not match:
            return {}, content
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")
        return metadata, match.group(2)
