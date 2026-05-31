"""Compliance checking engine with YAML-based rulesets."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import yaml

logger = logging.getLogger(__name__)


class ComplianceEngine:
    """Run compliance checks against device configurations."""

    def __init__(self, db, config_manager=None, rules_dir: str = "./data/compliance") -> None:
        self._db = db
        self._config_mgr = config_manager
        self._rules_dir = rules_dir
        self._rulesets: dict[str, dict] = {}

    async def load_rulesets(self) -> list[dict[str, Any]]:
        self._rulesets.clear()
        if not os.path.isdir(self._rules_dir):
            os.makedirs(self._rules_dir, exist_ok=True)
            return []

        for filename in os.listdir(self._rules_dir):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(self._rules_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if data and data.get("name"):
                        self._rulesets[data["name"]] = data
                except Exception as exc:
                    logger.warning("Failed to load ruleset %s: %s", filename, exc)

        return [{"name": r["name"], "description": r.get("description", ""),
                 "rule_count": len(r.get("rules", []))} for r in self._rulesets.values()]

    async def list_rulesets(self) -> list[dict[str, Any]]:
        if not self._rulesets:
            await self.load_rulesets()
        return [{"name": r["name"], "description": r.get("description", ""),
                 "rule_count": len(r.get("rules", []))} for r in self._rulesets.values()]

    async def run_compliance_check(
        self, device_id: str, ruleset_name: str | None = None,
    ) -> dict[str, Any]:
        if not self._rulesets:
            await self.load_rulesets()

        device = await self._db.get_device(device_id)
        if not device:
            return {"error": "Device not found"}

        # Get latest config
        config_text = ""
        if self._config_mgr:
            try:
                history = await self._config_mgr.get_config_history(device_id, limit=1)
                if history:
                    config_text = history[0].get("config_text", "")
            except Exception:
                pass

        rulesets_to_check = (
            {ruleset_name: self._rulesets[ruleset_name]}
            if ruleset_name and ruleset_name in self._rulesets
            else self._rulesets
        )

        all_results = []
        for rs_name, ruleset in rulesets_to_check.items():
            passed = 0
            failed = 0
            details = []

            for rule in ruleset.get("rules", []):
                check_result = self._check_rule(config_text, rule)
                details.append(check_result)
                if check_result["passed"]:
                    passed += 1
                else:
                    failed += 1

            total = passed + failed
            score = round((passed / total) * 100, 1) if total > 0 else 0

            result_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO compliance_results
                   (id, device_id, ruleset_name, total_checks, passed, failed, score, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result_id, device_id, rs_name, total, passed, failed, score, json.dumps(details), now),
            )

            all_results.append({
                "id": result_id, "device_id": device_id,
                "device_hostname": device.get("hostname", ""),
                "ruleset_name": rs_name,
                "total_checks": total, "passed": passed, "failed": failed,
                "score": score, "details": details, "created_at": now,
            })

        return {"results": all_results}

    async def run_all_devices(self, ruleset_name: str | None = None) -> list[dict[str, Any]]:
        devices = await self._db.list_devices()
        all_results = []
        for device in devices:
            try:
                result = await self.run_compliance_check(device["id"], ruleset_name)
                all_results.extend(result.get("results", []))
            except Exception as exc:
                logger.warning("Compliance check failed for %s: %s", device.get("hostname"), exc)
        return all_results

    def _check_rule(self, config_text: str, rule: dict) -> dict[str, Any]:
        rule_id = rule.get("id", "")
        name = rule.get("name", "")
        check_type = rule.get("check_type", "config_contains")
        pattern = rule.get("pattern", "")
        severity = rule.get("severity", "medium")

        passed = False
        if check_type == "config_contains":
            passed = pattern.lower() in config_text.lower()
        elif check_type == "config_not_contains":
            passed = pattern.lower() not in config_text.lower()
        elif check_type == "config_regex":
            passed = bool(re.search(pattern, config_text, re.IGNORECASE | re.MULTILINE))
        elif check_type == "config_not_regex":
            passed = not bool(re.search(pattern, config_text, re.IGNORECASE | re.MULTILINE))

        return {
            "rule_id": rule_id, "name": name, "severity": severity,
            "check_type": check_type, "pattern": pattern, "passed": passed,
            "remediation": rule.get("remediation", ""),
        }

    async def get_results(
        self, device_id: str | None = None, ruleset_name: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM compliance_results WHERE 1=1"
        params: list[Any] = []
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        if ruleset_name:
            query += " AND ruleset_name = ?"
            params.append(ruleset_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        results = await self._db.fetch_all(query, params)
        # Enrich with hostname
        for r in results:
            device = await self._db.get_device(r["device_id"])
            r["device_hostname"] = device.get("hostname", "") if device else ""
        return results

    async def get_result(self, result_id: str) -> dict[str, Any] | None:
        result = await self._db.fetch_one(
            "SELECT * FROM compliance_results WHERE id = ?", (result_id,)
        )
        if result and result.get("details"):
            try:
                result["details"] = json.loads(result["details"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result
