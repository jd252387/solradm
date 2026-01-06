import re

import typer


def parse_shard_spec(spec: str):
    """Parse shard number specifications into matching rules.

    Supports single numbers, ranges (e.g. ``1-3``), and arithmetic
    sequences (e.g. ``2+3-8`` meaning start at 2, step by 3, until 8).
    """
    rules = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        seq_match = re.fullmatch(r"(?:(\d+)?\+(\d+)(?:-(\d+))?)", part)
        if seq_match:
            start = int(seq_match.group(1)) if seq_match.group(1) else 1
            step = int(seq_match.group(2))
            end = int(seq_match.group(3)) if seq_match.group(3) else None
            rules.append(("seq", start, step, end))
            continue
        range_match = re.fullmatch(r"(\d+)-(\d+)", part)
        if range_match:
            rules.append(("range", int(range_match.group(1)), int(range_match.group(2))))
            continue
        if part.isdigit():
            rules.append(("eq", int(part)))
            continue
        raise typer.BadParameter(f"Invalid shard specification '{part}'")
    return rules


def matches_shard_number(rules, shard_num: int) -> bool:
    """Return True if the shard number matches one of the parsed rules."""
    for rule in rules:
        kind = rule[0]
        if kind == "eq" and shard_num == rule[1]:
            return True
        if kind == "range" and rule[1] <= shard_num <= rule[2]:
            return True
        if kind == "seq":
            start, step, end = rule[1], rule[2], rule[3]
            if shard_num >= start and (shard_num - start) % step == 0:
                if end is None or shard_num <= end:
                    return True
    return False


def shard_number_from_name(shard_name: str) -> int | None:
    match = re.findall(r"\d+", shard_name)
    return int(match[0]) if match else None


def matches_shard_name(rules, shard_name: str) -> bool:
    shard_num = shard_number_from_name(shard_name)
    if shard_num is None:
        return False
    return matches_shard_number(rules, shard_num)
