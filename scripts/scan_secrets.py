"""Heuristic tracked/untracked scan; reports locations, never matching values."""
import re
import subprocess
from pathlib import Path

RULES = {
    "provider key": r"(?<![\w-])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})(?![\w-])",
    "local bearer": r"(?<![A-Za-z0-9_-])[0-9a-f]{32}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "bearer literal": r"Bearer\s+[A-Za-z0-9_.-]{20,}",
    "telegram credential": r"\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b",
    "secret assignment": r"""(?im)^\s*(?:[A-Z_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))\s*=\s*[\x22\x27][A-Za-z0-9_./+-]{16,}[\x22\x27]""",
}


def main():
    names = set(subprocess.check_output(["git","ls-files","-z"]).decode().split("\0"))
    names.update(subprocess.check_output(["git","ls-files","--others","--exclude-standard","-z"]).decode().split("\0"))
    findings = []
    count = 0
    for name in sorted(names - {""}):
        path = Path(name)
        if not path.is_file():
            continue
        count += 1
        content = path.read_bytes().decode("utf-8-sig", errors="replace")
        for rule, pattern in RULES.items():
            for match in re.finditer(pattern, content):
                line = content.count("\n", 0, match.start()) + 1
                findings.append(f"{name}:{line}: {rule}")
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print(f"Secret scan OK: {count} tracked/untracked files; heuristic only")


if __name__ == "__main__":
    main()
